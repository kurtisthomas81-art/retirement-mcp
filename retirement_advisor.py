from mcp.server.fastmcp import FastMCP
import uvicorn

import config
import excel_reader
import monte_carlo
from api_routes import build_app

config.validate()

mcp = FastMCP("RetirementAuditor", host="0.0.0.0", port=8000)

# ── MCP resources ─────────────────────────────────────────────────────────────

@mcp.resource("finance://2026_rules")
def get_2026_rules() -> str:
    """Reads from config.RULES_2026 — single source of truth, updated annually in config.py."""
    from config import RULES_2026 as r, RMD_TABLE
    return f"""2026 TAX & RETIREMENT RULES (source: config.RULES_2026)

RETIREMENT CONTRIBUTIONS:
  401k/403b limit            : ${r['contrib_401k']:,}
  Catch-up age 50+           : +${r['catchup_50_plus']:,}
  Super catch-up age 60–63   : +${r['super_catchup_60_63']:,} (SECURE 2.0)
  IRA/Roth IRA (under 50)    : ${r['ira_roth_limit']:,}
  IRA/Roth IRA (age 50+)     : ${r['ira_roth_50_plus']:,}
  SIMPLE IRA                 : ${r['simple_ira_limit']:,}
  Rothification mandate      : FICA wages > ${r['rothification_income_threshold']:,} → catch-up must be Roth

FEDERAL INCOME TAX (2026):
  Standard deduction (single): ${r['std_deduction_single']:,}
  Standard deduction (MFJ)   : ${r['std_deduction_mfj']:,}
  65+ additional (single)    : +${r['senior_addl_deduction_single']:,}
  65+ additional (MFJ)       : +${r['senior_addl_deduction_mfj']:,}
  Senior bonus (65+)         : +${r['senior_bonus_deduction']:,} if MAGI < ${r['senior_bonus_magi_single']:,} single / ${r['senior_bonus_magi_mfj']:,} MFJ

LONG-TERM CAPITAL GAINS:
  0%  : ≤ ${r['ltcg_0pct_single']:,} single / ${r['ltcg_0pct_mfj']:,} MFJ
  15% : ≤ ${r['ltcg_15pct_single']:,} single / ${r['ltcg_15pct_mfj']:,} MFJ
  NIIT: +3.8% if MAGI > ${r['niit_threshold_single']:,} single / ${r['niit_threshold_mfj']:,} MFJ

ROTH IRA PHASE-OUT:
  Single: ${r['roth_phaseout_single_low']:,}–${r['roth_phaseout_single_high']:,}
  MFJ   : ${r['roth_phaseout_mfj_low']:,}–${r['roth_phaseout_mfj_high']:,}

BRIDGE PHASE PLANNING:
  ACA cliff (single, 62–64): ${r['aca_cliff_magi_single']:,} MAGI — stay below for ACA subsidy
  IRMAA Tier 1 (single, 65+): ${r['irmaa_tier1_single']:,} MAGI — Medicare premium surcharge
  IRMAA Tier 1 (MFJ, 65+)  : ${r['irmaa_tier1_mfj']:,} MAGI

RMD TABLE (age → divisor, IRS Pub 590-B): {RMD_TABLE}
"""

# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_stock_price(ticker: str) -> str:
    """Gets the current price for a stock ticker. Reads from portfolio cache first to conserve API calls; falls back to Alpha Vantage for tickers not in the portfolio."""
    import requests
    from urllib.parse import quote as urlquote
    symbol = ticker.strip().upper()
    try:
        holdings = excel_reader.read_portfolio_data()
        if isinstance(holdings, list):
            for h in holdings:
                if h.get("ticker", "").upper() == symbol:
                    price = h.get("cached_price")
                    if price:
                        return f"Current price for {symbol}: ${price:.2f} (from portfolio cache)"
    except Exception:
        pass
    if not config.AV_KEY:
        return f"No cached price found for {symbol} and AV_KEY is not configured on the server."
    try:
        url  = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={urlquote(symbol)}&apikey={config.AV_KEY}"
        r    = requests.get(url, timeout=10)
        data = r.json()
        price = data.get("Global Quote", {}).get("05. price")
        if not price:
            note = data.get("Note") or data.get("Information") or "No data returned"
            return f"Error fetching {symbol}: {note}"
        return f"Current price for {symbol}: ${float(price):.2f} (live from Alpha Vantage)"
    except Exception as e:
        return f"Error fetching {symbol}: {e}"


