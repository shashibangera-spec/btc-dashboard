import math

from iv_surface_research import calculate_iv_surface_research


def row(kind, strike, delta, iv, bid=0.01, ask=0.02):
    return {"option_type": kind, "strike_price": strike, "mark_iv": iv,
            "bid_price": bid, "ask_price": ask, "greeks": {"delta": delta}}


def test_paired_atm_uses_nearest_strike_with_both_sides():
    snap = {"spot_price": 100, "contracts": [
        row("CALL", 100, .51, .40), row("PUT", 100, -.49, .42),
        row("CALL", 101, .45, .39),
    ]}
    out = calculate_iv_surface_research(snap)
    assert out["atm_strike"] == 100
    assert out["atm_iv"] == .41


def test_rr25_linear_interpolation_call_minus_put_in_vol_points():
    snap = {"spot_price": 100, "contracts": [
        row("CALL", 90, .20, .30), row("CALL", 91, .30, .40),
        row("PUT", 110, -.20, .50), row("PUT", 111, -.30, .60),
    ]}
    out = calculate_iv_surface_research(snap)
    assert out["status"] == "OK"
    assert math.isclose(out["call_25d_iv"], .35)
    assert math.isclose(out["put_25d_iv"], .55)
    assert math.isclose(out["rr25_call_minus_put_vol_points"], -20.0)


def test_no_extrapolation_when_25_delta_not_bracketed():
    snap = {"spot_price": 100, "contracts": [
        row("CALL", 90, .30, .4), row("PUT", 110, -.30, .5),
    ]}
    out = calculate_iv_surface_research(snap)
    assert out["status"] == "UNAVAILABLE"
    assert out["rr25_call_minus_put_vol_points"] is None
    assert "MISSING_25D_BRACKET" in out["reason"]


def test_wrong_delta_sign_is_rejected():
    out = calculate_iv_surface_research({"spot_price": 100, "contracts": [
        row("CALL", 100, -.25, .4), row("PUT", 100, .25, .5),
    ]})
    assert out["eligible_contracts"] == 0
    assert out["invalid_delta"] == 2


def test_crossed_quote_and_one_percent_iv_sentinel_rejected():
    out = calculate_iv_surface_research({"spot_price": 100, "contracts": [
        row("CALL", 100, .25, .4, bid=2, ask=1),
        row("PUT", 100, -.25, .01),
    ]})
    assert out["eligible_contracts"] == 0
    assert out["crossed_or_invalid_price_quotes"] == 1
    assert out["invalid_or_sentinel_mark_iv"] == 1


def test_missing_or_nonfinite_spot_never_builds_atm_candidate():
    for spot in (None, float("nan"), float("inf"), "not-a-price"):
        out = calculate_iv_surface_research({"spot_price": spot, "contracts": [
            row("CALL", 100, .50, .4), row("PUT", 100, -.50, .5),
        ]})
        assert out["spot_price"] is None
        assert out["atm_strike"] is None
        assert out["atm_iv"] is None


def test_spot_price_guard_is_applied_before_atm_distance():
    out = calculate_iv_surface_research({"spot_price": None, "contracts": [
        row("CALL", 100, .50, .4), row("PUT", 100, -.50, .5),
    ]})
    assert out["atm_strike"] is None
