"""Phase 3.8 offline regression tests for research unit safety and export labels."""
import datetime
from decimal import Decimal, InvalidOperation
from pathlib import Path
import ast
import types

import numpy as np
import pandas as pd
import pytest

import options_filters as filters
import research_options_adapter as adapter

ROOT = Path(__file__).resolve().parent


class _Response:
    def __init__(self, data): self.data = data


class _Query:
    def __init__(self, row): self.row = row
    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def execute(self): return _Response([self.row])


class _Client:
    def __init__(self, row): self.row = row
    def table(self, name): return _Query(self.row)


def _canonical_snapshot():
    return filters.CanonicalSnapshot(
        snapshot_id="v2-current", expiry_date="2026-12-25",
        completed_at="2026-12-24T12:30:00Z", spot_price=100000.0,
        raw=pd.DataFrame(), metric_version=filters.EXPECTED_METRIC_VERSION,
        gex_contract_multiplier=1.0,
    )


def _derived_row(**overrides):
    row = {
        "snapshot_id": "v2-current", "expiry_date": "2026-12-25",
        "underlying_spot_price": 100000.0, "metric_status": {"chain": "OK"},
        "metric_version": filters.EXPECTED_METRIC_VERSION, "gex_contract_multiplier": 1.0,
        "max_pain_strike": 100000.0, "atm_strike": 100000.0,
        "atm_straddle_price": 1000.0, "atm_straddle_pct_spot": 1.0,
        "local_oi_pcr": 1.0, "call_gex_wall_strike": 101000.0,
        "call_gex_wall_value": 100.0, "put_gex_wall_strike": 99000.0,
        "put_gex_wall_value": 90.0,
    }
    row.update(overrides)
    return row


@pytest.mark.parametrize("overrides", [
    {"metric_version": "options-derived-v1.0"}, {"metric_version": None},
    {"metric_version": 3}, {"gex_contract_multiplier": None},
    {"gex_contract_multiplier": "bad"}, {"gex_contract_multiplier": True},
    {"gex_contract_multiplier": 0.001},
])
def test_research_adapter_fails_closed_on_missing_or_invalid_provenance(monkeypatch, overrides):
    snap = _canonical_snapshot()
    monkeypatch.setattr(adapter, "select_canonical_snapshot", lambda *a, **k: (snap, {"status": "OK"}))
    result = adapter.load_canonical_research_options(_Client(_derived_row(**overrides)))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "INVALID_DERIVED_METRIC_PROVENANCE"


def test_research_adapter_returns_v2_provenance_for_valid_record(monkeypatch):
    snap = _canonical_snapshot()
    monkeypatch.setattr(adapter, "select_canonical_snapshot", lambda *a, **k: (snap, {"status": "OK"}))
    result = adapter.load_canonical_research_options(_Client(_derived_row()))
    assert result["status"] == "OK"
    assert result["derived"]["source_metric_version"] == filters.EXPECTED_METRIC_VERSION
    assert result["derived"]["source_gex_contract_multiplier"] == 1.0


def _dashboard_export_helpers():
    source = (ROOT / "btc_live_dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    wanted = {"_extract_option_oi_values", "_decimal_value", "_build_option_chain_frame",
              "_prepare_contract_export", "_validate_contract_export"}
    nodes = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in wanted]
    assert {n.name for n in nodes} == wanted
    module = types.ModuleType("phase38_export_helpers")
    module.__dict__.update({"np": np, "pd": pd, "Decimal": Decimal,
                            "InvalidOperation": InvalidOperation, "datetime": datetime})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "btc_live_dashboard.py", "exec"), module.__dict__)
    return module


def test_export_uses_oi_btc_header_and_preserves_exact_source_values():
    helpers = _dashboard_export_helpers()
    btc_oi = Decimal("0.12345678901234567890123456789")
    usd_oi = Decimal("987654321.1234567890123456789")
    chain = [{
        "Product_ID": 101, "Symbol": "C-BTC-100000-251226", "Expiry": "251226",
        "Type": "Call", "Strike": 100000.0, "OI_Contracts": float(btc_oi),
        "OI_USD": float(usd_oi), "OI_BTC_Source": btc_oi, "OI_USD_Source": usd_oi,
    }]
    frame = helpers._build_option_chain_frame(chain)
    exported, status, reasons, tsv = helpers._prepare_contract_export(frame, "251226", True)
    assert status == "COMPLETE", reasons
    assert "OI_BTC" in exported.columns
    assert exported.loc[0, "OI_BTC"] == btc_oi and exported.loc[0, "OI_USD"] == usd_oi
    header = tsv.splitlines()[0].split("\t")
    assert header[-2:] == ["OI_BTC", "OI_USD"] and "OI_Contracts" not in header
    assert str(btc_oi) in tsv.splitlines()[1] and str(usd_oi) in tsv.splitlines()[1]


def test_export_does_not_treat_legacy_oi_contracts_field_as_oi_btc():
    helpers = _dashboard_export_helpers()
    frame = pd.DataFrame([{
        "Product_ID": 101, "Symbol": "C-BTC-100000-251226", "Expiry": "251226",
        "Type": "Call", "Strike": 100000.0, "OI_Contracts": 0.2, "OI_USD": 100.0,
    }])
    _, status, reasons, tsv = helpers._prepare_contract_export(frame, "251226", True)
    assert status == "INCOMPLETE"
    assert any("OI_BTC" in reason for reason in reasons)
    assert "OI_Contracts" not in tsv.splitlines()[0].split("\t")


def test_research_gex_caption_describes_unsigned_v2_methodology():
    source = (ROOT / "btc_live_research_dashboard.py").read_text(encoding="utf-8")
    caption = next(line for line in source.splitlines() if "GEX is model-derived from BTC-denominated" in line)
    assert "BTC-denominated OI (Delta ticker oi) with multiplier 1.0" in caption
    assert "Gross unsigned exposure concentration only" in source
    assert "0.001 BTC-per-contract multiplier" not in caption
    assert "dealer positioning or net gamma" not in caption
    assert 'OI Concentration Map (BTC)' in source
    assert 'BTC-denominated OI concentration across the same 10 nearest strikes' in source
    assert 'OI (BTC; Call left / Put right)' in source


def test_phase2_utility_is_documented_as_legacy_only_and_archive_provenance_limitation():
    doc = (ROOT / "PHASE3_8_RESEARCH_CONSUMER_UNIT_SAFETY.md").read_text(encoding="utf-8")
    assert "Do not apply them to v2 BTC-denominated" in doc
    assert "raw_payload" in doc and "metric_version" in doc
    assert "oi_contracts" in (ROOT / "phase2_oi_migration.py").read_text(encoding="utf-8")


def test_research_adapter_fails_closed_when_provenance_columns_are_missing(monkeypatch):
    snap = _canonical_snapshot()
    monkeypatch.setattr(adapter, "select_canonical_snapshot", lambda *a, **k: (snap, {"status": "OK"}))
    row = _derived_row()
    row.pop("gex_contract_multiplier")
    result = adapter.load_canonical_research_options(_Client(row))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "MISSING_DERIVED_FIELDS"
    assert "gex_contract_multiplier" in result["missing_columns"]
