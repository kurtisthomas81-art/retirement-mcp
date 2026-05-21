import math
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor

import numpy as np

import config


# ── Social Security / tax helpers ─────────────────────────────────────────────

def compute_ss_benefit(claimed_age, full_benefit):
    if claimed_age >= 70:
        return full_benefit * 1.24
    elif claimed_age > 67:
        return full_benefit * (1 + 0.08 * (claimed_age - 67))
    elif claimed_age == 67:
        return full_benefit
    else:
        months_early = (67 - claimed_age) * 12
        if months_early <= 36:
            reduction = months_early * (5 / 9) / 100
        else:
            reduction = 36 * (5 / 9) / 100 + (months_early - 36) * (5 / 12) / 100
        return full_benefit * (1 - reduction)


def compute_federal_tax(gross, year_off, infl, filing, reverted=False):
    if gross <= 0:
        return 0.0
    f = (1 + infl) ** year_off
    if reverted:
        std = (12700 if filing == "mfj" else 6350) * f
        if filing == "mfj":
            bkts = [(18650*f,.10),(75900*f,.15),(153100*f,.25),(233350*f,.28),(416700*f,.33),(470700*f,.35),(math.inf,.396)]
        else:
            bkts = [(9325*f,.10),(37950*f,.15),(91900*f,.25),(191650*f,.28),(416700*f,.33),(418400*f,.35),(math.inf,.396)]
    else:
        std = (config.RULES_2026["std_deduction_mfj"] if filing == "mfj" else config.RULES_2026["std_deduction_single"]) * f
        if filing == "mfj":
            bkts = [(23850*f,.10),(96950*f,.12),(206700*f,.22),(394600*f,.24),(501050*f,.32),(751600*f,.35),(math.inf,.37)]
        else:
            bkts = [(11925*f,.10),(48475*f,.12),(103350*f,.22),(197300*f,.24),(250525*f,.32),(626350*f,.35),(math.inf,.37)]
    taxable = max(0.0, gross - std)
    if taxable <= 0:
        return 0.0
    tax, prev = 0.0, 0.0
    for limit, rate in bkts:
        if taxable <= prev:
            break
        tax += (min(taxable, limit) - prev) * rate
        prev = limit
    return max(0.0, tax)


def taxable_ss_amount(ss_annual, other_income, filing):
    if ss_annual <= 0:
        return 0.0
    pi = other_income + ss_annual * 0.5
    lower = 32000 if filing == "mfj" else 25000
    upper = 44000 if filing == "mfj" else 34000
    if pi <= lower:
        return 0.0
    if pi <= upper:
        return min(0.5 * ss_annual, 0.5 * (pi - lower))
    return min(0.85 * ss_annual, 0.5 * (upper - lower) + 0.85 * (pi - upper))


def compute_conversion_amount(trad, ss, rate, year_off, infl, filing):
    if trad <= 0:
        return 0.0
    f = (1 + infl) ** year_off
    std = (config.RULES_2026["std_deduction_mfj"] if filing == "mfj" else config.RULES_2026["std_deduction_single"]) * f
    if filing == "mfj":
        bkts = [(23850*f,.10),(96950*f,.12),(206700*f,.22),(394600*f,.24),(501050*f,.32),(751600*f,.35)]
    else:
        bkts = [(11925*f,.10),(48475*f,.12),(103350*f,.22),(197300*f,.24),(250525*f,.32),(626350*f,.35)]
    top = bkts[-1][0]
    for lim, r in bkts:
        if r >= rate:
            top = lim
            break
    target = std + top
    lo, hi = 0.0, min(trad, target)
    for _ in range(30):
        mid = (lo + hi) / 2
        if mid + taxable_ss_amount(ss, mid, filing) >= target:
            hi = mid
        else:
            lo = mid
    return min((lo + hi) / 2, trad)


def mortality_mult(age):
    if age <= 70:
        return 1.0
    if age <= 80:
        return 1.0 - 0.03 * (age - 70)
    return max(0.15, 0.70 - (0.55 / 15) * (age - 80))


# ── Monte Carlo engine ────────────────────────────────────────────────────────

def _parse_params(params):
    g = lambda k, d: params.get(k, d)
    current_age = int(g("current_age", 45))
    target_age  = int(g("target_age", 62))
    if current_age >= target_age:
        raise ValueError(f"current_age ({current_age}) must be less than target_age ({target_age})")
    if current_age < 18 or current_age > 85:
        raise ValueError(f"current_age must be between 18 and 85, got {current_age}")
    return g, current_age, target_age


