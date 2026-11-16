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
    "snapshot_id", "status", "started_at", "completed_at",
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
    """Pin latest SUCCESS run, its nearest valid future expiry and raw chain.

    No fallback to an older run or another expiry occurs if the latest
    successful run fails validation.
    """
    today = as_of or datetime.now(timezone.utc).date()
    runs = _execute(
        client.table("options_collection_runs")
        .select(",".join(RUN_COLUMNS)).eq("status", "SUCCESS")
        .order("completed_at", desc=True).limit(2)
    )
    if not runs:
        return None, {"status": "UNAVAILABLE", "reason": "NO_SUCCESS_RUN"}
    missing = _missing_record_keys(runs, RUN_COLUMNS)
    if missing["missing_columns"]:
        return None, _missing_record_selection("options_collection_runs", missing)
    current = runs[0]
    if not _run_is_complete(current):
        return None, {"status": "UNAVAILABLE", "reason": "RUN_COUNTER_MISMATCH"}
    snapshot_id, completed_at = str(current.get("snapshot_id") or ""), current.get("completed_at")
    if not snapshot_id or not completed_at:
        return None, {"status": "UNAVAILABLE", "reason": "INVALID_RUN_IDENTITY"}
    derived = _execute(
        client.table("options_derived_snapshots")
        .select(",".join(DERIVED_COLUMNS)).eq("snapshot_id", snapshot_id)
        .order("expiry_date", desc=False)
    )
    future_valid = []
    for row in derived:
        expiry_day, quality = _iso_date(row.get("expiry_date")), row.get("metric_status")
        if expiry_day is not None and expiry_day >= today and isinstance(quality, dict) and quality.get("chain") == "OK":
            future_valid.append(row)
    missing = _missing_record_keys(derived, DERIVED_COLUMNS)
    if missing["missing_columns"]:
        return None, _missing_record_selection("options_derived_snapshots", missing)
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

    previous_id = previous_completed = previous_raw = None
    if len(runs) > 1:
        previous = runs[1]
        if _run_is_complete(previous):
            prev_id = str(previous.get("snapshot_id") or "")
            prev_derived = _execute(
                client.table("options_derived_snapshots")
                .select(",".join(DERIVED_COLUMNS)).eq("snapshot_id", prev_id)
                .eq("expiry_date", expiry).limit(1)
            )
            if prev_derived and isinstance(prev_derived[0].get("metric_status"), dict) and prev_derived[0]["metric_status"].get("chain") == "OK":
                try:
                    candidate = _query_raw(client, prev_id, expiry)
                except MissingRequiredRecordKeys as exc:
                    return None, _missing_record_selection(exc.source, exc.details)
                if validate_raw_snapshot(candidate, prev_id, expiry)["status"] == "OK":
                    current_ids = {_contract_identity(x) for x in raw.to_dict("records")}
                    previous_ids = {_contract_identity(x) for x in candidate.to_dict("records")}
                    if current_ids == previous_ids:
                        previous_id, previous_completed, previous_raw = prev_id, previous.get("completed_at"), candidate

    snapshot = CanonicalSnapshot(
        snapshot_id=snapshot_id, expiry_date=expiry, completed_at=completed_at,
        spot_price=validation["spot_price"], raw=raw,
        previous_snapshot_id=previous_id, previous_completed_at=previous_completed,
        previous_raw=previous_raw,
    )
    return snapshot, {"status": "OK", "snapshot_id": snapshot_id,
                      "expiry_date": expiry, "completed_at": completed_at,
                      "spot_price": validation["spot_price"],
                      "previous_snapshot_id": previous_id,
                      "previous_completed_at": previous_completed}


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
        return _result("OI Migration", "UNAVAILABLE", snapshot, reason="NO_ELIGIBLE_PREVIOUS_SNAPSHOT")
    current, previous = snapshot.raw.copy(), snapshot.previous_raw.copy()
    for frame in (current, previous):
        frame["oi_contracts"] = pd.to_numeric(frame["oi_contracts"], errors="coerce")
        if frame["oi_contracts"].isna().any():
            return _result("OI Migration", "INSUFFICIENT_DATA", snapshot, reason="INCOMPLETE_OI_COVERAGE")
        frame["option_type"] = frame["option_type"].astype(str).str.upper()
    current_ids = {_contract_identity(x) for x in current.to_dict("records")}
    previous_ids = {_contract_identity(x) for x in previous.to_dict("records")}
    if current_ids != previous_ids:
        return _result("OI Migration", "UNAVAILABLE", snapshot, reason="CONTRACT_UNIVERSE_MISMATCH")
    def peaks(frame):
        grouped = frame.groupby(["strike_price", "option_type"], sort=True)["oi_contracts"].sum().unstack()
        grouped = grouped.reindex(columns=["CALL", "PUT"])
        if grouped.isna().any().any() or grouped.empty:
            raise ValueError("MISSING_OI_SIDE")
        return {"CALL": float(grouped["CALL"].idxmax()), "PUT": float(grouped["PUT"].idxmax())}
    try:
        now_peaks, old_peaks = peaks(current), peaks(previous)
    except (ValueError, KeyError):
        return _result("OI Migration", "INSUFFICIENT_DATA", snapshot, reason="MISSING_OI_SIDE")
    call_shift, put_shift = now_peaks["CALL"] - old_peaks["CALL"], now_peaks["PUT"] - old_peaks["PUT"]
    status = "STABLE"
    if call_shift > 0 and put_shift > 0: status = "BULLISH_MIGRATION"
    elif call_shift < 0 and put_shift < 0: status = "BEARISH_MIGRATION"
    elif call_shift > 0 and put_shift <= 0: status = "MILD_BULLISH"
    t_now = pd.to_datetime(snapshot.completed_at, utc=True, errors="coerce")
    t_old = pd.to_datetime(snapshot.previous_completed_at, utc=True, errors="coerce")
    elapsed = (t_now - t_old).total_seconds() if pd.notna(t_now) and pd.notna(t_old) else None
    return _result("OI Migration", "OK", snapshot, classification=status,
                   call_shift=call_shift, put_shift=put_shift,
                   previous_snapshot_id=snapshot.previous_snapshot_id,
                   previous_completed_at=snapshot.previous_completed_at,
                   elapsed_seconds=elapsed)


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
