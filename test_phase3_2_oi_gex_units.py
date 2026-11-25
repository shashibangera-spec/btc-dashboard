"""Phase 3.2 BTC-denominated OI and GEX regression tests (offline only)."""
from __future__ import annotations
import ast
from pathlib import Path
import types
import numpy as np
import pandas as pd
import pytest
import options_collector as collector
import gex_attribution as attribution

ROOT = Path(__file__).resolve().parent
METRIC_VERSION = "options-derived-v2.0-btc-oi"
SPOT = 100_000.0


def _dashboard_helpers():
    source = (ROOT / "btc_live_dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"_extract_option_oi_values", "_decimal_value", "_validated_delta_spot_price",
             "_dashboard_gex_values", "_select_positive_wall", "_calculate_max_pain"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {node.name for node in nodes} == names
    from decimal import Decimal, InvalidOperation
    module = types.ModuleType("phase3_2_dashboard_helpers")
    module.__dict__.update({
        "np": np, "pd": pd, "Decimal": Decimal, "InvalidOperation": InvalidOperation,
    })
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "btc_live_dashboard.py", "exec"), module.__dict__)
    return module


def _row(side="CALL", strike=100_000.0, oi_btc=10.0, gamma=0.00002):
    return {"product_id": 1, "symbol": f"BTC-{side}-{strike}", "option_type": side,
            "strike_price": strike, "oi_contracts": oi_btc, "greeks": {"gamma": gamma}}


def test_collector_gex_uses_btc_oi_without_contract_size_multiplier():
    strike, gex, status = collector._calculate_gex_wall([_row()], "CALL", SPOT)
    assert status == "OK" and strike == 100_000.0
    assert gex == pytest.approx(0.00002 * 10.0 * SPOT**2 * 0.01)


def test_dashboard_gex_uses_btc_oi_without_001_conversion():
    helpers = _dashboard_helpers()
    frame = pd.DataFrame({"Gamma": [0.00002], "OI_BTC": [10.0]})
    result = helpers._dashboard_gex_values(frame, "Gamma", "OI_BTC", 1.0, SPOT, True)
    assert result.iloc[0] == pytest.approx(0.00002 * 10.0 * SPOT**2 * 0.01)


def test_collector_dashboard_attribution_gex_parity():
    helpers = _dashboard_helpers()
    rows = [_row("CALL", 100_000.0, 10.0, 0.00002), _row("CALL", 101_000.0, 5.0, 0.00004)]
    frame = pd.DataFrame([{"Strike": r["strike_price"], "Gamma": r["greeks"]["gamma"],
                           "OI_BTC": r["oi_contracts"]} for r in rows])
    dashboard = helpers._dashboard_gex_values(frame, "Gamma", "OI_BTC", 1.0, SPOT, True)
    attributed = attribution._side_exposures(rows, "CALL", SPOT, 1.0)
    for index, row in frame.iterrows():
        assert dashboard.iloc[index] == pytest.approx(attributed[row["Strike"]]["gex"])
    wall, wall_gex, status = collector._calculate_gex_wall(rows, "CALL", SPOT)
    assert status == "OK"
    assert wall == max(attributed, key=lambda strike: attributed[strike]["gex"])
    assert wall_gex == pytest.approx(attributed[wall]["gex"])


def test_delta_spot_validation_fails_closed_and_never_substitutes_binance():
    helpers = _dashboard_helpers()
    assert helpers._validated_delta_spot_price([101_000.0, 101_050.0]) == pytest.approx(101_025.0)
    assert helpers._validated_delta_spot_price([101_000.0, None]) is None
    assert helpers._validated_delta_spot_price([101_000.0, 0.0]) is None
    assert helpers._validated_delta_spot_price([100_000.0, 101_000.0]) is None
    frame = pd.DataFrame({"Gamma": [0.00002], "OI_BTC": [10.0]})
    assert helpers._dashboard_gex_values(frame, "Gamma", "OI_BTC", 1.0, None, True).isna().all()


def test_dashboard_uses_delta_spot_for_gex_and_binance_for_amt():
    source = (ROOT / "btc_live_dashboard.py").read_text(encoding="utf-8")
    assert '"Spot_Price": ticker.get("spot_price")' in source
    assert '_validated_delta_spot_price(raw_chain_df["Spot_Price"])' in source
    assert "valid_contract_size, delta_options_spot, call_gamma_complete" in source
    assert "valid_contract_size, delta_options_spot, put_gamma_complete" in source
    assert '_ui_hero("BTC / USDT", _ui_num(latest_price' in source
    assert '"BINANCE · SPOT · 30M"' in source
    assert "valid_contract_size, latest_price, call_gamma_complete" not in source


