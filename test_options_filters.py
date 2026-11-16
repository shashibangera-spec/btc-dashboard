"""Offline tests for canonical options snapshot selection and five filters."""
from datetime import date
import pandas as pd
import pytest
import options_filters as f


def raw_rows(snapshot="s1", expiry="2026-11-17", spot=1000.0):
    rows = []
    for strike, coi, poi, civ, vol, buy, sell in [
        (800, 100, 100, .60, 20, 1, 1), (900, 100, 100, .58, 30, 1, 1),
        (1000, 1000, 1000, .55, 40, 1, 1), (1100, 100, 100, .52, 50, 1, 1),
        (1200, 100, 100, .50, 60, 1, 1),
    ]:
        for side, oi, iv in (("CALL", coi, civ), ("PUT", poi, civ * .95)):
            rows.append({
                "snapshot_id": snapshot, "expiry_date": expiry, "symbol": f"{side}-{strike}",
                "product_id": f"{side}-{strike}", "option_type": side, "strike_price": strike,
                "oi_contracts": oi, "oi_value_usd": oi * spot, "volume_24h": vol,
                "turnover_usd": vol * spot, "mark_iv": iv, "taker_buy_volume": buy,
                "taker_sell_volume": sell, "spot_price": spot, "collected_at": "2026-11-16T12:00:00Z",
                "exchange_timestamp": None, "data_status": "OK",
            })
    return pd.DataFrame(rows, columns=f.RAW_COLUMNS)


def snapshot(raw=None, previous=None, previous_id=None):
    return f.CanonicalSnapshot(
        snapshot_id="s1", expiry_date="2026-11-17", completed_at="2026-11-16T12:30:00Z",
        spot_price=1000.0, raw=raw if raw is not None else raw_rows(),
        previous_snapshot_id=previous_id,
        previous_completed_at="2026-11-16T12:00:00Z" if previous is not None else None,
        previous_raw=previous,
    )


class Response:
    def __init__(self, data): self.data = data


class Query:
    def __init__(self, db, table):
        self.db, self.table, self.filters = db, table, []
        self.ordering = None
    def select(self, *_): return self
    def eq(self, key, value): self.filters.append((key, value)); return self
    def order(self, key, desc=False): self.ordering = (key, desc); return self
    def limit(self, n): self.limit_n = n; return self
    def range(self, start, end): self.range_n = (start, end); return self
    def execute(self):
        rows = list(self.db.get(self.table, []))
        for key, value in self.filters: rows = [r for r in rows if r.get(key) == value]
        if self.ordering:
            key, desc = self.ordering
            rows.sort(key=lambda r: r.get(key) or "", reverse=desc)
        if hasattr(self, "limit_n"): rows = rows[:self.limit_n]
        if hasattr(self, "range_n"):
            start, end = self.range_n
            rows = rows[start:end + 1]
        return Response(rows)


class Client:
    def __init__(self, db): self.db = db
    def table(self, name): return Query(self.db, name)


def database(rows=None, status="SUCCESS"):
    raw = (rows if rows is not None else raw_rows()).to_dict("records")
    return {
        "options_collection_runs": [{
            "snapshot_id": "s1", "status": status, "completed_at": "2026-11-16T12:30:00Z",
            "expected_contracts": len(raw), "received_contracts": len(raw), "inserted_contracts": len(raw),
        }],
        "options_derived_snapshots": [{
            "snapshot_id": "s1", "expiry_date": "2026-11-17", "metric_status": {"chain": "OK"},
            "calculated_at": "2026-11-16T12:30:00Z", "source_collected_at": "2026-11-16T12:00:00Z",
            "underlying_spot_price": 1000,
        }],
        "options_oi_snapshots": raw,
    }


def test_snapshot_validation_pins_identity_expiry_and_consistent_spot():
    raw = raw_rows()
    assert f.validate_raw_snapshot(raw, "s1", "2026-11-17")["status"] == "OK"
    assert "SNAPSHOT_ID_MISMATCH" in f.validate_raw_snapshot(raw, "other", "2026-11-17")["errors"]
    assert "EXPIRY_MISMATCH" in f.validate_raw_snapshot(raw, "s1", "2026-11-18")["errors"]
    raw.loc[0, "spot_price"] = 999
    assert "INCONSISTENT_SNAPSHOT_SPOT" in f.validate_raw_snapshot(raw, "s1", "2026-11-17")["errors"]


