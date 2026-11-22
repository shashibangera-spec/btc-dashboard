"""Validated Binance spot BTCUSDT 30m OHLC archive writer.

Historical mode: --start-date YYYY-MM-DD [--end-date YYYY-MM-DD]
Live mode: --lookback-hours N
Writes are opt-in with --write. All timestamps are UTC candle-open/close times.
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from datetime import date, datetime, time as dt_time, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

import requests

API_URL = "https://api.binance.com/api/v3/klines"
FALLBACK_API_URL = "https://data-api.binance.vision/api/v3/klines"
EXCHANGE = "BINANCE"
MARKET_TYPE = "SPOT"
SYMBOL = "BTCUSDT"
INTERVAL = "30m"
INTERVAL_MS = 30 * 60 * 1000
PAGE_LIMIT = 1000
UPSERT_BATCH_SIZE = 500
COLUMNS = (
    "exchange,market_type,symbol,interval,open_time,close_time,"
    "open_price,high_price,low_price,close_price,base_volume,quote_volume,"
    "trade_count,taker_buy_base_volume,taker_buy_quote_volume,source"
)


def utc_ms(value: datetime) -> int:
    if value.tzinfo is None:
        raise ValueError("datetime must be timezone-aware")
    return int(value.astimezone(timezone.utc).timestamp() * 1000)


def decimal_string(value: Any, field: str) -> str:
    try:
        number = Decimal(str(value))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError(f"{field} is not numeric") from exc
    if not number.is_finite():
        raise ValueError(f"{field} is not finite")
    return format(number, "f")


def parse_kline(kline: list[Any], now_ms: int) -> dict[str, Any] | None:
    if not isinstance(kline, (list, tuple)) or len(kline) < 11:
        raise ValueError("Binance kline has fewer than 11 fields")
    open_ms = int(kline[0])
    close_ms = int(kline[6])
    if open_ms % INTERVAL_MS != 0:
        raise ValueError("candle open time is not aligned to 30m UTC grid")
    if close_ms < open_ms + INTERVAL_MS - 1 or close_ms > open_ms + INTERVAL_MS:
        raise ValueError("candle close timestamp is outside expected 30m boundary")
    if close_ms > now_ms:
        return None

    op = Decimal(decimal_string(kline[1], "open"))
    hi = Decimal(decimal_string(kline[2], "high"))
    lo = Decimal(decimal_string(kline[3], "low"))
    cl = Decimal(decimal_string(kline[4], "close"))
    base = Decimal(decimal_string(kline[5], "base volume"))
    quote = Decimal(decimal_string(kline[7], "quote volume"))
    trades = int(kline[8])
    taker_base = Decimal(decimal_string(kline[9], "taker buy base volume"))
    taker_quote = Decimal(decimal_string(kline[10], "taker buy quote volume"))
    if min(op, hi, lo, cl) <= 0 or min(base, quote, taker_base, taker_quote) < 0 or trades < 0:
        raise ValueError("non-positive OHLC or negative volume/trade count")
    if hi < max(op, cl, lo) or lo > min(op, cl, hi):
        raise ValueError("invalid OHLC relationship")

    return {
        "exchange": EXCHANGE,
        "market_type": MARKET_TYPE,
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "open_time": datetime.fromtimestamp(open_ms / 1000, timezone.utc).isoformat(),
        "close_time": datetime.fromtimestamp(close_ms / 1000, timezone.utc).isoformat(),
        "open_price": format(op, "f"),
        "high_price": format(hi, "f"),
        "low_price": format(lo, "f"),
        "close_price": format(cl, "f"),
        "base_volume": format(base, "f"),
        "quote_volume": format(quote, "f"),
        "trade_count": trades,
        "taker_buy_base_volume": format(taker_base, "f"),
        "taker_buy_quote_volume": format(taker_quote, "f"),
        "source": "BINANCE_PUBLIC_KLINES",
    }


def fetch_klines(
    start_ms: int,
    end_ms_exclusive: int,
    now_ms: int,
    session: Any = requests,
) -> list[dict[str, Any]]:
    """Fetch a bounded UTC range with pagination and conservative retries."""
    if start_ms >= end_ms_exclusive:
        raise ValueError("start must be earlier than exclusive end")
    cursor = start_ms
    collected: dict[int, dict[str, Any]] = {}
    api_url = API_URL
    while cursor < end_ms_exclusive and cursor <= now_ms:
        params = {
            "symbol": SYMBOL,
            "interval": INTERVAL,
            "startTime": cursor,
            "endTime": min(end_ms_exclusive - 1, now_ms),
            "limit": PAGE_LIMIT,
        }
        response = None
        for attempt in range(5):
            response = session.get(api_url, params=params, timeout=30)
            if response.status_code in (403, 451) and api_url != FALLBACK_API_URL:
                api_url = FALLBACK_API_URL
                print("Primary Binance API restricted (HTTP %s); switching to public market-data endpoint." % response.status_code)
                continue
            if response.status_code in (418, 429) or response.status_code >= 500:
                if attempt == 4:
                    response.raise_for_status()
                retry_after = response.headers.get("Retry-After")
                delay = float(retry_after) if retry_after else min(2 ** attempt, 16)
                time.sleep(delay)
                continue
            response.raise_for_status()
            break
        payload = response.json()
        if not isinstance(payload, list):
            raise ValueError(f"Unexpected Binance response: {payload}")
        if not payload:
            break
        last_open = None
        for raw in payload:
            record = parse_kline(raw, now_ms)
            open_ms = int(raw[0])
            last_open = open_ms if last_open is None else max(last_open, open_ms)
            if record is not None and start_ms <= open_ms < end_ms_exclusive:
                collected[open_ms] = record
        if last_open is None or last_open < cursor:
            raise RuntimeError("Binance pagination did not advance")
        cursor = last_open + INTERVAL_MS
        if len(payload) < PAGE_LIMIT:
            break
    return [collected[key] for key in sorted(collected)]


def persist_records(client: Any, records: list[dict[str, Any]], write: bool) -> int:
    if not write:
        return 0
    count = 0
    conflict = "exchange,market_type,symbol,interval,open_time"
    for offset in range(0, len(records), UPSERT_BATCH_SIZE):
        batch = records[offset:offset + UPSERT_BATCH_SIZE]
        client.table("binance_ohlc_history").upsert(
            batch, on_conflict=conflict
        ).execute()
        count += len(batch)
    return count


def build_range(args: argparse.Namespace, now: datetime) -> tuple[int, int]:
    now_utc = now.astimezone(timezone.utc)
    if args.start_date:
        start = datetime.combine(args.start_date, dt_time.min, tzinfo=timezone.utc)
        end_date = args.end_date or now_utc.date()
        if end_date < args.start_date:
            raise ValueError("--end-date cannot be earlier than --start-date")
        end = datetime.combine(end_date + timedelta(days=1), dt_time.min, tzinfo=timezone.utc)
    else:
        if args.lookback_hours < 1:
            raise ValueError("--lookback-hours must be at least 1")
        start = now_utc - timedelta(hours=args.lookback_hours)
        start_ms = (utc_ms(start) // INTERVAL_MS) * INTERVAL_MS
        return start_ms, utc_ms(now_utc)
    return utc_ms(start), min(utc_ms(end), utc_ms(now_utc))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--start-date", type=date.fromisoformat)
    mode.add_argument("--lookback-hours", type=int)
    parser.add_argument("--end-date", type=date.fromisoformat)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args(argv)
    if args.end_date and not args.start_date:
        parser.error("--end-date requires --start-date")
    if args.lookback_hours is not None and args.lookback_hours < 1:
        parser.error("--lookback-hours must be at least 1")

    start_ms, end_ms = build_range(args, datetime.now(timezone.utc))
    if start_ms >= end_ms:
        print("No closed-candle time range to fetch.")
        return 0

    print(
        f"Binance {SYMBOL} {INTERVAL} UTC range: "
        f"{datetime.fromtimestamp(start_ms / 1000, timezone.utc).isoformat()} "
        f"to {datetime.fromtimestamp(end_ms / 1000, timezone.utc).isoformat()} (exclusive)"
    )
    records = fetch_klines(start_ms, end_ms, utc_ms(datetime.now(timezone.utc)))
    opens = [datetime.fromisoformat(row["open_time"]) for row in records]
    gaps = sum(
        1 for left, right in zip(opens, opens[1:])
        if right - left != timedelta(milliseconds=INTERVAL_MS)
    )
    print(f"Fetched {len(records)} closed candles; internal gaps: {gaps}.")
    if records:
        print(f"First: {records[0]['open_time']} | Last: {records[-1]['open_time']}")
    if not args.write:
        print("READ-ONLY DRY RUN: no database writes.")
        return 0

    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        print("Missing Supabase secrets.", file=sys.stderr)
        return 1
    from supabase import create_client
    client = create_client(url, key)
    written = persist_records(client, records, write=True)
    print(f"WRITE ENABLED: upserted {written} validated Binance candles.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
