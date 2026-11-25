"""Offline Phase 3 GEX validation tests. No network or database clients are invoked."""
from __future__ import annotations

import ast
import datetime as dt
import math
from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import numpy as np
import pandas as pd
import pytest

import options_collector as collector
import gex_attribution as attribution


ROOT = Path(__file__).resolve().parent
SPOT = 90_000.0
MULTIPLIER = 0.001

# Immutable shared synthetic fixture used by all three independent calculations.
BTC_OPTIONS_FIXTURE = tuple(
    MappingProxyType(row) for row in (
        {"product_id": 900001, "symbol": "BTC-25DEC26-90000-C", "option_type": "CALL",
         "strike_price": 90000.0, "oi_contracts": 1200.0, "gamma": 0.000020},
        {"product_id": 900002, "symbol": "BTC-25DEC26-91000-C", "option_type": "CALL",
         "strike_price": 91000.0, "oi_contracts": 800.0, "gamma": 0.000030},
        {"product_id": 900003, "symbol": "BTC-25DEC26-92000-C", "option_type": "CALL",
         "strike_price": 92000.0, "oi_contracts": 0.0, "gamma": 0.000040},
        {"product_id": 900004, "symbol": "BTC-25DEC26-89000-P", "option_type": "PUT",
         "strike_price": 89000.0, "oi_contracts": 500.0, "gamma": 0.000025},
        {"product_id": 900005, "symbol": "BTC-25DEC26-90000-P", "option_type": "PUT",
         "strike_price": 90000.0, "oi_contracts": 1500.0, "gamma": 0.000015},
    )
)


def _dashboard_helpers():
    source = (ROOT / "btc_live_dashboard.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"_validated_btc_contract_multiplier", "_validate_gamma_side", "_dashboard_gex_values", "_listed_ticker_identities_match"}
    nodes = [
        node for node in tree.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in names
    ]
    assert {node.name for node in nodes} == names
    import types
    module = types.ModuleType("phase3_dashboard_helpers")
    module.__dict__.update({"np": np, "pd": pd})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), "btc_live_dashboard.py", "exec"), module.__dict__)
    return module


def _ticker(oi_field, value):
    row = {
        "product_id": 1, "symbol": "BTC-25DEC26-90000-C",
        "contract_type": "call_options", "strike_price": 90000,
        "timestamp": "1798192800000000",
    }
    row[oi_field] = value
    return row


@pytest.mark.parametrize("field", ["oi", "oi_contracts"])
def test_collector_accepts_documented_and_legacy_oi_fields(field):
    row, error = collector.build_snapshot_row(
        _ticker(field, 17), "snapshot", "2026-12-25T00:00:00+00:00",
        dt.date(2026, 12, 25),
    )
    assert error is None
    assert row["oi_contracts"] == 17.0


def test_collector_preserves_zero_oi():
    assert collector.extract_oi_contracts({"oi": 0}) == 0.0
    row, error = collector.build_snapshot_row(
        _ticker("oi", 0), "snapshot", "2026-12-25T00:00:00+00:00",
        dt.date(2026, 12, 25),
    )
    assert error is None
    assert row["oi_contracts"] == 0.0


@pytest.mark.parametrize("value", [None, "", "not-a-number", float("nan"), float("inf"), -float("inf")])
def test_collector_missing_malformed_and_nonfinite_oi_remain_missing(value):
    assert collector.extract_oi_contracts({"oi": value}) is None


def test_collector_uses_legacy_field_only_when_documented_field_is_absent():
    assert collector.extract_oi_contracts({"oi_contracts": "4"}) == 4.0
    # A present-but-invalid documented field is not silently replaced by stale legacy data.
    assert collector.extract_oi_contracts({"oi": "bad", "oi_contracts": 99}) is None


