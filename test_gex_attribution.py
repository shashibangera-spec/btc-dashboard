from gex_attribution import (
    _gex,
    _shapley,
    build_attribution_rows,
)


def _row(side, strike, oi, gamma):
    return {
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