def test_selector_uses_success_run_and_matching_snapshot_rows():
    db = database()
    db["options_collection_runs"].append({
        "snapshot_id": "partial", "status": "PARTIAL", "completed_at": "2026-11-16T13:00:00Z",
    })
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.snapshot_id == "s1" and selected.expiry_date == "2026-11-17"
    assert selected.spot_price == 1000 and meta["status"] == "OK"


def test_latest_success_without_valid_expiry_fails_closed_without_fallback():
    db = database()
    db["options_collection_runs"].insert(0, {
        "snapshot_id": "newest", "status": "SUCCESS", "completed_at": "2026-11-16T13:00:00Z",
        "expected_contracts": 1, "received_contracts": 1, "inserted_contracts": 1,
    })
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected is None and meta["reason"] == "NO_VALID_FUTURE_EXPIRY"


def test_nulls_are_preserved_in_aggregation_not_converted_to_zero():
    raw = raw_rows()
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "volume_24h"] = None
    chain = f._aggregate_chain(raw)
    assert pd.isna(chain.loc[chain.strike_price == 1000, "Call Vol"].iloc[0])


def test_nearby_oi_uses_local_peak_and_strict_threshold():
    raw = raw_rows()
    raw.loc[raw.strike_price == 1000, "oi_contracts"] = 100
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "oi_contracts"] = 1000
    result = f.calculate_nearby_oi(snapshot(raw))
    assert result["status"] == "OK"
    assert {"strike": 1000.0, "type": "CALL", "oi": 1000.0} in result["walls"]


@pytest.mark.parametrize(("call_iv", "expected"), [
    (.60, "BULLISH_URGENCY"), (.50, "NEUTRAL"), (.40, "BEARISH_URGENCY"),
])
def test_iv_skew_classifications(call_iv, expected):
    raw = raw_rows()
    raw.loc[raw.option_type == "CALL", "mark_iv"] = call_iv
    raw.loc[raw.option_type == "PUT", "mark_iv"] = .50
    assert f.calculate_iv_skew(snapshot(raw))["classification"] == expected


def test_iv_skew_requires_both_iv_sides():
    raw = raw_rows()
    raw.loc[raw.option_type == "PUT", "mark_iv"] = None
    assert f.calculate_iv_skew(snapshot(raw))["status"] == "INSUFFICIENT_DATA"


def test_volume_oi_uses_24h_volume_and_fails_closed_on_null_nearest_contract():
    raw = raw_rows()
    raw["volume_24h"] = 600
    assert f.calculate_volume_oi(snapshot(raw))["classification"] == "FRESH_MONEY_ENTERING"
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "volume_24h"] = None
    assert f.calculate_volume_oi(snapshot(raw))["status"] == "INSUFFICIENT_DATA"


def test_migration_tie_break_is_lower_strike_and_elapsed_time_is_reported():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    current.loc[current.strike_price.isin([900, 1100]), "oi_contracts"] = 1000
    previous.loc[previous.strike_price.isin([900, 1100]), "oi_contracts"] = 1000
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "OK" and result["call_shift"] == 0
    assert result["elapsed_seconds"] == 1800


def test_migration_requires_same_contract_universe_and_complete_oi():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    assert f.calculate_oi_migration(snapshot(current, previous.iloc[:-1], "s0"))["reason"] == "CONTRACT_UNIVERSE_MISMATCH"
    previous.loc[0, "oi_contracts"] = None
    assert f.calculate_oi_migration(snapshot(current, previous, "s0"))["status"] == "INSUFFICIENT_DATA"


def test_taker_is_sampled_and_fails_closed_on_partial_coverage():
    raw = raw_rows()
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "taker_buy_volume"] = None
    assert f.calculate_taker_activity(snapshot(raw))["status"] == "PARTIAL"
    raw = raw_rows()
    raw["taker_buy_volume"], raw["taker_sell_volume"] = 20, 1
    assert f.calculate_taker_activity(snapshot(raw))["classification"] == "AGGRESSIVE_BUYING"


def test_one_calculator_exception_does_not_suppress_other_filters(monkeypatch):
    monkeypatch.setattr(f, "calculate_iv_skew", lambda _: (_ for _ in ()).throw(RuntimeError()))
    results = f.calculate_all_filters(snapshot())
    assert results["iv_skew"]["status"] == "ERROR"
    assert results["nearby_oi"]["status"] == results["volume_oi"]["status"] == "OK"
    assert results["taker_activity"]["status"] == "OK"
