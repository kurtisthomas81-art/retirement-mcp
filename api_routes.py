import asyncio
import itertools
import os
import time
from pathlib import Path

import requests
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles

import config
import excel_reader
import monte_carlo

STATIC_DIR = Path(__file__).parent / "static"
FINN_MEMORY_PATH = config.FINN_MEMORY_PATH


# ── Finn advisor helpers ──────────────────────────────────────────────────────

def _read_finn_memory() -> str:
    if not os.path.exists(FINN_MEMORY_PATH):
        return ""
    try:
        with open(FINN_MEMORY_PATH, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def _read_finn_brain() -> str:
    path = config.FINN_BRAIN_PATH
    if not os.path.exists(path):
        return ""
    try:
        with open(path, "r") as f:
            return f.read().strip()
    except Exception:
        return ""


def _fmt_system_prompt():
    from datetime import date
    dob         = config.CLIENT_DOB
    today_d     = date.today()
    age         = today_d.year - dob.year - ((today_d.month, today_d.day) < (dob.month, dob.day))
    retire_year = dob.year + config.CLIENT_RETIRE_AGE
    years_to_retire = retire_year - today_d.year
    return config.SYSTEM_PROMPT.format(
        today=today_d.isoformat(),
        age=age,
        retire_year=retire_year,
        years_to_retire=years_to_retire,
        employer=config.CLIENT_EMPLOYER,
    )


def _build_context_string(sim_data, dashboard_data):
    lines = []
    if sim_data:
        s = sim_data.get("stats", {})
        lines += [
            f"MONTE CARLO SIMULATION ({sim_data.get('trial_count', 0):,} trials):",
            f"  Success rate (alive at 95): {sim_data.get('success_pct')}%",
            f"  Arrival wealth at retirement (median): ${s.get('median_arrival', 0):,.0f}",
            f"  SS claim age (median): {s.get('median_ss_age')}",
            f"  Early SS (ripcord) rate: {s.get('ripcord_rate')}%",
            f"  SGOV moat breach rate: {s.get('moat_breach_rate')}%",
            f"  Terminal wealth at 95 (median): ${s.get('median_terminal', 0):,.0f}",
            f"  Go-Go discretionary spend (median): ${s.get('median_gogo_spend', 0):,.0f}",
            f"  Max drawdown (median): {s.get('median_drawdown')}%",
        ]
        if s.get("conv_tax_paid", 0) > 0:
            lines.append(f"  Roth conversion tax (median): ${s['conv_tax_paid']:,.0f}")
            lines.append(f"  Roth conversion tax savings (median): ${max(0, s.get('tax_savings', 0)):,.0f}")
        ruin = sim_data.get("ruin_by_age", {})
        if ruin:
            lines.append("  Ruin probability by age: " + ", ".join(f"age {a}: {v}%" for a, v in ruin.items()))
        ms = sim_data.get("milestones", [])
        if ms:
            lines.append("  Wealth percentiles (P10 / P50 / P90):")
            for m in ms:
                lines.append(f"    Age {m['age']}: ${m['p10']:,.0f} / ${m['p50']:,.0f} / ${m['p90']:,.0f}")
        rs = sim_data.get("ratchet_stats")
        if rs:
            lines.append(f"  Abundance ratchet Tier 1 (150%): {rs.get('tier1_pct')}% of trials, median age {rs.get('median_tier1_age')}")
        ls = sim_data.get("lifetime_spend")
        if ls:
            lines.append(f"  Lifetime spend P50: ${ls.get('p50_total', 0):,.0f} "
                         f"(go-go ${ls.get('p50_gogo', 0):,.0f} / slow-go ${ls.get('p50_slowgo', 0):,.0f} / no-go ${ls.get('p50_nogo', 0):,.0f})")
    if dashboard_data:
        m  = dashboard_data.get("metrics", {})
        mc = dashboard_data.get("mc_prefill", {})
        lnw       = m.get("LIQUID NET WORTH", 0) or 0
        tnw       = m.get("TOTAL NET WORTH", 0) or 0
        pct       = float(m.get("PROGRESS TO FI", 0) or 0) * 100
        fi_target = m.get("FI TARGET (Age 62)", 0) or 0
        runway    = m.get("SURVIVAL RUNWAY")
        lines += [
            "\nCURRENT FINANCIAL SNAPSHOT:",
            f"  Liquid net worth: ${lnw:,.0f}",
            f"  Total net worth: ${tnw:,.0f}",
            f"  FI progress: {pct:.1f}%  (target: ${fi_target:,.0f})",
        ]
        if runway:
            lines.append(f"  Survival runway: {runway}")
        if mc:
            if mc.get("engine_balance"):    lines.append(f"  VTI/brokerage balance: ${mc['engine_balance']:,.0f}")
            if mc.get("sgov_balance"):      lines.append(f"  SGOV / bridge fund balance: ${mc['sgov_balance']:,.0f}")
            if mc.get("checking_balance"):  lines.append(f"  Checking balance: ${mc['checking_balance']:,.0f}")
            if mc.get("full_ss_annual"):    lines.append(f"  Projected SS benefit (age 67): ${mc['full_ss_annual']:,.0f}/yr")
            if mc.get("monthly_burn"):      lines.append(f"  Monthly burn rate: ${mc['monthly_burn']:,.0f}")
            if mc.get("annual_floor_cost"): lines.append(f"  Annual floor cost: ${mc['annual_floor_cost']:,.0f}")
            if mc.get("net_monthly_income"): lines.append(f"  Net monthly income: ${mc['net_monthly_income']:,.0f}")

    lines += [
        "\nPLAN TARGETS (fixed numbers — cite these directly, do not recalculate):",
        "  Bridge account (T-bills) goal by age 62: $360,000",
        "  Annual draw from bridge (ages 62–67):     $72,000/yr, grows with inflation",
        "  Living expense floor:                     $17,000/yr in today's dollars",
        "  Social Security at 67:                    $36,697/yr in today's dollars",
        "  Bridge window:                            5 years (age 62 to 67)",
        "  Post-67 withdrawal rate:                  0% — Social Security covers everything",
    ]

    if dashboard_data and dashboard_data.get("freedom_levels"):
        lines.append("\nFREEDOM LEVELS:")
        for lv in dashboard_data["freedom_levels"]:
            prog   = lv.get("progress")
            status = lv.get("status") or ("✓ Achieved" if (prog and prog >= 1) else "… Pending")
            goal   = f"  (goal: ${lv['goal']:,.0f})" if isinstance(lv.get("goal"), (int, float)) else ""
            lines.append(f"  {lv['name']}{goal}: {status}")

    if dashboard_data and dashboard_data.get("allocation"):
        lines.append("\nASSET ALLOCATION:")
        for k, v in dashboard_data["allocation"].items():
            lines.append(f"  {k}: {v:.1f}%")

    if dashboard_data and dashboard_data.get("spending") and dashboard_data.get("spending_months"):
        sp_months = dashboard_data["spending_months"]
        period = f"{sp_months[0]}–{sp_months[-1]}" if len(sp_months) > 1 else (sp_months[0] if sp_months else "N/A")
        lines.append(f"\nSPENDING (monthly avg, {period}):")
        for cat, vals in dashboard_data["spending"].items():
            nums = [v for v in vals if isinstance(v, (int, float))]
            if nums:
                lines.append(f"  {cat}: ${sum(nums)/len(nums):,.0f}/mo avg")

    r = config.RULES_2026
    lines += [
        "\n2026 TAX & RETIREMENT RULES:",
        f"  401k limit: ${r['contrib_401k']:,}  |  Catch-up 50+: +${r['catchup_50_plus']:,}  |  Super catch-up 60–63: +${r['super_catchup_60_63']:,}",
        f"  IRA/Roth: ${r['ira_roth_limit']:,} (<50) / ${r['ira_roth_50_plus']:,} (50+)  |  SIMPLE IRA: ${r['simple_ira_limit']:,}",
        f"  Roth-ification threshold: ${r['rothification_income_threshold']:,} FICA wages (prior year)",
        f"  Std deduction: ${r['std_deduction_single']:,} single / ${r['std_deduction_mfj']:,} MFJ  (+${r['senior_addl_deduction_single']:,}/${r['senior_addl_deduction_mfj']:,} if 65+)",
        f"  Senior bonus deduction: ${r['senior_bonus_deduction']:,} if 65+ and MAGI < ${r['senior_bonus_magi_single']:,} single / ${r['senior_bonus_magi_mfj']:,} MFJ",
        f"  LTCG 0%: ≤${r['ltcg_0pct_single']:,} single / ≤${r['ltcg_0pct_mfj']:,} MFJ  |  NIIT 3.8%: MAGI >${r['niit_threshold_single']:,} single / >${r['niit_threshold_mfj']:,} MFJ",
        f"  Roth IRA phase-out: ${r['roth_phaseout_single_low']:,}–${r['roth_phaseout_single_high']:,} single / ${r['roth_phaseout_mfj_low']:,}–${r['roth_phaseout_mfj_high']:,} MFJ",
    ]

    memory = _read_finn_memory()
    if memory:
        lines.append(f"\nFINN'S MEMORY (corrections — follow these absolutely):\n{memory}")

    return "\n".join(lines) if lines else "No financial data available."


# ── Static file handlers ──────────────────────────────────────────────────────

async def dashboard(request: Request):
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(html, headers={"Cache-Control": "no-store, no-cache, must-revalidate", "Pragma": "no-cache"})


async def manifest(request: Request):
    return Response((STATIC_DIR / "manifest.json").read_text(), media_type="application/manifest+json")


async def service_worker(request: Request):
    return Response((STATIC_DIR / "sw.js").read_text(), media_type="application/javascript")


async def icon_svg(request: Request):
    return Response((STATIC_DIR / "icon.svg").read_text(), media_type="image/svg+xml")


# ── REST API handlers ─────────────────────────────────────────────────────────

async def api_rules(request: Request):
    return JSONResponse(config.RULES_2026)


async def api_finn_memory_get(request: Request):
    return JSONResponse({"memory": _read_finn_memory()})


async def api_finn_memory_add(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    note = str(body.get("note", "")).strip()
    if not note:
        return JSONResponse({"error": "note required"}, status_code=400)
    today = time.strftime("%Y-%m-%d")
    entry = f"- [{today}] {note}\n"
    with open(FINN_MEMORY_PATH, "a") as f:
        f.write(entry)
    return JSONResponse({"ok": True, "entry": entry.strip()})


async def api_ledger_dashboard(request: Request):
    try:
        data = await asyncio.to_thread(excel_reader.read_dashboard_data)
        if "error" in data:
            return JSONResponse(data, status_code=503)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_monte_carlo(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        result = await asyncio.to_thread(monte_carlo.run_monte_carlo, body)
        return JSONResponse(result)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_portfolio(request: Request):
    try:
        holdings = await asyncio.to_thread(excel_reader.read_portfolio_data)
        if isinstance(holdings, dict) and "error" in holdings:
            return JSONResponse(holdings, status_code=503)
        for h in holdings:
            if h["cached_price"] and h["shares"]:
                h["cached_value"] = round(h["cached_price"] * h["shares"], 2)
            else:
                h["cached_value"] = None
        return JSONResponse({"holdings": holdings, "as_of": "cached"})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_portfolio_refresh(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    api_key = body.get("api_key", "").strip()
    if not api_key:
        return JSONResponse({"error": "API key required"}, status_code=400)

    def _refresh_logic():
        holdings = excel_reader.read_portfolio_data()
        if isinstance(holdings, dict) and "error" in holdings:
            return holdings
        for h in holdings:
            if h.get("is_proxy"):
                h["live_price"]    = h["cached_price"]
                h["live_value"]    = h.get("cached_value")
                h["gain_loss"]     = None
                h["gain_loss_pct"] = None
                h["proxy_note"]    = "Voya proxy — cached only"
                continue
            try:
                price = excel_reader.fetch_av_price(h["ticker"], api_key, h["is_crypto"])
                h["live_price"] = price
                h["live_value"] = round(price * h["shares"], 2) if price else None
                cost_basis = (h["avg_cost"] or 0) * h["shares"]
                if price and h["avg_cost"] and cost_basis > 0:
                    h["gain_loss"]     = round(h["live_value"] - cost_basis, 2)
                    h["gain_loss_pct"] = round(h["gain_loss"] / cost_basis * 100, 2)
                else:
                    h["gain_loss"] = None; h["gain_loss_pct"] = None
            except Exception as ex:
                h["live_price"] = None; h["error"] = str(ex)
            time.sleep(0.3)  # inside sync thread — asyncio.sleep not available here
        total_value     = sum(h.get("live_value") or 0 for h in holdings)
        total_cost      = sum((h.get("avg_cost") or 0) * h["shares"] for h in holdings)
        total_gain_loss = total_value - total_cost
        return {
            "holdings": holdings,
            "summary": {
                "total_value":         round(total_value, 2),
                "total_cost":          round(total_cost, 2),
                "total_gain_loss":     round(total_gain_loss, 2),
                "total_gain_loss_pct": round(total_gain_loss / total_cost * 100, 2) if total_cost else 0,
            },
            "as_of": "live",
        }

    try:
        result_data = await asyncio.to_thread(_refresh_logic)
        if isinstance(result_data, dict) and "error" in result_data:
            return JSONResponse(result_data, status_code=503)
        return JSONResponse(result_data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_stock_price(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    ticker  = body.get("ticker", "").strip().upper()
    api_key = body.get("api_key", "").strip()
    if not ticker or not api_key:
        return JSONResponse({"error": "ticker and api_key are required"}, status_code=400)
    try:
        from urllib.parse import quote as urlquote
        url  = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={urlquote(ticker)}&apikey={api_key}"
        r    = requests.get(url, timeout=10)
        data = r.json()
        quote      = data.get("Global Quote", {})
        price      = quote.get("05. price")
        change_pct = quote.get("10. change percent", "").replace("%", "")
        if not price:
            note = data.get("Note") or data.get("Information")
            return JSONResponse({"error": note or f"No data found for {ticker}."})
        return JSONResponse({
            "ticker":     ticker,
            "price":      float(price),
            "change_pct": float(change_pct) if change_pct else None,
        })
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_send_digest(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    host = body.get("smtp_host", "smtp.gmail.com")
    port = int(body.get("smtp_port", 587))
    user = body.get("smtp_user", "")
    pwd  = body.get("smtp_pass", "")
    to   = body.get("to_email", user)
    if not user or not pwd:
        return JSONResponse({"error": "smtp_user and smtp_pass required"}, status_code=400)
    try:
        data = await asyncio.to_thread(excel_reader.read_dashboard_data)
        m    = data.get("metrics", {})
        lnw  = m.get("LIQUID NET WORTH", 0) or 0
        tnw  = m.get("TOTAL NET WORTH", 0) or 0
        pct  = float(m.get("PROGRESS TO FI", 0) or 0) * 100
        fl_levels = data.get("freedom_levels", [])
        fl_rows = "".join(
            f'<tr><td>{fl["name"]}</td><td align="right">'
            f'{"&#10003;" if fl.get("status") == "Achieved" else fl.get("status", "—")}'
            f"</td></tr>"
            for fl in fl_levels if fl.get("name")
        )
        html_body = (
            '<html><body style="font-family:sans-serif;max-width:520px;margin:auto;padding:20px;">'
            '<div style="background:#080d1a;color:#f0f4ff;padding:24px;border-radius:12px;">'
            '<h2 style="color:#ec4899;margin:0 0 16px;">&#x1F4B0; Road To FI Digest</h2>'
            '<table width="100%" cellpadding="8" style="font-size:14px;border-collapse:collapse;">'
            f'<tr><td>Liquid Net Worth</td><td align="right"><b>${lnw:,.0f}</b></td></tr>'
            f'<tr><td>Total Net Worth</td><td align="right"><b>${tnw:,.0f}</b></td></tr>'
            f'<tr><td>Progress to FI</td><td align="right"><b>{pct:.1f}%</b></td></tr>'
            "</table>"
            + (f'<hr style="border-color:#1a2540;margin:12px 0;"><table width="100%" cellpadding="6" style="font-size:13px;">{fl_rows}</table>' if fl_rows else "")
            + "</div>"
            '<p style="color:#666;font-size:11px;text-align:center;margin-top:12px;">'
            "Road To FI &mdash; automated digest</p>"
            "</body></html>"
        )
        msg = MIMEMultipart("alternative")
        msg["Subject"] = body.get("subject", "Road To FI Weekly Digest")
        msg["From"]    = user
        msg["To"]      = to
        msg.attach(MIMEText(html_body, "html"))
        with smtplib.SMTP(host, port) as s:
            s.ehlo(); s.starttls(); s.login(user, pwd)
            s.sendmail(user, to, msg.as_string())
        return JSONResponse({"ok": True, "sent_to": to})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_optimize_contribution(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        target_pct  = float(body.pop("target_success_pct", 95))
        lo, hi      = 0.0, 150000.0
        last_result = None
        for _ in range(18):
            mid = (lo + hi) / 2
            body["annual_contribution"] = mid
            last_result = monte_carlo.run_monte_carlo(body)
            if last_result["success_pct"] >= target_pct:
                hi = mid
            else:
                lo = mid
        return JSONResponse({
            "optimal_contribution": round(hi),
            "achieved_success_pct": round(last_result["success_pct"], 1) if last_result else 0,
        })
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_sensitivity(request: Request):
    try:
        base = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        base.setdefault("trials", 500)
        base_pct  = monte_carlo.run_monte_carlo(base)["success_pct"]
        moat_val  = base.get("moat_target", 360000)
        tests = [
            ("mean_return",         0.02,           "Return +2%",    "Return -2%"),
            ("volatility",          0.05,           "Vol -5%",       "Vol +5%"),
            ("inflation_rate",      0.01,           "Inflation -1%", "Inflation +1%"),
            ("annual_contribution", 10000,          "Contrib +$10k", "Contrib -$10k"),
            ("moat_target",         moat_val * 0.10, "Moat +10%",    "Moat -10%"),
        ]
        results = []
        for key, delta, pos_label, neg_label in tests:
            base_val  = base.get(key, 0)
            pos_delta = round(monte_carlo.run_monte_carlo({**base, key: base_val + delta})["success_pct"] - base_pct, 1)
            neg_delta = round(monte_carlo.run_monte_carlo({**base, key: max(0, base_val - delta)})["success_pct"] - base_pct, 1)
            results.append({"param": key, "pos_label": pos_label, "neg_label": neg_label,
                            "pos_delta": pos_delta, "neg_delta": neg_delta})
        results.sort(key=lambda x: max(abs(x["pos_delta"]), abs(x["neg_delta"])), reverse=True)
        return JSONResponse({"base_pct": base_pct, "results": results})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_ss_sensitivity(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    try:
        base_params = dict(body)
        base_params["trials"] = min(int(base_params.get("trials", 500)), 1000)
        base_terminal = None
        results = []
        for test_age in [62, 64, 67, 69, 70]:
            r = monte_carlo.run_monte_carlo({**base_params, "ss_age": test_age})
            terminal = r["stats"]["median_terminal"]
            results.append({
                "ss_age":          test_age,
                "success_pct":     r["success_pct"],
                "median_terminal": terminal,
                "median_ss_age":   r["stats"]["median_ss_age"],
            })
            if test_age == 67:
                base_terminal = terminal
        for row in results:
            row["delta_from_67"] = round(row["median_terminal"] - (base_terminal or 0))
        return JSONResponse({"results": results})
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=422)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_roadmap(request: Request):
    try:
        data = await asyncio.to_thread(excel_reader.read_roadmap_data)
        if "error" in data:
            return JSONResponse(data, status_code=503)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_transactions(request: Request):
    try:
        qs     = request.query_params
        page   = int(qs.get("page", 1))
        limit  = min(int(qs.get("limit", 50)), 200)
        month  = qs.get("month") or None
        txtype = qs.get("type") or None
        data = await asyncio.to_thread(excel_reader.read_transactions_data, page, limit, month, txtype)
        if isinstance(data, dict) and "error" in data:
            return JSONResponse(data, status_code=503)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_forecast(request: Request):
    try:
        data = await asyncio.to_thread(excel_reader.read_forecast_data)
        if "error" in data:
            return JSONResponse(data, status_code=503)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_tax_loss(request: Request):
    try:
        data = await asyncio.to_thread(excel_reader.read_tax_loss_data)
        if "error" in data:
            return JSONResponse(data, status_code=503)
        return JSONResponse(data)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_grid_search(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    base_params = dict(body.get("base_params", {}))
    grid_axes   = body.get("grid_axes", {})
    if not grid_axes:
        return JSONResponse({"error": "grid_axes required"}, status_code=400)
    axis_names  = list(grid_axes.keys())
    axis_values = [grid_axes[k] for k in axis_names]
    combos      = list(itertools.product(*axis_values))
    MAX_COMBOS  = 300
    if len(combos) > MAX_COMBOS:
        return JSONResponse({"error": f"Too many combinations ({len(combos)}). Max {MAX_COMBOS}."}, status_code=400)
    params_list = []
    for combo in combos:
        p = {**base_params}
        for name, val in zip(axis_names, combo):
            p[name] = val
        p.setdefault("trials", 500)
        p["trials"] = min(int(p["trials"]), 500)
        params_list.append(p)
    t0 = time.time()
    try:
        results_raw = await asyncio.to_thread(monte_carlo._run_grid_sync, params_list)
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)
    output = []
    for combo, res, _ in zip(combos, results_raw, params_list):
        row = {name: round(float(val), 6) for name, val in zip(axis_names, combo)}
        row["success_pct"]      = res["success_pct"]
        row["median_terminal"]  = res["stats"]["median_terminal"]
        row["ripcord_rate"]     = res["stats"]["ripcord_rate"]
        row["moat_breach_rate"] = res["stats"]["moat_breach_rate"]
        output.append(row)
    output.sort(key=lambda r: r["success_pct"], reverse=True)
    return JSONResponse({
        "results":     output,
        "axis_names":  axis_names,
        "combo_count": len(combos),
        "runtime_ms":  round((time.time() - t0) * 1000, 1),
    })


async def api_chat_stream(request: Request):
    import json as _json
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    message        = str(body.get("message", "")).strip()[:4096]
    context_type   = body.get("context_type", "all")
    model          = body.get("model", "llama3.1:8b")
    history        = body.get("history", [])
    sim_data       = body.get("sim_data") if context_type in ("all", "simulation") else None
    dashboard_data = None
    if context_type in ("all", "dashboard"):
        try:
            dashboard_data = await asyncio.to_thread(excel_reader.read_dashboard_data)
            if isinstance(dashboard_data, dict) and "error" in dashboard_data:
                dashboard_data = None
        except Exception:
            pass
    ctx = _build_context_string(sim_data, dashboard_data)
    brain = _read_finn_brain()
    brain_section = f"\n\nKNOWLEDGE BASE:\n{brain}" if brain else ""
    system_prompt = _fmt_system_prompt() + brain_section + f"\n\nLIVE FINANCIAL DATA:\n{ctx}"
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})

    def generate():
        try:
            resp = requests.post(
                f"{config.OLLAMA_URL}/api/chat",
                json={"model": model, "messages": messages, "stream": True,
                      "options": {"num_ctx": 8192}},
                stream=True, timeout=120
            )
            for line in resp.iter_lines():
                if line:
                    chunk = _json.loads(line)
                    token = chunk.get("message", {}).get("content", "")
                    if token:
                        yield f"data: {_json.dumps({'token': token})}\n\n"
                    if chunk.get("done"):
                        yield "data: [DONE]\n\n"
                        break
        except requests.exceptions.ConnectionError:
            yield f"data: {_json.dumps({'error': f'Ollama unreachable at {config.OLLAMA_URL}'})}\n\n"
        except Exception as e:
            yield f"data: {_json.dumps({'error': str(e)})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


async def api_chat(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    message        = str(body.get("message", "")).strip()[:4096]
    context_type   = body.get("context_type", "all")
    model          = body.get("model", "llama3.1:8b")
    history        = body.get("history", [])
    sim_data       = body.get("sim_data") if context_type in ("all", "simulation") else None
    dashboard_data = None
    if context_type in ("all", "dashboard"):
        try:
            dashboard_data = await asyncio.to_thread(excel_reader.read_dashboard_data)
            if isinstance(dashboard_data, dict) and "error" in dashboard_data:
                dashboard_data = None
        except Exception:
            pass
    ctx = _build_context_string(sim_data, dashboard_data)
    brain = _read_finn_brain()
    brain_section = f"\n\nKNOWLEDGE BASE:\n{brain}" if brain else ""
    system_prompt = _fmt_system_prompt() + brain_section + f"\n\nLIVE FINANCIAL DATA:\n{ctx}"
    messages = [{"role": "system", "content": system_prompt}]
    for turn in history[-8:]:
        if turn.get("role") in ("user", "assistant") and turn.get("content"):
            messages.append({"role": turn["role"], "content": turn["content"]})
    messages.append({"role": "user", "content": message})
    t0 = time.time()
    try:
        resp = await asyncio.to_thread(
            requests.post,
            f"{config.OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False,
                  "options": {"num_ctx": 8192}},
            timeout=120
        )
        data = resp.json()
        if "error" in data:
            return JSONResponse({"error": f"Ollama error: {data['error']}"})
        reply = data["message"]["content"]
    except requests.exceptions.ConnectionError:
        return JSONResponse({"error": f"Ollama unreachable at {config.OLLAMA_URL} — is the Ollama container running?"})
    except Exception as e:
        return JSONResponse({"error": f"Ollama error: {e}"})
    return JSONResponse({"reply": reply, "model": model, "elapsed_ms": round((time.time() - t0) * 1000)})


async def api_summarize(request: Request):
    try:
        body = await request.json()
    except Exception:
        return JSONResponse({"error": "invalid JSON"}, status_code=400)
    model        = body.get("model", "llama3.1:8b")
    summary_type = body.get("summary_type", "playbook")
    sim_data     = body.get("sim_data")
    dash_data    = body.get("dashboard_data")
    if not dash_data:
        try:
            dash_data = await asyncio.to_thread(excel_reader.read_dashboard_data)
            if isinstance(dash_data, dict) and "error" in dash_data:
                dash_data = None
        except Exception:
            pass
    ctx   = _build_context_string(sim_data, dash_data)
    today = time.strftime("%Y-%m-%d")
    if summary_type == "playbook":
        prompt = (
            f"You are a fiduciary retirement planning AI. Today is {today}.\n"
            f"The user plans to retire at 62, claim SS at 67, and uses a SGOV bridge moat strategy.\n\n"
            f"SIMULATION DATA:\n{ctx}\n\n"
            f"Write an Advisor Playbook with exactly three sections using these exact labels:\n\n"
            f"WHAT WORKS\n"
            f"1–3 sentences on the strongest signals — what the plan is doing right. Reference specific numbers.\n\n"
            f"A SECOND LOOK\n"
            f"1–3 sentences on the most significant risk or weakness visible in the data. Reference specific numbers.\n\n"
            f"FINN SUGGESTS\n"
            f"1–3 sentences on the single most actionable next step based on the data.\n\n"
            f"Rules: No bullets. No sub-headers. No extra sections. Plain sentences only. "
            f"Use the exact section labels above. Max 3 sentences per section."
        )
    else:
        prompt = (
            f"You are Finn — Financial Independence Network Navigator. Today is {today}.\n"
            f"You're talking directly to someone you know well — they're targeting retirement at 62, "
            f"SS at 67, and running a SGOV bridge moat strategy. You're rooting for them.\n\n"
            f"SIMULATION DATA:\n{ctx}\n\n"
            f"Write a Plan Narrative with exactly three sections using these exact labels:\n\n"
            f"YOUR BRIDGE\n"
            f"2–3 sentences on the 62–67 SGOV moat period — how well it's funded, breach risk, "
            f"and what happens if markets drop during this window. Use specific numbers. "
            f"Speak directly: 'your bridge', 'you're covered', etc.\n\n"
            f"AFTER 67\n"
            f"2–3 sentences on life after SS kicks in — the floor it creates, how the equity engine "
            f"runs from here, ratchet trigger likelihood, and terminal wealth trajectory. "
            f"Make it feel like arrival, not just math.\n\n"
            f"KEEP AN EYE ON\n"
            f"2–3 sentences on the single biggest risk in the data right now and one concrete thing "
            f"to do about it. Specific, actionable, warm — not a warning label.\n\n"
            f"Rules: Use 'you/your' throughout — never 'the client'. Warm and direct, like a real "
            f"advisor who knows this person. Motivational as much as informational. "
            f"Each section max 3 sentences. Exact section labels. No bullets."
        )
    brain = _read_finn_brain()
    brain_section = f"\n\nKNOWLEDGE BASE:\n{brain}" if brain else ""
    sys_prompt = _fmt_system_prompt() + brain_section
    messages   = [
        {"role": "system", "content": sys_prompt},
        {"role": "user",   "content": prompt},
    ]
    t0 = time.time()
    try:
        resp = await asyncio.to_thread(
            requests.post,
            f"{config.OLLAMA_URL}/api/chat",
            json={"model": model, "messages": messages, "stream": False,
                  "options": {"num_ctx": 8192}},
            timeout=120
        )
        data = resp.json()
        if "error" in data:
            return JSONResponse({"error": f"Ollama error: {data['error']}"})
        summary = data["message"]["content"]
    except requests.exceptions.ConnectionError:
        return JSONResponse({"error": f"Ollama unreachable at {config.OLLAMA_URL} — is the Ollama container running?"})
    except Exception as e:
        return JSONResponse({"error": f"Ollama error: {e}"})
    return JSONResponse({"summary": summary, "model": model, "elapsed_ms": round((time.time() - t0) * 1000)})


# ── OAuth bypass (local LAN trust) ────────────────────────────────────────────

async def oauth_metadata(request: Request):
    base = str(request.base_url).rstrip("/")
    return JSONResponse({
        "issuer":                base,
        "authorization_endpoint": f"{base}/oauth/authorize",
        "token_endpoint":         f"{base}/oauth/token",
        "response_types_supported": ["code"],
        "grant_types_supported":    ["authorization_code", "client_credentials"],
        "token_endpoint_auth_methods_supported": ["none"],
    })


async def oauth_token(request: Request):
    return JSONResponse({
        "access_token": "local-lan-bypass",
        "token_type":   "bearer",
        "expires_in":   86400,
    })


# ── Starlette app factory ─────────────────────────────────────────────────────

MAX_UPLOAD_BYTES = 20 * 1024 * 1024  # 20 MB


async def api_upload_ledger(request: Request):
    try:
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            return JSONResponse({"error": "no file provided"}, status_code=400)
        if not upload.filename.lower().endswith(".xlsx"):
            return JSONResponse({"error": "file must be .xlsx"}, status_code=400)
        content = await upload.read()
        if len(content) > MAX_UPLOAD_BYTES:
            return JSONResponse({"error": f"File too large ({len(content)//1024}KB). Max 20MB."}, status_code=413)
        dest = Path(config.LEDGER_PATH)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(content)
        return JSONResponse({"ok": True, "saved_to": str(dest), "bytes": len(content)})
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


async def api_health(request: Request):
    ledger_ok = Path(config.LEDGER_PATH).exists()
    ollama_ok = False
    try:
        r = requests.get(f"{config.OLLAMA_URL}/api/tags", timeout=3)
        ollama_ok = r.status_code == 200
    except Exception:
        pass
    return JSONResponse({
        "ledger": "ok" if ledger_ok else "missing",
        "ollama": "ok" if ollama_ok else "unreachable",
        "ledger_path": config.LEDGER_PATH,
        "ollama_url":  config.OLLAMA_URL,
    })


def build_app(mcp_app):
    return Starlette(routes=[
        Route("/",                          dashboard),
        Route("/manifest.json",             manifest),
        Route("/sw.js",                     service_worker),
        Route("/icon-192.svg",              icon_svg),
        Route("/icon-512.svg",              icon_svg),
        Route("/api/rules",                 api_rules),
        Route("/api/finn/memory",           api_finn_memory_get,       methods=["GET"]),
        Route("/api/finn/memory/add",       api_finn_memory_add,       methods=["POST"]),
        Route("/api/ledger/dashboard",      api_ledger_dashboard),
        Route("/api/monte-carlo",           api_monte_carlo,           methods=["POST"]),
        Route("/api/portfolio",             api_portfolio),
        Route("/api/portfolio/refresh",     api_portfolio_refresh,     methods=["POST"]),
        Route("/api/stock-price",           api_stock_price,           methods=["POST"]),
        Route("/api/send-digest",           api_send_digest,           methods=["POST"]),
        Route("/api/optimize-contribution", api_optimize_contribution, methods=["POST"]),
        Route("/api/sensitivity",           api_sensitivity,           methods=["POST"]),
        Route("/api/ss-sensitivity",        api_ss_sensitivity,        methods=["POST"]),
        Route("/api/roadmap",               api_roadmap),
        Route("/api/transactions",          api_transactions),
        Route("/api/forecast",              api_forecast),
        Route("/api/tax-loss",              api_tax_loss),
        Route("/api/grid-search",           api_grid_search,           methods=["POST"]),
        Route("/api/chat",                  api_chat,                  methods=["POST"]),
        Route("/api/chat/stream",           api_chat_stream,           methods=["POST"]),
        Route("/api/summarize",             api_summarize,             methods=["POST"]),
        Route("/api/upload-ledger",         api_upload_ledger,         methods=["POST"]),
        Route("/api/health",                api_health),
        Route("/.well-known/oauth-authorization-server", oauth_metadata),
        Route("/oauth/token",               oauth_token,               methods=["GET", "POST"]),
        Mount("/static",                    StaticFiles(directory=str(STATIC_DIR)), name="static"),
        Mount("/",                          mcp_app),
    ])
