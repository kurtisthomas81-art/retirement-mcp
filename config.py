import json
import os
from datetime import date
from pathlib import Path
from dotenv import load_dotenv

_CONFIG_DIR = Path(__file__).parent / "config"

load_dotenv(Path(__file__).parent / ".env")


def validate():
    warnings = []
    if not os.environ.get("CLIENT_DOB"):
        warnings.append("CLIENT_DOB not set — using default 1981-01-29")
    if not os.environ.get("CLIENT_NAME"):
        warnings.append("CLIENT_NAME not set")
    if not os.environ.get("LEDGER_PATH") and not Path(__file__).parent.joinpath("data", "ledger.xlsx").exists():
        warnings.append("LEDGER_PATH not set and data/ledger.xlsx not found — upload a ledger via the Portfolio tab")
    if not os.environ.get("OLLAMA_URL"):
        warnings.append("OLLAMA_URL not set — using default http://172.17.0.1:11434")
    for w in warnings:
        print(f"[config] WARNING: {w}")
    return warnings

# ── Client identity (loaded from .env — never hard-code PII in source) ─────────

CLIENT_DOB       = date.fromisoformat(os.environ.get("CLIENT_DOB", "1981-01-29"))
CLIENT_RETIRE_AGE = int(os.environ.get("CLIENT_RETIRE_AGE", "62"))
CLIENT_NAME      = os.environ.get("CLIENT_NAME", "")
CLIENT_EMPLOYER  = os.environ.get("CLIENT_EMPLOYER", "")

# ── Infrastructure ──────────────────────────────────────────────────────────────

LEDGER_PATH = os.environ.get(
    "LEDGER_PATH",
    str(Path(__file__).parent / "data" / "ledger.xlsx")
)
OLLAMA_URL   = os.environ.get("OLLAMA_URL", "http://172.17.0.1:11434")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen3.5:9b")
AV_KEY       = os.environ.get("AV_KEY", "")

FINN_MEMORY_PATH = str(Path(__file__).parent / "finn_memory.md")
FINN_BRAIN_PATH  = str(Path(__file__).parent / "finn_brain.md")
PLANS_PATH       = Path(__file__).parent / "data" / "plans.json"
PROFILE_PATH     = Path(__file__).parent / "data" / "profile.json"

# ── Plan defaults (used when plans.json is missing or as seed values) ───────────

PLAN_DEFAULTS = {
    "ollama_model":          "",               # "" = use OLLAMA_MODEL env var
    "retire_age":            62,
    "ss_age":                67,
    "full_ss_annual":        25620,
    "fi_target":                  0,        # 0 = read from Excel; >0 = locked nominal override
    "annual_engine_contribution": 0,        # yearly $ added to engine portfolio; 0 = use static coast formula
    "bridge_target":         360000,
    "bridge_draw_annual":    72000,
    "biological_floor":      17000,
    "ratchet_multiplier":    1.5,
    "withdrawal_rate_post_ss": 0.0,
    "mean_return":           0.10,
    "volatility":            0.15,
    "sgov_yield":            0.04,
    "inflation_rate":        0.03,
    "dividend_yield":        0.015,
    "gk_trigger":            0.20,
    "gk_cut_rate":           0.50,
    "bear_streak_years":     3,
    "bear_streak_cut":       0.25,
    "portfolio_cap":         5000000,
}

_SEED_PLAN = {
    "id":          "money-machine-v3-0",
    "name":        "Money Machine V3.0",
    "description": "Nominal math locked: 10% return / 3% COLA / 3.5-4% SGOV. SS $25,620 current → $49,090 nominal at 67. $360k SGOV bridge. Engine untouched 62–67.",
    "created_at":  "2026-05-19",
    "updated_at":  "2026-05-19",
    "client": {
        "retire_age":     62,
        "ss_age":         67,
        "full_ss_annual": 25620,
    },
    "strategy": {
        "fi_target":                  0,
        "annual_engine_contribution": 0,
        "bridge_target":           360000,
        "bridge_draw_annual":      72000,
        "biological_floor":        17000,
        "ratchet_multiplier":      1.5,
        "withdrawal_rate_post_ss": 0.0,
    },
    "market": {
        "mean_return":    0.10,
        "volatility":     0.15,
        "sgov_yield":     0.04,
        "inflation_rate": 0.03,
        "dividend_yield": 0.015,
    },
    "risk": {
        "gk_trigger":        0.20,
        "gk_cut_rate":       0.50,
        "bear_streak_years": 3,
        "bear_streak_cut":   0.25,
        "portfolio_cap":     5000000,
    },
    "finn": {
        "ollama_model": "",   # "" = use OLLAMA_MODEL env var
    },
}


