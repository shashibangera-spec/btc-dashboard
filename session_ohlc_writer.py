"""Independent, read-only-by-default session OHLC history writer.

This module reads the existing Binance 30m rows in public.amt_snapshots.
It does not fetch market data and does not modify the AMT collector or engine.

Writes are opt-in via --write. Unit tests exercise the pure calculation path
without Supabase credentials or network access.
"""
from __future__ import annotations

import argparse
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any


CALCULATION_VERSION = "session_ohlc_v1"
SOURCE_EXCHANGE = "BINANCE"
SOURCE_SYMBOL = "BTCUSDT"
SOURCE_TIMEFRAME = "30"
CANDLE_INTERVAL = timedelta(minutes=30)
ACCEPTED_SOURCE_STATUSES = {"VALID", "OK"}

# Boundaries are UTC equivalents of the user's 05:30 IST AMT reset.
# Each AMT date runs 00:00 UTC through the following 00:00 UTC.
SESSION_SPECS = {
    "ASIA": (time(0, 0), time(8, 0), 16),
    "EUROPE": (time(8, 0), time(12, 0), 8),
    "US_CASH": (time(12, 0), time(20, 0), 16),
    "US_OVERNIGHT": (time(20, 0), time(0, 0), 8),
}

SOURCE_COLUMNS = (
    "candle_open_time,candle_close_time,open_price,high_price,"
    "low_price,close_price,volume,data_status"
)


def parse_timestamp(value: Any) -> datetime:
    """Parse an ISO timestamp and require an explicit timezone."""
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        raise ValueError("timestamp is not an ISO string or datetime")
    if parsed.tzinfo is None:
        raise ValueError("timestamp has no timezone")
    return parsed.astimezone(timezone.utc)


def session_bounds(amt_date: date, session_name: str) -> tuple[datetime, datetime, int]:
    """Return UTC start/end and expected candle count for an AMT-date session."""
    if session_name not in SESSION_SPECS:
        raise ValueError(f"unknown session: {session_name}")
    start_time, end_time, expected_count = SESSION_SPECS[session_name]
    start = datetime.combine(amt_date, start_time, tzinfo=timezone.utc)
    end_date = amt_date + timedelta(days=1) if end_time <= start_time else amt_date
    end = datetime.combine(end_date, end_time, tzinfo=timezone.utc)
    return start, end, expected_count


def _numeric(row: dict[str, Any], field: str) -> Decimal:
    value = row.get(field)
    if value is None:
        raise ValueError(f"{field} is null")
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} is not finite")
    return number


def validate_and_aggregate(
    amt_date: date,
    session_name: str,
    rows: list[dict[str, Any]],
    now: datetime,
) -> dict[str, Any]:
    """Validate a closed session and return a database-ready status/summary.

    Missing/duplicate/invalid candles never produce COMPLETE status. Incomplete
    or invalid windows deliberately carry null OHLC/volume to avoid presenting
    partial aggregates as session values.
    """
    start, end, expected_count = session_bounds(amt_date, session_name)
    now_utc = parse_timestamp(now)

    base = {
        "amt_date": amt_date.isoformat(),
        "session_name": session_name,
        "session_start": start.isoformat(),
        "session_end": end.isoformat(),
        "calculation_version": CALCULATION_VERSION,
    }

    if end > now_utc:
        return {
            **base, "data_status": "INCOMPLETE", "candle_count": 0,
            "open_price": None, "high_price": None, "low_price": None,
            "close_price": None, "volume": None,
            "reason": "session has not ended",
        }

    parsed_rows: list[tuple[datetime, dict[str, Any]]] = []
    seen: set[datetime] = set()
    invalid_reasons: list[str] = []

    for row in rows:
        try:
            opened = parse_timestamp(row.get("candle_open_time"))
            closed = parse_timestamp(row.get("candle_close_time"))
            if opened < start or opened >= end:
                invalid_reasons.append("candle outside session window")
                continue
            if opened in seen:
                invalid_reasons.append("duplicate candle timestamp")
                continue
            seen.add(opened)
            expected_close = opened + CANDLE_INTERVAL
            # Binance close timestamps are inclusive; Supabase stores them as
            # xx:xx:xx.999. Accept [interval_end - 1ms, interval_end].
            if not expected_close - timedelta(milliseconds=1) <= closed <= expected_close:
                invalid_reasons.append("candle close time is outside the 30m candle boundary")
                continue
            if closed > now_utc:
                invalid_reasons.append("candle has not closed")
                continue
            if str(row.get("data_status") or "").upper() not in ACCEPTED_SOURCE_STATUSES:
                invalid_reasons.append("source candle status is not VALID/OK")
                continue

            op = _numeric(row, "open_price")
            hi = _numeric(row, "high_price")
            lo = _numeric(row, "low_price")
            cl = _numeric(row, "close_price")
            vol = _numeric(row, "volume")
            if min(op, hi, lo, cl) <= 0 or vol < 0:
                invalid_reasons.append("non-positive OHLC or negative volume")
                continue
            if hi < max(op, cl, lo) or lo > min(op, cl, hi):
                invalid_reasons.append("OHLC relationship is invalid")
                continue
            parsed_rows.append((opened, row))
        except (TypeError, ValueError, OverflowError) as exc:
            invalid_reasons.append(str(exc))

    parsed_rows.sort(key=lambda item: item[0])
    actual_count = len(parsed_rows)
    expected_opens = {
        start + CANDLE_INTERVAL * index for index in range(expected_count)
    }
    actual_opens = {opened for opened, _ in parsed_rows}

    if invalid_reasons:
        status = "INVALID"
        reason = "; ".join(sorted(set(invalid_reasons)))
    elif actual_count != expected_count or actual_opens != expected_opens:
        status = "INCOMPLETE"
        reason = (
            f"expected {expected_count} distinct 30m candles; "
            f"received {actual_count}"
        )
    else:
        status = "COMPLETE"
        reason = "all expected candles validated"

    result = {
        **base,
        "data_status": status,
        "candle_count": min(actual_count, expected_count),
        "open_price": None,
        "high_price": None,
        "low_price": None,
        "close_price": None,
        "volume": None,
        "reason": reason,
    }

    if status == "COMPLETE":
        ordered = [row for _, row in parsed_rows]
        result.update({
            "open_price": str(_numeric(ordered[0], "open_price")),
            "high_price": str(max(_numeric(row, "high_price") for row in ordered)),
            "low_price": str(min(_numeric(row, "low_price") for row in ordered)),
            "close_price": str(_numeric(ordered[-1], "close_price")),
            "volume": str(sum((_numeric(row, "volume") for row in ordered), Decimal("0"))),
        })
    return result


