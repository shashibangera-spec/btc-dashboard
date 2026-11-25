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
        metric_version=f.EXPECTED_METRIC_VERSION,
        gex_contract_multiplier=f.EXPECTED_GEX_CONTRACT_MULTIPLIER,
        previous_metric_version=f.EXPECTED_METRIC_VERSION if previous is not None else None,
        previous_gex_contract_multiplier=f.EXPECTED_GEX_CONTRACT_MULTIPLIER if previous is not None else None,
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
            "snapshot_id": "s1", "status": status, "started_at": "2026-11-16T12:00:00Z", "completed_at": "2026-11-16T12:30:00Z", "underlying": "BTCUSD",
            "expected_contracts": len(raw), "received_contracts": len(raw), "inserted_contracts": len(raw),
        }],
        "options_derived_snapshots": [{
            "snapshot_id": "s1", "expiry_date": "2026-11-17", "metric_status": {"chain": "OK"},
            "calculated_at": "2026-11-16T12:30:00Z", "source_collected_at": "2026-11-16T12:00:00Z",
            "underlying_spot_price": 1000,
            "metric_version": f.EXPECTED_METRIC_VERSION,
            "gex_contract_multiplier": f.EXPECTED_GEX_CONTRACT_MULTIPLIER,
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
        "snapshot_id": "partial", "status": "PARTIAL", "completed_at": "2026-11-16T13:00:00Z", "underlying": "BTCUSD",
    })
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.snapshot_id == "s1" and selected.expiry_date == "2026-11-17"
    assert selected.spot_price == 1000 and meta["status"] == "OK"