@mcp.tool()
def get_fi_dashboard() -> str:
    """Returns a snapshot of the Road To FI dashboard — net worth, FI progress, freedom levels."""
    data = excel_reader.read_dashboard_data()
    if "error" in data:
        return f"Error reading ledger: {data['error']}"
    m   = data.get("metrics", {})
    pct = float(m.get("PROGRESS TO FI", 0) or 0) * 100
    return (
        f"FREEDOM LEDGER SNAPSHOT\n"
        f"  Liquid Net Worth : ${m.get('LIQUID NET WORTH', 0):,.0f}\n"
        f"  Total Net Worth  : ${m.get('TOTAL NET WORTH', 0):,.0f}\n"
        f"  Liquid Cash      : ${m.get('LIQUID CASH', 0):,.0f}\n"
        f"  Survival Runway  : {m.get('SURVIVAL RUNWAY', 'N/A')}\n"
        f"  FI Target (62)   : ${m.get('FI TARGET (Age 62)', 0):,.0f}\n"
        f"  Progress to FI   : {pct:.2f}%"
    )


@mcp.tool()
def run_retirement_simulation(
    current_age: int = 45,
    retirement_age: int = 62,
    engine_balance: float = 0,
    sgov_balance: float = 0,
    checking_balance: float = 5000,
    ss_benefit_67: float = 36697,
    floor_annual: float = 17000,
    annual_contribution: float = 0,
) -> str:
    """Runs a Monte Carlo retirement simulation (1,000 trials).
    ss_benefit_67: SS benefit in current dollars at full retirement age 67.
    floor_annual: biological floor (survival spending) in current dollars — post-SS gap target.
    Returns success rate, wealth percentiles at key ages, and key simulation statistics."""
    params = {
        "current_age": current_age,
        "target_age":  retirement_age,
        "start_engine":    engine_balance,
        "start_sgov":      sgov_balance,
        "start_checking":  checking_balance,
        "full_ss":         ss_benefit_67,
        "biological_floor": floor_annual,
        "bridge_draw_ann": 72000,
        "annual_contribution": annual_contribution,
        "trials": 1000,
        "mean_return": 0.10, "volatility": 0.15, "sgov_yield": 0.04,
        "inflation_rate": 0.03,
        "use_mortality_weighting": True,
        "gogo_e": 0.25, "gogo_n": 0.15, "slowgo_e": 0.20, "slowgo_n": 0.10,
        "nogo_e": 0.10, "nogo_n": 0.05, "gk_trigger": 0.20, "gk_cut_rate": 0.50,
        "bear_streak_years": 3, "bear_streak_cut": 0.25,
        "portfolio_cap": 5000000, "cap_inflation": 0.03,
        "cap_gogo": 0.10, "cap_slowgo": 0.05, "cap_nogo": 0.02,
        "tent_skim_rate": 0.50, "dividend_yield": 0.015, "wage_growth": 0.02,
    }
    result = monte_carlo.run_monte_carlo(params)
    s = result["stats"]
    lines = [
        f"SUCCESS RATE: {result['success_pct']}%  ({result['trial_count']:,} trials, {result['runtime_ms']}ms)",
        "",
        "WEALTH PERCENTILES:",
    ]
    for m in result["milestones"]:
        lines.append(f"  Age {m['age']:2d}:  P10=${m['p10']:>12,.0f}   P50=${m['p50']:>12,.0f}   P90=${m['p90']:>12,.0f}")
    lines += [
        "",
        "KEY STATISTICS:",
        f"  Arrival wealth (median):  ${s['median_arrival']:>12,.0f}",
        f"  SS claim age (median):    {s['median_ss_age']}",
        f"  Ripcord rate (early SS):  {s['ripcord_rate']}%",
        f"  Moat breach rate:         {s['moat_breach_rate']}%",
        f"  Terminal wealth (median): ${s['median_terminal']:>12,.0f}",
        f"  Go-go spend (median):     ${s['median_gogo_spend']:>12,.0f}",
        f"  Max drawdown (median):    {s['median_drawdown']}%",
    ]
    return "\n".join(lines)


# ── App assembly and entry point ──────────────────────────────────────────────

mcp_app = mcp.sse_app()
app     = build_app(mcp_app)

if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
