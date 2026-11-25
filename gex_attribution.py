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
GEX_METRIC_VERSION = "options-derived-v2.0-btc-oi"
GEX_OI_SCALE = 1.0
GEX_CHANGE_THRESHOLD_PCT = 2.0
GEX_EPSILON = 1e-12
MAX_PREVIOUS_RUNS_TO_CHECK = 20
MAX_CHAIN_ROWS = 5000
HISTORICAL_PAGE_SIZE = 1000


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
    new_exposure_min_pct: float = 2.0,
) -> List[Dict[str, Any]]:
    """Emit event rows from the previous/current Top-3 union.

    NEW_EXPOSURE is measured against 2% of the current #1 wall GEX.
    DISAPPEARED means a previous Top-3 strike is absent or has zero GEX.
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
        if not previous_top:
            continue

        tracked_union = set(previous_top) | set(current_top)
        previous_wall = next(iter(previous_top))
        current_wall = next(iter(current_top)) if current_top else None
        current_wall_gex = current_top[current_wall]["gex"] if current_wall is not None else 0.0
        new_exposure_floor = current_wall_gex * (new_exposure_min_pct / 100.0)

        # Event map ensures a strike is persisted once per comparison/side.
        events: Dict[float, Dict[str, Any]] = {}

        # If every current strike has zero GEX, all previous Top-3 walls have
        # disappeared. Do not skip this case merely because current Top-3 is empty.
        if current_wall is None:
            for strike in previous_top:
                events[strike] = {
                    "event": "DISAPPEARED", "margin_contributions": None,
                    "margin_change": None, "driver": "UNATTRIBUTED",
                }

        # Wall events retain precedence for the two wall strikes.
        elif previous_wall != current_wall:
            old_p, old_c = previous_all.get(previous_wall), current_all.get(previous_wall)
            new_p, new_c = previous_all.get(current_wall), current_all.get(current_wall)
            margin_contributions = None
            margin_change = None
            driver = "UNATTRIBUTED"
            if all(value is not None for value in (old_p, old_c, new_p, new_c)):
                old_components = _shapley(old_p, old_c, p_mult)
                new_components = _shapley(new_p, new_c, p_mult)
                margin_contributions = {
                    factor: new_components[factor] - old_components[factor]
                    for factor in ("gamma", "oi", "spot")
                }
                margin_change = (new_c["gex"] - old_c["gex"]) - (new_p["gex"] - old_p["gex"])
                driver = _dominant_driver(margin_contributions)
            for strike in (previous_wall, current_wall):
                events[strike] = {
                    "event": "WINNER_SWITCH",
                    "margin_contributions": margin_contributions,
                    "margin_change": margin_change,
                    "driver": driver,
                }
        elif current_wall is not None:
            p_wall, c_wall = previous_all.get(previous_wall), current_all.get(current_wall)
            if p_wall and c_wall and abs(p_wall["gex"]) > GEX_EPSILON:
                pct = abs(c_wall["gex"] - p_wall["gex"]) / abs(p_wall["gex"]) * 100.0
                if pct >= threshold_pct:
                    components = _shapley(p_wall, c_wall, p_mult)
                    events[previous_wall] = {
                        "event": "SIGNIFICANT_MAGNITUDE_CHANGE",
                        "margin_contributions": components,
                        "margin_change": c_wall["gex"] - p_wall["gex"],
                        "driver": _dominant_driver(components),
                    }

        # Any newly Top-3 strike above the materiality floor is an event,
        # even when it does not become the #1 wall.
        for strike, values in current_top.items():
            if current_wall is not None and strike not in previous_top and values["gex"] > new_exposure_floor and strike not in events:
                events[strike] = {
                    "event": "NEW_EXPOSURE", "margin_contributions": None,
                    "margin_change": None, "driver": "UNATTRIBUTED",
                }

        # Previous Top-3 exposure that vanishes or falls to zero is retained.
        for strike in previous_top:
            current_values = current_all.get(strike)
            if (current_values is None or current_values["gex"] <= GEX_EPSILON) and strike not in events:
                events[strike] = {
                    "event": "DISAPPEARED", "margin_contributions": None,
                    "margin_change": None, "driver": "UNATTRIBUTED",
                }

        for strike, event in events.items():
            p_values, c_values = previous_all.get(strike), current_all.get(strike)
            components = _row_attribution(p_values, c_values, p_mult)
            p_gex = p_values["gex"] if p_values else None
            c_gex = c_values["gex"] if c_values else None
            change = c_gex - p_gex if p_gex is not None and c_gex is not None else None
            change_pct = (
                abs(change) / abs(p_gex) * 100.0
                if change is not None and abs(p_gex) > GEX_EPSILON else None
            )
            if event["event"] == "NEW_EXPOSURE":
                status = "NEW_EXPOSURE"
            elif event["event"] == "DISAPPEARED":
                status = "DISAPPEARED"
            elif components is not None:
                status = "OK"
            else:
                status = "INSUFFICIENT_DATA"

            contributions = components
            output.append({
                "comparison_id": comparison_id,
                "previous_snapshot_id": previous_snapshot_id,
                "current_snapshot_id": current_snapshot_id,
                "expiry_date": expiry_date,
                "option_type": side,
                "strike_price": strike,
                "wall_event_type": event["event"],
                "previous_wall_strike": previous_wall,
                "current_wall_strike": current_wall,
                "previous_top3_rank": previous_top.get(strike, {}).get("rank"),
                "current_top3_rank": current_top.get(strike, {}).get("rank"),
                "previous_collected_at": previous_collected_at,
                "current_collected_at": current_collected_at,
                "gamma_previous": p_values["gamma"] if p_values else None,
                "gamma_current": c_values["gamma"] if c_values else None,
                "oi_previous": p_values["oi"] if p_values else None,
                "oi_current": c_values["oi"] if c_values else None,
                "spot_previous": p_spot,
                "spot_current": c_spot,
                "contract_multiplier": p_mult,
                "gex_previous": p_gex,
                "gex_current": c_gex,
                "gex_change": change,
                "gex_change_pct": change_pct,
                "gamma_contribution": contributions["gamma"] if contributions else None,
                "oi_contribution": contributions["oi"] if contributions else None,
                "spot_contribution": contributions["spot"] if contributions else None,
                "wall_margin_change": event["margin_change"],
                "margin_gamma_contribution": event["margin_contributions"]["gamma"] if event["margin_contributions"] else None,
                "margin_oi_contribution": event["margin_contributions"]["oi"] if event["margin_contributions"] else None,
                "margin_spot_contribution": event["margin_contributions"]["spot"] if event["margin_contributions"] else None,
                "dominant_driver": event["driver"] if event["event"] == "WINNER_SWITCH" else (
                    _dominant_driver(components) if components else event["driver"]
                ),
                "attribution_status": status,
                "threshold_pct": threshold_pct,
            })

    return output


def run_attribution_safely(client, current_snapshot_id, expiry_date, current_rows, current_derived, logger=None):
    """Non-blocking wrapper used by the production collector."""
    try:
        return persist_gex_attribution(
            client, current_snapshot_id, expiry_date, current_rows, current_derived
        )
    except Exception as exc:
        message = f"GEX attribution skipped for {expiry_date}: {exc}"
        if logger is not None:
            logger(message)
        else:
            print(message)
        return 0

def _valid_derived(derived: Dict[str, Any]) -> bool:
    status = derived.get("metric_status") or {}
    multiplier = _number(derived.get("gex_contract_multiplier"))
    return (
        derived.get("metric_version") == GEX_METRIC_VERSION
        and status.get("chain") == "OK"
        and status.get("call_gex") == "OK"
        and status.get("put_gex") == "OK"
        and _number(derived.get("underlying_spot_price")) is not None
        and multiplier is not None
        and math.isclose(multiplier, GEX_OI_SCALE, rel_tol=0.0, abs_tol=1e-12)
    )


def _rows_match_chain_evidence(rows: List[Dict[str, Any]], metric_status: Dict[str, Any]):
    expected = metric_status.get("chain_expected_contracts")
    if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
        return False, "MISSING_EXPECTED_CONTRACT_COUNT"
    if expected > MAX_CHAIN_ROWS:
        return False, "HISTORICAL_CHAIN_EXCEEDS_RETRIEVAL_LIMIT"
    if len(rows) != expected:
        return False, "HISTORICAL_ROW_COUNT_MISMATCH"
    product_ids = [row.get("product_id") for row in rows]
    symbols = [row.get("symbol") for row in rows]
    if any(value is None for value in product_ids) or any(not value for value in symbols):
        return False, "HISTORICAL_CONTRACT_IDENTITY_MISSING"
    if len(set(str(value) for value in product_ids)) != expected:
        return False, "DUPLICATE_HISTORICAL_PRODUCT_ID"
    if len(set(str(value) for value in symbols)) != expected:
        return False, "DUPLICATE_HISTORICAL_SYMBOL"
    return True, "OK"


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
    last_reason = "NO_PRIOR_SUCCESSFUL_RUN"
    for run in runs.data or []:
        snapshot_id = run.get("snapshot_id")
        if not snapshot_id:
            continue
        derived_response = (
            client.table("options_derived_snapshots")
            .select(
                "snapshot_id,expiry_date,source_collected_at,underlying_spot_price,"
                "gex_contract_multiplier,metric_version,metric_status"
            )
            .eq("snapshot_id", snapshot_id)
            .eq("expiry_date", expiry_date)
            .limit(1)
            .execute()
        )
        derived_rows = derived_response.data or []
        if not derived_rows or not _valid_derived(derived_rows[0]):
            last_reason = "PREVIOUS_DERIVED_METRICS_INCOMPLETE"
            continue
        metric_status = derived_rows[0].get("metric_status") or {}
        expected = metric_status.get("chain_expected_contracts")
        if isinstance(expected, bool) or not isinstance(expected, int) or expected <= 0:
            last_reason = "MISSING_EXPECTED_CONTRACT_COUNT"
            continue
        if expected > MAX_CHAIN_ROWS:
            last_reason = "HISTORICAL_CHAIN_EXCEEDS_RETRIEVAL_LIMIT"
            continue
        raw_rows = []
        for start in range(0, expected, HISTORICAL_PAGE_SIZE):
            end = min(start + HISTORICAL_PAGE_SIZE, expected) - 1
            raw_response = (
                client.table("options_oi_snapshots")
                .select(
                    "snapshot_id,expiry_date,option_type,strike_price,product_id,symbol,oi_contracts,greeks"
                )
                .eq("snapshot_id", snapshot_id)
                .eq("expiry_date", expiry_date)
                .order("strike_price").order("product_id")
                .range(start, end)
                .execute()
            )
            page = raw_response.data or []
            if len(page) != end - start + 1:
                last_reason = "HISTORICAL_RETRIEVAL_INCOMPLETE"
                raw_rows = []
                break
            raw_rows.extend(page)
        if not raw_rows:
            continue
        complete, reason = _rows_match_chain_evidence(raw_rows, metric_status)
        if complete:
            return derived_rows[0], raw_rows, "OK"
        last_reason = reason
    return None, None, last_reason


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

    current_status = current_derived.get("metric_status") or {}
    current_complete, current_reason = _rows_match_chain_evidence(current_rows, current_status)
    if not current_complete:
        print(f"GEX attribution skipped for {expiry_date}: current chain invalid: {current_reason}.")
        return 0

    previous_derived, previous_rows, previous_reason = _previous_complete_snapshot(
        client, expiry_date, current_snapshot_id
    )
    if not previous_derived or not previous_rows:
        print(f"GEX attribution skipped for {expiry_date}: {previous_reason}.")
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
