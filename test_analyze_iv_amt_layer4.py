from scripts.analyze_iv_amt_layer4 import (
    dte_bucket,
    legacy_nearest_five_skew,
    pearson,
    quote_exposure,
)


def test_legacy_skew_uses_nearest_five_unique_strikes_and_legacy_formula():
    contracts = []
    for strike, call, put in [
        (90, 0.50, 0.60), (95, 0.51, 0.61), (100, 0.52, 0.62),
        (105, 0.53, 0.63), (110, 0.54, 0.64), (120, 9.0, 9.0),
    ]:
        contracts.extend([
            {"strike_price": strike, "option_type": "CALL", "mark_iv": call},
            {"strike_price": strike, "option_type": "PUT", "mark_iv": put},
        ])
    result = legacy_nearest_five_skew(contracts, 100)
    assert result["legacy_skew_pct"] is not None
    assert result["legacy_skew_pct"] < 0


def test_dte_buckets_are_explicit():
    assert dte_bucket(0) == "0DTE"
    assert dte_bucket(2) == "1-2 DTE"
    assert dte_bucket(5) == "Weekly (3-7 DTE)"
    assert dte_bucket(30) == "Monthly (>7 DTE)"


def test_quote_exposure_flags_wide_and_sentinel_independently():
    result = quote_exposure([{
        "bid_price": 80, "ask_price": 120,
        "bid_iv": 0.01, "ask_iv": 0.55, "mark_iv": 0.01,
    }])
    assert result["wide_spread_exposed"] is True
    assert result["sentinel_exposed"] is True
    assert result["mark_iv_sentinel_contract_count"] == 1


def test_pearson_positive_and_sample_count():
    value, n = pearson([(1, 2), (2, 4), (3, 6)])
    assert n == 3
    assert value == 1.0


class _FakeResponse:
    def __init__(self, data):
        self.data = data


class _FakeQuery:
    def __init__(self, table):
        self.table = table
        self.selected = None
        self.snapshot_id = None

    def select(self, columns):
        self.selected = columns
        return self

    def eq(self, column, value):
        assert column == "snapshot_id"
        self.snapshot_id = value
        return self

    def execute(self):
        self.table.calls.append((self.selected, self.snapshot_id))
        return _FakeResponse(self.table.rows_by_snapshot.get(self.snapshot_id, []))


class _FakeTable:
    def __init__(self, rows_by_snapshot):
        self.rows_by_snapshot = rows_by_snapshot
        self.calls = []

    def table(self, name):
        assert name == "options_oi_snapshots"
        return _FakeQuery(self)


def test_contract_fetch_uses_one_narrow_query_per_fresh_snapshot():
    from scripts.analyze_iv_amt_layer4 import fetch_contracts_for_fresh_rows

    rows = [
        {"snapshot_id": "fresh-a", "expiry_date": "2026-11-20"},
        {"snapshot_id": "fresh-a", "expiry_date": "2026-11-27"},
        {"snapshot_id": "fresh-b", "expiry_date": "2026-11-20"},
    ]
    raw_a = [
        {"snapshot_id": "fresh-a", "expiry_date": "2026-11-20", "option_type": "CALL",
         "strike_price": 65000, "mark_iv": 0.5, "bid_price": 10, "ask_price": 12,
         "bid_iv": 0.49, "ask_iv": 0.51},
        {"snapshot_id": "fresh-a", "expiry_date": "2026-11-27", "option_type": "PUT",
         "strike_price": 65000, "mark_iv": 0.55, "bid_price": 11, "ask_price": 13,
         "bid_iv": 0.54, "ask_iv": 0.56},
    ]
    raw_b = [
        {"snapshot_id": "fresh-b", "expiry_date": "2026-11-20", "option_type": "PUT",
         "strike_price": 65000, "mark_iv": 0.6, "bid_price": 9, "ask_price": 14,
         "bid_iv": 0.59, "ask_iv": 0.61},
    ]
    client = _FakeTable({"fresh-a": raw_a, "fresh-b": raw_b, "stale": [{"snapshot_id": "stale"}]})

    result = fetch_contracts_for_fresh_rows(client, rows)

    assert len(client.calls) == 2
    assert {sid for _, sid in client.calls} == {"fresh-a", "fresh-b"}
    expected_columns = (
        "snapshot_id,expiry_date,option_type,strike_price,mark_iv,"
        "bid_price,ask_price,bid_iv,ask_iv"
    )
    assert all(columns == expected_columns for columns, _ in client.calls)
    assert len(result[("fresh-a", "2026-11-20")]) == 1
    assert len(result[("fresh-a", "2026-11-27")]) == 1
    assert len(result[("fresh-b", "2026-11-20")]) == 1
    assert ("stale", "") not in result
