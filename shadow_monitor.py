"""Non-alerting, completed-candle auction deterioration shadow monitor.

Reads canonical rows from public.amt_snapshots and idempotently rebuilds compact
metrics in public.auction_deterioration_metrics. It never changes AMT state,
production alerts, options data, or trading rules.
"""
from __future__ import annotations

import math
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

from supabase import create_client

CALCULATION_VERSION = "shadow-v1.0"
EXCHANGE = "BINANCE"
SYMBOL = "BTCUSDT"
TIMEFRAME = "30"
CANDLE_INTERVAL = timedelta(minutes=30)
PAGE_SIZE = 1000
UPSERT_BATCH_SIZE = 250

SOURCE_COLUMNS = (
    "exchange,symbol,timeframe,candle_open_time,candle_close_time,"
    "profile_id,confirmed_state,close_price,high_price,low_price,"
    "developing_val,developing_poc,atr_21,data_status"
)


def _number(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def _timestamp(value: Any) -> datetime:
    if isinstance(value, datetime):
        result = value
    else:
        raw = str(value or "").strip()
        if not raw:
            raise ValueError("Missing candle timestamp.")
        result = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if result.tzinfo is None:
        result = result.replace(tzinfo=timezone.utc)
    return result.astimezone(timezone.utc)


def _valid_source(row: Dict[str, Any]) -> Tuple[bool, str]:
    status = str(row.get("data_status") or "OK").upper()
    if status not in {"OK", "VALID", "SUCCESS"}:
        return False, f"DATA_STATUS_{status}"
    required = (
        "close_price", "high_price", "low_price",
        "developing_val", "developing_poc", "atr_21",
    )
    if any(_number(row.get(key)) is None for key in required):
        return False, "MISSING_OR_INVALID_METRIC"
    close = _number(row["close_price"])
    high = _number(row["high_price"])
    low = _number(row["low_price"])
    atr = _number(row["atr_21"])
    if close <= 0 or low <= 0 or high < low or low > close or close > high:
        return False, "INVALID_OHLC"
    if atr <= 0:
        return False, "INVALID_ATR"
    if not row.get("profile_id") or not row.get("confirmed_state"):
        return False, "MISSING_PROFILE_OR_CONFIRMED_STATE"
    try:
        _timestamp(row.get("candle_open_time"))
        _timestamp(row.get("candle_close_time"))
    except (TypeError, ValueError, OverflowError):
        return False, "INVALID_TIMESTAMP"
    return True, "OK"


def calculate_shadow_rows(source_rows: Iterable[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Pure chronological calculation; deterministic on repeated full-history runs."""
    rows = sorted(
        list(source_rows),
        key=lambda item: (_timestamp(item["candle_close_time"]), str(item.get("profile_id") or "")),
    )
    output: List[Dict[str, Any]] = []

    anchor_profile = None
    anchor_state = None
    anchor_time = None
    anchor_price = None
    min_close = max_close = min_low = max_high = None
    previous_valid = None
    force_anchor_reset = False

    active_test = False
    active_test_time = None
    active_test_level = None
    test_count = 0
    rejection_count = 0

    for row in rows:
        valid, reason = _valid_source(row)
        if not valid:
            # Do not bridge an invalid candle as if the auction were continuous.
            previous_valid = None
            force_anchor_reset = True
            active_test = False
            active_test_time = None
            active_test_level = None
            continue

        candle_open = _timestamp(row["candle_open_time"])
        candle_close = _timestamp(row["candle_close_time"])
        profile_id = str(row["profile_id"])
        state = str(row["confirmed_state"])
        close = float(row["close_price"])
        high = float(row["high_price"])
        low = float(row["low_price"])
        val = float(row["developing_val"])
        poc = float(row["developing_poc"])
        atr = float(row["atr_21"])

        profile_reset = anchor_profile is not None and profile_id != anchor_profile
        state_change = anchor_state is not None and state != anchor_state
        state_reset = anchor_profile is None or profile_reset or state_change or force_anchor_reset
        gap_reset = False
        if previous_valid is not None:
            prior_open = _timestamp(previous_valid["candle_open_time"])
            gap_reset = candle_open - prior_open > CANDLE_INTERVAL + timedelta(seconds=2)

        status = "NONE"
        reported_test_time = active_test_time
        reported_test_level = active_test_level
        if state_reset or gap_reset:
            if (gap_reset or force_anchor_reset) and not profile_reset and not state_change:
                status = "INCOMPLETE_GAP"
            elif anchor_profile is not None:
                status = "RESET_CONTEXT"
            if profile_reset:
                test_count = 0
                rejection_count = 0
            active_test = False
            active_test_time = None
            active_test_level = None

            anchor_profile = profile_id
            anchor_state = state
            anchor_time = candle_close
            anchor_price = close
            min_close = max_close = close
            min_low = low
            max_high = high
            force_anchor_reset = False
        else:
            min_close = min(min_close, close)
            max_close = max(max_close, close)
            min_low = min(min_low, low)
            max_high = max(max_high, high)

        # One episode is counted once. Its VAL is frozen at the first qualifying
        # completed candle and is not moved by subsequent developing-VAL updates.
        if not state_reset and not gap_reset:
            if active_test:
                if close < active_test_level:
                    active_test = False
                    status = "BROKEN"
                    active_test_time = None
                    active_test_level = None
                elif low <= active_test_level:
                    status = "CONTINUING"
                else:
                    active_test = False
                    status = "REJECTED"
                    rejection_count += 1
                    active_test_time = None
                    active_test_level = None
            elif previous_valid is not None:
                previous_close = _number(previous_valid.get("close_price"))
                previous_val = _number(previous_valid.get("developing_val"))
                if (
                    previous_close is not None
                    and previous_val is not None
                    and previous_close > previous_val
                    and low <= val
                    and close > val
                ):
                    active_test = True
                    active_test_time = candle_close
                    active_test_level = val
                    reported_test_time = candle_close
                    reported_test_level = val
                    test_count += 1
                    status = "STARTED"

        down_close = max(0.0, anchor_price - min_close)
        up_close = max(0.0, max_close - anchor_price)
        down_wick = max(0.0, anchor_price - min_low)
        up_wick = max(0.0, max_high - anchor_price)

        result = {
            "exchange": str(row.get("exchange") or EXCHANGE),
            "symbol": str(row.get("symbol") or SYMBOL),
            "timeframe": str(row.get("timeframe") or TIMEFRAME),
            "profile_id": profile_id,
            "candle_open_time": candle_open.isoformat(),
            "candle_close_time": candle_close.isoformat(),
            "confirmed_state": state,
            "close_price": close,
            "high_price": high,
            "low_price": low,
            "developing_val": val,
            "developing_poc": poc,
            "atr_21": atr,
            "transition_anchor_time": anchor_time.isoformat(),
            "transition_anchor_price": anchor_price,
            "downside_close_excursion_usd": down_close,
            "upside_close_excursion_usd": up_close,
            "downside_wick_excursion_usd": down_wick,
            "upside_wick_excursion_usd": up_wick,
            "downside_close_excursion_atr": down_close / atr,
            "upside_close_excursion_atr": up_close / atr,
            "downside_wick_excursion_atr": down_wick / atr,
            "upside_wick_excursion_atr": up_wick / atr,
            "downside_close_excursion_pct": down_close / anchor_price * 100.0,
            "upside_close_excursion_pct": up_close / anchor_price * 100.0,
            "downside_wick_excursion_pct": down_wick / anchor_price * 100.0,
            "upside_wick_excursion_pct": up_wick / anchor_price * 100.0,
            "poc_distance_atr": abs(close - poc) / atr,
            "val_test_active": active_test,
            "val_test_started_at": reported_test_time.isoformat() if reported_test_time else None,
            "val_test_level": reported_test_level,
            "val_test_status": status,
            "val_test_count": test_count,
            "val_rejection_count": rejection_count,
            "calculation_version": CALCULATION_VERSION,
        }
        output.append(result)
        previous_valid = row

    return output


def fetch_source_rows(client) -> List[Dict[str, Any]]:
    """Read all canonical AMT rows in pages so state/episode history is reproducible."""
    collected: List[Dict[str, Any]] = []
    offset = 0
    while True:
        response = (
            client.table("amt_snapshots")
            .select(SOURCE_COLUMNS)
            .eq("exchange", EXCHANGE)
            .eq("symbol", SYMBOL)
            .eq("timeframe", TIMEFRAME)
            .order("candle_close_time")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        page = response.data or []
        collected.extend(page)
        if len(page) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return collected


def latest_persisted_close(client) -> Optional[datetime]:
    response = (
        client.table("auction_deterioration_metrics")
        .select("candle_close_time")
        .eq("exchange", EXCHANGE)
        .eq("symbol", SYMBOL)
        .eq("timeframe", TIMEFRAME)
        .order("candle_close_time", desc=True)
        .limit(1)
        .execute()
    )
    latest = response.data or []
    return _timestamp(latest[0]["candle_close_time"]) if latest else None


def persist_rows(client, rows: List[Dict[str, Any]]) -> int:
    written = 0
    for start in range(0, len(rows), UPSERT_BATCH_SIZE):
        batch = rows[start:start + UPSERT_BATCH_SIZE]
        if not batch:
            continue
        client.table("auction_deterioration_metrics").upsert(
            batch,
            on_conflict="exchange,symbol,timeframe,profile_id,candle_close_time",
        ).execute()
        written += len(batch)
    return written


def main() -> int:
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.", file=sys.stderr)
        return 1

    try:
        client = create_client(url, key)
        source_rows = fetch_source_rows(client)
        if not source_rows:
            print("No AMT source rows found; nothing written.")
            return 0
        calculated = calculate_shadow_rows(source_rows)
        latest = latest_persisted_close(client)
        pending = [
            row for row in calculated
            if latest is None or _timestamp(row["candle_close_time"]) >= latest
        ]
        written = persist_rows(client, pending)
        print(
            f"Shadow monitor complete: source_rows={len(source_rows)}, "
            f"valid_metric_rows={len(calculated)}, pending_rows={len(pending)}, upserted={written}, "
            f"version={CALCULATION_VERSION}"
        )
        return 0
    except Exception as exc:
        # The caller workflow marks this step non-blocking; surface a clear error.
        print(f"Shadow monitor failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
