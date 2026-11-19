"""Event-focused attribution for per-expiry BTC options GEX walls.

This module stores only meaningful wall events. It deliberately does not store
full-chain or unchanged Top-3 observations.
"""
from __future__ import annotations

import itertools
import math
import uuid
from typing import Any, Dict, Iterable, List, Optional, Tuple

TOP_N = 3
GEX_CHANGE_THRESHOLD_PCT = 2.0
GEX_EPSILON = 1e-12
MAX_PREVIOUS_RUNS_TO_CHECK = 20
MAX_CHAIN_ROWS = 5000


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _gex(gamma: float, oi: float, spot: float, multiplier: float) -> float:
    return gamma * oi * multiplier * (spot ** 2) * 0.01


def _side_exposures(
    rows: Iterable[Dict[str, Any]],
    option_type: str,
    spot: float,
    multiplier: float,
) -> Dict[float, Dict[str, float]]:
    """Aggregate a side by strike without treating missing data as zero."""
    grouped: Dict[float, Dict[str, float]] = {}
    found = False
    for row in rows:
        if str(row.get("option_type", "")).upper() != option_type:
            continue
        found = True
        strike = _number(row.get("strike_price"))
        oi = _number(row.get("oi_contracts"))
        greeks = row.get("greeks")
        gamma = _number(greeks.get("gamma")) if isinstance(greeks, dict) else None
        if strike is None or strike <= 0 or oi is None or oi < 0 or gamma is None or gamma < 0:
            raise ValueError(f"Invalid {option_type} strike/OI/Gamma data; attribution skipped.")
        bucket = grouped.setdefault(strike, {"oi": 0.0, "gamma_oi": 0.0})
        bucket["oi"] += oi
        bucket["gamma_oi"] += gamma * oi

    if not found:
        raise ValueError(f"Missing {option_type} chain; attribution skipped.")

    result: Dict[float, Dict[str, float]] = {}
    for strike, bucket in grouped.items():
        total_oi = bucket["oi"]
        weighted_gamma = bucket["gamma_oi"] / total_oi if total_oi > 0 else 0.0
        exposure = _gex(weighted_gamma, total_oi, spot, multiplier)
        result[strike] = {
            "gamma": weighted_gamma,
            "oi": total_oi,
            "spot": spot,
            "multiplier": multiplier,
            "gex": exposure,
        }
    return result


def _top_n(exposures: Dict[float, Dict[str, float]], count: int = TOP_N):
    ranked = sorted(
        ((strike, values) for strike, values in exposures.items() if values["gex"] > GEX_EPSILON),
        key=lambda item: (-item[1]["gex"], item[0]),
    )[:count]
    return {
        strike: {**values, "rank": rank}
        for rank, (strike, values) in enumerate(ranked, start=1)
    }


def _shapley(
    previous: Dict[str, float],
    current: Dict[str, float],
    multiplier: float,
) -> Dict[str, float]:
    """Exact three-factor Shapley decomposition over all six replacement orders."""
    factors = ("gamma", "oi", "spot")
    totals = {factor: 0.0 for factor in factors}
    permutations = tuple(itertools.permutations(factors))

    for order in permutations:
        state = dict(previous)
        before = _gex(state["gamma"], state["oi"], state["spot"], multiplier)
        for factor in order:
            state[factor] = current[factor]
            after = _gex(state["gamma"], state["oi"], state["spot"], multiplier)
            totals[factor] += after - before
            before = after

    result = {factor: value / len(permutations) for factor, value in totals.items()}
    expected = (
        _gex(current["gamma"], current["oi"], current["spot"], multiplier)
        - _gex(previous["gamma"], previous["oi"], previous["spot"], multiplier)
    )
    residual = expected - sum(result.values())
    # Allocate only floating-point round-off to the final component.
    result["spot"] += residual
    return result


def _dominant_driver(contributions: Dict[str, float]) -> str:
    ordered = sorted(
        ((factor.upper(), abs(value)) for factor, value in contributions.items()),
        key=lambda item: item[1],
        reverse=True,
    )
    if not ordered or ordered[0][1] <= GEX_EPSILON:
        return "UNATTRIBUTED"
    if len(ordered) > 1 and ordered[1][1] >= ordered[0][1] * 0.80:
        return "MIXED"
    return ordered[0][0]


def _row_attribution(previous, current, multiplier):
    if previous is None or current is None:
        return None
    return _shapley(previous, current, multiplier)