def test_delta_oi_is_primary_and_usd_oi_and_raw_payload_are_unchanged():
    helpers = _dashboard_helpers()
    assert helpers._extract_option_oi_values({"oi": "12.5", "oi_contracts": 99, "oi_value_usd": "125000"}) == ("12.5", "125000")
    assert helpers._extract_option_oi_values({"oi_contracts": 4, "oi_value_usd": 40}) == (4, 40)
    import datetime as dt
    ticker = {"product_id": 1, "symbol": "BTC-25DEC26-100000-C",
              "contract_type": "call_options", "strike_price": 100000,
              "oi": "12.5", "oi_value_usd": "125000.25", "timestamp": "1798192800000000"}
    row, error = collector.build_snapshot_row(ticker, "snapshot", "2026-12-25T00:00:00+00:00", dt.date(2026, 12, 25))
    assert error is None and row["oi_contracts"] == 12.5
    assert row["oi_value_usd"] == 125000.25 and row["raw_payload"] == ticker


def test_usd_oi_walls_still_use_exchange_usd_values():
    helpers = _dashboard_helpers()
    frame = pd.DataFrame({"Strike": [99_000, 100_000], "OI_USD_Call": [1_000_000, 3_000_000]})
    wall = helpers._select_positive_wall(frame, "OI_USD_Call")
    assert wall["Strike"] == 100_000 and wall["OI_USD_Call"] == 3_000_000


def test_max_pain_and_pcr_are_invariant_to_common_oi_scaling():
    rows = [
        {"option_type": "CALL", "strike_price": 90.0, "oi_contracts": 2.0},
        {"option_type": "CALL", "strike_price": 100.0, "oi_contracts": 5.0},
        {"option_type": "PUT", "strike_price": 90.0, "oi_contracts": 7.0},
        {"option_type": "PUT", "strike_price": 100.0, "oi_contracts": 3.0},
    ]
    scaled = [dict(row, oi_contracts=row["oi_contracts"] * 1000.0) for row in rows]
    assert collector._calculate_max_pain(rows, 95.0) == collector._calculate_max_pain(scaled, 95.0)
    pcr_a, pcr_b = collector._calculate_local_pcr(rows, 95.0), collector._calculate_local_pcr(scaled, 95.0)
    assert pcr_a[0] == pytest.approx(pcr_b[0])
    assert pcr_a[1] * 1000.0 == pytest.approx(pcr_b[1])
    assert pcr_a[2] * 1000.0 == pytest.approx(pcr_b[2])


def test_legacy_and_corrected_metric_versions_are_isolated():
    valid = {"metric_version": METRIC_VERSION, "underlying_spot_price": SPOT,
             "gex_contract_multiplier": 1.0,
             "metric_status": {"chain": "OK", "call_gex": "OK", "put_gex": "OK"}}
    legacy = dict(valid, metric_version="options-derived-v1.0", gex_contract_multiplier=0.001)
    assert attribution._valid_derived(valid)
    assert not attribution._valid_derived(legacy)
    assert not attribution._valid_derived(dict(valid, gex_contract_multiplier=0.001))


def test_zero_oi_is_valid_but_missing_malformed_and_negative_oi_fail_closed():
    assert collector._calculate_gex_wall([_row(oi_btc=0.0)], "CALL", SPOT)[2] == "ZERO_GAMMA_ONLY"
    for bad in (None, "not-a-number", float("nan"), float("inf"), -1.0):
        row = _row(oi_btc=bad)
        assert collector._calculate_gex_wall([row], "CALL", SPOT)[2] == "MISSING_OR_INVALID_OI"
        with pytest.raises(ValueError):
            attribution._side_exposures([row], "CALL", SPOT, 1.0)


def test_missing_malformed_and_negative_gamma_fail_closed():
    for gamma in (None, "bad", float("nan"), float("inf"), -0.01):
        row = _row(gamma=gamma)
        assert collector._calculate_gex_wall([row], "CALL", SPOT)[2] in {"MISSING_GAMMA", "INVALID_NEGATIVE_GAMMA"}
        with pytest.raises(ValueError):
            attribution._side_exposures([row], "CALL", SPOT, 1.0)