def test_latest_success_without_valid_expiry_fails_closed_without_fallback():
    db = database()
    db["options_collection_runs"].insert(0, {
        "snapshot_id": "newest", "status": "SUCCESS", "started_at": "2026-11-16T12:45:00Z", "completed_at": "2026-11-16T13:00:00Z", "underlying": "BTCUSD",
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
    assert {"strike": 1000.0, "type": "CALL", "oi_btc": 1000.0} in result["walls"]
    assert result["threshold_value"] == 0.5 and result["threshold_unit"] == "BTC"
    assert result["source_metric_version"] == f.EXPECTED_METRIC_VERSION
    assert result["source_gex_contract_multiplier"] == 1.0


@pytest.mark.parametrize("call_iv", [.60, .525, .50, .475, .40])
def test_iv_skew_exposes_continuous_call_iv_premium_without_directional_classes(call_iv):
    raw = raw_rows()
    raw.loc[raw.option_type == "CALL", "mark_iv"] = call_iv
    raw.loc[raw.option_type == "PUT", "mark_iv"] = .50
    result = f.calculate_iv_skew(snapshot(raw))
    assert result["status"] == "OK"
    assert result["metric"] == "CALL_IV_PREMIUM"
    assert result["call_iv_premium_pct"] == pytest.approx((call_iv - .50) / .50 * 100)
    assert "classification" not in result
    assert "BULLISH_URGENCY" not in str(result)
    assert "BEARISH_URGENCY" not in str(result)


def test_iv_skew_has_no_five_percent_boundary():
    raw = raw_rows()
    raw.loc[raw.option_type == "CALL", "mark_iv"] = .5255
    raw.loc[raw.option_type == "PUT", "mark_iv"] = .50
    result = f.calculate_iv_skew(snapshot(raw))
    assert result["call_iv_premium_pct"] == pytest.approx(5.1)
    assert result["metric"] == "CALL_IV_PREMIUM"


def test_iv_skew_requires_both_iv_sides():
    raw = raw_rows()
    raw.loc[raw.option_type == "PUT", "mark_iv"] = None
    assert f.calculate_iv_skew(snapshot(raw))["status"] == "INSUFFICIENT_DATA"


def test_volume_oi_is_unavailable_until_source_unit_is_verified():
    raw = raw_rows()
    original = raw["volume_24h"].copy(deep=True)
    result = f.calculate_volume_oi(snapshot(raw))
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "VOLUME_UNIT_UNVERIFIED"
    assert result["volume_source_field"] == "volume_24h"
    assert result["volume_source_unit"] is None
    assert result["oi_source_unit"] == "BTC"
    assert result["classification_available"] is False
    pd.testing.assert_series_equal(raw["volume_24h"], original)


def test_migration_tie_break_is_lower_strike_and_elapsed_time_is_reported():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    current.loc[current.strike_price.isin([900, 1100]), "oi_contracts"] = 1000
    previous.loc[previous.strike_price.isin([900, 1100]), "oi_contracts"] = 1000
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "OK" and result["call_shift"] == 0
    assert result["elapsed_seconds"] == 1800


def test_migration_requires_same_contract_universe_and_complete_oi():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    assert f.calculate_oi_migration(snapshot(current, previous.iloc[:-1], "s0"))["reason"] == "FEWER_THAN_5_VALID_PAIRED_STRIKES"
    previous.loc[0, "oi_contracts"] = None
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["reason"] == "FEWER_THAN_5_VALID_PAIRED_STRIKES"
    assert result["comparable_paired_strikes"] == 4


def test_taker_is_sampled_descriptive_and_fails_closed_on_partial_coverage():
    raw = raw_rows()
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "taker_buy_volume"] = None
    partial = f.calculate_taker_activity(snapshot(raw))
    assert partial["status"] == "PARTIAL"
    assert "sampled_buy_volume" not in partial

    raw = raw_rows()
    raw["taker_buy_volume"], raw["taker_sell_volume"] = 20, 1
    result = f.calculate_taker_activity(snapshot(raw))
    assert result["status"] == "OK"
    assert result["sampled_buy_volume"] == pytest.approx(200)
    assert result["sampled_sell_volume"] == pytest.approx(10)
    assert result["sampled_volume_balance"] == pytest.approx(190)
    assert result["sampled_volume_balance_pct"] == pytest.approx(90.4761904762)
    assert "classification" not in result
    assert result.get("metric") is None


def test_one_calculator_exception_does_not_suppress_other_filters(monkeypatch):
    monkeypatch.setattr(f, "calculate_iv_skew", lambda _: (_ for _ in ()).throw(RuntimeError()))
    results = f.calculate_all_filters(snapshot())
    assert results["iv_skew"]["status"] == "ERROR"
    assert results["nearby_oi"]["status"] == "OK"
    assert results["volume_oi"]["status"] == "UNAVAILABLE"
    assert results["taker_activity"]["status"] == "OK"



@pytest.mark.parametrize(
    ("table", "missing_column", "expected_source"),
    [
        ("options_collection_runs", "expected_contracts", "options_collection_runs"),
        ("options_derived_snapshots", "calculated_at", "options_derived_snapshots"),
        ("options_oi_snapshots", "mark_iv", "options_oi_snapshots"),
    ],
)
def test_database_boundary_missing_required_record_key_fails_closed(
    table, missing_column, expected_source, caplog
):
    db = database()
    if table == "options_oi_snapshots":
        for row in db[table]:
            row.pop(missing_column, None)
    else:
        db[table][0].pop(missing_column, None)

    selected, meta = f.select_canonical_snapshot(
        Client(db), as_of=date(2026, 11, 16)
    )

    assert selected is None
    assert meta["status"] == "UNAVAILABLE"
    assert meta["reason"] == "MISSING_REQUIRED_COLUMNS"
    assert meta["source"] == expected_source
    assert missing_column in meta["missing_columns"]
    assert missing_column in caplog.text


def test_missing_required_keys_report_record_indexes():
    details = f._missing_record_keys(
        [{"snapshot_id": "s1"}, {"snapshot_id": "s2", "status": "SUCCESS"}],
        ("snapshot_id", "status"),
    )
    assert details == {
        "missing_columns": ["status"],
        "row_indexes": {"status": [0]},
    }


def test_production_backend_contains_no_legacy_directional_iv_or_taker_labels():
    from pathlib import Path
    source = Path("options_filters.py").read_text(encoding="utf-8")
    for legacy in (
        "BULLISH_URGENCY", "BEARISH_URGENCY",
        "AGGRESSIVE_BUYING", "AGGRESSIVE_SELLING", "NEUTRAL_FLOW",
    ):
        assert legacy not in source
    assert "CALL_IV_PREMIUM" in source
    assert "call_iv_premium_pct" in source
    assert "sampled_buy_volume" in source
    assert "sampled_sell_volume" in source
    assert "sampled_volume_balance" in source


def test_zero_sampled_taker_total_has_null_percentage_balance():
    raw = raw_rows()
    raw["taker_buy_volume"] = 0
    raw["taker_sell_volume"] = 0
    result = f.calculate_taker_activity(snapshot(raw))
    assert result["status"] == "OK"
    assert result["sampled_volume_balance"] == 0
    assert result["sampled_volume_balance_pct"] is None


def _add_previous_snapshot(db, snapshot_id, completed_at, raw=None, underlying="BTCUSD"):
    rows = (raw if raw is not None else raw_rows(snapshot=snapshot_id)).copy()
    rows["snapshot_id"] = snapshot_id
    rows["collected_at"] = completed_at
    db["options_collection_runs"].append({
        "snapshot_id": snapshot_id, "status": "SUCCESS",
        "started_at": completed_at, "completed_at": completed_at,
        "underlying": underlying,
        "expected_contracts": len(rows), "received_contracts": len(rows),
        "inserted_contracts": len(rows),
    })
    db["options_derived_snapshots"].append({
        "snapshot_id": snapshot_id, "expiry_date": "2026-11-17",
        "metric_status": {"chain": "OK"}, "calculated_at": completed_at,
        "source_collected_at": completed_at, "underlying_spot_price": 1000,
        "metric_version": f.EXPECTED_METRIC_VERSION,
        "gex_contract_multiplier": f.EXPECTED_GEX_CONTRACT_MULTIPLIER,
    })
    db["options_oi_snapshots"].extend(rows.to_dict("records"))


def test_selector_skips_three_minute_run_and_selects_30_minute_baseline():
    db = database()
    _add_previous_snapshot(db, "s_3m", "2026-11-16T12:27:00Z")
    _add_previous_snapshot(db, "s_30m", "2026-11-16T12:00:00Z")
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.previous_snapshot_id == "s_30m"
    assert meta["previous_completed_at"] == "2026-11-16T12:00:00Z"


@pytest.mark.parametrize("minutes", [25, 45])
def test_selector_accepts_inclusive_25_and_45_minute_boundaries(minutes):
    db = database()
    timestamp = pd.Timestamp("2026-11-16T12:30:00Z") - pd.Timedelta(minutes=minutes)
    _add_previous_snapshot(db, "eligible", timestamp.isoformat().replace("+00:00", "Z"))
    selected, _ = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.previous_snapshot_id == "eligible"


@pytest.mark.parametrize(
    ("previous_time", "expected_reason"),
    [
        ("2026-11-16T12:27:00Z", "SNAPSHOTS_TOO_CLOSE"),
        ("2026-11-16T11:30:00Z", "SNAPSHOT_GAP_TOO_LARGE"),
    ],
)
def test_selector_reports_out_of_window_previous_snapshot_reason(previous_time, expected_reason):
    db = database()
    _add_previous_snapshot(db, "outside", previous_time)
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected is not None and selected.previous_snapshot_id is None
    assert meta["previous_snapshot_reason"] == expected_reason
    result = f.calculate_oi_migration(selected)
    assert result["status"] == "UNAVAILABLE" and result["reason"] == expected_reason


def test_selector_reports_no_previous_snapshot():
    selected, meta = f.select_canonical_snapshot(Client(database()), as_of=date(2026, 11, 16))
    assert selected.previous_snapshot_id is None
    assert meta["previous_snapshot_reason"] == "NO_ELIGIBLE_PREVIOUS_SNAPSHOT"


def test_selector_skips_invalid_candidate_inside_window():
    db = database()
    invalid = raw_rows(snapshot="invalid")
    invalid["spot_price"] = 0
    _add_previous_snapshot(db, "invalid", "2026-11-16T12:05:00Z", invalid)
    _add_previous_snapshot(db, "valid", "2026-11-16T12:00:00Z")
    selected, _ = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.previous_snapshot_id == "valid"


def test_migration_uses_identity_intersection_and_excludes_unmatched_strikes():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    current = current[current.strike_price != 800]
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["reason"] == "FEWER_THAN_5_VALID_PAIRED_STRIKES"
    assert result["comparable_paired_strikes"] == 4


def test_migration_accepts_changed_contract_universe_when_five_pairs_survive():
    current = raw_rows()
    current_extra = raw_rows()
    current_extra["strike_price"] = 1300
    current_extra["symbol"] = current_extra["option_type"].map(lambda side: f"{side}-1300")
    current_extra["product_id"] = current_extra["symbol"]
    current_extra["oi_contracts"] = 200
    current = pd.concat([current, current_extra], ignore_index=True)

    previous = raw_rows(snapshot="s0")
    previous_extra = raw_rows(snapshot="s0")
    previous_extra["strike_price"] = 1300
    previous_extra["symbol"] = previous_extra["option_type"].map(lambda side: f"{side}-1300")
    previous_extra["product_id"] = previous_extra["symbol"]
    previous_extra["oi_contracts"] = 200
    previous = pd.concat([previous, previous_extra], ignore_index=True)

    current = current[current.strike_price != 800]
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "OK"
    assert result["comparable_paired_strikes"] == 5
    assert "CONTRACT_UNIVERSE_MISMATCH" not in str(result)


def test_migration_excludes_entire_strike_when_one_matched_side_has_null_oi():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    current.loc[(current.strike_price == 800) & (current.option_type == "CALL"), "oi_contracts"] = None
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["reason"] == "FEWER_THAN_5_VALID_PAIRED_STRIKES"
    assert result["comparable_paired_strikes"] == 4


def test_migration_requires_five_valid_paired_strikes_and_reports_count():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    current = current[current.strike_price != 800]
    previous = previous[previous.strike_price != 800]
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "INSUFFICIENT_DATA"
    assert result["reason"] == "FEWER_THAN_5_VALID_PAIRED_STRIKES"
    assert result["comparable_paired_strikes"] == 4


def test_migration_keeps_lower_strike_tie_break_on_comparable_intersection():
    current, previous = raw_rows(), raw_rows(snapshot="s0")
    current.loc[current.strike_price.isin([900, 1100]), "oi_contracts"] = 1000
    previous.loc[previous.strike_price.isin([900, 1100]), "oi_contracts"] = 1000
    result = f.calculate_oi_migration(snapshot(current, previous, "s0"))
    assert result["status"] == "OK"
    assert result["call_shift"] == 0 and result["put_shift"] == 0


# Isolated IV-surface research tests. These do not alter legacy nearest-five tests.
def iv_research_rows():
    rows = []
    for side, points in (
        ("CALL", [(0.20, .42, 1100), (0.30, .40, 1050)]),
        ("PUT", [(-0.20, .46, 900), (-0.30, .50, 950)]),
    ):
        for delta, iv, strike in points:
            rows.append({
                "snapshot_id": "s1", "expiry_date": "2026-11-17",
                "symbol": f"{side}-{strike}", "product_id": f"{side}-{strike}",
                "option_type": side, "strike_price": strike, "spot_price": 1000.0,
                "mark_iv": iv, "bid_iv": 0.01, "ask_iv": 0.01,
                "bid_price": 10.0, "ask_price": 12.0,
                "greeks": {"delta": delta},
                "exchange_timestamp": "2026-11-16T12:29:00Z",
                "collected_at": "2026-11-16T12:29:01Z", "data_status": "OK",
            })
    # Same-strike ATM pair to test ATM IV independently from RR25 wings.
    for side, delta, iv in (("CALL", .51, .31), ("PUT", -.49, .33)):
        rows.append({
            "snapshot_id": "s1", "expiry_date": "2026-11-17",
            "symbol": f"{side}-1000", "product_id": f"{side}-1000",
            "option_type": side, "strike_price": 1000, "spot_price": 1000.0,
            "mark_iv": iv, "bid_iv": .30, "ask_iv": .32,
            "bid_price": 20.0, "ask_price": 22.0,
            "greeks": {"delta": delta},
            "exchange_timestamp": "2026-11-16T12:29:00Z",
            "collected_at": "2026-11-16T12:29:01Z", "data_status": "OK",
        })
    return pd.DataFrame(rows)


def test_rr25_interpolates_puts_using_absolute_negative_delta():
    rows = iv_research_rows().to_dict("records")
    call = f._interpolate_delta_iv(rows, "CALL")
    put = f._interpolate_delta_iv(rows, "PUT")
    assert call["status"] == put["status"] == "OK"
    assert call["method"] == put["method"] == "LINEAR_DELTA_INTERPOLATION"
    assert call["iv_25d"] == pytest.approx(.41)
    assert put["iv_25d"] == pytest.approx(.48)
    assert put["lower_delta"] == pytest.approx(.20)
    assert put["upper_delta"] == pytest.approx(.30)
    assert all(point["abs_delta"] > 0 for point in put["curve_points"])


def test_rr25_rejects_wrong_put_delta_sign_and_does_not_extrapolate():
    rows = iv_research_rows().to_dict("records")
    puts = [row for row in rows if row["option_type"] == "PUT"]
    for row in puts:
        row["greeks"] = {"delta": abs(row["greeks"]["delta"])}
    result = f._interpolate_delta_iv(puts, "PUT")
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "NO_QUALIFIED_CONTRACTS"
    assert all("DELTA_SIGN_MISMATCH" in item["rejection_reasons"]
               for item in result["rejected_contracts"])


def test_rr25_requires_bracket_and_never_extrapolates():
    rows = iv_research_rows().to_dict("records")
    calls = [row for row in rows if row["option_type"] == "CALL"]
    for row in calls:
        row["greeks"] = {"delta": .35}
    result = f._interpolate_delta_iv(calls, "CALL")
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "MISSING_25D_BRACKET"


def test_iv_sentinel_001_is_flagged_but_does_not_discard_valid_mark_iv():
    rows = iv_research_rows().to_dict("records")
    result = f._interpolate_delta_iv(rows, "PUT")
    assert result["status"] == "OK"
    quality = f._research_quote_diagnostics(rows[0])
    assert "BID_IV_SENTINEL_0_01" in quality["rejection_reasons"]
    assert "ASK_IV_SENTINEL_0_01" in quality["rejection_reasons"]
    assert f._research_iv(.01) is None
    assert f._research_iv(.0100001) == pytest.approx(.0100001)


def test_quote_quality_flags_crossed_market_and_reports_spread_without_cutoff():
    row = iv_research_rows().iloc[0].to_dict()
    row["bid_price"], row["ask_price"] = 12, 10
    result = f._research_quote_diagnostics(row)
    assert result["quality_status"] == "REVIEW"
    assert "CROSSED_MARKET" in result["rejection_reasons"]
    assert result["mid_price"] is None


def test_iv_surface_research_returns_atm_and_rr25_with_methodology_version():
    raw = iv_research_rows()
    result = f.calculate_iv_surface_research(snapshot(raw), raw)
    assert result["methodology_version"] == f.IV_RESEARCH_VERSION
    assert result["atm_strike"] == 1000
    assert result["atm_iv"] == pytest.approx(.32)
    assert result["rr25_status"] == "OK"
    assert result["rr25_call_minus_put_vol_points"] == pytest.approx(-7.0)
    assert result["quote_quality"]["contracts_review"] >= 4


def test_iv_research_loader_uses_read_only_select_query_and_pins_identity():
    class ReadOnlyQuery(Query):
        def __init__(self, db, table):
            super().__init__(db, table)
            self.selected = None
        def select(self, columns):
            self.selected = columns
            return self
    class ReadOnlyClient(Client):
        def table(self, name):
            return ReadOnlyQuery(self.db, name)
    raw = iv_research_rows()
    db = {"options_oi_snapshots": raw.to_dict("records")}
    result = f.load_iv_research_chain(ReadOnlyClient(db), snapshot())
    assert result["status"] == "OK"
    assert "greeks" in result["raw"].columns
    assert "bid_price" in result["raw"].columns
    assert set(result["raw"]["snapshot_id"]) == {"s1"}


def test_nearby_oi_threshold_uses_strict_btc_equivalent_of_legacy_contract_gate():
    raw = raw_rows()
    raw["oi_contracts"] = 0.1
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "oi_contracts"] = 0.5
    exact = f.calculate_nearby_oi(snapshot(raw))
    assert exact["status"] == "OK"
    assert not any(w["type"] == "CALL" and w["strike"] == 1000 for w in exact["walls"])
    raw.loc[(raw.strike_price == 1000) & (raw.option_type == "CALL"), "oi_contracts"] = 0.50001
    above = f.calculate_nearby_oi(snapshot(raw))
    assert {"strike": 1000.0, "type": "CALL", "oi_btc": 0.50001} in above["walls"]
    assert above["threshold_legacy_equivalent_contracts"] == 500


