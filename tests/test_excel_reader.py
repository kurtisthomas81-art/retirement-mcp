import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
import config
import excel_reader


def test_missing_ledger_dashboard_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = excel_reader.read_dashboard_data()
    assert "error" in result
    assert "not found" in result["error"].lower() or "missing" in result["error"].lower()


def test_missing_ledger_portfolio_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = excel_reader.read_portfolio_data()
    assert isinstance(result, dict) and "error" in result


def test_missing_ledger_roadmap_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = excel_reader.read_roadmap_data()
    assert "error" in result


def test_missing_ledger_transactions_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = excel_reader.read_transactions_data()
    assert "error" in result


def test_missing_ledger_forecast_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = excel_reader.read_forecast_data()
    assert "error" in result


def test_missing_ledger_tax_loss_returns_error(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "LEDGER_PATH", str(tmp_path / "missing.xlsx"))
    result = excel_reader.read_tax_loss_data()
    assert "error" in result