def _generate_returns(rng, return_model, n_trials, n_years, mu, sigma, params):
    g = lambda k, d: params.get(k, d)
    if return_model == "fat_tail":
        fat_df = int(g("fat_tail_df", 5))
        raw = rng.standard_t(fat_df, size=(n_trials, n_years))
        return raw * (sigma / np.sqrt(fat_df / (fat_df - 2))) + mu
    elif return_model == "regime_switch":
        mu_bull    = float(g("mu_bull", 0.12));   sigma_bull = float(g("sigma_bull", 0.14))
        mu_bear    = float(g("mu_bear", -0.05));  sigma_bear = float(g("sigma_bear", 0.22))
        p_to_bear  = float(g("p_bull_to_bear", 0.15))
        p_to_bull  = float(g("p_bear_to_bull", 0.35))
        states = np.zeros((n_trials, n_years), dtype=np.int8)
        trans  = rng.random((n_trials, n_years))
        for t in range(1, n_years):
            flip_b = (states[:, t-1] == 0) & (trans[:, t] < p_to_bear)
            flip_u = (states[:, t-1] == 1) & (trans[:, t] < p_to_bull)
            states[:, t] = np.where(flip_b, 1, np.where(flip_u, 0, states[:, t-1]))
        bull_mask = (states == 0)
        return np.where(
            bull_mask,
            rng.normal(mu_bull, sigma_bull, (n_trials, n_years)),
            rng.normal(mu_bear, sigma_bear, (n_trials, n_years)),
        )
    elif return_model == "garch":
        g_omega = float(g("garch_omega", 0.0001))
        g_alpha = float(g("garch_alpha", 0.15))
        g_beta  = float(g("garch_beta",  0.80))
        var_t   = np.full(n_trials, sigma ** 2)
        rets    = np.zeros((n_trials, n_years))
        z = rng.standard_normal((n_trials, n_years))
        for t in range(n_years):
            rets[:, t] = mu + np.sqrt(np.maximum(var_t, 1e-8)) * z[:, t]
            var_t = g_omega + g_alpha * rets[:, t] ** 2 + g_beta * var_t
        return rets
    else:
        return rng.normal(mu, sigma, (n_trials, n_years))


def _init_arrays(n_trials):
    return {
        "arrival_arr":    np.zeros(n_trials),
        "ss_age_arr":     np.zeros(n_trials),
        "ripcord_arr":    np.zeros(n_trials, dtype=bool),
        "breach_arr":     np.zeros(n_trials, dtype=bool),
        "gogo_arr":       np.zeros(n_trials),
        "slgo_arr":       np.zeros(n_trials),
        "nogo_arr":       np.zeros(n_trials),
        "conv_tx_arr":    np.zeros(n_trials),
        "shadow_tx_arr":  np.zeros(n_trials),
        "dd_arr":         np.zeros(n_trials),
        "ph_peak_arr":    np.zeros(n_trials),
        "ph_harv_arr":    np.zeros(n_trials),
        "ph_drawn_arr":   np.zeros(n_trials),
        "ph_refill_arr":  np.zeros(n_trials),
        "ph_funded_arr":  np.zeros(n_trials),
        "euph_arr":       np.zeros(n_trials),
        "aca_mod_arr":    np.zeros(n_trials, dtype=bool),
        "irmaa_mod_arr":  np.zeros(n_trials, dtype=bool),
    }