def test_labels_call_quantities_btc_oi_and_preserve_compatibility_fields():
    source = (ROOT / "btc_live_dashboard.py").read_text(encoding="utf-8")
    assert '"OI (BTC)"' in source and "Local BTC OI PCR" in source
    assert "Local BTC OI PCR (±10% spot)" in source
    assert '"OI (Contracts)"' not in source
    assert '"OI_Contracts"' in source and '"OI_Contracts_Source"' in source
    collector_source = (ROOT / "options_collector.py").read_text(encoding="utf-8")
    assert '"oi_contracts": extract_oi_btc(ticker)' in collector_source
    assert '"gex_contract_multiplier": GEX_OI_SCALE' in collector_source


class _HistoryQuery:
    """Minimal offline query-chain fake for _previous_complete_snapshot()."""
    def __init__(self, responses, table_name):
        self.responses, self.table_name = responses, table_name
    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def neq(self, *args, **kwargs): return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def range(self, *args, **kwargs): return self
    def execute(self):
        assert self.responses, f"Unexpected query for {self.table_name}"
        return types.SimpleNamespace(data=self.responses.pop(0))


class _HistoryClient:
    def __init__(self, responses): self.responses = list(responses)
    def table(self, name): return _HistoryQuery(self.responses, name)


def test_previous_complete_snapshot_skips_legacy_and_incomplete_v2_candidates():
    """Only the newest fully valid v2 candidate may become the predecessor."""
    good_status = {
        "chain": "OK", "call_gex": "OK", "put_gex": "OK",
        "chain_expected_contracts": 2,
    }
    legacy_v1 = {
        "snapshot_id": "legacy-v1", "expiry_date": "2026-12-25",
        "source_collected_at": "2026-11-19T09:00:00+00:00",
        "underlying_spot_price": SPOT, "gex_contract_multiplier": 0.001,
        "metric_version": "options-derived-v1.0", "metric_status": good_status,
    }
    incomplete_v2 = {
        "snapshot_id": "incomplete-v2", "expiry_date": "2026-12-25",
        "source_collected_at": "2026-11-19T09:30:00+00:00",
        "underlying_spot_price": SPOT, "gex_contract_multiplier": 1.0,
        "metric_version": METRIC_VERSION,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "INCOMPLETE",
            "chain_expected_contracts": 2,
        },
    }
    valid_v2 = {
        "snapshot_id": "valid-v2", "expiry_date": "2026-12-25",
        "source_collected_at": "2026-11-19T10:00:00+00:00",
        "underlying_spot_price": SPOT, "gex_contract_multiplier": 1.0,
        "metric_version": METRIC_VERSION, "metric_status": good_status,
    }
    raw_rows = [
        {"snapshot_id": "valid-v2", "expiry_date": "2026-12-25",
         "option_type": "CALL", "strike_price": 100_000.0,
         "product_id": 1, "symbol": "BTC-25DEC26-100000-C",
         "oi_contracts": 10.0, "greeks": {"gamma": 0.00002}},
        {"snapshot_id": "valid-v2", "expiry_date": "2026-12-25",
         "option_type": "PUT", "strike_price": 100_000.0,
         "product_id": 2, "symbol": "BTC-25DEC26-100000-P",
         "oi_contracts": 8.0, "greeks": {"gamma": 0.00002}},
    ]
    client = _HistoryClient([
        [
            {"snapshot_id": "legacy-v1", "completed_at": "3"},
            {"snapshot_id": "incomplete-v2", "completed_at": "2"},
            {"snapshot_id": "valid-v2", "completed_at": "1"},
        ],
        [legacy_v1], [incomplete_v2], [valid_v2], raw_rows,
    ])
    selected, selected_rows, reason = attribution._previous_complete_snapshot(
        client, "2026-12-25", "current-snapshot"
    )
    assert reason == "OK"
    assert selected["snapshot_id"] == "valid-v2"
    assert selected["metric_version"] == METRIC_VERSION
    assert selected_rows == raw_rows
    assert client.responses == []


def test_gex_epsilon_is_negligible_for_realistic_corrected_btc_oi_exposure():
    realistic_gex = attribution._gex(0.00002, 10.0, SPOT, 1.0)
    assert realistic_gex == pytest.approx(20_000.0)
    assert realistic_gex / attribution.GEX_EPSILON > 1e15
    ranked = attribution._top_n({
        100_000.0: {"gex": realistic_gex},
        101_000.0: {"gex": realistic_gex * 0.5},
    })
    assert list(ranked) == [100_000.0, 101_000.0]
    assert attribution._dominant_driver({
        "gamma": realistic_gex, "oi": 0.0, "spot": 0.0,
    }) == "GAMMA"


