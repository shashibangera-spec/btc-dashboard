"""Research-only, in-memory IV surface calculations.

Version: iv-surface-research-v1.0
No production dashboard or collector imports are required.
"""
from __future__ import annotations

import math
from typing import Any, Mapping, Sequence

VERSION = "iv-surface-research-v1.0"
TARGET_ABS_DELTA = 0.25


def _finite(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _valid_two_sided_price(row: Mapping[str, Any]) -> bool:
    bid = _finite(row.get("bid_price"))
    ask = _finite(row.get("ask_price"))
    return bid is not None and ask is not None and bid > 0 and ask > 0 and bid <= ask


def _valid_mark_iv(row: Mapping[str, Any]) -> float | None:
    value = _finite(row.get("mark_iv"))
    if value is None or value <= 0 or math.isclose(value, 0.01, abs_tol=1e-12):
        return None
    return value


def _delta(row: Mapping[str, Any]) -> float | None:
    greeks = row.get("greeks")
    if isinstance(greeks, str):
        import json
        try:
            greeks = json.loads(greeks)
        except (TypeError, ValueError):
            return None
    if not isinstance(greeks, Mapping):
        return None
    value = _finite(greeks.get("delta"))
    if value is None or abs(value) > 1 or value == 0:
        return None
    return value


def _interpolate(points: Sequence[tuple[float, float]], target: float) -> dict[str, Any]:
    ordered = sorted(points, key=lambda item: item[0])
    lower = next((p for p in reversed(ordered) if p[0] <= target), None)
    upper = next((p for p in ordered if p[0] >= target), None)
    if lower is None or upper is None:
        return {"status": "UNAVAILABLE", "reason": "MISSING_25D_BRACKET"}
    if lower[0] == upper[0]:
        return {"status": "OK", "iv": lower[1], "lower_delta": lower[0],
                "upper_delta": upper[0], "lower_iv": lower[1], "upper_iv": upper[1]}
    weight = (target - lower[0]) / (upper[0] - lower[0])
    iv = lower[1] + weight * (upper[1] - lower[1])
    return {"status": "OK", "iv": iv, "lower_delta": lower[0],
            "upper_delta": upper[0], "lower_iv": lower[1], "upper_iv": upper[1]}


def calculate_iv_surface_research(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """Calculate paired ATM IV and interpolated 25-delta risk reversal.

    Input is a mapping with spot_price and contracts (option rows). A contract is
    eligible only with valid mark IV and a non-crossed, positive two-sided price.
    Calls require positive delta; puts require negative delta. 25-delta values
    are linearly interpolated in absolute-delta space and never extrapolated.
    """
    spot = _finite(snapshot.get("spot_price"))
    contracts = snapshot.get("contracts") or []
    result: dict[str, Any] = {
        "calculation_version": VERSION,
        "status": "UNAVAILABLE",
        "reason": None,
        "spot_price": spot,
        "atm_strike": None,
        "atm_call_iv": None,
        "atm_put_iv": None,
        "atm_iv": None,
        "call_25d_iv": None,
        "put_25d_iv": None,
        "rr25_call_minus_put_vol_points": None,
        "call_25d_interpolation": None,
        "put_25d_interpolation": None,
        "contracts_reviewed": len(contracts),
        "eligible_contracts": 0,
        "crossed_or_invalid_price_quotes": 0,
        "invalid_or_sentinel_mark_iv": 0,
        "invalid_delta": 0,
    }
    # Explicit guard: without a finite spot, no ATM candidate is constructed.
    atm_candidates: dict[float, dict[str, float]] = {}
    call_points: list[tuple[float, float]] = []
    put_points: list[tuple[float, float]] = []
    for row in contracts:
        if not isinstance(row, Mapping):
            continue
        option_type = str(row.get("option_type") or "").upper()
        strike = _finite(row.get("strike_price"))
        iv = _valid_mark_iv(row)
        if not _valid_two_sided_price(row):
            result["crossed_or_invalid_price_quotes"] += 1
            continue
        if iv is None:
            result["invalid_or_sentinel_mark_iv"] += 1
            continue
        delta = _delta(row)
        if delta is None:
            result["invalid_delta"] += 1
            continue
        if option_type == "CALL" and delta <= 0:
            result["invalid_delta"] += 1
            continue
        if option_type == "PUT" and delta >= 0:
            result["invalid_delta"] += 1
            continue
        result["eligible_contracts"] += 1
        if option_type == "CALL":
            call_points.append((abs(delta), iv))
        elif option_type == "PUT":
            put_points.append((abs(delta), iv))
        if spot is not None and strike is not None and option_type in ("CALL", "PUT"):
            sides = atm_candidates.setdefault(strike, {})
            sides[option_type] = iv

    if spot is not None:
        paired = [(strike, sides) for strike, sides in atm_candidates.items()
                  if "CALL" in sides and "PUT" in sides]
        if paired:
            strike, sides = min(paired, key=lambda item: (abs(item[0] - spot), item[0]))
            result.update(atm_strike=strike, atm_call_iv=sides["CALL"],
                          atm_put_iv=sides["PUT"],
                          atm_iv=(sides["CALL"] + sides["PUT"]) / 2.0)

    call_interp = _interpolate(call_points, TARGET_ABS_DELTA)
    put_interp = _interpolate(put_points, TARGET_ABS_DELTA)
    result["call_25d_interpolation"] = call_interp
    result["put_25d_interpolation"] = put_interp
    if call_interp["status"] == "OK":
        result["call_25d_iv"] = call_interp["iv"]
    if put_interp["status"] == "OK":
        result["put_25d_iv"] = put_interp["iv"]
    if call_interp["status"] == "OK" and put_interp["status"] == "OK":
        result["rr25_call_minus_put_vol_points"] = (
            call_interp["iv"] - put_interp["iv"]
        ) * 100.0
        result["status"] = "OK"
    else:
        reasons = [x["reason"] for x in (call_interp, put_interp)
                   if x["status"] != "OK"]
        result["reason"] = ";".join(reasons)
    return result
