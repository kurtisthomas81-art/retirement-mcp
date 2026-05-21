import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import config
import excel_reader


@pytest.mark.parametrize("read_fn,check_error", [
    (excel_reader.read_dashboard_data,
     lambda r: "not found" in r["error"].lower() or "missing" in r["error"].lower()),
    (excel_reader.read_portfolio_data,  lambda r: "error" in r),
    (excel_reader.read_roadmap_data,    lambda r: "error" in r),
    (excel_reader.read_transactions_data, lambda r: "error" in r),
    (excel_reader.read_forecast_data,   lambda r: "error" in r),
    (excel_reader.read_tax_loss_data,   lambda r: "error" in r),
])
def test_missing_ledger_returns_error(tmp_path, monkeypatch, read_fn, check_error):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = read_fn()
    assert isinstance(result, dict) and "error" in result
    assert check_error(result)