def _seed_plans_file():
    """Create plans.json with the default plan if it doesn't exist."""
    PLANS_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = {"active_id": _SEED_PLAN["id"], "plans": [_SEED_PLAN]}
    PLANS_PATH.write_text(json.dumps(data, indent=2))
    return data


def load_active_plan() -> dict:
    """Return flattened active plan dict. Falls back to PLAN_DEFAULTS if file missing."""
    try:
        if not PLANS_PATH.exists():
            return PLAN_DEFAULTS.copy()
        data = json.loads(PLANS_PATH.read_text())
        active_id = data.get("active_id")
        for p in data.get("plans", []):
            if p["id"] == active_id:
                flat = {}
                for section in ("client", "strategy", "market", "risk", "finn"):
                    flat.update(p.get(section, {}))
                flat.update({k: v for k, v in p.items()
                             if k not in ("client", "strategy", "market", "risk")})
                return flat
    except Exception:
        pass
    return PLAN_DEFAULTS.copy()


def load_profile() -> dict:
    """Return profile dict. profile.json overrides .env defaults."""
    defaults = {
        "name":     CLIENT_NAME,
        "dob":      CLIENT_DOB.isoformat(),
        "employer": CLIENT_EMPLOYER,
        "email":    os.environ.get("CLIENT_EMAIL", ""),
    }
    try:
        if PROFILE_PATH.exists():
            saved = json.loads(PROFILE_PATH.read_text())
            defaults.update({k: v for k, v in saved.items() if v is not None})
    except Exception:
        pass
    return defaults


def _seed_profile_file() -> dict:
    PROFILE_PATH.parent.mkdir(parents=True, exist_ok=True)
    data = load_profile()
    PROFILE_PATH.write_text(json.dumps(data, indent=2))
    return data

# ── Advisor system prompt ───────────────────────────────────────────────────────
# Placeholders filled at runtime by _fmt_system_prompt() in api_routes.py:
# {age}, {retire_year}, {years_to_retire}, {today}, {employer}

SYSTEM_PROMPT = (_CONFIG_DIR / "system_prompt.md").read_text()

# ── 2026 tax and retirement rules ───────────────────────────────────────────────

RULES_2026 = {
    "contrib_401k": 24500,
    "catchup_50_plus": 8000,
    "super_catchup_60_63": 11250,
    "ira_roth_limit": 7500,
    "ira_roth_50_plus": 8600,
    "simple_ira_limit": 17000,
    "rothification_income_threshold": 150000,
    "std_deduction_single": 16100,
    "std_deduction_mfj": 32200,
    "senior_addl_deduction_single": 2050,
    "senior_addl_deduction_mfj": 1650,
    "senior_bonus_deduction": 6000,
    "senior_bonus_magi_single": 75000,
    "senior_bonus_magi_mfj": 150000,
    "ltcg_0pct_single": 49450,
    "ltcg_0pct_mfj": 98900,
    "ltcg_15pct_single": 492300,
    "ltcg_15pct_mfj": 553850,
    "niit_threshold_single": 200000,
    "niit_threshold_mfj": 250000,
    "roth_phaseout_single_low": 153000,
    "roth_phaseout_single_high": 168000,
    "roth_phaseout_mfj_low": 242000,
    "roth_phaseout_mfj_high": 252000,
    "aca_cliff_magi_single": 62600,
    "irmaa_tier1_single": 106000,
    "irmaa_tier1_mfj": 212000,
    "year": 2026,
    "return_models": ["normal", "fat_tail", "regime_switch", "garch"],
    "sim_sizes": ["1k", "10k", "100k", "1m"],
}

# ── RMD table ───────────────────────────────────────────────────────────────────

RMD_TABLE = {
    75:22.9, 76:22.0, 77:21.1, 78:20.2, 79:19.4, 80:18.7, 81:17.9, 82:17.1,
    83:16.3, 84:15.5, 85:14.8, 86:14.1, 87:13.4, 88:12.7, 89:12.0, 90:11.4,
    91:10.8, 92:10.2, 93:9.6,  94:9.1,  95:8.6
}
