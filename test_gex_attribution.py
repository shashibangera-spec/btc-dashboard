from gex_attribution import (
    _gex,
    _shapley,
    build_attribution_rows,
)


def _row(side, strike, oi, gamma):
    side_code = 1 if side == "CALL" else 2
    return {
        "product_id": int(strike) * 10 + side_code,
        "symbol": f"BTC-{int(strike)}-{side}",
        "option_type": side,
        "strike_price": strike,
        "oi_contracts": oi,
        "greeks": {"gamma": gamma},
    }


def _chain(call_values, put_values):
    return (
        [_row("CALL", strike, oi, gamma) for strike, oi, gamma in call_values]
        + [_row("PUT", strike, oi, gamma) for strike, oi, gamma in put_values]
    )


def _args(previous, current, previous_spot=100.0, current_spot=100.0):
    return dict(
        previous_rows=previous,
        current_rows=current,
        previous_spot=previous_spot,
        current_spot=current_spot,
        previous_multiplier=0.001,
        current_multiplier=0.001,
        previous_snapshot_id="00000000-0000-0000-0000-000000000001",
        current_snapshot_id="00000000-0000-0000-0000-000000000002",
        expiry_date="2026-12-25",
        previous_collected_at="2026-11-19T10:00:00+00:00",
        current_collected_at="2026-11-19T10:03:00+00:00",
    )


def test_shapley_components_reconcile_exactly_to_gex_delta():
    previous = {"gamma": 0.02, "oi": 1000.0, "spot": 100.0}
    current = {"gamma": 0.03, "oi": 1300.0, "spot": 105.0}
    contributions = _shapley(previous, current, 0.001)
    actual_change = (
        _gex(current["gamma"], current["oi"], current["spot"], 0.001)
        - _gex(previous["gamma"], previous["oi"], previous["spot"], 0.001)
    )
    assert abs(sum(contributions.values()) - actual_change) < 1e-9