def test_collector_rejects_duplicate_product_ids_and_duplicate_symbols():
    base = [
        {"product_id": 1, "symbol": "BTC-C1"},
        {"product_id": 2, "symbol": "BTC-P1"},
    ]
    duplicate_id = [base[0], {"product_id": 1, "symbol": "BTC-P1"}]
    duplicate_symbol = [base[0], {"product_id": 2, "symbol": "BTC-C1"}]
    assert collector.validate_contract_identities(duplicate_id) == (False, "DUPLICATE_PRODUCT_ID")
    assert collector.validate_contract_identities(duplicate_symbol) == (False, "DUPLICATE_SYMBOL")


def test_collector_detects_missing_contract_substituted_by_duplicate():
    discovery = [
        {"product_id": 1, "symbol": "BTC-C1"},
        {"product_id": 2, "symbol": "BTC-P1"},
    ]
    received = [
        {"product_id": 1, "symbol": "BTC-C1"},
        {"product_id": 1, "symbol": "BTC-C1"},
    ]
    assert len(received) == len(discovery)
    assert collector.validate_contract_identities(discovery) == (True, "OK")
    assert collector.validate_contract_identities(received) == (False, "DUPLICATE_PRODUCT_ID")


def _products_for_side(side):
    contract_type = "call_options" if side == "Call" else "put_options"
    option_type = "CALL" if side == "Call" else "PUT"
    return [
        {"symbol": row["symbol"], "contract_type": contract_type}
        for row in BTC_OPTIONS_FIXTURE if row["option_type"] == option_type
    ]


def _raw_side_frame(side, gamma_override=None, zero_oi_missing_gamma=False):
    rows = []
    for item in BTC_OPTIONS_FIXTURE:
        if item["option_type"] != ("CALL" if side == "Call" else "PUT"):
            continue
        gamma = item["gamma"] if gamma_override is None else gamma_override
        if zero_oi_missing_gamma and item["oi_contracts"] == 0:
            gamma = np.nan
        rows.append({
            "Symbol": item["symbol"], "Type": side, "Gamma": gamma,
            "Strike": item["strike_price"], "OI_Contracts": item["oi_contracts"],
        })
    return pd.DataFrame(rows)


@pytest.mark.parametrize("bad_gamma,expected", [
    (np.nan, "MISSING_GAMMA"),
    (np.inf, "INVALID_GAMMA"),
    (-0.1, "INVALID_GAMMA"),
])
def test_dashboard_gamma_completeness_rejects_missing_nonfinite_and_negative(bad_gamma, expected):
    helpers = _dashboard_helpers()
    raw = _raw_side_frame("Call")
    raw.loc[0, "Gamma"] = bad_gamma
    assert helpers._validate_gamma_side(_products_for_side("Call"), raw, "Call") == expected


def test_dashboard_zero_oi_missing_gamma_remains_invalid_under_existing_policy():
    helpers = _dashboard_helpers()
    raw = _raw_side_frame("Call", zero_oi_missing_gamma=True)
    assert helpers._validate_gamma_side(_products_for_side("Call"), raw, "Call") == "MISSING_GAMMA"


def test_dashboard_ticker_product_ids_must_match_listed_products_by_symbol():
    helpers = _dashboard_helpers()
    products = [
        {"id": 101, "symbol": "BTC-CALL"},
        {"id": 202, "symbol": "BTC-PUT"},
    ]
    matching = [
        {"product_id": 202, "symbol": "BTC-PUT"},
        {"product_id": 101, "symbol": "BTC-CALL"},
    ]
    assert helpers._listed_ticker_identities_match(products, matching)

    wrong_id_for_symbol = [
        {"product_id": 999, "symbol": "BTC-CALL"},
        {"product_id": 202, "symbol": "BTC-PUT"},
    ]
    assert not helpers._listed_ticker_identities_match(products, wrong_id_for_symbol)
    assert not helpers._listed_ticker_identities_match(products, matching[:1])

    duplicate_symbol = matching + [{"product_id": 303, "symbol": "BTC-CALL"}]
    assert not helpers._listed_ticker_identities_match(products, duplicate_symbol)