def _aggregate_results(t0, n_trials, current_age, target_age, end_age, all_paths, moat_paths,
                        bridge_years, arrays, use_ph,
                        spend_paths=None, ss_inc_paths=None,
                        survival_floor=0.0, infl=0.03, rng=None):
    term_vals     = np.maximum(0.0, all_paths[:, end_age - current_age])
    gogo_arr      = arrays["gogo_arr"]
    slgo_arr      = arrays["slgo_arr"]
    nogo_arr      = arrays["nogo_arr"]
    conv_tx_arr   = arrays["conv_tx_arr"]
    shadow_tx_arr = arrays["shadow_tx_arr"]
    dd_arr        = arrays["dd_arr"]

    n_years = end_age - current_age + 1
    ages = list(range(current_age, end_age + 1))
    pcts = [10, 25, 50, 75, 90]
    bands = {"ages": ages}
    for p in pcts:
        bands[f"p{p}"] = [float(np.percentile(all_paths[:, i], p)) for i in range(n_years)]

    moat_ages = list(range(target_age, min(target_age + bridge_years + 1, end_age + 1)))
    moat_bands = {"ages": moat_ages}
    for p in [10, 50, 90]:
        moat_bands[f"p{p}"] = [
            float(np.percentile(moat_paths[:, a - current_age], p))
            for a in moat_ages
        ]

    milestones = sorted({target_age, min(target_age + 5, 70), 70, 75, 80, 85, 90, 95} &
                        set(range(current_age, end_age + 1)))
    mile_out = []
    for ma in milestones:
        idx = ma - current_age
        mile_out.append({"age": ma,
            "p10": float(np.percentile(all_paths[:, idx], 10)),
            "p50": float(np.percentile(all_paths[:, idx], 50)),
            "p90": float(np.percentile(all_paths[:, idx], 90))})

    ruin_by_age = {}
    for ra in [70, 75, 80, 85, 90]:
        if ra > current_age:
            idx = min(ra - current_age, n_years - 1)
            ruin_by_age[str(ra)] = round(float(np.mean(all_paths[:, idx] <= 0)) * 100, 1)

    sav = shadow_tx_arr - conv_tx_arr
    ss_hist = {}
    for a in range(62, 71):
        ss_hist[str(a)] = int(np.sum(arrays["ss_age_arr"] == a))

    total_spend_arr = gogo_arr + slgo_arr + nogo_arr
    spt = total_spend_arr + term_vals
    med_spt = float(np.median(spt))
    lifetime_spend = {
        "p50_total":   round(float(np.median(total_spend_arr))),
        "p90_total":   round(float(np.percentile(total_spend_arr, 90))),
        "p50_gogo":    round(float(np.median(gogo_arr))),
        "p50_slowgo":  round(float(np.median(slgo_arr))),
        "p50_nogo":    round(float(np.median(nogo_arr))),
        "spend_ratio": round(float(np.median(total_spend_arr)) / med_spt * 100, 1) if med_spt > 0 else 0.0,
    }

    euph_arr = arrays["euph_arr"]
    euphoria_stats = {
        "p10_bonus": round(float(np.percentile(euph_arr, 10))),
        "p50_bonus": round(float(np.median(euph_arr))),
        "p90_bonus": round(float(np.percentile(euph_arr, 90))),
    }

    prime_harvest_stats = None
    if use_ph:
        funded = arrays["ph_funded_arr"][arrays["ph_funded_arr"] > 0]
        prime_harvest_stats = {
            "median_peak":       round(float(np.median(arrays["ph_peak_arr"]))),
            "funded_pct":        round(float(len(funded) / n_trials * 100), 1),
            "median_funded_age": round(float(np.median(funded))) if len(funded) else None,
            "median_drawn":      round(float(np.median(arrays["ph_drawn_arr"]))),
            "median_refills":    round(float(np.median(arrays["ph_refill_arr"])), 1),
            "recycled_pct":      round(float(np.mean(arrays["ph_refill_arr"] >= 1) * 100), 1),
        }

    # Per-year spending bands (floor + discretionary, per trial per age)
    sp_bands = None
    ss_line  = None
    floor_line = None
    if spend_paths is not None and np.any(spend_paths > 0):
        sp_bands = {"ages": ages}
        for p in [10, 25, 50, 75, 90]:
            sp_bands[f"p{p}"] = [round(float(np.percentile(spend_paths[:, i], p))) for i in range(n_years)]
        ss_line = [round(float(np.median(ss_inc_paths[:, i]))) for i in range(n_years)]
        floor_line = [
            round(survival_floor * (1 + infl) ** (a - current_age)) if a >= target_age else None
            for a in ages
        ]

    spend_scenarios = {
        "labels": ["Go-Go (62–75)", "Slow-Go (76–85)", "No-Go (86+)"],
        "p10": [round(float(np.percentile(a, 10))) for a in [gogo_arr, slgo_arr, nogo_arr]],
        "p25": [round(float(np.percentile(a, 25))) for a in [gogo_arr, slgo_arr, nogo_arr]],
        "p50": [round(float(np.percentile(a, 50))) for a in [gogo_arr, slgo_arr, nogo_arr]],
        "p75": [round(float(np.percentile(a, 75))) for a in [gogo_arr, slgo_arr, nogo_arr]],
        "p90": [round(float(np.percentile(a, 90))) for a in [gogo_arr, slgo_arr, nogo_arr]],
    }

    return {
        "success_pct":         round(float(np.mean(term_vals > 0) * 100), 1),
        "milestones":          mile_out,
        "bands":               bands,
        "moat_bands":          moat_bands,
        "ruin_by_age":         ruin_by_age,
        "ss_histogram":        ss_hist,
        "lifetime_spend":      lifetime_spend,
        "spend_scenarios":     spend_scenarios,
        "spend_bands":         sp_bands,
        "ss_line":             ss_line,
        "floor_line":          floor_line,
        "prime_harvest_stats": prime_harvest_stats,
        "euphoria_stats":      euphoria_stats,
        "stats": {
            "median_arrival":     round(float(np.median(arrays["arrival_arr"]))),
            "median_ss_age":      round(float(np.median(arrays["ss_age_arr"])), 1),
            "ripcord_rate":       round(float(np.mean(arrays["ripcord_arr"]) * 100), 1),
            "moat_breach_rate":   round(float(np.mean(arrays["breach_arr"]) * 100), 1),
            "median_terminal":    round(float(np.median(term_vals))),
            "median_gogo_spend":  round(float(np.median(gogo_arr))),
            "median_total_spend": round(float(np.median(total_spend_arr))),
            "conv_tax_paid":          round(float(np.median(conv_tx_arr))),
            "tax_savings":            round(float(np.median(sav))),
            "median_drawdown":        round(float(np.median(dd_arr) * 100), 1),
            "aca_cliff_modulated_pct": round(float(np.mean(arrays["aca_mod_arr"]) * 100), 1),
            "irmaa_blast_pct":         round(float(np.mean(arrays["irmaa_mod_arr"]) * 100), 1),
        },
        "trial_count": n_trials,
        "runtime_ms":  round((time.time() - t0) * 1000, 1),
    }


