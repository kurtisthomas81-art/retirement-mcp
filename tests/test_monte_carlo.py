import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from monte_carlo import run_monte_carlo, compute_ss_benefit, compute_federal_tax

FUNDED = {
    "current_age": 45,
    "target_age":  62,
    "start_engine": 800000,
    "start_sgov":   300000,
    "start_checking": 10000,
    "full_ss": 36697,
    "annual_contribution": 24500,
    "trials": 500,
}


def test_basic_run_returns_expected_keys():
    result = run_monte_carlo(FUNDED, seed=42)
    for key in ("success_pct", "milestones", "bands", "moat_bands", "stats", "trial_count", "runtime_ms"):
        assert key in result, f"missing key: {key}"
    assert 0 <= result["success_pct"] <= 100


def test_success_rate_reasonable_for_funded_plan():
    result = run_monte_carlo(FUNDED, seed=42)
    assert result["success_pct"] > 50, f"Expected >50% success for well-funded plan, got {result['success_pct']}"


def test_zero_balance_yields_zero_success():
    result = run_monte_carlo({"current_age": 45, "target_age": 62, "trials": 200}, seed=0)
    assert result["success_pct"] == 0.0


def test_seed_reproducibility():
    r1 = run_monte_carlo(FUNDED, seed=99)
    r2 = run_monte_carlo(FUNDED, seed=99)
    assert r1["success_pct"] == r2["success_pct"]
    assert r1["stats"]["median_terminal"] == r2["stats"]["median_terminal"]


def test_different_seeds_differ():
    r1 = run_monte_carlo(FUNDED, seed=1)
    r2 = run_monte_carlo(FUNDED, seed=2)
    assert r1["success_pct"] != r2["success_pct"] or r1["stats"]["median_terminal"] != r2["stats"]["median_terminal"]


def test_invalid_age_raises():
    with pytest.raises(ValueError, match="current_age"):
        run_monte_carlo({"current_age": 80, "target_age": 62})


def test_invalid_age_range_raises():
    with pytest.raises(ValueError):
        run_monte_carlo({"current_age": 62, "target_age": 62})


def test_milestones_include_target_age():
    result = run_monte_carlo(FUNDED, seed=1)
    ages = [m["age"] for m in result["milestones"]]
    assert 62 in ages


def test_trial_count_respected():
    result = run_monte_carlo({**FUNDED, "trials": 300}, seed=1)
    assert result["trial_count"] == 300


def test_stats_non_negative():
    result = run_monte_carlo(FUNDED, seed=7)
    s = result["stats"]
    assert s["median_arrival"] >= 0
    assert s["median_terminal"] >= 0
    assert 0 <= s["ripcord_rate"] <= 100
    assert 0 <= s["moat_breach_rate"] <= 100


# ── Tax / SS helpers ─────────────────────────────────────────────────────────

def test_ss_at_67_equals_full_benefit():
    assert compute_ss_benefit(67, 36697) == 36697


def test_ss_at_70_is_higher():
    assert compute_ss_benefit(70, 36697) > compute_ss_benefit(67, 36697)


def test_ss_at_62_is_lower():
    assert compute_ss_benefit(62, 36697) < compute_ss_benefit(67, 36697)


def test_federal_tax_zero_income():
    assert compute_federal_tax(0, 0, 0.03, "single") == 0.0


def test_federal_tax_below_standard_deduction():
    assert compute_federal_tax(10000, 0, 0.03, "single") == 0.0


def test_federal_tax_positive_above_deduction():
    tax = compute_federal_tax(100000, 0, 0.03, "single")
    assert tax > 0
