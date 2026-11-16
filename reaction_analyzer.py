"""
reaction_analyzer.py
Version: reaction-v1.0

Daily, non-directional market-reaction analysis for system_signal_journal.
Only Binance Spot BTCUSDT 30m candles strictly after the event timestamp
and fully closed by event_timestamp + 4 hours are eligible. Exactly eight
contiguous completed candles are required. The event candle is excluded.
Original signal journal rows are read-only.
"""

import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from supabase import create_client

VERSION = "reaction-v1.0"
SIGNAL_TABLE = "system_signal_journal"
REACTION_TABLE = "signal_reaction_analysis"
BINANCE_BASE_URL = os.getenv("BINANCE_BASE_URL", "https://api.binance.com")
SYMBOL = "BTCUSDT"
INTERVAL = "30m"
CANDLE_MS = 30 * 60 * 1000
CANDLE_CLOSE_OFFSET_MS = CANDLE_MS - 1
EXPECTED_CANDLES = 8
ANALYSIS_WINDOW = timedelta(hours=4)
PAGE_SIZE = 500
HTTP_TIMEOUT_SECONDS = 20
HTTP_RETRIES = 3


def utc_now():
    return datetime.now(timezone.utc)


def parse_timestamp(value):
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except (TypeError, ValueError):
        return None


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def to_epoch_ms(value):
    return int(value.timestamp() * 1000)


def safe_float(value):
    try:
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return result
    except (TypeError, ValueError):
        return None


def fetch_json(url):
    last_error = None
    for attempt in range(1, HTTP_RETRIES + 1):
        try:
            request = Request(url, headers={
                "User-Agent": "BTC-Market-Reaction-Analyzer/1.0",
                "Accept": "application/json",
            })
            with urlopen(request, timeout=HTTP_TIMEOUT_SECONDS) as response:
                if response.status != 200:
                    raise RuntimeError(f"Binance HTTP status {response.status}")
                return json.loads(response.read().decode("utf-8"))
        except HTTPError as exc:
            last_error = f"HTTP {exc.code}: {exc.reason}"
            if exc.code not in (418, 429, 500, 502, 503, 504):
                break
        except (URLError, TimeoutError, json.JSONDecodeError, RuntimeError) as exc:
            last_error = str(exc)
        if attempt < HTTP_RETRIES:
            time.sleep(min(2 ** attempt, 8))
    raise RuntimeError(f"Binance request failed after {HTTP_RETRIES} attempts: {last_error}")


def fetch_post_event_candles(event_time):
    """Fetch only bars wholly inside (event_time, event_time + 4h]."""
    window_end = event_time + ANALYSIS_WINDOW
    start_ms = to_epoch_ms(event_time) + 1
    end_ms = to_epoch_ms(window_end)
    params = urlencode({
        "symbol": SYMBOL,
        "interval": INTERVAL,
        "startTime": start_ms,
        "endTime": end_ms,
        "limit": EXPECTED_CANDLES,
    })
    raw = fetch_json(f"{BINANCE_BASE_URL}/api/v3/klines?{params}")

    candles = []
    event_ms = to_epoch_ms(event_time)
    for item in raw:
        if not isinstance(item, list) or len(item) < 7:
            continue
        try:
            candle = {
                "open_time_ms": int(item[0]),
                "open": safe_float(item[1]),
                "high": safe_float(item[2]),
                "low": safe_float(item[3]),
                "close": safe_float(item[4]),
                "close_time_ms": int(item[6]),
            }
        except (TypeError, ValueError, IndexError):
            continue

        if any(candle[key] is None for key in ("open", "high", "low", "close")):
            continue
        if candle["open_time_ms"] <= event_ms or candle["close_time_ms"] > end_ms:
            continue
        if candle["close_time_ms"] - candle["open_time_ms"] != CANDLE_CLOSE_OFFSET_MS:
            continue
        if candle["high"] < max(candle["open"], candle["close"], candle["low"]):
            continue
        if candle["low"] > min(candle["open"], candle["close"], candle["high"]):
            continue
        candles.append(candle)

    candles.sort(key=lambda row: row["open_time_ms"])
    unique = {row["open_time_ms"]: row for row in candles}
    return list(unique.values())[:EXPECTED_CANDLES], window_end