def build_attribution_rows(
    previous_rows: List[Dict[str, Any]],
    current_rows: List[Dict[str, Any]],
    previous_spot: Any,
    current_spot: Any,
    previous_multiplier: Any,
    current_multiplier: Any,
    previous_snapshot_id: str,
    current_snapshot_id: str,
    expiry_date: str,
    previous_collected_at: str,
    current_collected_at: str,
    threshold_pct: float = GEX_CHANGE_THRESHOLD_PCT,
) -> List[Dict[str, Any]]:
    """Build event-only records for CALL and PUT wall changes.

    The comparison set is the union of the previous and current Top-3 strike
    sets. Full-chain maps are retained in memory only to calculate both sides
    of a winner-switch margin; no full-chain rows are persisted.
    """
    p_spot, c_spot = _number(previous_spot), _number(current_spot)
    p_mult, c_mult = _number(previous_multiplier), _number(current_multiplier)
    if None in (p_spot, c_spot, p_mult, c_mult) or min(p_spot, c_spot, p_mult, c_mult) <= 0:
        raise ValueError("Invalid spot or GEX multiplier.")
    if not math.isclose(p_mult, c_mult, rel_tol=0.0, abs_tol=1e-12):
        raise ValueError("GEX contract multiplier changed; attribution is not comparable.")

    comparison_id = str(uuid.uuid4())
    output: List[Dict[str, Any]] = []

    for side in ("CALL", "PUT"):
        previous_all = _side_exposures(previous_rows, side, p_spot, p_mult)
        current_all = _side_exposures(current_rows, side, c_spot, c_mult)
        previous_top = _top_n(previous_all)
        current_top = _top_n(current_all)
        if not previous_top or not current_top:
            continue

        # Union is essential: the old wall can fall out of today's Top-3.
        tracked_union = set(previous_top) | set(current_top)
        previous_wall = next(iter(previous_top))
        current_wall = next(iter(current_top))

        event_type = None
        event_strikes: List[float] = []
        margin_contributions = None
        margin_change = None
        wall_driver = "UNATTRIBUTED"

        if previous_wall != current_wall:
            event_type = "WINNER_SWITCH"
            event_strikes = [previous_wall, current_wall]

            old_p = previous_all.get(previous_wall)
            old_c = current_all.get(previous_wall)
            new_p = previous_all.get(current_wall)
            new_c = current_all.get(current_wall)

            if all(value is not None for value in (old_p, old_c, new_p, new_c)):
                old_components = _shapley(old_p, old_c, p_mult)
                new_components = _shapley(new_p, new_c, p_mult)
                margin_contributions = {
                    factor: new_components[factor] - old_components[factor]
                    for factor in ("gamma", "oi", "spot")
                }
                previous_margin = new_p["gex"] - old_p["gex"]
                current_margin = new_c["gex"] - old_c["gex"]
                margin_change = current_margin - previous_margin
                wall_driver = _dominant_driver(margin_contributions)
            else:
                wall_driver = "UNATTRIBUTED"
        else:
            previous_wall_values = previous_all.get(previous_wall)
            current_wall_values = current_all.get(current_wall)
            if previous_wall_values is None or current_wall_values is None:
                continue
            previous_gex = previous_wall_values["gex"]
            current_gex = current_wall_values["gex"]
            if abs(previous_gex) <= GEX_EPSILON:
                continue
            change_pct = abs(current_gex - previous_gex) / abs(previous_gex) * 100.0
            if change_pct < threshold_pct:
                continue
            event_type = "SIGNIFICANT_MAGNITUDE_CHANGE"
            event_strikes = [previous_wall]
            components = _shapley(previous_wall_values, current_wall_values, p_mult)
            margin_contributions = components
            margin_change = current_gex - previous_gex
            wall_driver = _dominant_driver(components)

        # All persisted strike rows are drawn from the previous/current Top-3 union.
        event_strikes = [strike for strike in event_strikes if strike in tracked_union]
        for strike in event_strikes:
            p_values = previous_all.get(strike)
            c_values = current_all.get(strike)
            p_rank = previous_top.get(strike, {}).get("rank")
            c_rank = current_top.get(strike, {}).get("rank")
            components = _row_attribution(p_values, c_values, p_mult)
            previous_gex = p_values["gex"] if p_values else None
            current_gex = c_values["gex"] if c_values else None
            change = (
                current_gex - previous_gex
                if previous_gex is not None and current_gex is not None else None
            )
            change_pct = (
                abs(change) / abs(previous_gex) * 100.0
                if change is not None and abs(previous_gex) > GEX_EPSILON else None
            )

            if components is not None:
                attribution_status = "OK"
                individual_driver = _dominant_driver(components)
            elif p_values is None and c_values is not None:
                attribution_status = "NEW_EXPOSURE"
                individual_driver = "UNATTRIBUTED"
            elif p_values is not None and c_values is None:
                attribution_status = "DISAPPEARED"
                individual_driver = "UNATTRIBUTED"
            else:
                attribution_status = "INSUFFICIENT_DATA"
                individual_driver = "UNATTRIBUTED"

            output.append({
                "comparison_id": comparison_id,
                "previous_snapshot_id": previous_snapshot_id,
                "current_snapshot_id": current_snapshot_id,
                "expiry_date": expiry_date,
                "option_type": side,
                "strike_price": strike,
                "wall_event_type": event_type,
                "previous_wall_strike": previous_wall,
                "current_wall_strike": current_wall,
                "previous_top3_rank": p_rank,
                "current_top3_rank": c_rank,
                "previous_collected_at": previous_collected_at,
                "current_collected_at": current_collected_at,
                "gamma_previous": p_values["gamma"] if p_values else None,
                "gamma_current": c_values["gamma"] if c_values else None,
                "oi_previous": p_values["oi"] if p_values else None,
                "oi_current": c_values["oi"] if c_values else None,
                "spot_previous": p_spot,
                "spot_current": c_spot,
                "contract_multiplier": p_mult,
                "gex_previous": previous_gex,
                "gex_current": current_gex,
                "gex_change": change,
                "gex_change_pct": change_pct,
                "gamma_contribution": components["gamma"] if components else None,
                "oi_contribution": components["oi"] if components else None,
                "spot_contribution": components["spot"] if components else None,
                "wall_margin_change": margin_change,
                "margin_gamma_contribution": (
                    margin_contributions["gamma"] if margin_contributions else None
                ),
                "margin_oi_contribution": (
                    margin_contributions["oi"] if margin_contributions else None
                ),
                "margin_spot_contribution": (
                    margin_contributions["spot"] if margin_contributions else None
                ),
                "dominant_driver": wall_driver if event_type == "WINNER_SWITCH" else individual_driver,
                "attribution_status": attribution_status,
                "threshold_pct": threshold_pct,
            })

    return output