def test_dashboard_multiplier_requires_explicit_btc_units_and_consistent_metadata():
    helpers = _dashboard_helpers()
    assert helpers._validated_btc_contract_multiplier([
        {"contract_value": "0.001", "contract_unit_currency": "BTC"},
        {"contract_value": 0.001, "contract_unit_currency": "BTC"},
    ]) == pytest.approx(0.001)
    assert helpers._validated_btc_contract_multiplier([
        {"contract_value": 0.001, "contract_unit_currency": None},
    ]) is None
    assert helpers._validated_btc_contract_multiplier([
        {"contract_value": 0.001, "contract_unit_currency": "USD"},
    ]) is None
    assert helpers._validated_btc_contract_multiplier([
        {"contract_value": 0.001, "contract_unit_currency": "BTC"},
        {"contract_value": 0.01, "contract_unit_currency": "BTC"},
    ]) is None


def test_dashboard_valid_zero_exposure_is_numeric_zero_not_unavailable():
    helpers = _dashboard_helpers()
    frame = pd.DataFrame({"Gamma_Call": [0.00004], "OI_Contracts_Call": [0.0]})
    values = helpers._dashboard_gex_values(
        frame, "Gamma_Call", "OI_Contracts_Call", MULTIPLIER, SPOT, True
    )
    assert values.iloc[0] == 0.0
    unavailable = helpers._dashboard_gex_values(
        frame, "Gamma_Call", "OI_Contracts_Call", MULTIPLIER, SPOT, False
    )
    assert math.isnan(unavailable.iloc[0])


def test_dashboard_missing_or_invalid_gamma_side_returns_only_unavailable_values():
    helpers = _dashboard_helpers()
    frame = pd.DataFrame({"Gamma_Call": [np.nan], "OI_Contracts_Call": [100.0]})
    values = helpers._dashboard_gex_values(
        frame, "Gamma_Call", "OI_Contracts_Call", MULTIPLIER, SPOT, False
    )
    assert values.isna().all()


@pytest.mark.parametrize("side", ["Call", "Put"])
def test_dashboard_collector_and_attribution_gex_parity_on_identical_fixture(side):
    helpers = _dashboard_helpers()
    option_type = "CALL" if side == "Call" else "PUT"
    fixture_rows = [
        dict(item, greeks={"gamma": item["gamma"]})
        for item in BTC_OPTIONS_FIXTURE if item["option_type"] == option_type
    ]
    raw = pd.DataFrame([
        {"Symbol": row["symbol"], "Type": side, "Gamma": row["gamma"],
         "Strike": row["strike_price"], "OI_Contracts": row["oi_contracts"]}
        for row in fixture_rows
    ])
    assert helpers._validate_gamma_side(_products_for_side(side), raw, side) == "OK"
    dashboard_values = helpers._dashboard_gex_values(
        raw, "Gamma", "OI_Contracts", MULTIPLIER, SPOT, True
    )
    collector_wall, collector_value, collector_status = collector._calculate_gex_wall(
        fixture_rows, option_type, SPOT
    )
    attribution_values = attribution._side_exposures(
        fixture_rows, option_type, SPOT, MULTIPLIER
    )
    assert collector_status == "OK"
    for (_, row), dashboard_gex in zip(raw.iterrows(), dashboard_values):
        strike = float(row["Strike"])
        assert dashboard_gex == pytest.approx(attribution_values[strike]["gex"])
    assert collector_wall == max(attribution_values, key=lambda strike: attribution_values[strike]["gex"])
    assert collector_value == pytest.approx(attribution_values[collector_wall]["gex"])