def validate_eight_candles(candles, event_time, window_end):
    if len(candles) != EXPECTED_CANDLES:
        return False, f"Expected 8 complete candles; received {len(candles)}."

    event_ms = to_epoch_ms(event_time)
    deadline_ms = to_epoch_ms(window_end)
    for index, candle in enumerate(candles):
        if candle["open_time_ms"] <= event_ms:
            return False, f"Candle {index + 1} opened at/before the event."
        if candle["close_time_ms"] > deadline_ms:
            return False, f"Candle {index + 1} closed after the four-hour deadline."
        if index and candle["open_time_ms"] - candles[index - 1]["open_time_ms"] != CANDLE_MS:
            return False, f"Missing/non-contiguous candle before candle {index + 1}."
        if candle["close_time_ms"] != candle["open_time_ms"] + CANDLE_CLOSE_OFFSET_MS:
            return False, "A candle is not a complete standard 30m bar."
    return True, None


def calculate_reaction(anchor_price, candles, window_end):
    if anchor_price is None or anchor_price <= 0:
        raise ValueError("Signal anchor price is missing or non-positive.")

    # Candle 1, 2, 4 and 8 close at the 30m, 1h, 2h and 4h horizons
    # when the event is aligned to a 30m candle-close boundary.
    horizon_closes = [candles[index]["close"] for index in (0, 1, 3, 7)]
    window_high = max(row["high"] for row in candles)
    window_low = min(row["low"] for row in candles)
    # Excursions are floored at zero on the non-occurring side.
    # max_down remains signed (zero or negative) for neutral reporting.
    max_up_usd = max(0.0, window_high - anchor_price)
    max_down_usd = min(0.0, window_low - anchor_price)
    first_open = datetime.fromtimestamp(candles[0]["open_time_ms"] / 1000, tz=timezone.utc)

    return {
        "window_start": iso_utc(first_open),
        "window_end": iso_utc(window_end),
        "price_after_30m": horizon_closes[0],
        "price_after_1h": horizon_closes[1],
        "price_after_2h": horizon_closes[2],
        "price_after_4h": horizon_closes[3],
        "window_high": window_high,
        "window_low": window_low,
        "max_up_excursion_usd": max_up_usd,
        "max_down_excursion_usd": max_down_usd,
        "max_up_excursion_pct": max_up_usd / anchor_price * 100.0,
        "max_down_excursion_pct": max_down_usd / anchor_price * 100.0,
        "candles_expected": EXPECTED_CANDLES,
        "candles_received": len(candles),
        "analysis_status": "COMPLETE",
        "data_source": "BINANCE_SPOT_API:BTCUSDT:30m",
        "error_message": None,
        "analyzed_at": iso_utc(utc_now()),
    }


def build_incomplete_result(candles, window_end, reason):
    first_open = None
    if candles:
        first_open = datetime.fromtimestamp(candles[0]["open_time_ms"] / 1000, tz=timezone.utc)
    return {
        "window_start": iso_utc(first_open) if first_open else None,
        "window_end": iso_utc(window_end),
        "price_after_30m": None,
        "price_after_1h": None,
        "price_after_2h": None,
        "price_after_4h": None,
        "window_high": None,
        "window_low": None,
        "max_up_excursion_usd": None,
        "max_down_excursion_usd": None,
        "max_up_excursion_pct": None,
        "max_down_excursion_pct": None,
        "candles_expected": EXPECTED_CANDLES,
        "candles_received": len(candles),
        "analysis_status": "INCOMPLETE",
        "data_source": "BINANCE_SPOT_API:BTCUSDT:30m",
        "error_message": reason[:1000],
        "analyzed_at": iso_utc(utc_now()),
    }