def test_winner_switch_tracks_union_of_previous_and_current_top_three():
    previous = _chain(
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03), (120, 700, 0.03)],
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    current = _chain(
        [(100, 700, 0.03), (110, 900, 0.03), (90, 800, 0.03), (120, 3000, 0.03)],
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    rows = build_attribution_rows(**_args(previous, current))
    calls = [row for row in rows if row["option_type"] == "CALL"]
    assert len(calls) == 2
    assert {row["strike_price"] for row in calls} == {100, 120}
    assert all(row["wall_event_type"] == "WINNER_SWITCH" for row in calls)
    assert all(row["wall_margin_change"] is not None for row in calls)


def test_unchanged_wall_under_two_percent_is_not_persisted():
    previous = _chain(
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    current = _chain(
        [(100, 1005, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
        [(100, 1005, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    assert build_attribution_rows(**_args(previous, current)) == []


def test_unchanged_wall_over_two_percent_is_persisted():
    previous = _chain(
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    current = _chain(
        [(100, 1100, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
        [(100, 1100, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    rows = build_attribution_rows(**_args(previous, current))
    assert {row["wall_event_type"] for row in rows} == {"SIGNIFICANT_MAGNITUDE_CHANGE"}
    assert len(rows) == 2


def test_missing_gamma_is_not_silently_replaced_with_zero():
    previous = _chain(
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
        [(100, 1000, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    current = _chain(
        [(100, 1100, None), (110, 900, 0.03), (90, 800, 0.03)],
        [(100, 1100, 0.03), (110, 900, 0.03), (90, 800, 0.03)],
    )
    try:
        build_attribution_rows(**_args(previous, current))
    except ValueError as exc:
        assert "Invalid CALL" in str(exc)
    else:
        raise AssertionError("Missing Gamma must stop attribution.")


def test_new_exposure_is_logged_without_becoming_wall():
    previous = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03), (120, 10, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    current = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03), (120, 850, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    rows = build_attribution_rows(**_args(previous, current))
    new_rows = [r for r in rows if r["option_type"] == "CALL" and r["strike_price"] == 120]
    assert len(new_rows) == 1
    assert new_rows[0]["wall_event_type"] == "NEW_EXPOSURE"
    assert new_rows[0]["attribution_status"] == "NEW_EXPOSURE"


def test_previous_top3_missing_from_current_is_disappeared():
    previous = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    current = _chain(
        [(100, 1000, .03), (110, 900, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    rows = build_attribution_rows(**_args(previous, current))
    gone = [r for r in rows if r["option_type"] == "CALL" and r["strike_price"] == 90]
    assert len(gone) == 1
    assert gone[0]["wall_event_type"] == "DISAPPEARED"


def test_previous_top3_zero_gex_is_disappeared():
    previous = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    current = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 0, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    rows = build_attribution_rows(**_args(previous, current))
    gone = [r for r in rows if r["option_type"] == "CALL" and r["strike_price"] == 90]
    assert len(gone) == 1
    assert gone[0]["wall_event_type"] == "DISAPPEARED"


from unittest.mock import MagicMock, call
from gex_attribution import (
    _previous_complete_snapshot,
    persist_gex_attribution,
    run_attribution_safely,
)


class _Query:
    def __init__(self, responses):
        self.responses = responses
        self.filters = []
        self.table_name = None

    def select(self, *args, **kwargs): return self
    def eq(self, *args, **kwargs): self.filters.append(("eq", args)); return self
    def neq(self, *args, **kwargs): self.filters.append(("neq", args)); return self
    def order(self, *args, **kwargs): return self
    def limit(self, *args, **kwargs): return self
    def range(self, *args, **kwargs): return self
    def upsert(self, payload, **kwargs):
        self.payload = payload
        self.upsert_kwargs = kwargs
        return self
    def execute(self):
        return self.responses.pop(0)


class _MockSupabase:
    def __init__(self, responses):
        self.responses = list(responses)
        self.queries = []
    def table(self, name):
        q = _Query(self.responses)
        q.table_name = name
        self.queries.append(q)
        return q


def _response(data):
    return MagicMock(data=data)


def test_previous_snapshot_selection_skips_partial_and_incomplete_runs():
    partial_derived = {
        "snapshot_id": "partial", "expiry_date": "2026-12-25",
        "metric_status": {"chain": "INCOMPLETE", "call_gex": "OK", "put_gex": "OK"},
    }
    valid_derived = {
        "snapshot_id": "valid", "expiry_date": "2026-12-25",
        "source_collected_at": "2026-11-19T10:00:00+00:00",
        "underlying_spot_price": 100, "gex_contract_multiplier": .001,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "OK",
            "chain_expected_contracts": 2,
        },
    }
    rows = [_row("CALL", 100, 1000, .03), _row("PUT", 100, 1000, .03)]
    client = _MockSupabase([
        # Failed runs are excluded by the status=SUCCESS query; partial SUCCESS
        # runs remain candidates but are rejected by derived metric_status.
        _response([
            {"snapshot_id": "partial", "completed_at": "2"},
            {"snapshot_id": "valid", "completed_at": "1"},
        ]),
        _response([partial_derived]),
        _response([valid_derived]),
        _response(rows),
    ])
    derived, fetched_rows, reason = _previous_complete_snapshot(client, "2026-12-25", "current")
    assert derived["snapshot_id"] == "valid"
    assert fetched_rows == rows
    assert reason == "OK"
    run_query = client.queries[0]
    assert ("eq", ("status", "SUCCESS")) in run_query.filters


def test_persistence_upserts_comparison_id_and_event_payload():
    previous = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    current = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03), (120, 850, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    prev = {
        "snapshot_id": "00000000-0000-0000-0000-000000000001",
        "source_collected_at": "2026-11-19T10:00:00+00:00",
        "underlying_spot_price": 100, "gex_contract_multiplier": .001,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "OK",
            "chain_expected_contracts": 6,
        },
    }
    valid_rows = [
        _row("CALL", 100, 1000, .03), _row("CALL", 110, 900, .03), _row("CALL", 90, 800, .03),
        _row("CALL", 120, 850, .03),
        _row("PUT", 100, 1000, .03), _row("PUT", 110, 900, .03), _row("PUT", 90, 800, .03),
    ]
    # _previous_complete_snapshot consumes run, derived and raw-row responses.
    client = _MockSupabase([
        _response([{"snapshot_id": prev["snapshot_id"], "completed_at": "1"}]),
        _response([prev]),
        _response(previous),
        _response([{"id": 1}]),
    ])
    current_derived = {
        "snapshot_id": "00000000-0000-0000-0000-000000000002",
        "source_collected_at": "2026-11-19T10:03:00+00:00",
        "underlying_spot_price": 100, "gex_contract_multiplier": .001,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "OK",
            "chain_expected_contracts": 7,
        },
    }
    saved = persist_gex_attribution(
        client, current_derived["snapshot_id"], "2026-12-25", current, current_derived
    )
    assert saved == 1
    upsert_query = client.queries[-1]
    assert upsert_query.table_name == "gex_strike_attribution"
    assert upsert_query.upsert_kwargs["on_conflict"] == (
        "previous_snapshot_id,current_snapshot_id,expiry_date,option_type,strike_price"
    )
    assert upsert_query.payload[0]["comparison_id"]
    assert {row["wall_event_type"] for row in upsert_query.payload} == {"NEW_EXPOSURE"}


def test_attribution_database_error_is_non_blocking_and_logged():
    logger = MagicMock()
    client = MagicMock()
    # Force a write failure after snapshot lookup/build by making persistence raise.
    with __import__("unittest.mock").mock.patch(
        "gex_attribution.persist_gex_attribution", side_effect=RuntimeError("database unavailable")
    ):
        result = run_attribution_safely(
            client, "current", "2026-12-25", [], {}, logger=logger
        )
    assert result == 0
    logger.assert_called_once()
    assert "database unavailable" in logger.call_args.args[0]



def test_database_upsert_error_is_caught_by_non_blocking_wrapper():
    from unittest.mock import patch

    previous_rows = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    current_rows = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03), (120, 850, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    previous_derived = {
        "snapshot_id": "00000000-0000-0000-0000-000000000001",
        "source_collected_at": "2026-11-19T10:00:00+00:00",
        "underlying_spot_price": 100, "gex_contract_multiplier": .001,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "OK",
            "chain_expected_contracts": 6,
        },
    }
    current_derived = {
        "snapshot_id": "00000000-0000-0000-0000-000000000002",
        "source_collected_at": "2026-11-19T10:03:00+00:00",
        "underlying_spot_price": 100, "gex_contract_multiplier": .001,
        "metric_status": {
            "chain": "OK", "call_gex": "OK", "put_gex": "OK",
            "chain_expected_contracts": 7,
        },
    }
    client = MagicMock()
    query = client.table.return_value
    query.upsert.return_value.execute.side_effect = RuntimeError("database unavailable")
    logger = MagicMock()

    with patch("gex_attribution._previous_complete_snapshot",
               return_value=(previous_derived, previous_rows, "OK")):
        result = run_attribution_safely(
            client, current_derived["snapshot_id"], "2026-12-25",
            current_rows, current_derived, logger=logger,
        )

    assert result == 0
    client.table.assert_called_with("gex_strike_attribution")
    query.upsert.assert_called_once()
    logger.assert_called_once()
    assert "database unavailable" in logger.call_args.args[0]



def test_all_current_side_gex_zero_marks_previous_top3_disappeared():
    previous = _chain(
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    current = _chain(
        [(100, 0, .03), (110, 0, .03), (90, 0, .03)],
        [(100, 1000, .03), (110, 900, .03), (90, 800, .03)],
    )
    rows = build_attribution_rows(**_args(previous, current))
    disappeared = [
        r for r in rows
        if r["option_type"] == "CALL" and r["wall_event_type"] == "DISAPPEARED"
    ]
    assert {r["strike_price"] for r in disappeared} == {100, 110, 90}



def test_dominant_driver_uses_absolute_shapley_impact_for_negative_and_zero_values():
    from gex_attribution import _dominant_driver

    # A zero OI contribution must not outrank negative Gamma/Spot impacts.
    contributions = {
        "gamma": -12.61037874655949,
        "oi": 0.0,
        "spot": -23.52682250067346,
    }
    assert _dominant_driver(contributions) == "SPOT"

    # All-negative inputs still select the largest absolute impact.
    assert _dominant_driver({"gamma": -5.0, "oi": -2.0, "spot": -1.0}) == "GAMMA"

    # No measurable contribution remains explicitly unattributed.
    assert _dominant_driver({"gamma": 0.0, "oi": 0.0, "spot": 0.0}) == "UNATTRIBUTED"
