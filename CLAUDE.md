# CLAUDE.md

This file provides guidance to Claude Code when working in the Road To FI retirement planning project.

## Project Overview

**Road To FI** is a personal retirement planning tool with Monte Carlo simulation, tax optimization, Social Security strategy, and a fiduciary AI advisor ("Finn"). Built with Python + Starlette + FastMCP, deployed in Docker on Unraid.

## Running & Testing

```bash
# Run the app (requires .env and data/ledger.xlsx)
python3 retirement_advisor.py

# Run tests (no ledger required — excel reader tests use monkeypatching)
pytest tests/
pytest tests/test_monte_carlo.py
pytest tests/test_excel_reader.py
```

**Dependencies:** `pip install fastmcp starlette uvicorn[standard] openpyxl numpy requests python-dotenv python-multipart`

## Architecture

### Data Flow
```
data/ledger.xlsx → excel_reader.py → api_routes.py (REST endpoints)
                                           ↓
                                   monte_carlo.py (simulation engine)
                                           ↓
                                   config.py (Finn persona, RULES_2026)
                                           ↓
                                   retirement_advisor.py (MCP + Uvicorn)
                                           ↓
                                   static/index.html (6-tab PWA SPA)
```

### Key Modules

| Module | Role |
|--------|------|
| `retirement_advisor.py` | FastMCP server entry point; exposes `get_stock_price`, `get_fi_dashboard`, `run_retirement_simulation` MCP tools + `finance://2026_rules` resource |
| `api_routes.py` | 25+ REST endpoints; Finn/Ollama chat integration; `_build_context_string()`, `_fmt_system_prompt()` |
| `config.py` | `SYSTEM_PROMPT` (Finn persona), `RULES_2026` (tax constants), `RMD_TABLE`, env config |
| `monte_carlo.py` | Simulation engine: 4 return models, SS benefit curves, federal tax brackets, Roth conversion, ripcord, spending smile, lifestyle ratchet, bridge drawdown (1–1M trials) |
| `excel_reader.py` | openpyxl parser for 6 sheets: DASHBOARD, PORTFOLIO, ROADMAP, TRANSACTIONS, FORECAST_V3, TAX-LOSS |
| `static/index.html` | 6-tab PWA SPA with Chart.js (Overview, Simulation, Plan, Portfolio, AI Advisor, Transactions) |

### Key Risk Areas
- **Simulation math** (`monte_carlo.py`): SS/tax/RMD formulas drive real retirement decisions — wrong math is expensive
- **Tax constants** (`config.py` `RULES_2026`): Go stale every October when IRS publishes new limits; duplicated in 3 places
- **Excel column mappings** (`excel_reader.py`): Break silently when ledger columns are renamed
- **Finn fiduciary tone** (`config.py` `SYSTEM_PROMPT`): Must stay non-advisory, age-accurate, no product recommendations
- **MCP contract** (`retirement_advisor.py` vs `monte_carlo.py`): Parameter name translation (`retirement_age` → `target_age`, etc.) can drift silently
- **Docker PII gate**: `.env` and `data/ledger.xlsx` must never enter image layers — requires `.dockerignore`

### Deployment
Docker on Unraid. **After every code change: commit the file(s) by name, push to GitHub, then immediately provide the Unraid rebuild command below.**

**Unraid repo path:** `/mnt/user/appdata/retirement-mcp/src` — remind the user to `cd` here first if there's any chance they aren't already there.

**Ollama container name:** `ollama` — pull models via `docker exec ollama ollama pull <model>`.

Rebuild command (single line, run from `/mnt/user/appdata/retirement-mcp/src`):
```
cd /mnt/user/appdata/retirement-mcp/src && git pull && docker compose up -d --build
```

---

## Agent Team — Road To FI

Five specialist agents handle ongoing development. Invoke each proactively on the triggers below. There is no standing director — route contradictions to manual review.

### Specialist Agents (invoke proactively)

**`simulation-math-guardian`** — Numerical correctness in `monte_carlo.py`. Invoke before/after any edit to:
- `compute_ss_benefit()`, `compute_federal_tax()`, `taxable_ss_amount()`, `compute_conversion_amount()`, `mortality_mult()`
- The main `run_monte_carlo()` loop: ripcord trigger (`gk_trig`/`bear_yrs`), spending smile phase transitions, lifestyle ratchet tiers, bridge drawdown logic
- `_aggregate_results()` output keys or percentile calculations
- Any new return model or new `params.get(...)` parameter added to the engine
- When any test in `tests/test_monte_carlo.py` is failing or being added

What it does: verifies SS benefit curve against SSA schedule, checks federal tax brackets against IRS tables, audits ripcord/spending-smile/ratchet logic, runs `pytest tests/test_monte_carlo.py`, confirms `RULES_2026` in `config.py` agrees with the bracket tables in `monte_carlo.py`.