def load_existing_complete_signal_ids(client):
    # COMPLETE rows are final for this analysis version.
    # INCOMPLETE rows are retried on the next daily run so a temporary
    # exchange-data gap cannot permanently suppress a valid analysis.
    existing = set()
    offset = 0
    while True:
        response = (
            client.table(REACTION_TABLE)
            .select("signal_id,analysis_status")
            .eq("analysis_version", VERSION)
            .eq("analysis_status", "COMPLETE")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows = response.data or []
        existing.update(
            int(row["signal_id"])
            for row in rows
            if row.get("signal_id") is not None
        )
        if len(rows) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return existing


def load_eligible_signals(client, cutoff):
    signals = []
    offset = 0
    while True:
        response = (
            client.table(SIGNAL_TABLE)
            .select("id,event_timestamp,event_type,price")
            .lte("event_timestamp", iso_utc(cutoff))
            .order("event_timestamp", desc=False)
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        batch = response.data or []
        signals.extend(batch)
        if len(batch) < PAGE_SIZE:
            break
        offset += PAGE_SIZE
    return signals


def save_result(client, signal_id, anchor_price, result):
    payload = {
        "signal_id": signal_id,
        "analysis_version": VERSION,
        "anchor_price": anchor_price,
        **result,
    }
    client.table(REACTION_TABLE).upsert(
        payload, on_conflict="signal_id,analysis_version"
    ).execute()


def main():
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not service_key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")

    client = create_client(supabase_url, service_key)
    run_time = utc_now()
    cutoff = run_time - ANALYSIS_WINDOW

    print(f"[START] {iso_utc(run_time)} | version={VERSION}")
    print(f"[FILTER] event_timestamp <= {iso_utc(cutoff)}")

    existing_ids = load_existing_complete_signal_ids(client)
    eligible = load_eligible_signals(client, cutoff)
    pending = [row for row in eligible if int(row["id"]) not in existing_ids]

    print(f"[QUEUE] eligible={len(eligible)} already_complete={len(existing_ids)} pending={len(pending)}")
    complete = incomplete = skipped = failed = 0

    for signal in pending:
        signal_id = int(signal["id"])
        event_time = parse_timestamp(signal.get("event_timestamp"))
        anchor_price = safe_float(signal.get("price"))
        event_type = signal.get("event_type", "UNKNOWN")

        if event_time is None or anchor_price is None or anchor_price <= 0:
            print(f"[SKIP] id={signal_id}: invalid event timestamp or anchor price.")
            skipped += 1
            continue

        try:
            candles, window_end = fetch_post_event_candles(event_time)
            valid, reason = validate_eight_candles(candles, event_time, window_end)
            if valid:
                result = calculate_reaction(anchor_price, candles, window_end)
                save_result(client, signal_id, anchor_price, result)
                complete += 1
                print(
                    f"[COMPLETE] id={signal_id} type={event_type} "
                    f"up={result['max_up_excursion_pct']:+.4f}% "
                    f"down={result['max_down_excursion_pct']:+.4f}%"
                )
            else:
                result = build_incomplete_result(candles, window_end, reason)
                save_result(client, signal_id, anchor_price, result)
                incomplete += 1
                print(f"[INCOMPLETE] id={signal_id} type={event_type}: {reason}")
        except Exception as exc:
            # Transient failures are not persisted; the signal remains retryable.
            failed += 1
            print(f"[ERROR] id={signal_id}: {exc}", file=sys.stderr)

    print(
        f"[SUMMARY] complete={complete} incomplete={incomplete} "
        f"skipped={skipped} failed_retryable={failed}"
    )
    print("[END] Reaction analysis finished.")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"[FATAL] {exc}", file=sys.stderr)
        sys.exit(1)
