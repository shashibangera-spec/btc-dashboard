"""Backend-only options research filters with snapshot-pinned data contracts.

No Streamlit imports, UI behavior, writes, or persistence. The caller supplies a
Supabase-compatible read client. All filter results retain source provenance.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timezone
import math
import logging
from typing import Any, List, Optional, Tuple

import pandas as pd

logger = logging.getLogger(__name__)

RAW_COLUMNS = (
    "snapshot_id", "expiry_date", "symbol", "product_id", "option_type",
    "strike_price", "oi_contracts", "oi_value_usd", "volume_24h",
    "turnover_usd", "mark_iv", "taker_buy_volume", "taker_sell_volume",
    "spot_price", "collected_at", "exchange_timestamp", "data_status",
)
RUN_COLUMNS = (
    "snapshot_id", "status", "started_at", "completed_at", "underlying",
    "expected_contracts", "received_contracts", "inserted_contracts",
)
DERIVED_COLUMNS = (
    "snapshot_id", "expiry_date", "calculated_at", "source_collected_at",
    "underlying_spot_price", "metric_status",
)


@dataclass(frozen=True)
class CanonicalSnapshot:
    snapshot_id: str
    expiry_date: str
    completed_at: str
    spot_price: float
    raw: pd.DataFrame
    previous_snapshot_id: Optional[str] = None
    previous_completed_at: Optional[str] = None
    previous_raw: Optional[pd.DataFrame] = None
    previous_snapshot_reason: Optional[str] = None


def _rows(response: Any) -> List[dict]:
    data = getattr(response, "data", None)
    if data is None and isinstance(response, dict):
        data = response.get("data")
    return list(data or [])


def _missing_record_keys(records: List[dict], required_columns: Tuple[str, ...]) -> dict:
    """Return exact missing required keys by column and zero-based record index."""
    row_indexes = {}
    for index, record in enumerate(records):
        missing = (
            list(required_columns) if not isinstance(record, dict)
            else [column for column in required_columns if column not in record]
        )
        for column in missing:
            row_indexes.setdefault(column, []).append(index)
    return {
        "missing_columns": sorted(row_indexes),
        "row_indexes": {key: row_indexes[key] for key in sorted(row_indexes)},
    }


def _missing_record_selection(source: str, details: dict) -> dict:
    logger.warning(
        "Options Filters database record validation failed: source=%s missing_columns=%s row_indexes=%s",
        source, details["missing_columns"], details["row_indexes"],
    )
    return {
        "status": "UNAVAILABLE",
        "reason": "MISSING_REQUIRED_COLUMNS",
        "source": source,
        **details,
    }


class MissingRequiredRecordKeys(ValueError):
    def __init__(self, source: str, details: dict):
        self.source = source
        self.details = details
        super().__init__(
            f"{source} missing required columns: {', '.join(details['missing_columns'])}"
        )


def _execute(query: Any) -> List[dict]:
    return _rows(query.execute())


def _finite(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def _iso_date(value: Any) -> Optional[date]:
    try:
        return date.fromisoformat(str(value)[:10])
    except (TypeError, ValueError):
        return None


def _contract_identity(row: dict) -> Tuple[str, str, str]:
    product = row.get("product_id")
    symbol = row.get("symbol")
    identity = str(product) if product not in (None, "") else str(symbol or "")
    return identity, str(row.get("option_type", "")).upper(), str(row.get("strike_price", ""))


def _query_raw(client: Any, snapshot_id: str, expiry: str) -> pd.DataFrame:
    query = (
        client.table("options_oi_snapshots")
        .select(",".join(RAW_COLUMNS))
        .eq("snapshot_id", snapshot_id)
        .eq("expiry_date", expiry)
    )
    collected: List[dict] = []
    start, page_size = 0, 500
    while True:
        page = _execute(query.range(start, start + page_size - 1))
        collected.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    missing = _missing_record_keys(collected, RAW_COLUMNS)
    if missing["missing_columns"]:
        raise MissingRequiredRecordKeys("options_oi_snapshots", missing)
    return pd.DataFrame(collected, columns=list(RAW_COLUMNS))


def validate_raw_snapshot(raw: pd.DataFrame, snapshot_id: str, expiry: str) -> dict:
    """Validate chain identity and provenance without coercing NULL to zero."""
    errors: List[str] = []
    if raw is None or raw.empty:
        return {"status": "UNAVAILABLE", "errors": ["EMPTY_RAW_CHAIN"]}
    if not set(RAW_COLUMNS).issubset(raw.columns):
        return {"status": "UNAVAILABLE", "errors": ["MISSING_RAW_COLUMNS"]}
    if set(raw["snapshot_id"].dropna().astype(str)) != {str(snapshot_id)}:
        errors.append("SNAPSHOT_ID_MISMATCH")
    if set(raw["expiry_date"].dropna().astype(str)) != {str(expiry)}:
        errors.append("EXPIRY_MISMATCH")
    types = set(raw["option_type"].dropna().astype(str).str.upper())
    if not {"CALL", "PUT"}.issubset(types):
        errors.append("MISSING_OPTION_SIDE")
    identities = [_contract_identity(row) for row in raw.to_dict("records")]
    if len(identities) != len(set(identities)):
        errors.append("DUPLICATE_CONTRACT_IDENTITY")
    strikes = pd.to_numeric(raw["strike_price"], errors="coerce")
    if strikes.isna().any() or (~strikes.map(math.isfinite)).any():
        errors.append("INVALID_STRIKE")
    spots = pd.to_numeric(raw["spot_price"], errors="coerce")
    valid_spots = [x for x in (_finite(v) for v in spots.tolist()) if x is not None and x > 0]
    if not valid_spots:
        errors.append("MISSING_SNAPSHOT_SPOT")
        spot = None
    else:
        spot = float(valid_spots[0])
        if any(not math.isclose(x, spot, rel_tol=0, abs_tol=1e-8) for x in valid_spots):
            errors.append("INCONSISTENT_SNAPSHOT_SPOT")
    return {
        "status": "OK" if not errors else "INVALID", "errors": errors,
        "spot_price": spot, "contract_count": len(raw),
        "strike_count": int(strikes.nunique()) if not strikes.isna().all() else 0,
        "null_counts": {field: int(raw[field].isna().sum()) for field in (
            "oi_contracts", "mark_iv", "volume_24h",
            "taker_buy_volume", "taker_sell_volume"
        )},
    }


def _run_is_complete(run: dict) -> bool:
    if str(run.get("status", "")).upper() != "SUCCESS":
        return False
    counters = [run.get(k) for k in (
        "expected_contracts", "received_contracts", "inserted_contracts"
    )]
    supplied = [x for x in counters if x is not None]
    return not supplied or (
        len(supplied) == 3 and int(counters[0]) == int(counters[1]) == int(counters[2])
    )


def select_canonical_snapshot(client: Any, *, as_of: Optional[date] = None):
    """Pin the latest SUCCESS snapshot and select a bounded same-expiry baseline.

    The current snapshot remains fail-closed. Previous snapshots are searched
    newest-first within the inclusive 25-45 minute comparison window.
    """
    today = as_of or datetime.now(timezone.utc).date()
    runs = _execute(
        client.table("options_collection_runs")
        .select(",".join(RUN_COLUMNS)).eq("status", "SUCCESS")
        .order("completed_at", desc=True).limit(1)
    )
    if not runs:
        return None, {"status": "UNAVAILABLE", "reason": "NO_SUCCESS_RUN"}
    missing = _missing_record_keys(runs, tuple(column for column in RUN_COLUMNS if column != "underlying"))
    if missing["missing_columns"]:
        return None, _missing_record_selection("options_collection_runs", missing)
    current = runs[0]
    if not _run_is_complete(current):
        return None, {"status": "UNAVAILABLE", "reason": "RUN_COUNTER_MISMATCH"}
    snapshot_id = str(current.get("snapshot_id") or "")
    completed_at = current.get("completed_at")
    underlying = str(current.get("underlying") or "BTCUSD")
    if not snapshot_id or not completed_at or not underlying:
        return None, {"status": "UNAVAILABLE", "reason": "INVALID_RUN_IDENTITY"}
    history = _execute(
        client.table("options_collection_runs")
        .select(",".join(RUN_COLUMNS)).eq("status", "SUCCESS")
        .eq("underlying", underlying)
        .order("completed_at", desc=True).limit(500)
    )
    missing = _missing_record_keys(history, tuple(column for column in RUN_COLUMNS if column != "underlying"))
    if missing["missing_columns"]:
        return None, _missing_record_selection("options_collection_runs", missing)
    runs = [current] + [
        row for row in history
        if str(row.get("snapshot_id") or "") != snapshot_id
    ]

    derived = _execute(
        client.table("options_derived_snapshots")
        .select(",".join(DERIVED_COLUMNS)).eq("snapshot_id", snapshot_id)
        .order("expiry_date", desc=False)
    )
    missing = _missing_record_keys(derived, DERIVED_COLUMNS)
    if missing["missing_columns"]:
        return None, _missing_record_selection("options_derived_snapshots", missing)
    future_valid = []
    for row in derived:
        expiry_day, quality = _iso_date(row.get("expiry_date")), row.get("metric_status")
        if (expiry_day is not None and expiry_day >= today
                and isinstance(quality, dict) and quality.get("chain") == "OK"):
            future_valid.append(row)
    if not future_valid:
        return None, {"status": "UNAVAILABLE", "reason": "NO_VALID_FUTURE_EXPIRY",
                      "snapshot_id": snapshot_id, "completed_at": completed_at}
    expiry = str(future_valid[0]["expiry_date"])
    try:
        raw = _query_raw(client, snapshot_id, expiry)
    except MissingRequiredRecordKeys as exc:
        return None, _missing_record_selection(exc.source, exc.details)
    validation = validate_raw_snapshot(raw, snapshot_id, expiry)
    if validation["status"] != "OK":
        return None, {"status": "UNAVAILABLE", "reason": "RAW_CHAIN_INVALID",
                      "snapshot_id": snapshot_id, "expiry_date": expiry, "validation": validation}

    current_time = pd.to_datetime(completed_at, utc=True, errors="coerce")
    earlier = []
    for run in runs[1:]:
        if str(run.get("underlying") or "") != underlying or not _run_is_complete(run):
            continue
        run_time = pd.to_datetime(run.get("completed_at"), utc=True, errors="coerce")
        if pd.isna(run_time) or pd.isna(current_time) or run_time >= current_time:
            continue
        elapsed_minutes = (current_time - run_time).total_seconds() / 60.0
        earlier.append((elapsed_minutes, run))
    earlier.sort(key=lambda item: item[0])
    previous_id = previous_completed = previous_raw = None
    previous_reason = "NO_ELIGIBLE_PREVIOUS_SNAPSHOT"
    if earlier:
        nearest_gap = earlier[0][0]
        eligible = [(gap, run) for gap, run in earlier if 25 <= gap <= 45]
        if not eligible:
            previous_reason = (
                "SNAPSHOTS_TOO_CLOSE" if nearest_gap < 25
                else "SNAPSHOT_GAP_TOO_LARGE"
            )
        else:
            previous_reason = "NO_VALID_PREVIOUS_SNAPSHOT_IN_WINDOW"
            for _, previous in eligible:
                prev_id = str(previous.get("snapshot_id") or "")
                if not prev_id:
                    continue
                prev_derived = _execute(
                    client.table("options_derived_snapshots")
                    .select(",".join(DERIVED_COLUMNS)).eq("snapshot_id", prev_id)
                    .eq("expiry_date", expiry).limit(1)
                )
                if not prev_derived:
                    continue
                if _missing_record_keys(prev_derived, DERIVED_COLUMNS)["missing_columns"]:
                    continue
                quality = prev_derived[0].get("metric_status")
                if not isinstance(quality, dict) or quality.get("chain") != "OK":
                    continue
                try:
                    candidate = _query_raw(client, prev_id, expiry)
                except MissingRequiredRecordKeys:
                    continue
                if validate_raw_snapshot(candidate, prev_id, expiry)["status"] != "OK":
                    continue
                previous_id = prev_id
                previous_completed = previous.get("completed_at")
                previous_raw = candidate
                previous_reason = None
                break

    snapshot = CanonicalSnapshot(
        snapshot_id=snapshot_id, expiry_date=expiry, completed_at=completed_at,
        spot_price=validation["spot_price"], raw=raw,
        previous_snapshot_id=previous_id, previous_completed_at=previous_completed,
        previous_raw=previous_raw, previous_snapshot_reason=previous_reason,
    )
    return snapshot, {"status": "OK", "snapshot_id": snapshot_id,
                      "expiry_date": expiry, "completed_at": completed_at,
                      "spot_price": validation["spot_price"],
                      "previous_snapshot_id": previous_id,
                      "previous_completed_at": previous_completed,
                      "previous_snapshot_reason": previous_reason}


def _aggregate_chain(raw: pd.DataFrame) -> pd.DataFrame:
    """Aggregate by strike while preserving all-null groups as NaN."""
    frame = raw.copy()
    frame["strike_price"] = pd.to_numeric(frame["strike_price"], errors="coerce")
    frame["option_type"] = frame["option_type"].astype(str).str.upper()
    for col in ("oi_contracts", "mark_iv", "volume_24h", "taker_buy_volume", "taker_sell_volume"):
        frame[col] = pd.to_numeric(frame[col], errors="coerce")
    records = []
    for strike, group in frame.groupby("strike_price", sort=True):
        row = {"strike_price": float(strike)}
        for side, prefix in (("CALL", "Call"), ("PUT", "Put")):
            side_rows = group[group["option_type"] == side]
            for source, target in (("oi_contracts", f"{prefix} OI"),
                                   ("mark_iv", f"{prefix} IV"),
                                   ("volume_24h", f"{prefix} Vol")):
                values = side_rows[source].dropna()
                row[target] = (
                    float(values.mean()) if source == "mark_iv" and not values.empty
                    else float(values.sum()) if not values.empty else math.nan
                )
        for col in ("taker_buy_volume", "taker_sell_volume"):
            values = group[col].dropna()
            row[col] = float(values.sum()) if not values.empty else math.nan
        records.append(row)
    return pd.DataFrame(records).sort_values("strike_price").reset_index(drop=True)


def _nearest(chain: pd.DataFrame, spot: float, count: int = 5) -> pd.DataFrame:
    frame = chain.copy()
    frame["distance"] = (frame["strike_price"] - float(spot)).abs()
    return frame.nsmallest(count, "distance").sort_values("strike_price").reset_index(drop=True)


def _result(name: str, status: str, snapshot: CanonicalSnapshot, **values: Any) -> dict:
    return {"filter": name, "status": status, "snapshot_id": snapshot.snapshot_id,
            "expiry_date": snapshot.expiry_date, "snapshot_completed_at": snapshot.completed_at,
            "spot_price": snapshot.spot_price, "calculation_version": "options-filters-v1.0", **values}


def calculate_nearby_oi(snapshot: CanonicalSnapshot) -> dict:
    chain = _nearest(_aggregate_chain(snapshot.raw), snapshot.spot_price)
    if chain.empty:
        return _result("Nearby OI", "UNAVAILABLE", snapshot, reason="NO_DATA")
    usable = chain.dropna(subset=["Call OI", "Put OI"])
    if len(usable) < 3:
        return _result("Nearby OI", "INSUFFICIENT_DATA", snapshot, reason="FEWER_THAN_3_PAIRED_STRIKES")
    walls = []
    for side, column in (("CALL", "Call OI"), ("PUT", "Put OI")):
        values = usable[column].tolist()
        for i in range(1, len(values) - 1):
            neighbor_avg = (values[i - 1] + values[i + 1]) / 2
            if values[i] > neighbor_avg * 1.5 and values[i] > 500:
                walls.append({"strike": float(usable.iloc[i]["strike_price"]),
                              "type": side, "oi": float(values[i])})
    return _result("Nearby OI", "OK", snapshot, walls=walls,
                   usable_strikes=usable["strike_price"].tolist())


def calculate_iv_skew(snapshot: CanonicalSnapshot) -> dict:
    chain = _nearest(_aggregate_chain(snapshot.raw), snapshot.spot_price)
    if chain.empty:
        return _result("IV Skew", "UNAVAILABLE", snapshot, reason="NO_DATA")
    calls, puts = chain["Call IV"].dropna(), chain["Put IV"].dropna()
    if calls.empty or puts.empty:
        return _result("IV Skew", "INSUFFICIENT_DATA", snapshot, reason="NULL_IV_SIDE")
    avg_call, avg_put = float(calls.mean()), float(puts.mean())
    if avg_put == 0:
        return _result("IV Skew", "INSUFFICIENT_DATA", snapshot, reason="ZERO_PUT_IV")
    call_iv_premium = ((avg_call - avg_put) / avg_put) * 100
    return _result(
        "IV Skew", "OK", snapshot,
        metric="CALL_IV_PREMIUM",
        call_iv_premium_pct=call_iv_premium,
        avg_call_iv=avg_call, avg_put_iv=avg_put,
        call_observations=len(calls), put_observations=len(puts),
    )


def calculate_volume_oi(snapshot: CanonicalSnapshot) -> dict:
    chain = _nearest(_aggregate_chain(snapshot.raw), snapshot.spot_price)
    if chain.empty:
        return _result("24h Volume / OI", "UNAVAILABLE", snapshot, reason="NO_DATA")
    fields = ["Call Vol", "Put Vol", "Call OI", "Put OI"]
    if chain[fields].isna().any().any():
        return _result("24h Volume / OI", "INSUFFICIENT_DATA", snapshot,
                       reason="INCOMPLETE_NEAREST_STRIKE_COVERAGE")
    call_vol, call_oi = float(chain["Call Vol"].sum()), float(chain["Call OI"].sum())
    put_vol, put_oi = float(chain["Put Vol"].sum()), float(chain["Put OI"].sum())
    if call_oi == 0 or put_oi == 0:
        return _result("24h Volume / OI", "INSUFFICIENT_DATA", snapshot, reason="ZERO_OI")
    call_ratio, put_ratio = call_vol / call_oi, put_vol / put_oi
    activity = "FRESH_MONEY_ENTERING" if call_ratio > .5 or put_ratio > .5 else "STALE_POSITIONING"
    direction = "BULLISH_ACTIVITY" if call_ratio > put_ratio * 1.5 else (
        "BEARISH_ACTIVITY" if put_ratio > call_ratio * 1.5 else "NEUTRAL")
    return _result("24h Volume / OI", "OK", snapshot, classification=activity,
                   direction=direction, call_ratio=call_ratio, put_ratio=put_ratio)


def calculate_oi_migration(snapshot: CanonicalSnapshot) -> dict:
    if snapshot.previous_raw is None or not snapshot.previous_snapshot_id:
        return _result(
            "OI Migration", "UNAVAILABLE", snapshot,
            reason=snapshot.previous_snapshot_reason or "NO_ELIGIBLE_PREVIOUS_SNAPSHOT",
        )

    current = snapshot.raw.copy()
    previous = snapshot.previous_raw.copy()
    for frame in (current, previous):
        frame["strike_price"] = pd.to_numeric(frame["strike_price"], errors="coerce")
        frame["oi_contracts"] = pd.to_numeric(frame["oi_contracts"], errors="coerce")
        frame["option_type"] = frame["option_type"].astype(str).str.upper()
        frame["contract_identity"] = [
            _contract_identity(row) for row in frame.to_dict("records")
        ]

    current_by_id = {
        _contract_identity(row): row for row in current.to_dict("records")
    }
    previous_by_id = {
        _contract_identity(row): row for row in previous.to_dict("records")
    }
    shared_ids = current_by_id.keys() & previous_by_id.keys()
    if len(shared_ids) == 0:
        return _result(
            "OI Migration", "INSUFFICIENT_DATA", snapshot,
            reason="FEWER_THAN_5_VALID_PAIRED_STRIKES",
            comparable_paired_strikes=0,
        )

    matched = []
    invalid_strikes = set()
    for identity in shared_ids:
        now_row = current_by_id[identity]
        old_row = previous_by_id[identity]
        strike = _finite(now_row["strike_price"])
        side = str(now_row["option_type"]).upper()
        if strike is None:
            continue
        now_oi, old_oi = _finite(now_row["oi_contracts"]), _finite(old_row["oi_contracts"])
        if (now_oi is None or old_oi is None or now_oi < 0 or old_oi < 0
                or _finite(old_row["strike_price"]) != strike
                or str(old_row["option_type"]).upper() != side):
            invalid_strikes.add(strike)
            continue
        matched.append({
            "strike_price": strike, "option_type": side,
            "current_oi": now_oi, "previous_oi": old_oi,
        })

    if not matched:
        return _result(
            "OI Migration", "INSUFFICIENT_DATA", snapshot,
            reason="FEWER_THAN_5_VALID_PAIRED_STRIKES",
            comparable_paired_strikes=0,
        )
    matched_frame = pd.DataFrame(matched)
    # A strike is eligible only when both sides have at least one comparable
    # contract and every matched contract at that strike has valid OI.
    valid_strikes = []
    for strike, group in matched_frame.groupby("strike_price", sort=True):
        sides = set(group["option_type"])
        if float(strike) not in invalid_strikes and sides == {"CALL", "PUT"}:
            valid_strikes.append(float(strike))
    valid = matched_frame[matched_frame["strike_price"].isin(valid_strikes)]
    if valid.empty or len(valid_strikes) < 5:
        return _result(
            "OI Migration", "INSUFFICIENT_DATA", snapshot,
            reason="FEWER_THAN_5_VALID_PAIRED_STRIKES",
            comparable_paired_strikes=len(valid_strikes),
        )

    grouped = valid.groupby(["strike_price", "option_type"], sort=True).agg(
        current_oi=("current_oi", "sum"),
        previous_oi=("previous_oi", "sum"),
    ).reset_index()
    current_peaks = {}
    previous_peaks = {}
    for side in ("CALL", "PUT"):
        side_rows = grouped[grouped["option_type"] == side].sort_values("strike_price")
        if side_rows.empty:
            return _result(
                "OI Migration", "INSUFFICIENT_DATA", snapshot,
                reason="FEWER_THAN_5_VALID_PAIRED_STRIKES",
                comparable_paired_strikes=len(valid_strikes),
            )
        current_peaks[side] = float(
            side_rows.loc[side_rows["current_oi"].idxmax(), "strike_price"]
        )
        previous_peaks[side] = float(
            side_rows.loc[side_rows["previous_oi"].idxmax(), "strike_price"]
        )

    call_shift = current_peaks["CALL"] - previous_peaks["CALL"]
    put_shift = current_peaks["PUT"] - previous_peaks["PUT"]
    classification = "STABLE"
    if call_shift > 0 and put_shift > 0:
        classification = "BULLISH_MIGRATION"
    elif call_shift < 0 and put_shift < 0:
        classification = "BEARISH_MIGRATION"
    elif call_shift > 0 and put_shift <= 0:
        classification = "MILD_BULLISH"
    t_now = pd.to_datetime(snapshot.completed_at, utc=True, errors="coerce")
    t_old = pd.to_datetime(snapshot.previous_completed_at, utc=True, errors="coerce")
    elapsed = (t_now - t_old).total_seconds() if pd.notna(t_now) and pd.notna(t_old) else None
    return _result(
        "OI Migration", "OK", snapshot,
        classification=classification, call_shift=call_shift, put_shift=put_shift,
        previous_snapshot_id=snapshot.previous_snapshot_id,
        previous_completed_at=snapshot.previous_completed_at,
        elapsed_seconds=elapsed, comparable_paired_strikes=len(valid_strikes),
    )


def calculate_taker_activity(snapshot: CanonicalSnapshot) -> dict:
    # Validate every raw contract at the five nearest strikes before summing.
    # A populated Put must not hide a missing Call observation at the same strike.
    nearest = _nearest(_aggregate_chain(snapshot.raw), snapshot.spot_price)
    if nearest.empty:
        return _result("Sampled Taker Trade Activity", "UNAVAILABLE", snapshot, reason="NO_DATA")
    selected_strikes = set(nearest["strike_price"].tolist())
    sample = snapshot.raw.copy()
    sample["strike_price"] = pd.to_numeric(sample["strike_price"], errors="coerce")
    sample = sample[sample["strike_price"].isin(selected_strikes)]
    fields = ["taker_buy_volume", "taker_sell_volume"]
    for field in fields:
        sample[field] = pd.to_numeric(sample[field], errors="coerce")
    if sample.empty or sample[fields].isna().any().any():
        return _result("Sampled Taker Trade Activity", "PARTIAL", snapshot,
                       reason="INCOMPLETE_NEAREST_STRIKE_TAKER_SAMPLE",
                       buy_observations=int(sample[fields[0]].count()),
                       sell_observations=int(sample[fields[1]].count()),
                       sample_strikes=sorted(selected_strikes))
    buy, sell = float(sample[fields[0]].sum()), float(sample[fields[1]].sum())
    total = buy + sell
    balance = buy - sell
    balance_pct = (balance / total) * 100 if total > 0 else None
    return _result(
        "Sampled Taker Trade Activity", "OK", snapshot,
        sampled_buy_volume=buy,
        sampled_sell_volume=sell,
        sampled_volume_balance=balance,
        sampled_volume_balance_pct=balance_pct,
        sample_strikes=sorted(selected_strikes),
        sample_contract_count=int(len(sample)),
    )

def calculate_all_filters(snapshot: CanonicalSnapshot) -> dict:
    """Isolate each calculator so one failure cannot suppress the other four."""
    calculators = (
        ("nearby_oi", calculate_nearby_oi), ("iv_skew", calculate_iv_skew),
        ("volume_oi", calculate_volume_oi), ("oi_migration", calculate_oi_migration),
        ("taker_activity", calculate_taker_activity),
    )
    results = {}
    for key, calculator in calculators:
        try:
            results[key] = calculator(snapshot)
        except Exception as exc:
            results[key] = _result(key, "ERROR", snapshot, reason=type(exc).__name__)
    return results


def load_and_calculate_options_filters(client: Any, *, as_of: Optional[date] = None) -> dict:
    snapshot, selection = select_canonical_snapshot(client, as_of=as_of)
    if snapshot is None:
        return {"status": "UNAVAILABLE", "selection": selection, "filters": {}}
    return {"status": "OK", "selection": selection, "filters": calculate_all_filters(snapshot)}


# Additive IV-surface research layer. The legacy nearest-five calculator above is
# intentionally unchanged; this layer uses a separate read-only raw-chain query.
IV_RESEARCH_VERSION = "iv-surface-research-v1.0"
IV_RESEARCH_COLUMNS = (
    "snapshot_id", "expiry_date", "symbol", "product_id", "option_type",
    "strike_price", "spot_price", "mark_iv", "bid_iv", "ask_iv",
    "bid_price", "ask_price", "greeks", "exchange_timestamp",
    "collected_at", "data_status",
)


def load_iv_research_chain(client: Any, snapshot: CanonicalSnapshot) -> dict:
    """Read the extra quote/Greek fields needed by the isolated IV research layer.

    This function only SELECTs from options_oi_snapshots. It does not write,
    mutate, or change the legacy CanonicalSnapshot.raw contract.
    """
    query = (
        client.table("options_oi_snapshots")
        .select(",".join(IV_RESEARCH_COLUMNS))
        .eq("snapshot_id", snapshot.snapshot_id)
        .eq("expiry_date", snapshot.expiry_date)
    )
    records: List[dict] = []
    start, page_size = 0, 500
    try:
        while True:
            page = _execute(query.range(start, start + page_size - 1))
            records.extend(page)
            if len(page) < page_size:
                break
            start += page_size
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": "IV_RESEARCH_READ_FAILED",
                "error_type": type(exc).__name__, "snapshot_id": snapshot.snapshot_id,
                "expiry_date": snapshot.expiry_date}
    missing = _missing_record_keys(records, IV_RESEARCH_COLUMNS)
    if missing["missing_columns"]:
        return {"status": "UNAVAILABLE", "reason": "MISSING_IV_RESEARCH_COLUMNS",
                **missing, "snapshot_id": snapshot.snapshot_id,
                "expiry_date": snapshot.expiry_date}
    frame = pd.DataFrame(records, columns=list(IV_RESEARCH_COLUMNS))
    if frame.empty:
        return {"status": "UNAVAILABLE", "reason": "EMPTY_IV_RESEARCH_CHAIN",
                "snapshot_id": snapshot.snapshot_id, "expiry_date": snapshot.expiry_date}
    if set(frame["snapshot_id"].dropna().astype(str)) != {snapshot.snapshot_id}:
        return {"status": "UNAVAILABLE", "reason": "SNAPSHOT_ID_MISMATCH",
                "snapshot_id": snapshot.snapshot_id, "expiry_date": snapshot.expiry_date}
    if set(frame["expiry_date"].dropna().astype(str)) != {snapshot.expiry_date}:
        return {"status": "UNAVAILABLE", "reason": "EXPIRY_MISMATCH",
                "snapshot_id": snapshot.snapshot_id, "expiry_date": snapshot.expiry_date}
    return {"status": "OK", "raw": frame, "snapshot_id": snapshot.snapshot_id,
            "expiry_date": snapshot.expiry_date}


def _research_delta(row: dict) -> Optional[float]:
    greeks = row.get("greeks")
    if not isinstance(greeks, dict):
        return None
    delta = _finite(greeks.get("delta", greeks.get("Delta")))
    if delta is None or abs(delta) > 1:
        return None
    return delta


def _research_iv(value: Any) -> Optional[float]:
    """Accept positive finite decimal IV, but reject the known 0.01 sentinel."""
    iv = _finite(value)
    if iv is None or iv <= 0 or math.isclose(iv, 0.01, rel_tol=0, abs_tol=1e-12):
        return None
    return iv


def _research_quote_diagnostics(row: dict) -> dict:
    bid, ask = _finite(row.get("bid_price")), _finite(row.get("ask_price"))
    bid_iv, ask_iv = _finite(row.get("bid_iv")), _finite(row.get("ask_iv"))
    mark_iv = _research_iv(row.get("mark_iv"))
    reasons: List[str] = []
    if bid is None:
        reasons.append("MISSING_OR_INVALID_BID_PRICE")
    elif bid <= 0:
        reasons.append("NON_POSITIVE_BID_PRICE")
    if ask is None:
        reasons.append("MISSING_OR_INVALID_ASK_PRICE")
    elif ask <= 0:
        reasons.append("NON_POSITIVE_ASK_PRICE")
    if bid is not None and ask is not None and bid > 0 and ask > 0 and bid > ask:
        reasons.append("CROSSED_MARKET")
    if bid_iv is None or bid_iv <= 0:
        reasons.append("MISSING_OR_INVALID_BID_IV")
    elif math.isclose(bid_iv, 0.01, rel_tol=0, abs_tol=1e-12):
        reasons.append("BID_IV_SENTINEL_0_01")
    if ask_iv is None or ask_iv <= 0:
        reasons.append("MISSING_OR_INVALID_ASK_IV")
    elif math.isclose(ask_iv, 0.01, rel_tol=0, abs_tol=1e-12):
        reasons.append("ASK_IV_SENTINEL_0_01")
    if mark_iv is None:
        reasons.append("INVALID_OR_SENTINEL_MARK_IV")
    mid = ((bid + ask) / 2) if bid is not None and ask is not None and bid > 0 and ask > 0 and bid <= ask else None
    spread_pct = ((ask - bid) / mid * 100) if mid and mid > 0 else None
    if spread_pct is not None and spread_pct < 0:
        reasons.append("NEGATIVE_SPREAD")
    # No arbitrary wide-spread cutoff is imposed. The measured spread is exposed
    # for review, rather than silently treating an unapproved threshold as truth.
    return {
        "bid_price": bid, "ask_price": ask, "mid_price": mid,
        "spread_pct_mid": spread_pct,
        "bid_iv_valid": _research_iv(bid_iv) is not None,
        "ask_iv_valid": _research_iv(ask_iv) is not None,
        "mark_iv_valid": mark_iv is not None,
        "quality_status": "OK" if not reasons else "REVIEW",
        "rejection_reasons": reasons,
    }


def _interpolate_delta_iv(rows: List[dict], side: str) -> dict:
    """Interpolate mark IV at |delta|=0.25 without extrapolation.

    Delta Exchange Put deltas are negative; their absolute magnitude is used
    for target matching while the original signed delta remains in diagnostics.
    """
    points = []
    rejected = []
    for row in rows:
        option_type = str(row.get("option_type", "")).upper()
        if option_type != side:
            continue
        delta = _research_delta(row)
        iv = _research_iv(row.get("mark_iv"))
        quote = _research_quote_diagnostics(row)
        # RR25 uses mark_iv with signed Greeks Delta. Bid/ask IV sentinel
        # flags remain visible in quote diagnostics but do not invalidate an
        # otherwise usable mark-IV observation when bid/ask prices are valid.
        reasons = []
        if quote["mid_price"] is None:
            reasons.append("UNUSABLE_TWO_SIDED_PRICE_QUOTE")
        if delta is None:
            reasons.append("INVALID_OR_MISSING_DELTA")
        elif (side == "CALL" and delta <= 0) or (side == "PUT" and delta >= 0):
            reasons.append("DELTA_SIGN_MISMATCH")
        if iv is None:
            reasons.append("INVALID_OR_SENTINEL_MARK_IV")
        if reasons:
            rejected.append({"symbol": row.get("symbol"), "strike_price": row.get("strike_price"),
                             "option_type": side, "rejection_reasons": sorted(set(reasons))})
            continue
        points.append({"abs_delta": abs(delta), "signed_delta": delta, "iv": iv,
                       "strike_price": _finite(row.get("strike_price")),
                       "symbol": row.get("symbol"), "spread_pct_mid": quote["spread_pct_mid"]})
    if not points:
        return {"status": "UNAVAILABLE", "reason": "NO_QUALIFIED_CONTRACTS",
                "rejected_contracts": rejected}
    # Multiple contracts at identical absolute delta are averaged at that delta
    # before interpolation; no strike-based extrapolation is performed.
    grouped = {}
    for point in points:
        grouped.setdefault(point["abs_delta"], []).append(point)
    curve = [{"abs_delta": delta,
              "iv": sum(p["iv"] for p in group) / len(group),
              "contracts": [p["symbol"] for p in group]}
             for delta, group in sorted(grouped.items())]
    target = 0.25
    exact = [p for p in curve if math.isclose(p["abs_delta"], target, abs_tol=1e-9)]
    if exact:
        value = exact[0]["iv"]
        return {"status": "OK", "iv_25d": value, "method": "EXACT_DELTA",
                "lower_delta": target, "upper_delta": target,
                "rejected_contracts": rejected, "curve_points": curve}
    lower = [p for p in curve if p["abs_delta"] < target]
    upper = [p for p in curve if p["abs_delta"] > target]
    if not lower or not upper:
        return {"status": "UNAVAILABLE", "reason": "MISSING_25D_BRACKET",
                "rejected_contracts": rejected, "curve_points": curve}
    lo, hi = lower[-1], upper[0]
    weight = (target - lo["abs_delta"]) / (hi["abs_delta"] - lo["abs_delta"])
    value = lo["iv"] + weight * (hi["iv"] - lo["iv"])
    return {"status": "OK", "iv_25d": value, "method": "LINEAR_DELTA_INTERPOLATION",
            "lower_delta": lo["abs_delta"], "upper_delta": hi["abs_delta"],
            "rejected_contracts": rejected, "curve_points": curve}


def calculate_iv_surface_research(snapshot: CanonicalSnapshot, raw: Optional[pd.DataFrame] = None) -> dict:
    """Calculate additive ATM IV and 25-delta risk reversal research metrics."""
    frame = (raw if raw is not None else snapshot.raw).copy()
    required = {"option_type", "strike_price", "mark_iv", "greeks",
                "bid_price", "ask_price", "bid_iv", "ask_iv"}
    missing = sorted(required - set(frame.columns))
    base = {"methodology_version": IV_RESEARCH_VERSION,
            "snapshot_id": snapshot.snapshot_id, "expiry_date": snapshot.expiry_date,
            "snapshot_completed_at": snapshot.completed_at,
            "spot_price": snapshot.spot_price}
    if missing:
        return {**base, "status": "UNAVAILABLE", "reason": "MISSING_IV_RESEARCH_FIELDS",
                "missing_columns": missing}
    rows = frame.to_dict("records")
    calls = _interpolate_delta_iv(rows, "CALL")
    puts = _interpolate_delta_iv(rows, "PUT")
    rr_status = "OK" if calls.get("status") == "OK" and puts.get("status") == "OK" else "UNAVAILABLE"
    rr = (calls["iv_25d"] - puts["iv_25d"]) * 100 if rr_status == "OK" else None

    # ATM pair: nearest strike to snapshot spot with valid mark IV on both sides
    # and usable, non-crossed two-sided prices. IV sentinel 0.01 is excluded.
    work = []
    for strike, group in frame.groupby(pd.to_numeric(frame["strike_price"], errors="coerce"), dropna=True):
        side_values = {}
        side_symbols = {}
        for side in ("CALL", "PUT"):
            candidates = group[group["option_type"].astype(str).str.upper() == side]
            valid = []
            for row in candidates.to_dict("records"):
                quote = _research_quote_diagnostics(row)
                iv = _research_iv(row.get("mark_iv"))
                if iv is not None and quote["mid_price"] is not None:
                    valid.append((iv, row.get("symbol")))
            if valid:
                side_values[side] = sum(item[0] for item in valid) / len(valid)
                side_symbols[side] = [item[1] for item in valid]
        if set(side_values) == {"CALL", "PUT"}:
            work.append({"strike": float(strike), "distance": abs(float(strike) - snapshot.spot_price),
                         "call_iv": side_values["CALL"], "put_iv": side_values["PUT"],
                         "symbols": side_symbols})
    work.sort(key=lambda item: (item["distance"], item["strike"]))
    atm = work[0] if work else None
    atm_iv = ((atm["call_iv"] + atm["put_iv"]) / 2) if atm else None

    quality_rows = []
    for row in rows:
        diag = _research_quote_diagnostics(row)
        quality_rows.append({
            "symbol": row.get("symbol"), "option_type": str(row.get("option_type", "")).upper(),
            "strike_price": _finite(row.get("strike_price")),
            "delta": _research_delta(row), **diag,
        })
    quality_counts = {
        "contracts_reviewed": len(quality_rows),
        "contracts_ok": sum(item["quality_status"] == "OK" for item in quality_rows),
        "contracts_review": sum(item["quality_status"] != "OK" for item in quality_rows),
        "sentinel_iv_contracts": sum(
            any(reason.endswith("SENTINEL_0_01") for reason in item["rejection_reasons"])
            for item in quality_rows
        ),
    }
    return {
        **base, "status": "OK" if atm is not None or rr_status == "OK" else "INSUFFICIENT_DATA",
        "atm_iv": atm_iv,
        "atm_strike": atm["strike"] if atm else None,
        "atm_call_iv": atm["call_iv"] if atm else None,
        "atm_put_iv": atm["put_iv"] if atm else None,
        "atm_method": "NEAREST_STRIKE_WITH_VALID_CALL_PUT_PAIR" if atm else None,
        "rr25_status": rr_status, "rr25_reason": None if rr_status == "OK" else {
            "call": calls.get("reason"), "put": puts.get("reason")},
        "call_iv_25d": calls.get("iv_25d"), "put_iv_25d": puts.get("iv_25d"),
        "rr25_call_minus_put_vol_points": rr,
        "call_25d_method": calls.get("method"), "put_25d_method": puts.get("method"),
        "call_25d_curve": calls.get("curve_points", []),
        "put_25d_curve": puts.get("curve_points", []),
        "call_rejected_contracts": calls.get("rejected_contracts", []),
        "put_rejected_contracts": puts.get("rejected_contracts", []),
        "quote_quality": quality_counts, "quote_quality_rows": quality_rows,
        "spread_policy": "SPREAD_PERCENT_REPORTED_NO_UNAPPROVED_WIDE_SPREAD_CUTOFF",
    }