def test_current_v1_snapshot_is_rejected_and_historical_fixture_is_unchanged():
    import copy
    db = database()
    db["options_derived_snapshots"][0]["metric_version"] = "options-derived-v1.0"
    before = copy.deepcopy(db["options_derived_snapshots"])
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected is None
    assert meta["reason"] == "UNSUPPORTED_METRIC_VERSION"
    assert db["options_derived_snapshots"] == before


def test_previous_v1_snapshot_is_skipped_without_mixing_with_current_v2():
    db = database()
    _add_previous_snapshot(db, "legacy-v1", "2026-11-16T12:00:00Z")
    legacy = next(row for row in db["options_derived_snapshots"] if row["snapshot_id"] == "legacy-v1")
    legacy["metric_version"] = "options-derived-v1.0"
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected is not None and selected.metric_version == f.EXPECTED_METRIC_VERSION
    assert selected.previous_snapshot_id is None
    assert meta["previous_snapshot_reason"] == "NO_COMPATIBLE_V2_PREVIOUS_SNAPSHOT_IN_WINDOW"


def test_previous_v2_snapshot_requires_same_multiplier_and_version():
    db = database()
    _add_previous_snapshot(db, "bad-multiplier", "2026-11-16T12:00:00Z")
    previous = next(row for row in db["options_derived_snapshots"] if row["snapshot_id"] == "bad-multiplier")
    previous["gex_contract_multiplier"] = 0.001
    selected, _ = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.previous_snapshot_id is None