def fetch_session_rows(client: Any, start: datetime, end: datetime) -> list[dict[str, Any]]:
    """Fetch every source candle in a session, paging explicitly."""
    rows: list[dict[str, Any]] = []
    offset = 0
    page_size = 500
    while True:
        response = (
            client.table("amt_snapshots")
            .select(SOURCE_COLUMNS)
            .eq("exchange", SOURCE_EXCHANGE)
            .eq("symbol", SOURCE_SYMBOL)
            .eq("timeframe", SOURCE_TIMEFRAME)
            .gte("candle_open_time", start.isoformat())
            .lt("candle_open_time", end.isoformat())
            .order("candle_open_time")
            .range(offset, offset + page_size - 1)
            .execute()
        )
        page = response.data or []
        rows.extend(page)
        if len(page) < page_size:
            break
        offset += page_size
    return rows


def process_date(
    client: Any,
    amt_date: date,
    now: datetime,
    write: bool = False,
) -> list[dict[str, Any]]:
    """Calculate all ended sessions for one AMT date; write only with opt-in."""
    outputs = []
    for session_name in SESSION_SPECS:
        start, end, _ = session_bounds(amt_date, session_name)
        if end > parse_timestamp(now):
            continue
        source_rows = fetch_session_rows(client, start, end)
        record = validate_and_aggregate(amt_date, session_name, source_rows, now)
        record.pop("reason", None)
        outputs.append(record)
        # Persist only fully validated sessions. This prevents a transient
        # source gap from downgrading an existing COMPLETE record.
        if write and record["data_status"] == "COMPLETE":
            (
                client.table("session_ohlc_history")
                .upsert(record, on_conflict="amt_date,session_name")
                .execute()
            )
    return outputs


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start-date", type=date.fromisoformat)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--lookback-days", type=int, default=3)
    parser.add_argument(
        "--write", action="store_true",
        help="Persist validated session records. Omit for read-only dry run.",
    )
    args = parser.parse_args(argv)

    if args.lookback_days < 1:
        parser.error("--lookback-days must be at least 1")
    now = datetime.now(timezone.utc)
    current_amt_date = now.date()
    start_date = args.start_date or (current_amt_date - timedelta(days=args.lookback_days))
    end_date = args.end_date or current_amt_date
    if end_date < start_date:
        parser.error("--end-date must not be earlier than --start-date")

    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        print("Missing Supabase secrets.", file=sys.stderr)
        return 1

    from supabase import create_client

    client = create_client(supabase_url, supabase_key)
    total = 0
    for day_offset in range((end_date - start_date).days + 1):
        amt_date = start_date + timedelta(days=day_offset)
        records = process_date(client, amt_date, now, write=args.write)
        for record in records:
            print(
                f"{record['amt_date']} {record['session_name']}: "
                f"{record['data_status']} ({record['candle_count']} candles)"
            )
        total += len(records)

    mode = "WRITE ENABLED" if args.write else "READ-ONLY DRY RUN"
    print(f"{mode}: processed {total} ended session windows.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