def _valid_derived(derived: Dict[str, Any]) -> bool:
    status = derived.get("metric_status") or {}
    return (
        status.get("chain") == "OK"
        and status.get("call_gex") == "OK"
        and status.get("put_gex") == "OK"
        and _number(derived.get("underlying_spot_price")) is not None
        and _number(derived.get("gex_contract_multiplier")) is not None
    )


def _previous_complete_snapshot(client, expiry_date: str, current_snapshot_id: str):
    runs = (
        client.table("options_collection_runs")
        .select("snapshot_id,completed_at")
        .eq("underlying", "BTC")
        .eq("status", "SUCCESS")
        .neq("snapshot_id", current_snapshot_id)
        .order("completed_at", desc=True)
        .limit(MAX_PREVIOUS_RUNS_TO_CHECK)
        .execute()
    )
    for run in runs.data or []:
        snapshot_id = run.get("snapshot_id")
        if not snapshot_id:
            continue
        derived_response = (
            client.table("options_derived_snapshots")
            .select(
                "snapshot_id,expiry_date,source_collected_at,underlying_spot_price,"
                "gex_contract_multiplier,metric_status"
            )
            .eq("snapshot_id", snapshot_id)
            .eq("expiry_date", expiry_date)
            .limit(1)
            .execute()
        )
        derived_rows = derived_response.data or []
        if not derived_rows or not _valid_derived(derived_rows[0]):
            continue
        raw_response = (
            client.table("options_oi_snapshots")
            .select("snapshot_id,expiry_date,option_type,strike_price,oi_contracts,greeks")
            .eq("snapshot_id", snapshot_id)
            .eq("expiry_date", expiry_date)
            .order("strike_price")
            .range(0, MAX_CHAIN_ROWS - 1)
            .execute()
        )
        raw_rows = raw_response.data or []
        if raw_rows:
            return derived_rows[0], raw_rows
    return None, None


def persist_gex_attribution(
    client,
    current_snapshot_id: str,
    expiry_date: str,
    current_rows: List[Dict[str, Any]],
    current_derived: Dict[str, Any],
) -> int:
    """Compare with the prior successful same-expiry snapshot and upsert events."""
    if not _valid_derived(current_derived):
        print(f"GEX attribution skipped for {expiry_date}: current derived metrics incomplete.")
        return 0

    previous_derived, previous_rows = _previous_complete_snapshot(
        client, expiry_date, current_snapshot_id
    )
    if not previous_derived or not previous_rows:
        print(f"GEX attribution skipped for {expiry_date}: no prior complete snapshot.")
        return 0

    current_collected_at = current_derived.get("source_collected_at")
    previous_collected_at = previous_derived.get("source_collected_at")
    if not current_collected_at or not previous_collected_at:
        print(f"GEX attribution skipped for {expiry_date}: missing source timestamps.")
        return 0

    records = build_attribution_rows(
        previous_rows=previous_rows,
        current_rows=current_rows,
        previous_spot=previous_derived.get("underlying_spot_price"),
        current_spot=current_derived.get("underlying_spot_price"),
        previous_multiplier=previous_derived.get("gex_contract_multiplier"),
        current_multiplier=current_derived.get("gex_contract_multiplier"),
        previous_snapshot_id=previous_derived["snapshot_id"],
        current_snapshot_id=current_snapshot_id,
        expiry_date=expiry_date,
        previous_collected_at=previous_collected_at,
        current_collected_at=current_collected_at,
    )
    if not records:
        print(f"GEX attribution: no qualifying wall event for {expiry_date}.")
        return 0

    response = (
        client.table("gex_strike_attribution")
        .upsert(
            records,
            on_conflict=(
                "previous_snapshot_id,current_snapshot_id,expiry_date,option_type,strike_price"
            ),
        )
        .execute()
    )
    saved = len(response.data or [])
    print(f"GEX attribution: saved {saved} event rows for {expiry_date}.")
    return saved