def run_monte_carlo(params, seed=None):
    t0 = time.time()

    g, current_age, target_age = _parse_params(params)

    filing       = g("filing_status", "single")
    end_age      = 95
    n_years      = end_age - current_age + 1

    engine0  = float(g("start_engine", 0))
    sgov0    = float(g("start_sgov", 0))
    chk0     = float(g("start_checking", 0))
    contrib  = float(g("annual_contribution", 0))
    wage_gr  = float(g("wage_growth", 0.02))

    moat_target     = float(g("moat_target", 360000))
    full_moat       = moat_target
    bridge_draw_ann = float(g("bridge_draw_ann", float(g("strict_moat_cost", 72000))))
    survival_floor  = float(g("biological_floor", 17000))

    full_ss     = float(g("full_ss", 36697))
    ss_age_tgt  = int(g("ss_age", 67))
    use_haircut = bool(g("use_ss_haircut", False))
    haircut_pct = float(g("ss_haircut_pct", 0.21))

    mu     = float(g("mean_return", 0.10))
    sigma  = float(g("volatility", 0.15))
    syld   = float(g("sgov_yield", 0.04))
    divyld = float(g("dividend_yield", 0.015))

    infl         = float(g("inflation_rate", 0.03))
    use_si       = bool(g("use_stochastic_inflation", False))
    infl_vol     = float(g("inflation_volatility", 0.01))
    infl_min     = float(g("inflation_min", 0.01))
    infl_max     = float(g("inflation_max", 0.08))
    stag_corr    = float(g("stagflation_corr", 0.30))

    use_aca_shock  = bool(g("use_aca_shock", False))
    aca_shock_prob = float(g("aca_shock_prob", 0.30))
    aca_shock_mag  = float(g("aca_shock_mag", 15000))

    use_tax_rev  = bool(g("use_tax_reversion", False))
    tax_risk_near = float(g("tax_risk_near", 0.20))
    tax_risk_mid  = float(g("tax_risk_mid", 0.40))
    tax_risk_late = float(g("tax_risk_late", 0.60))

    euph_trig      = float(g("euph_trig",      0.20))
    hwm_infl_rate  = float(g("hwm_infl_rate",  0.030))
    max_wr_gogo    = float(g("max_wr_gogo",    0.035))
    max_wr_slowgo  = float(g("max_wr_slowgo",  0.025))
    max_wr_nogo    = float(g("max_wr_nogo",    0.015))
    euph_bonus_pct = float(g("euph_bonus_pct", 0.10))

    use_gf      = bool(g("use_gogo_floor", False))
    gogo_fl_ann = float(g("gogo_floor_monthly", 1000)) * 12

    use_conv         = bool(g("use_conversion", False))
    trad0            = float(g("trad_balance", 0))
    ann_match        = float(g("annual_match", 0))
    ann_roth_contrib = float(g("annual_roth_contrib", 0))
    tgt_bkt   = float(g("target_bracket", 0.12))
    cust_conv = float(g("custom_conv_amt", 0))
    state_tx  = float(g("state_tax_rate", 0.0399))
    use_aca_cliff   = bool(g("use_aca_cliff", False))
    aca_cliff_magi  = float(g("aca_cliff_magi", 60240))
    use_irmaa_blast = bool(g("use_irmaa_blast", False))
    irmaa_t1_magi   = float(g("irmaa_tier1_magi", 106000))

    use_mc  = bool(g("use_medicare_surcharge", False))
    mc_ann  = float(g("medicare_monthly", 500)) * 12
    hc_infl = float(g("healthcare_inflation_rate", 0.05))

    use_tail = bool(g("use_tail_shock", False))
    tail_ret = float(g("tail_shock_return", -0.25))
    tail_cnt = int(g("tail_shock_count", 1))
    use_mr   = bool(g("use_mean_reversion", False))
    mr_str   = float(g("mean_reversion_strength", 0.15))

    p_cap   = float(g("portfolio_cap", 5_000_000))
    cap_infl = float(g("cap_inflation", 0.03))
    cap_gg   = float(g("cap_gogo", 0.10))
    cap_sg   = float(g("cap_slowgo", 0.05))
    cap_ng   = float(g("cap_nogo", 0.02))

    use_super_catchup    = bool(g("use_super_catchup", False))
    super_catchup_annual = float(g("super_catchup_annual", 11250))

    use_mort = bool(g("use_mortality_weighting", True))
    use_res  = bool(g("use_residual_draw", False))
    res_ann  = float(g("residual_draw_monthly", 500)) * 12
    use_ph   = bool(g("use_prime_harvest", False))
    ph_yrs   = float(g("phase3_moat_years", 2))
    tent_rate = float(g("tent_skim_rate", 0.50))

    SIZE_MAP = {"1k": 1_000, "10k": 10_000, "100k": 100_000, "1m": 1_000_000}
    sim_size = g("sim_size", None)
    if sim_size:
        n_trials = SIZE_MAP.get(str(sim_size).lower(), 1_000)
    else:
        n_trials = min(int(g("trials", 1000)), 1_000_000)

    return_model = str(g("return_model", "normal")).lower()
    seq_yr       = int(g("seq_shock_year", 0))

    bridge_years  = max(1, ss_age_tgt - target_age)

    rng = np.random.default_rng(seed)

    rets_all = _generate_returns(rng, return_model, n_trials, n_years, mu, sigma, params)
    infl_all = rng.normal(infl, infl_vol, (n_trials, n_years)) if use_si else None

    all_paths    = np.zeros((n_trials, n_years))
    moat_paths   = np.zeros((n_trials, n_years))
    spend_paths  = np.zeros((n_trials, n_years))
    ss_inc_paths = np.zeros((n_trials, n_years))
    arrays     = _init_arrays(n_trials)

    eng      = np.full(n_trials, engine0)
    sg       = np.full(n_trials, sgov0)
    chk      = np.full(n_trials, chk0)
    trad     = np.full(n_trials, trad0)
    sh_trad  = np.full(n_trials, trad0)
    ph3_moat      = np.zeros(n_trials)
    infl_acc      = np.ones(n_trials)
    infl_acc_at_ret = np.zeros(n_trials)  # infl_acc snapshot at retirement entry
    cum_dev  = np.zeros(n_trials)
    ath      = np.full(n_trials, engine0)
    eng_ret  = np.zeros(n_trials)
    br_moat  = np.zeros(n_trials)
    cl_ss    = np.full(n_trials, float(ss_age_tgt))
    ss_ben   = np.zeros(n_trials)
    ripcord  = np.zeros(n_trials, dtype=bool)
    breached = np.zeros(n_trials, dtype=bool)
    ph3_peak      = np.zeros(n_trials)
    ph_total_harv = np.zeros(n_trials)
    ph_total_drawn = np.zeros(n_trials)
    ph_refills    = np.zeros(n_trials)
    ph_was_drawn  = np.zeros(n_trials, dtype=bool)
    ph_funded_age = np.zeros(n_trials)
    tot_gg   = np.zeros(n_trials)
    tot_sg   = np.zeros(n_trials)
    tot_ng   = np.zeros(n_trials)
    tot_euph = np.zeros(n_trials)
    tot_ctx = np.zeros(n_trials)
    tot_shx = np.zeros(n_trials)
    max_dd  = np.zeros(n_trials)
    yp      = 0

    shock_mask = np.zeros((n_trials, n_years), dtype=bool)
    if use_tail:
        for ti in range(n_trials):
            cands = list(range(target_age, min(target_age + 10, end_age + 1)))
            chosen = random.sample(cands, min(tail_cnt, len(cands)))
            for age_c in chosen:
                shock_mask[ti, age_c - current_age] = True

    if use_tax_rev:
        rv = rng.random(n_trials)
        tax_rev_age_arr = np.where(
            rv < tax_risk_near,
            target_age + rng.integers(0, 5, n_trials),
            np.where(
                rv < tax_risk_near + tax_risk_mid,
                target_age + 5 + rng.integers(0, 5, n_trials),
                np.where(
                    rv < tax_risk_near + tax_risk_mid + tax_risk_late,
                    target_age + 10 + rng.integers(0, 10, n_trials),
                    np.full(n_trials, end_age + 1)
                )
            )
        )
    else:
        tax_rev_age_arr = np.full(n_trials, end_age + 1)
    tax_rev_mult = np.ones(n_trials)

    for ai in range(n_years):
        age = current_age + ai

        tax_rev_mult = np.where(use_tax_rev & (age >= tax_rev_age_arr), 1.13, tax_rev_mult)

        raw = rets_all[:, ai].copy()
        if use_mr and yp > 0:
            raw += -mr_str * cum_dev
        if seq_yr > 0 and age == target_age + seq_yr - 1:
            ret = np.full(n_trials, tail_ret)
        else:
            ret = np.where(shock_mask[:, ai], tail_ret, raw)
        cum_dev = (cum_dev + ret - mu) * 0.9

        if use_si:
            sh = np.where(ret < 0, np.abs(ret) * stag_corr, 0.0)
            ai_infl = np.clip(infl_all[:, ai] + sh, infl_min, infl_max)
        else:
            ai_infl = np.full(n_trials, infl)
        infl_acc *= (1 + ai_infl)

        # ── Accumulation phase ────────────────────────────────────────────────
        if age < target_age:
            c = contrib * ((1 + wage_gr) ** yp)
            # SECURE 2.0 super catch-up: ages 60–63, optional 2-yr sprint pre-retirement
            if use_super_catchup and (target_age - 2) <= age <= (target_age - 1):
                c = c + super_catchup_annual * ((1 + wage_gr) ** yp)
            tent_eligible = (age >= target_age - 4) & (ret > mu) & (sg < full_moat)
            excess = eng * ret - eng * mu
            sk = np.where(tent_eligible & (excess > 0),
                          np.minimum(excess * tent_rate, full_moat - sg), 0.0)
            eng = eng * (1 + ret) + c + ann_roth_contrib * ((1 + wage_gr) ** yp) - sk
            sg  = sg + sk
            sg  = sg * (1 + syld)
            if use_conv:
                trad    = trad * (1 + ret) + ann_match * ((1 + wage_gr) ** yp)
                sh_trad = sh_trad * (1 + ret) + ann_match * ((1 + wage_gr) ** yp)
            all_paths[:, ai] = eng + sg + chk
            yp += 1
            continue

        # ── Retirement transition ─────────────────────────────────────────────
        entering = (age == target_age) & (eng_ret == 0.0)
        if np.any(entering):
            port = eng + sg + chk
            arrays["arrival_arr"] = np.where(entering, port, arrays["arrival_arr"])
            dy      = np.where(entering, np.minimum(port, moat_target), 0.0)
            new_eng = np.where(entering, np.maximum(0.0, port - dy - chk), eng)
            eng_ret = np.where(entering, new_eng, eng_ret)
            br_moat = np.where(entering, dy, br_moat)
            eng     = new_eng
            sg      = np.where(entering, 0.0, sg)
            runway  = np.where(bridge_draw_ann > 0, dy / bridge_draw_ann, float(bridge_years))
            rip     = runway < bridge_years
            ripcord = np.where(entering, rip, ripcord)
            new_cl  = np.where(rip,
                               np.round(np.minimum(70.0, np.maximum(62.0, target_age + runway))),
                               float(ss_age_tgt))
            cl_ss   = np.where(entering, new_cl, cl_ss)
            arrays["ripcord_arr"] = np.where(entering, rip, arrays["ripcord_arr"])
            arrays["ss_age_arr"]  = np.where(entering, new_cl, arrays["ss_age_arr"])
            raw_ss = np.array([compute_ss_benefit(float(a), full_ss) for a in cl_ss])
            raw_ss = np.where(entering, raw_ss, ss_ben)
            ss_ben = np.where(entering,
                              raw_ss * (1 - haircut_pct) if use_haircut else raw_ss,
                              ss_ben)
            infl_acc_at_ret = np.where(entering, infl_acc, infl_acc_at_ret)
            ath = np.where(entering & (eng > ath), eng, ath)

        # ── Bridge phase ──────────────────────────────────────────────────────
        in_bridge = age < cl_ss
        if np.any(in_bridge):
            eng_new = np.where(in_bridge, eng * (1 + ret), eng)
            ath = np.where(in_bridge & (eng_new > ath), eng_new, ath)
            # Bridge draw in retirement-day nominal dollars, growing from retirement date
            bridge_infl = np.where(infl_acc_at_ret > 0,
                                   infl_acc / infl_acc_at_ret,
                                   np.ones(n_trials))
            draw = bridge_draw_ann * bridge_infl
            if use_mc and age < 65:
                draw = draw + mc_ann * ((1 + hc_infl) ** (age - target_age))
            if use_aca_shock and target_age <= age < min(target_age + 3, int(np.min(cl_ss)) + 1):
                aca_hit = rng.random(n_trials) < aca_shock_prob
                draw = np.where(in_bridge & aca_hit, draw + aca_shock_mag * bridge_infl, draw)
            # 6-period SGOV model: pull 60 days of cash at a time (≈6 pulls/year)
            # B_end = B·(1+r6)^6 − (draw/6)·[(1+r6)^6−1]/r6  where r6 = syld/6
            r6        = syld / 6.0
            compound6 = (1.0 + r6) ** 6
            geo_sum   = (compound6 - 1.0) / r6 if r6 > 0.0 else 6.0
            br_moat_new = np.where(in_bridge, br_moat * compound6 - (draw / 6.0) * geo_sum, br_moat)
            overflow = br_moat_new < 0
            eng_new = np.where(in_bridge & overflow, np.maximum(0.0, eng_new + br_moat_new), eng_new)
            br_moat_new = np.where(in_bridge & overflow, 0.0, br_moat_new)
            breached = np.where(in_bridge & overflow, True, breached)
            arrays["breach_arr"] = np.where(in_bridge & overflow, True, arrays["breach_arr"])
            if use_conv:
                conv_eligible = in_bridge & (age <= 74) & (trad > 0)
                if np.any(conv_eligible):
                    trad    = np.where(conv_eligible, trad * (1 + ret), trad)
                    sh_trad = np.where(conv_eligible, sh_trad * (1 + ret), sh_trad)
                    yo = age - current_age
                    cv_arr = np.zeros(n_trials)
                    for ti in np.where(conv_eligible)[0]:
                        if tgt_bkt == "custom":
                            cv_arr[ti] = min(cust_conv, trad[ti])
                        else:
                            cv_arr[ti] = compute_conversion_amount(trad[ti], 0, tgt_bkt, yo, infl, filing)
                        # ACA cliff: only SGOV interest counts as MAGI — principal withdrawals
                        # are return of basis and non-taxable. Cliff is anchored to its
                        # retirement-entry nominal value (COLA'd from today to age 62).
                        if use_aca_cliff and age < 65:
                            sgov_interest_ti = float(br_moat[ti]) * syld
                            cliff_at_ret = aca_cliff_magi * float(infl_acc_at_ret[ti])
                            headroom = max(0.0, cliff_at_ret - sgov_interest_ti)
                            if cv_arr[ti] > headroom:
                                cv_arr[ti] = headroom
                                arrays["aca_mod_arr"][ti] = True
                        # Medicare Blast: at ages 65–66, expand conversions toward IRMAA Tier 1 ceiling
                        elif use_irmaa_blast and 65 <= age <= 66:
                            draw_ti = float(draw[ti]) if hasattr(draw, '__len__') else float(draw)
                            headroom = max(0.0, irmaa_t1_magi * float(infl_acc[ti]) - draw_ti)
                            irmaa_target = min(float(trad[ti]), headroom)
                            if irmaa_target > cv_arr[ti]:
                                cv_arr[ti] = irmaa_target
                                arrays["irmaa_mod_arr"][ti] = True
                    has_conv = conv_eligible & (cv_arr > 0)
                    if np.any(has_conv):
                        fed_arr = np.array([compute_federal_tax(cv_arr[ti], yo, infl, filing)
                                            if has_conv[ti] else 0.0 for ti in range(n_trials)])
                        ttx = fed_arr * tax_rev_mult + cv_arr * state_tx
                        trad = np.where(has_conv, trad - cv_arr, trad)
                        eng_new = np.where(has_conv, eng_new + cv_arr, eng_new)
                        fc = np.minimum(chk, ttx)
                        chk = np.where(has_conv, chk - fc, chk)
                        eng_new = np.where(has_conv, np.maximum(0.0, eng_new - (ttx - fc)), eng_new)
                        tot_ctx = np.where(has_conv, tot_ctx + ttx, tot_ctx)
                    trad = np.where(conv_eligible & (trad < 1), 0.0, trad)
            dd = np.where(ath > 0, (ath - eng_new) / ath, 0.0)
            max_dd = np.maximum(max_dd, dd)
            all_paths[:, ai]  = np.where(in_bridge, eng_new + br_moat_new, all_paths[:, ai])
            moat_paths[:, ai] = np.where(in_bridge, br_moat_new, moat_paths[:, ai])
            spend_paths[:, ai] = np.where(in_bridge, draw, spend_paths[:, ai])
            eng    = np.where(in_bridge, eng_new, eng)
            br_moat = np.where(in_bridge, br_moat_new, br_moat)
            yp += 1
            continue

        # ── SS claim — merge moat into engine ─────────────────────────────────
        at_ss = (age == cl_ss.astype(int)) & (br_moat > 0)
        eng = np.where(at_ss, eng + br_moat, eng)
        br_moat = np.where(at_ss, 0.0, br_moat)

        # ── Dynamic Decumulation Protocol ─────────────────────────────────────
        s_eng = eng.copy()
        divs  = s_eng * divyld
        eng   = eng * (1 + ret)
        mgain = eng - s_eng

        if use_ph:
            ph3_moat *= (1 + syld)
            euph     = ret >= euph_trig
            tgt_ph   = bridge_draw_ann * infl_acc * ph_yrs
            can_harv = euph & (mgain > 0) & (ph3_moat < tgt_ph)
            hv = np.where(can_harv, np.minimum(tgt_ph - ph3_moat, mgain), 0.0)
            eng -= hv; ph3_moat += hv; mgain -= hv
            ph_total_harv += hv
            newly_funded  = can_harv & (ph3_moat >= tgt_ph)
            ph_funded_age = np.where(newly_funded & (ph_funded_age == 0), age, ph_funded_age)
            refilling     = can_harv & ph_was_drawn
            ph_refills    = np.where(refilling, ph_refills + 1, ph_refills)
            ph_was_drawn  = np.where(refilling, False, ph_was_drawn)
            ph3_peak      = np.maximum(ph3_peak, ph3_moat)

        ss_now    = ss_ben * infl_acc
        floor_now = survival_floor * infl_acc
        if use_mc and age < 65:
            floor_now = floor_now + mc_ann * ((1 + hc_infl) ** (age - target_age))
        gap = np.maximum(0.0, floor_now - ss_now)
        fd = np.minimum(divs, gap); divs -= fd; eng -= fd; gap -= fd
        if use_ph:
            ph_d = np.where(gap > 0, np.minimum(ph3_moat, gap), 0.0)
            ph3_moat -= ph_d; gap -= ph_d
            ph_total_drawn += ph_d
            ph_was_drawn = ph_was_drawn | (ph_d > 0)
        eng = np.maximum(0.0, eng - gap)

        if use_res and not ripcord.all():
            rgap = np.maximum(0.0, res_ann * infl_acc - ss_now)
            rgap = np.where(ripcord, 0.0, rgap)
            fd2 = np.minimum(divs, rgap); divs -= fd2; eng -= fd2; rgap -= fd2
            if use_ph:
                ph_d2 = np.where(rgap > 0, np.minimum(ph3_moat, rgap), 0.0)
                ph3_moat -= ph_d2; rgap -= ph_d2
                ph_total_drawn += ph_d2
                ph_was_drawn = ph_was_drawn | (ph_d2 > 0)
            eng = np.maximum(0.0, eng - rgap)

        # ── Drawdown tracking ─────────────────────────────────────────────────
        dd = np.where(ath > 0, np.maximum(0.0, (ath - eng) / ath), 0.0)
        max_dd = np.maximum(max_dd, dd)

        # ── Crest Line from trailing HWM ──────────────────────────────────────
        hwm_c = ath * (1 + hwm_infl_rate)
        above_crest = eng > hwm_c

        # ── Phase-adjusted SWR ceiling ────────────────────────────────────────
        max_wr_phase = np.where(age <= 75, max_wr_gogo,
                       np.where(age <= 85, max_wr_slowgo, max_wr_nogo))
        if use_mort:
            mm = mortality_mult(age)
            max_wr_phase = max_wr_phase * mm

        # ── Standard skim: exact excess over crest, capped at phase ceiling ───
        excess_crest  = np.where(above_crest, eng - hwm_c, 0.0)
        standard_skim = np.where(above_crest,
                            np.minimum(excess_crest, eng * max_wr_phase),
                            0.0)

        # ── Euphoria Tranche: additive bonus, no ceiling ──────────────────────
        euph_vec      = ret >= euph_trig
        euph_excess_r = np.maximum(0.0, ret - euph_trig)
        euph_bonus    = np.where(euph_vec,
                            s_eng * euph_excess_r * euph_bonus_pct,
                            0.0)

        skim = np.minimum(standard_skim + euph_bonus, eng)
        tot_euph += euph_bonus

        # ── Apply skim; stamp trailing HWM post-withdrawal ────────────────────
        eng -= skim
        ath  = np.maximum(ath, eng)

        tot_gg = np.where(age <= 75, tot_gg + skim, tot_gg)
        tot_sg = np.where((age > 75) & (age <= 85), tot_sg + skim, tot_sg)
        tot_ng = np.where(age > 85, tot_ng + skim, tot_ng)
        spend_yr = floor_now + skim

        if use_gf and age <= 75:
            top = np.maximum(0.0, gogo_fl_ann * infl_acc - skim)
            a = np.minimum(top, np.maximum(0.0, eng))
            eng -= a; tot_gg += a
            spend_yr = spend_yr + a

        cr   = np.where(age <= 75, cap_gg, np.where(age <= 85, cap_sg, cap_ng))
        nc   = p_cap * ((1 + cap_infl) ** (age - target_age))
        over_cap = eng > nc
        hc   = np.where(over_cap, (eng - nc) * cr, 0.0)
        eng -= hc
        tot_gg = np.where(over_cap & (age <= 75), tot_gg + hc, tot_gg)
        tot_sg = np.where(over_cap & (age > 75) & (age <= 85), tot_sg + hc, tot_sg)
        tot_ng = np.where(over_cap & (age > 85), tot_ng + hc, tot_ng)
        spend_yr = spend_yr + hc
        spend_paths[:, ai]  = spend_yr
        ss_inc_paths[:, ai] = ss_now

        yo = age - current_age
        if use_conv and age <= 74:
            conv_ok = trad > 0
            if np.any(conv_ok):
                trad    = np.where(conv_ok, trad * (1 + ret), trad)
                sh_trad = np.where(conv_ok, sh_trad * (1 + ret), sh_trad)
                cv_arr  = np.zeros(n_trials)
                for ti in np.where(conv_ok)[0]:
                    if tgt_bkt == "custom":
                        cv_arr[ti] = min(cust_conv, trad[ti])
                    else:
                        cv_arr[ti] = compute_conversion_amount(trad[ti], ss_ben[ti] * infl_acc[ti], tgt_bkt, yo, infl, filing)
                has_cv = conv_ok & (cv_arr > 0)
                if np.any(has_cv):
                    for ti in np.where(has_cv)[0]:
                        ss_n = ss_ben[ti] * infl_acc[ti]
                        twc  = taxable_ss_amount(ss_n, cv_arr[ti], filing)
                        tnc  = taxable_ss_amount(ss_n, 0, filing)
                        fed  = max(0.0,
                            compute_federal_tax(cv_arr[ti] + twc, yo, infl, filing) -
                            compute_federal_tax(tnc, yo, infl, filing))
                        ttx  = fed * tax_rev_mult[ti] + cv_arr[ti] * state_tx
                        trad[ti] -= cv_arr[ti]; eng[ti] += cv_arr[ti]
                        fc = min(chk[ti], ttx); chk[ti] -= fc
                        eng[ti] = max(0.0, eng[ti] - (ttx - fc))
                        tot_ctx[ti] += ttx
                trad = np.where(conv_ok & (trad < 1), 0.0, trad)

        if use_conv and age >= 75:
            rmd_ok = trad > 0
            if np.any(rmd_ok):
                trad   = np.where(rmd_ok, trad * (1 + ret), trad)
                rf_val = config.RMD_TABLE.get(min(age, 95), 8.6)
                rmd    = np.where(rmd_ok, trad / rf_val, 0.0)
                for ti in np.where(rmd_ok)[0]:
                    ss_n = ss_ben[ti] * infl_acc[ti]
                    tss  = taxable_ss_amount(ss_n, rmd[ti], filing)
                    fed  = max(0.0,
                        compute_federal_tax(rmd[ti] + tss, yo, infl, filing) -
                        compute_federal_tax(tss, yo, infl, filing))
                    st = rmd[ti] * state_tx
                    eng[ti] += max(0.0, rmd[ti] - fed - st)
                    trad[ti] -= rmd[ti]
                    tot_ctx[ti] += fed + st
                    if sh_trad[ti] > 0:
                        sh_trad[ti] *= (1 + ret[ti])
                        sr  = sh_trad[ti] / rf_val
                        sts = taxable_ss_amount(ss_n, sr, filing)
                        sf  = max(0.0,
                            compute_federal_tax(sr + sts, yo, infl, filing) -
                            compute_federal_tax(sts, yo, infl, filing))
                        tot_shx[ti] += sf + sr * state_tx
                        sh_trad[ti] -= sr
                        if sh_trad[ti] < 0:
                            sh_trad[ti] = 0.0
                trad = np.where(rmd_ok & (trad < 0), 0.0, trad)

        all_paths[:, ai] = np.maximum(0.0, eng + ph3_moat)
        yp += 1

    arrays["gogo_arr"]      = tot_gg
    arrays["slgo_arr"]      = tot_sg
    arrays["nogo_arr"]      = tot_ng
    arrays["euph_arr"]      = tot_euph
    arrays["conv_tx_arr"]   = tot_ctx
    arrays["shadow_tx_arr"] = tot_shx
    arrays["dd_arr"]        = max_dd
    if use_ph:
        arrays["ph_peak_arr"]   = ph3_peak
        arrays["ph_harv_arr"]   = ph_total_harv
        arrays["ph_drawn_arr"]  = ph_total_drawn
        arrays["ph_refill_arr"] = ph_refills
        arrays["ph_funded_arr"] = ph_funded_age

    return _aggregate_results(
        t0, n_trials, current_age, target_age, end_age,
        all_paths, moat_paths, bridge_years, arrays, use_ph,
        spend_paths=spend_paths, ss_inc_paths=ss_inc_paths,
        survival_floor=survival_floor, infl=infl,
    )


# ── ProcessPoolExecutor helpers ───────────────────────────────────────────────

def _run_mc_worker(params):
    return run_monte_carlo(params)


def _run_grid_sync(params_list):
    workers = min(os.cpu_count() or 4, len(params_list))
    with ProcessPoolExecutor(max_workers=workers) as ex:
        return list(ex.map(_run_mc_worker, params_list))