**`tax-rules-auditor`** — Tax constant accuracy across `config.py` (`RULES_2026` + `RMD_TABLE` + `SYSTEM_PROMPT` tax block) and `retirement_advisor.py` (`get_2026_rules` resource). Invoke when:
- Any edit touches `RULES_2026`, `SYSTEM_PROMPT`, `RMD_TABLE`, or `get_2026_rules()` in `retirement_advisor.py`
- A `finn_memory.md` correction references a tax limit that differs from `RULES_2026`
- **Every November** — IRS publishes annual contribution limit updates in late October; run this agent proactively, do not wait for a code trigger

What it does: audits all three representations of tax constants for agreement (they are duplicated and drift independently), checks RMD divisors against IRS Pub 590-B, verifies age-gated rules in the system prompt are correct for the client's current age from `CLIENT_DOB`.

**`ledger-schema-sentinel`** — Excel column mapping integrity in `excel_reader.py`. Invoke when:
- Any edit to `excel_reader.py`
- A dashboard metric shows 0, None, or an unexpected value (first-line debugging)
- A new sheet is expected from the ledger workbook
- `api_upload_ledger` changes in `api_routes.py`

What it does: documents exact column-index-to-field mappings for all 6 sheets, verifies `data_only=True` is set in `_open_ledger()` (without it, formula cells return None silently), flags fragile string-match patterns used for column detection, confirms every `read_*` function returns `{"error": str(e)}` on FileNotFoundError.

**`finn-persona-curator`** — Fiduciary tone and accuracy in `SYSTEM_PROMPT`, `api_summarize()` prompt templates, and `_build_context_string()`. Invoke when:
- Any edit to `SYSTEM_PROMPT` in `config.py`
- Any edit to the Advisor Playbook or Plan Narrative prompt templates in `api_routes.py`
- Any new entry added to `finn_memory.md`
- `CLIENT_DOB` or `CLIENT_RETIRE_AGE` changes in `.env`

What it does: verifies fiduciary guardrails (no product recommendations, no "As an AI", no "Great question"), checks age-gating is correct for current client age, validates plan vocabulary (SGOV bridge, ripcord, lifestyle ratchet, spending smile, moat), confirms `_fmt_system_prompt()` math for `age`/`retire_year`/`years_to_retire` is correct, reviews `finn_memory.md` for entries that contradict the current system prompt.

**`mcp-contract-enforcer`** — MCP tool signatures in `retirement_advisor.py` synchronized with `run_monte_carlo()` API in `monte_carlo.py`. Invoke when:
- Any edit to `retirement_advisor.py`
- Any change to `run_monte_carlo()` params dict keys in `monte_carlo.py`
- Any change to `api_monte_carlo()` in `api_routes.py`
- FastMCP version changes in `requirements.txt`

What it does: audits the 23 hard-coded parameters in `run_retirement_simulation()` against `g("key", default)` calls in the engine, verifies the 7 user-facing MCP parameter names translate correctly to engine keys (e.g., `retirement_age` → `target_age`, `engine_balance` → `start_engine`), checks `get_fi_dashboard()` handles the `{"error": ...}` return path, verifies the SSE app assembly wiring remains intact, confirms all `@mcp.tool()` decorators have docstrings.

### QA/Release Agent (invoke before every Docker push)

**`deployment-gatekeeper`** — Validates Docker build and Unraid deployment before any container is rebuilt. Never run `start.sh` without a clean pass from this agent.

What it does: runs `pytest tests/` (all 21 tests must pass), verifies `.dockerignore` excludes `.env`/`data/`/`*.xlsx`/`finn_memory.md` (absence of `.dockerignore` is a hard stop — PII would enter the image), checks `requirements.txt` covers all runtime imports, confirms `.env.example` matches all variables `config.py` reads, produces the ordered deployment checklist: `docker stop` → `docker build -t retirement-advisor .` → `docker rm retirement-advisor` → `sh start.sh`.

---

## Standard Workflows

### Changing `monte_carlo.py`
1. **`simulation-math-guardian`** — maps current invariants for the function(s) being changed
2. Implement the change
3. **`simulation-math-guardian`** — audits the diff and runs `pytest tests/test_monte_carlo.py`
4. If `params` dict keys changed → **`mcp-contract-enforcer`** checks tool signatures
5. **`deployment-gatekeeper`** — clears full test suite before push

### Changing Finn's system prompt
1. Edit `config.py` `SYSTEM_PROMPT` or `api_routes.py` summarize templates
2. **`finn-persona-curator`** — audits fiduciary guardrails, age-gating, vocabulary
3. If tax constants touched → **`tax-rules-auditor`** verifies `RULES_2026` sync across 3 locations
4. If context string format changed → **`mcp-contract-enforcer`** verifies field references

### Pushing a Docker release to Unraid
1. **`deployment-gatekeeper`** — runs `pytest tests/`, checks `.dockerignore`, verifies `requirements.txt`, produces checklist
2. Execute checklist: `docker stop` → `docker build` → `docker rm` → `sh start.sh`

---

## UI Backlog — `static/index.html`

All changes are frontend-only unless noted. No Python/API changes required for Tier 1–2.