@pytest.mark.parametrize("bad_gamma", [None, float("nan"), float("inf"), -0.01])
def test_invalid_gamma_fails_closed_across_collector_and_attribution(bad_gamma):
    rows = [
        {"product_id": 1, "symbol": "BTC-C", "option_type": "CALL", "strike_price": 90000,
         "oi_contracts": 100, "greeks": {"gamma": bad_gamma}},
    ]
    _, _, status = collector._calculate_gex_wall(rows, "CALL", SPOT)
    assert status in {"MISSING_GAMMA", "INVALID_NEGATIVE_GAMMA"}
    with pytest.raises(ValueError):
        attribution._side_exposures(rows, "CALL", SPOT, MULTIPLIER)


class _Response:
    def __init__(self, data):
        self.data = data


class _Query:
    def __init__(self, client, table):
        self.client, self.table_name = client, table
        self.start, self.end = None, None
    def order(self, column, **kwargs):
        self.client.orders.append((self.table_name, column, kwargs.get("desc", False)))
        return self
    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): return self
    def neq(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def range(self, start, end):
        self.start, self.end = start, end
        self.client.ranges.append((start, end))
        return self
    def execute(self):
        return _Response(self.client.responses.pop(0))


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.ranges = []
        self.orders = []
    def table(self, name):
        return _Query(self, name)


def _valid_derived(expected):
    return {
        "snapshot_id": "previous", "expiry_date": "2026-12-25",
        "source_collected_at": "2026-12-25T00:00:00+00:00",
        "underlying_spot_price": SPOT, "gex_contract_multiplier": MULTIPLIER,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "OK",
            "chain_expected_contracts": expected,
        },
    }


def _historical_rows(count):
    return [
        {"snapshot_id": "previous", "expiry_date": "2026-12-25",
         "option_type": "CALL" if i % 2 == 0 else "PUT",
         "strike_price": 90000 + i, "product_id": i + 1, "symbol": f"BTC-{i}",
         "oi_contracts": 1, "greeks": {"gamma": 0.00001}}
        for i in range(count)
    ]


def test_historical_retrieval_paginates_and_reconciles_exact_expected_count():
    rows = _historical_rows(1001)
    client = _Client([
        [{"snapshot_id": "previous", "completed_at": "1"}],
        [_valid_derived(1001)],
        rows[:1000],
        rows[1000:],
    ])
    derived, fetched, reason = attribution._previous_complete_snapshot(
        client, "2026-12-25", "current"
    )
    assert derived["snapshot_id"] == "previous"
    assert len(fetched) == 1001
    assert reason == "OK"
    assert client.ranges == [(0, 999), (1000, 1000)]
    assert client.orders[-2:] == [
        ("options_oi_snapshots", "strike_price", False),
        ("options_oi_snapshots", "product_id", False),
    ]


def test_historical_retrieval_refuses_more_than_configured_cap():
    client = _Client([
        [{"snapshot_id": "previous", "completed_at": "1"}],
        [_valid_derived(5001)],
    ])
    derived, rows, reason = attribution._previous_complete_snapshot(
        client, "2026-12-25", "current"
    )
    assert derived is None and rows is None
    assert reason == "HISTORICAL_CHAIN_EXCEEDS_RETRIEVAL_LIMIT"
    assert client.ranges == []


def test_historical_retrieval_rejects_short_page_and_duplicate_contract_identity():
    client = _Client([
        [{"snapshot_id": "previous", "completed_at": "1"}],
        [_valid_derived(2)],
        _historical_rows(1),
    ])
    derived, rows, reason = attribution._previous_complete_snapshot(
        client, "2026-12-25", "current"
    )
    assert derived is None and rows is None
    assert reason == "HISTORICAL_RETRIEVAL_INCOMPLETE"

    duplicate = _historical_rows(2)
    duplicate[1]["product_id"] = duplicate[0]["product_id"]
    ok, reason = attribution._rows_match_chain_evidence(
        duplicate, _valid_derived(2)["metric_status"]
    )
    assert not ok and reason == "DUPLICATE_HISTORICAL_PRODUCT_ID"

    duplicate_symbol = _historical_rows(2)
    duplicate_symbol[1]["symbol"] = duplicate_symbol[0]["symbol"]
    ok, reason = attribution._rows_match_chain_evidence(
        duplicate_symbol, _valid_derived(2)["metric_status"]
    )
    assert not ok and reason == "DUPLICATE_HISTORICAL_SYMBOL"

    missing_product_id = _historical_rows(2)
    missing_product_id[1]["product_id"] = None
    ok, reason = attribution._rows_match_chain_evidence(
        missing_product_id, _valid_derived(2)["metric_status"]
    )
    assert not ok and reason == "HISTORICAL_CONTRACT_IDENTITY_MISSING"