@pytest.mark.parametrize("field,value", [
    ("metric_version", None), ("metric_version", 2),
    ("gex_contract_multiplier", None), ("gex_contract_multiplier", "not-a-number"),
    ("gex_contract_multiplier", True), ("gex_contract_multiplier", float("nan")),
    ("gex_contract_multiplier", 0.001),
])
def test_current_snapshot_rejects_missing_or_invalid_metric_provenance(field, value):
    db = database()
    db["options_derived_snapshots"][0][field] = value
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected is None and meta["status"] == "UNAVAILABLE"
    assert meta["reason"] in {
        "METRIC_VERSION_MISSING_OR_INVALID", "UNSUPPORTED_METRIC_VERSION",
        "INVALID_GEX_CONTRACT_MULTIPLIER",
    }


def test_current_snapshot_missing_provenance_column_fails_closed():
    db = database()
    db["options_derived_snapshots"][0].pop("metric_version")
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected is None and meta["reason"] == "MISSING_REQUIRED_COLUMNS"
    assert "metric_version" in meta["missing_columns"]


def test_filter_results_include_source_metric_provenance():
    selected, meta = f.select_canonical_snapshot(Client(database()), as_of=date(2026, 11, 16))
    result = f.calculate_iv_skew(selected)
    assert meta["metric_version"] == f.EXPECTED_METRIC_VERSION
    assert meta["gex_contract_multiplier"] == 1.0
    assert result["source_metric_version"] == f.EXPECTED_METRIC_VERSION
    assert result["source_gex_contract_multiplier"] == 1.0


