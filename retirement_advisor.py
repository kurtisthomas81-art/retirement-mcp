from mcp.server.fastmcp import FastMCP
import uvicorn

import config
import excel_reader
import monte_carlo
from api_routes import build_app

mcp = FastMCP("RetirementAuditor", host="0.0.0.0", port=8000)

# ── MCP resources ─────────────────────────────────────────────────────────────

@mcp.resource("finance://2026_rules")
def get_2026_rules() -> str:
    return """
    2026 TAX & RETIREMENT RULES (GROUND TRUTH — IRS confirmed):

    RETIREMENT CONTRIBUTION LIMITS:
    - 401k/403b employee limit: $24,500
    - Catch-up (age 50+): $8,000 additional
    - Super catch-up (age 60–63): $11,250 additional (SECURE 2.0)
    - IRA/Roth IRA limit: $7,500 (under 50) / $8,600 (age 50+)
    - SIMPLE IRA limit: $17,000
    - ROTH-IFICATION MANDATE: If 2025 FICA wages > $150,000, ALL catch-up contributions must be Roth.

    ROTH IRA INCOME PHASE-OUT:
    - Single / Head of Household: $153,000–$168,000
    - Married Filing Jointly: $242,000–$252,000

    FEDERAL INCOME TAX BRACKETS (2026, 7 brackets: 10/12/22/24/32/35/37%):
    - Standard deduction: $16,100 (single) / $32,200 (married filing jointly)
    - Additional standard deduction (age 65+): $2,050 (single) / $1,650 (MFJ)
    - Senior bonus deduction (age 65+, MAGI < $75k single / $150k MFJ): extra $6,000

    LONG-TERM CAPITAL GAINS RATES (2026):
    - 0% rate: taxable income ≤ $49,450 (single) / $98,900 (MFJ)
    - 15% rate: up to $492,300 (single) / $553,850 (MFJ)
    - 20% rate: above those thresholds
    - Net Investment Income Tax (NIIT): +3.8% if MAGI > $200k (single) / $250k (MFJ)
    """

# ── MCP tools ─────────────────────────────────────────────────────────────────

@mcp.tool()
def get_stock_price(ticker: str, api_key: str) -> str:
    """Gets the current price for any stock ticker."""
    import requests
    url  = f"https://www.alphavantage.co/query?function=GLOBAL_QUOTE&symbol={ticker}&apikey={api_key}"
    r    = requests.get(url, timeout=10)
    data = r.json()
    return f"Current price for {ticker}: ${data['Global Quote']['05. price']}"


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
    ss_benefit_67: float = 25620,
    annual_contribution: float = 0,
) -> str:
    """Runs a Monte Carlo retirement simulation (1,000 trials) using V4-equivalent parameters.
    Returns success rate, wealth percentiles at key ages, and key simulation statistics."""
    params = {
        "current_age": current_age,
        "target_age":  retirement_age,
        "start_engine":    engine_balance,
        "start_sgov":      sgov_balance,
        "start_checking":  checking_balance,
        "full_ss":         ss_benefit_67,
        "annual_contribution": annual_contribution,
        "trials": 1000,
        "mean_return": 0.09, "volatility": 0.16, "sgov_yield": 0.04,
        "inflation_rate": 0.03, "strict_moat_cost": 96000, "full_moat_cost": 96000,
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