def test_historical_retrieval_rejects_legacy_snapshot_without_expected_count_evidence():
    derived = _valid_derived(2)
    del derived["metric_status"]["chain_expected_contracts"]
    client = _Client([
        [{"snapshot_id": "previous", "completed_at": "1"}],
        [derived],
    ])
    result = attribution._previous_complete_snapshot(client, "2026-12-25", "current")
    assert result == (None, None, "MISSING_EXPECTED_CONTRACT_COUNT")


@pytest.mark.parametrize(
    "received,expected",
    [
        (
            [{"product_id": 1, "symbol": "BTC-C1"}, {"product_id": 2, "symbol": "BTC-P1"}],
            (True, "OK"),
        ),
        (
            [{"product_id": 2, "symbol": "BTC-C1"}, {"product_id": 1, "symbol": "BTC-P1"}],
            (False, "DISCOVERY_CHAIN_IDENTITY_MISMATCH"),
        ),
        (
            [{"product_id": 99, "symbol": "BTC-C1"}, {"product_id": 2, "symbol": "BTC-P1"}],
            (False, "DISCOVERY_CHAIN_IDENTITY_MISMATCH"),
        ),
        (
            [{"symbol": "BTC-C1"}, {"product_id": 2, "symbol": "BTC-P1"}],
            (False, "MISSING_CONTRACT_IDENTITY"),
        ),
        (
            [{"product_id": 1, "symbol": "BTC-C1"}, {"product_id": 1, "symbol": "BTC-P1"}],
            (False, "DUPLICATE_PRODUCT_ID"),
        ),
        (
            [{"product_id": 1, "symbol": "BTC-C1"}, {"product_id": 2, "symbol": "BTC-C1"}],
            (False, "DUPLICATE_SYMBOL"),
        ),
    ],
)
def test_collector_requires_exact_discovery_symbol_to_product_id_mapping(received, expected):
    discovery = [
        {"product_id": 1, "symbol": "BTC-C1"},
        {"product_id": 2, "symbol": "BTC-P1"},
    ]
    assert collector.validate_contract_identity_mapping(discovery, received) == expected


def test_collector_rejects_swapped_mapping_even_when_counts_and_individual_sets_match():
    discovery = [
        {"product_id": 101, "symbol": "BTC-CALL"},
        {"product_id": 202, "symbol": "BTC-PUT"},
    ]
    received = [
        {"product_id": 202, "symbol": "BTC-CALL"},
        {"product_id": 101, "symbol": "BTC-PUT"},
    ]
    assert len(discovery) == len(received)
    assert {row["product_id"] for row in discovery} == {row["product_id"] for row in received}
    assert {row["symbol"] for row in discovery} == {row["symbol"] for row in received}
    assert collector.validate_contract_identity_mapping(discovery, received) == (
        False, "DISCOVERY_CHAIN_IDENTITY_MISMATCH"
    )


def test_collector_rejects_malformed_symbol_in_identity_mapping():
    discovery = [{"product_id": 1, "symbol": " BTC-C1 "}]
    received = [{"product_id": 1, "symbol": "BTC-C1"}]
    assert collector.validate_contract_identity_mapping(discovery, received) == (
        False, "MALFORMED_CONTRACT_IDENTITY"
    )