def test_volume_oi_reports_unavailable_when_source_unit_is_incompatible(monkeypatch):
    monkeypatch.setattr(f, "VOLUME_24H_UNIT", "CONTRACTS")
    result = f.calculate_volume_oi(snapshot())
    assert result["status"] == "UNAVAILABLE"
    assert result["reason"] == "VOLUME_OI_UNIT_MISMATCH"
    assert result["volume_source_unit"] == "CONTRACTS"
    assert result["oi_source_unit"] == "BTC"
    assert result["classification_available"] is False


def test_selector_accepts_valid_v2_current_and_previous_with_matching_provenance():
    db = database()
    _add_previous_snapshot(db, "valid-v2-previous", "2026-11-16T12:00:00Z")
    selected, meta = f.select_canonical_snapshot(Client(db), as_of=date(2026, 11, 16))
    assert selected.snapshot_id == "s1"
    assert selected.metric_version == f.EXPECTED_METRIC_VERSION
    assert selected.gex_contract_multiplier == 1.0
    assert selected.previous_snapshot_id == "valid-v2-previous"
    assert selected.previous_metric_version == f.EXPECTED_METRIC_VERSION
    assert selected.previous_gex_contract_multiplier == 1.0
    assert meta["metric_version"] == meta["previous_metric_version"] == f.EXPECTED_METRIC_VERSION