def test_gex_epsilon_only_suppresses_sub_epsilon_values_as_written():
    below_threshold = attribution.GEX_EPSILON / 10.0
    assert attribution._top_n({100_000.0: {"gex": below_threshold}}) == {}
    assert attribution._dominant_driver({
        "gamma": below_threshold, "oi": 0.0, "spot": 0.0,
    }) == "UNATTRIBUTED"


@pytest.mark.parametrize("bad_oi", [
    "not-a-number", True, False, float("nan"), float("inf"), float("-inf"),
    -0.0001, Decimal("NaN"), Decimal("Infinity"),
])
def test_invalid_delta_oi_is_unavailable_in_collector_and_dashboard(bad_oi):
    helpers = _dashboard_helpers()
    ticker = {"oi": bad_oi, "oi_contracts": 25, "oi_value_usd": "250000"}
    assert collector.extract_oi_btc(ticker) is None
    dashboard_oi, usd_oi = helpers._extract_option_oi_values(ticker)
    assert dashboard_oi is None
    assert usd_oi == "250000"


def test_delta_btc_oi_extraction_never_falls_back_to_contract_count():
    helpers = _dashboard_helpers()
    ticker = {"oi_contracts": 25, "oi_value_usd": "250000"}
    assert collector.extract_oi_btc(ticker) is None
    assert helpers._extract_option_oi_values(ticker) == (None, "250000")


def test_delta_btc_oi_wins_when_both_source_fields_are_present():
    helpers = _dashboard_helpers()
    ticker = {"oi": "0.025", "oi_contracts": 25, "oi_value_usd": "250000"}
    assert collector.extract_oi_btc(ticker) == pytest.approx(0.025)
    assert helpers._extract_option_oi_values(ticker) == ("0.025", "250000")


def test_zero_delta_btc_oi_is_valid_in_collector_and_dashboard():
    helpers = _dashboard_helpers()
    ticker = {"oi": 0, "oi_contracts": 25, "oi_value_usd": 0}
    assert collector.extract_oi_btc(ticker) == 0.0
    assert helpers._extract_option_oi_values(ticker) == (0, 0)
    assert collector._calculate_gex_wall([_row(oi_btc=0.0)], "CALL", SPOT)[2] == "ZERO_GAMMA_ONLY"
    frame = pd.DataFrame({"Gamma": [0.00002], "OI_BTC": [0.0]})
    result = helpers._dashboard_gex_values(frame, "Gamma", "OI_BTC", 1.0, SPOT, True)
    assert result.iloc[0] == 0.0


def test_invalid_or_missing_btc_oi_cannot_produce_valid_gex():
    helpers = _dashboard_helpers()
    invalid_values = [None, "bad", True, float("nan"), float("inf"), -1.0]
    for value in invalid_values:
        ticker = {"oi": value, "oi_contracts": 25}
        oi_btc = collector.extract_oi_btc(ticker)
        assert oi_btc is None
        row = _row(oi_btc=oi_btc)
        assert collector._calculate_gex_wall([row], "CALL", SPOT)[2] == "MISSING_OR_INVALID_OI"
        frame = pd.DataFrame({"Gamma": [0.00002], "OI_BTC": [oi_btc]})
        dashboard_gex = helpers._dashboard_gex_values(
            frame, "Gamma", "OI_BTC", 1.0, SPOT, True
        )
        assert dashboard_gex.isna().all()


def test_valid_btc_oi_produces_expected_gex_with_neutral_multiplier():
    helpers = _dashboard_helpers()
    oi_btc = collector.extract_oi_btc({"oi": "10", "oi_contracts": 10_000})
    assert oi_btc == 10.0
    expected = 0.00002 * 10.0 * SPOT**2 * 0.01
    strike, collector_gex, status = collector._calculate_gex_wall(
        [_row(oi_btc=oi_btc)], "CALL", SPOT
    )
    assert status == "OK" and strike == 100_000.0
    assert collector_gex == pytest.approx(expected)
    frame = pd.DataFrame({"Gamma": [0.00002], "OI_BTC": [oi_btc]})
    dashboard_gex = helpers._dashboard_gex_values(
        frame, "Gamma", "OI_BTC", 1.0, SPOT, True
    )
    assert dashboard_gex.iloc[0] == pytest.approx(expected)