### Overview Tab — completed
- Removed 3-card key stats row (Total NW / Runway / Savings Rate) from FI Ring panel — duplicated Financial Picture
- Removed linear progress bar from Financial Picture — duplicated FI Ring %
- Removed Monthly Cash Flow sub-panel from Allocation collapsible — duplicated bar chart above; renamed section to "Asset Allocation"
- Removed stat card grid (LNW / Total NW / Liquid Cash / Runway / FI Target / Savings Rate) from Financial Picture — covered by FI Ring hero

### Tier 1 — High impact, data already available
1. ✅ **Years-to-FI countdown** — `tfiCardWrap` + `calcTimeToFI()` in `loadHome()`; shows "~Xy Ymo · Est. Mon YYYY"
2. ✅ **Ahead/behind pace indicator** — `ov-pace-chip` in `loadHome()`; green/red pill vs. required monthly delta
3. **Freedom Level next milestone callout** — surface next unachieved level + gap amount in the FI Ring meta row; CSS classes `.hnl-label/.hnl-name/.hnl-gap` are stubbed but no HTML/JS wires them; add `id="ov-next-level-row"` to firing meta + populate in `loadHome()` after freedom levels block
4. ✅ **Savings rate sparkline** — `ov-sr-content` panel with 6-month Chart.js line in `loadHome()`

### Tier 2 — Medium impact, moderate build
5. ✅ **Spending velocity / daily burn** — `ov-sv-content` panel; burn vs. floor ratio in `loadHome()`
6. ✅ **Category drilldown** — `tx-drilldown-badge` CSS + JS routing at line ~4168; click spending row → Transactions tab pre-filtered
7. ✅ **NW sparklines in stat cards** — `_spark()` helper in `loadHome()`; 4 cards (LNW, Engine, Bridge, Cash) from `retAdv_nwHistory`
8. ✅ **Milestone toast** — `showToast()` in `loadHome()`; $50k–$1M milestones via `retAdv_lastMilestone` localStorage

### Tier 3 — Bigger lifts
9. ✅ **Scenario compare mode** — `pinScenario()` + `.compare-strip` table; pin any run as Scenario A, compare against current run; diff colors green/red per metric
10. ✅ **SWR live display** — `ov-swr-content` panel; colors green ≤4%, amber ≤5%, red >5%
11. ✅ **30/60/90 day projected cash position** — Forecast tab; 90-day extension via avg daily pattern from last 14 rows (~line 4255)

### Tier 4 — Infrastructure / reliability gaps (Python + backend)
12. ✅ **`/api/optimize-contribution`** — binary search (18 iterations) to find minimum annual contribution for 95% success rate; in `api_routes.py`
13. ✅ **`/api/send-digest`** — SMTP HTML digest: LNW, TNW, FI %, freedom levels; in `api_routes.py`
14. ✅ **Excel column validation** — `validate_ledger_schema()` in `excel_reader.py`; called in `api_upload_ledger` before saving; checks required sheets + key section headers
15. ✅ **Tax constant deduplication** — `get_2026_rules()` in `retirement_advisor.py` now reads from `config.RULES_2026` (single source of truth); also added ACA cliff + IRMAA + RMD table to the resource
16. ✅ **`.dockerignore`** — created; blocks `.env`, `data/`, `*.xlsx`, `finn_memory.md`, `finn_history.json` from image layers

### Tier 5 — New features (not yet in codebase)
17. ✅ **SS claiming strategy comparator** — overview panel `ov-ss-compare-wrap`; shows 62/67/70 monthly + annual + breakeven age vs. 67; visible when ledger has SS benefit data
18. ✅ **Bridge fund health gauge** — `ov-bridge-meta` now shows: `X% of goal · Xmo runway · depletes Mon YYYY` using `rules.bridge_draw_ann` and `rules.moat_target`
19. ✅ **ACA cliff proximity alert** — `ov-bridge-alerts` panel; shows bridge draw vs. $60,240 cliff with ✓/⚠ indicator; Roth conversion headroom before IRMAA Tier 1
20. ✅ **IRMAA tier preview** — part of `ov-bridge-alerts`; shows conversion headroom before $106k Tier 1 Medicare surcharge
21. ✅ **Tax bracket waterfall** — `renderTaxRefPanel()` renders after MC run; table of 7 key thresholds (std deduction, LTCG 0%, ACA cliff, 12% top, IRMAA, NIIT, LTCG 15%) with planning notes
22. ✅ **Actual vs. projected NW overlay** — "You are here" green dot at current age/LNW plotted on MC wealth bands chart; shows how today's position compares to P10–P90 range
23. ✅ **Finn conversation history** — `chatMessages` persisted to `localStorage.retAdv_chatHistory` (last 20); restored on chat tab open with "↑ N messages from last session" notice; cleared on Clear button
24. ✅ **Ollama fallback message** — when Ollama unreachable, `sendChat()` renders styled offline card with `docker start ollama` command; replaces raw error string
