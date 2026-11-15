"""
signal_detector.py
Version: 1.0
Purpose: Detect and journal significant BTC AMT, GEX and Futures OI events.

Runs after the AMT, Futures OI and Options collectors. It reads existing
Supabase snapshots only; it does not call market-data APIs or change collectors.

Important:
- All event decisions use completed AMT candles.
- Futures and Options context is strictly as-of the event timestamp.
- Developing AMT states are never treated as confirmed state transitions.
- GEX breach confirmation requires the same expiry/side/strike wall on
  two consecutive completed candles.
- All events start with INFO severity.
"""

import json
import os
import sys
from datetime import datetime, timezone

from supabase import create_client


VERSION = "signal-detector-v1.0"

AMT_TABLE = "amt_snapshots"
FUTURES_TABLE = "futures_oi_snapshots"
OPTIONS_RUNS_TABLE = "options_collection_runs"
OPTIONS_DERIVED_TABLE = "options_derived_snapshots"
SIGNAL_TABLE = "system_signal_journal"

SYMBOL = "BTCUSDT"
TIMEFRAME = "30"

AMT_HISTORY_LIMIT = 1000
FUTURES_HISTORY_LIMIT = 120
OPTIONS_RUN_LIMIT = 120
OPTIONS_DERIVED_LIMIT = 1000

OI_SHOCK_THRESHOLD_PCT = 3.0
OI_MIN_INTERVAL_MINUTES = 25.0
OI_MAX_INTERVAL_MINUTES = 35.0

VALUE_MIGRATION_THRESHOLD_USD = 150.0
CANDLE_INTERVAL_SECONDS = 30 * 60
CANDLE_INTERVAL_TOLERANCE_SECONDS = 5.0

MAX_OPTIONS_AGE_MINUTES = 45.0
MAX_FUTURES_CONTEXT_AGE_MINUTES = 45.0
GEX_EVENT_RECENCY_MINUTES = 45.0


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
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def safe_float(value):
    try:
        if value is None:
            return None
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")):
            return None
        return result
    except (TypeError, ValueError):
        return None


def parse_json_object(value):
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, dict) else {}
        except (TypeError, ValueError, json.JSONDecodeError):
            return {}
    return {}


def is_valid_amt_row(row):
    state = row.get("confirmed_state")
    price = safe_float(row.get("close_price"))
    close_time = parse_timestamp(row.get("candle_close_time"))
    status = str(row.get("data_status") or "").upper()

    if not state or price is None or close_time is None:
        return False
    if status and status not in ("OK", "VALID"):
        return False
    return True


def are_consecutive_candles(previous, current):
    previous_open = parse_timestamp(previous.get("candle_open_time"))
    current_open = parse_timestamp(current.get("candle_open_time"))
    if previous_open is None or current_open is None:
        return False
    delta = (current_open - previous_open).total_seconds()
    return abs(delta - CANDLE_INTERVAL_SECONDS) <= CANDLE_INTERVAL_TOLERANCE_SECONDS


def load_amt_history(client, now):
    response = (
        client.table(AMT_TABLE)
        .select(
            "candle_open_time,candle_close_time,close_price,confirmed_state,"
            "developing_state,developing_poc,developing_vah,developing_val,data_status"
        )
        .eq("exchange", "BINANCE")
        .eq("symbol", SYMBOL)
        .eq("timeframe", TIMEFRAME)
        .lte("candle_close_time", iso_utc(now))
        .order("candle_close_time", desc=True)
        .limit(AMT_HISTORY_LIMIT)
        .execute()
    )

    rows = [row for row in (response.data or []) if is_valid_amt_row(row)]
    rows.sort(key=lambda row: parse_timestamp(row["candle_close_time"]))
    return rows


def load_futures_history(client, now):
    response = (
        client.table(FUTURES_TABLE)
        .select("created_at,exchange,oi_usd,status")
        .eq("exchange", "AGGREGATE")
        .lte("created_at", iso_utc(now))
        .order("created_at", desc=True)
        .limit(FUTURES_HISTORY_LIMIT)
        .execute()
    )

    rows = []
    for row in response.data or []:
        timestamp = parse_timestamp(row.get("created_at"))
        oi_value = safe_float(row.get("oi_usd"))
        if timestamp is None or oi_value is None or oi_value <= 0:
            continue

        status = parse_json_object(row.get("status"))
        contributors = status.get("coverage", {}).get("contributors")
        if not isinstance(contributors, list):
            contributors = status.get("oi_contributors", [])
        contributors = sorted({str(name).upper() for name in contributors if name})

        # Use the collector's source observation timestamp for event timing.
        # created_at is only the database insertion time; it is not a substitute
        # for source time when enforcing the historical as-of rule.
        observed_at = parse_timestamp(status.get("collected_at"))
        if observed_at is None:
            print(
                "[FUTURES] Skipping aggregate row without a reliable source "
                f"timestamp (created_at={iso_utc(timestamp)})."
            )
            continue

        rows.append({
            "created_at": timestamp,
            "observed_at": observed_at,
            "oi_usd": oi_value,
            "contributors": contributors,
            "status_payload": status,
        })

    rows.sort(key=lambda row: row["observed_at"])
    return rows


def load_options_derived(client):
    runs_response = (
        client.table(OPTIONS_RUNS_TABLE)
        .select("snapshot_id,status,underlying,completed_at")
        .eq("underlying", "BTC")
        .eq("status", "SUCCESS")
        .order("completed_at", desc=True)
        .limit(OPTIONS_RUN_LIMIT)
        .execute()
    )

    successful_ids = [
        row.get("snapshot_id")
        for row in (runs_response.data or [])
        if row.get("snapshot_id") and row.get("completed_at")
    ]
    if not successful_ids:
        return []

    derived_response = (
        client.table(OPTIONS_DERIVED_TABLE)
        .select(
            "snapshot_id,expiry_date,source_collected_at,underlying_spot_price,"
            "call_gex_wall_strike,put_gex_wall_strike,metric_status"
        )
        .in_("snapshot_id", successful_ids)
        .order("source_collected_at", desc=True)
        .limit(OPTIONS_DERIVED_LIMIT)
        .execute()
    )

    successful_id_set = set(successful_ids)
    rows = []
    for row in derived_response.data or []:
        if row.get("snapshot_id") not in successful_id_set:
            continue
        source_time = parse_timestamp(row.get("source_collected_at"))
        if source_time is None:
            continue
        rows.append({**row, "_source_time": source_time})

    rows.sort(key=lambda row: row["_source_time"], reverse=True)
    return rows


def valid_gex_row(row):
    status = parse_json_object(row.get("metric_status"))
    required = ("chain", "spot", "call_gex", "put_gex")
    if any(str(status.get(key, "")).upper() != "OK" for key in required):
        return False

    expiry = row.get("expiry_date")
    spot = safe_float(row.get("underlying_spot_price"))
    call_wall = safe_float(row.get("call_gex_wall_strike"))
    put_wall = safe_float(row.get("put_gex_wall_strike"))

    return bool(expiry and spot and call_wall and put_wall and put_wall < call_wall)


def options_walls_asof(options_rows, event_time):
    """
    Select the most recent successful options snapshot available at event_time,
    then select its nearest unexpired expiry. No later source observation is used.
    """
    eligible = [
        row for row in options_rows
        if row["_source_time"] <= event_time and valid_gex_row(row)
    ]
    if not eligible:
        return None

    newest_source_time = eligible[0]["_source_time"]
    age_minutes = (event_time - newest_source_time).total_seconds() / 60.0
    if age_minutes < 0 or age_minutes > MAX_OPTIONS_AGE_MINUTES:
        return None

    newest_snapshot_id = eligible[0]["snapshot_id"]
    same_snapshot = [
        row for row in eligible
        if row["snapshot_id"] == newest_snapshot_id
        and row["_source_time"] <= event_time
        and str(row["expiry_date"]) >= event_time.date().isoformat()
    ]
    if not same_snapshot:
        return None

    same_snapshot.sort(key=lambda row: str(row["expiry_date"]))
    selected = same_snapshot[0]

    return {
        "snapshot_id": selected["snapshot_id"],
        "source_collected_at": selected["_source_time"],
        "expiry_date": str(selected["expiry_date"]),
        "spot": safe_float(selected.get("underlying_spot_price")),
        "call_wall": safe_float(selected.get("call_gex_wall_strike")),
        "put_wall": safe_float(selected.get("put_gex_wall_strike")),
    }


def futures_oi_asof(futures_rows, event_time):
    eligible = [row for row in futures_rows if row["observed_at"] <= event_time]
    if not eligible:
        return None

    selected = eligible[-1]
    age_minutes = (event_time - selected["observed_at"]).total_seconds() / 60.0
    if age_minutes < 0 or age_minutes > MAX_FUTURES_CONTEXT_AGE_MINUTES:
        return None
    return selected


def amt_row_asof(amt_rows, event_time):
    eligible = [
        row for row in amt_rows
        if parse_timestamp(row["candle_close_time"]) <= event_time
    ]
    return eligible[-1] if eligible else None


def build_event_row(event_type, event_time, event_key, market_row,
                    futures_rows, options_rows, event_details):
    if market_row is None:
        return None

    event_dt = parse_timestamp(event_time)
    if event_dt is None:
        return None

    amt_close_time = parse_timestamp(market_row.get("candle_close_time"))
    snapshot_time = min(event_dt, amt_close_time) if amt_close_time else event_dt

    futures = futures_oi_asof(futures_rows, snapshot_time)
    walls = options_walls_asof(options_rows, snapshot_time)

    return {
        "event_timestamp": iso_utc(event_dt),
        "event_type": event_type,
        "event_key": event_key,
        "severity": "INFO",
        "price": safe_float(market_row.get("close_price")),
        "amt_state": market_row.get("confirmed_state"),
        "dev_poc": safe_float(market_row.get("developing_poc")),
        "dev_vah": safe_float(market_row.get("developing_vah")),
        "dev_val": safe_float(market_row.get("developing_val")),
        "call_wall": walls["call_wall"] if walls else None,
        "put_wall": walls["put_wall"] if walls else None,
        "oi_usd": futures["oi_usd"] if futures else None,
        "event_details": event_details,
        "detection_version": VERSION,
    }


def detect_latest_amt_events(amt_rows, futures_rows, options_rows):
    events = []
    if len(amt_rows) < 2:
        print("[AMT] Not enough completed candles to compare.")
        return events

    previous, current = amt_rows[-2], amt_rows[-1]
    if not are_consecutive_candles(previous, current):
        print("[AMT] Latest records are not consecutive 30-minute candles; comparisons skipped.")
        return events

    previous_close = parse_timestamp(previous["candle_close_time"])
    current_close = parse_timestamp(current["candle_close_time"])

    previous_state = previous.get("confirmed_state")
    current_state = current.get("confirmed_state")

    if previous_state and current_state and previous_state != current_state:
        event = build_event_row(
            "AMT_STATE_CHANGE",
            current_close,
            f"{previous_state}->{current_state}",
            current,
            futures_rows,
            options_rows,
            {
                "previous_state": previous_state,
                "new_state": current_state,
                "previous_candle_close": iso_utc(previous_close),
                "confirmation_candle_close": iso_utc(current_close),
                "state_source": "confirmed_state",
            },
        )
        if event:
            events.append(event)

    previous_poc = safe_float(previous.get("developing_poc"))
    current_poc = safe_float(current.get("developing_poc"))

    if previous_poc is not None and current_poc is not None:
        movement = current_poc - previous_poc
        if abs(movement) > VALUE_MIGRATION_THRESHOLD_USD:
            event = build_event_row(
                "VALUE_MIGRATION",
                current_close,
                "DEVELOPING_POC",
                current,
                futures_rows,
                options_rows,
                {
                    "previous_poc": previous_poc,
                    "current_poc": current_poc,
                    "movement_usd": movement,
                    "absolute_movement_usd": abs(movement),
                },
            )
            if event:
                events.append(event)

    return events


def detect_oi_shock(futures_rows, amt_rows, options_rows):
    if len(futures_rows) < 2:
        print("[OI] Not enough aggregate OI snapshots.")
        return None

    current = futures_rows[-1]
    current_time = current["observed_at"]
    if not current["contributors"]:
        print("[OI] Latest aggregate has no contributor list; OI shock skipped.")
        return None

    eligible_baselines = []
    for previous in futures_rows[:-1]:
        age_minutes = (current_time - previous["observed_at"]).total_seconds() / 60.0
        if OI_MIN_INTERVAL_MINUTES <= age_minutes <= OI_MAX_INTERVAL_MINUTES:
            if previous["contributors"] == current["contributors"]:
                eligible_baselines.append((abs(age_minutes - 30.0), previous, age_minutes))

    if not eligible_baselines:
        mismatched = any(
            OI_MIN_INTERVAL_MINUTES
            <= (current_time - row["created_at"]).total_seconds() / 60.0
            <= OI_MAX_INTERVAL_MINUTES
            for row in futures_rows[:-1]
        )
        reason = "exchange coverage differs" if mismatched else "no 25-35 minute baseline"
        print(f"[OI] Shock check skipped: {reason}.")
        return None

    eligible_baselines.sort(key=lambda item: item[0])
    _, previous, age_minutes = eligible_baselines[0]

    if previous["oi_usd"] <= 0:
        print("[OI] Invalid baseline OI; shock skipped.")
        return None

    change_pct = ((current["oi_usd"] / previous["oi_usd"]) - 1.0) * 100.0
    if abs(change_pct) <= OI_SHOCK_THRESHOLD_PCT:
        print(f"[OI] No shock: {change_pct:+.2f}% (threshold is strictly greater than 3.0%).")
        return None

    market_row = amt_row_asof(amt_rows, current_time)
    if market_row is None:
        print("[OI] No AMT candle available as-of the OI observation; snapshot context unavailable.")
        return None

    event = build_event_row(
        "OI_SHOCK",
        current_time,
        "AGGREGATE:" + ",".join(current["contributors"]),
        market_row,
        futures_rows,
        options_rows,
        {
            "previous_oi_usd": previous["oi_usd"],
            "current_oi_usd": current["oi_usd"],
            "change_pct": change_pct,
            "change_usd": current["oi_usd"] - previous["oi_usd"],
            "baseline_timestamp": iso_utc(previous["observed_at"]),
            "current_timestamp": iso_utc(current_time),
            "baseline_age_minutes": round(age_minutes, 2),
            "exchange_coverage": current["contributors"],
            "coverage_match": True,
        },
    )
    return event


def detect_latest_gex_breach(amt_rows, options_rows, futures_rows, now):
    """
    Reconstruct the wall-breach/re-arm state from recent completed candles.
    Only a confirmation on a recent candle is emitted; older history is used
    to establish whether price had returned inside the walls.
    """
    if len(amt_rows) < 3:
        print("[GEX] Not enough completed candles to establish breach/re-arm state.")
        return None

    armed = False
    candidate = None
    confirmed_event = None

    for row in amt_rows:
        candle_close = parse_timestamp(row.get("candle_close_time"))
        price = safe_float(row.get("close_price"))
        if candle_close is None or price is None:
            candidate = None
            continue

        walls = options_walls_asof(options_rows, candle_close)
        if walls is None:
            # Without a valid wall observation we cannot establish either
            # an outside breach or an inside-wall re-arm. Require a fresh,
            # observed inside-wall close before starting a new breach cycle.
            armed = False
            candidate = None
            continue

        call_wall = walls["call_wall"]
        put_wall = walls["put_wall"]
        if put_wall >= call_wall:
            candidate = None
            continue

        inside_walls = put_wall <= price <= call_wall
        if inside_walls:
            armed = True
            candidate = None
            continue

        if not armed:
            candidate = None
            continue

        if price > call_wall:
            side = "CALL"
            wall_level = call_wall
        elif price < put_wall:
            side = "PUT"
            wall_level = put_wall
        else:
            candidate = None
            continue

        wall_identity = f"{side}:{walls['expiry_date']}:{wall_level:.8f}"
        current_candidate = {
            "identity": wall_identity,
            "side": side,
            "wall_level": wall_level,
            "expiry_date": walls["expiry_date"],
            "first_close": candle_close,
            "first_price": price,
            "walls": walls,
        }

        if candidate and candidate["identity"] == wall_identity:
            interval = (candle_close - candidate["first_close"]).total_seconds()
            if abs(interval - CANDLE_INTERVAL_SECONDS) <= CANDLE_INTERVAL_TOLERANCE_SECONDS:
                age_minutes = (now - candle_close).total_seconds() / 60.0
                if 0 <= age_minutes <= GEX_EVENT_RECENCY_MINUTES:
                    event_market_row = row
                    event = build_event_row(
                        "GEX_WALL_BREACH",
                        candle_close,
                        wall_identity,
                        event_market_row,
                        futures_rows,
                        options_rows,
                        {
                            "wall_side": side,
                            "wall_identity": wall_identity,
                            "expiry_date": walls["expiry_date"],
                            "breached_level": wall_level,
                            "first_close_timestamp": iso_utc(candidate["first_close"]),
                            "first_close_price": candidate["first_price"],
                            "confirmation_close_timestamp": iso_utc(candle_close),
                            "confirmation_close_price": price,
                            "wall_source_snapshot_id": walls["snapshot_id"],
                            "wall_source_collected_at": iso_utc(walls["source_collected_at"]),
                            "consecutive_confirmation": True,
                            "rearm_rule": "A completed candle must close inside both walls before another breach can be recorded.",
                        },
                    )
                    confirmed_event = event
                armed = False
                candidate = None
                continue

        candidate = current_candidate

    if confirmed_event is None:
        print("[GEX] No newly confirmed two-candle wall breach on a recent candle.")
    return confirmed_event


def insert_event(client, event):
    response = (
        client.table(SIGNAL_TABLE)
        .upsert(
            event,
            on_conflict="event_type,event_timestamp,event_key",
            ignore_duplicates=True,
        )
        .execute()
    )

    returned = getattr(response, "data", None) or []
    if returned:
        print(
            "[SIGNAL LOGGED] "
            f"{event['event_type']} | {event['event_timestamp']} | "
            f"key={event['event_key']} | price={event.get('price')}"
        )
    else:
        print(
            "[DUPLICATE / ALREADY RECORDED] "
            f"{event['event_type']} | {event['event_timestamp']} | "
            f"key={event['event_key']}"
        )


def main():
    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url or not service_role_key:
        print("ERROR: SUPABASE_URL and SUPABASE_SERVICE_ROLE_KEY are required.", file=sys.stderr)
        return 1

    now = utc_now()
    print("BTC System Signal Detector")
    print(f"Version: {VERSION}")
    print(f"Started: {iso_utc(now)}")

    try:
        client = create_client(supabase_url, service_role_key)

        # Fail early with an explicit message if the approved table has not
        # yet been created or is not accessible to the service-role client.
        try:
            client.table(SIGNAL_TABLE).select("id").limit(1).execute()
        except Exception as exc:
            raise RuntimeError(
                f"Required table public.{SIGNAL_TABLE} is missing or inaccessible. "
                "Create the approved table and verify service_role grants before running this detector. "
                f"Original error: {exc}"
            )

        print("[1/4] Loading completed AMT candles...")
        amt_rows = load_amt_history(client, now)
        print(f"      Valid completed AMT candles loaded: {len(amt_rows)}")

        print("[2/4] Loading aggregate Futures OI history...")
        futures_rows = load_futures_history(client, now)
        print(f"      Valid aggregate OI observations loaded: {len(futures_rows)}")

        print("[3/4] Loading successful Options snapshots...")
        options_rows = load_options_derived(client)
        print(f"      Valid Options derived rows loaded: {len(options_rows)}")

        events = []

        print("[4/4] Evaluating event rules...")
        events.extend(detect_latest_amt_events(amt_rows, futures_rows, options_rows))

        oi_event = detect_oi_shock(futures_rows, amt_rows, options_rows)
        if oi_event:
            events.append(oi_event)

        gex_event = detect_latest_gex_breach(amt_rows, options_rows, futures_rows, now)
        if gex_event:
            events.append(gex_event)

        if not events:
            print("No qualifying system signals detected.")
            print(f"Finished: {iso_utc(utc_now())}")
            return 0

        print(f"Qualifying events: {len(events)}")
        for event in events:
            insert_event(client, event)

        print(f"Signal detection completed. Events evaluated: {len(events)}")
        print(f"Finished: {iso_utc(utc_now())}")
        return 0

    except Exception as exc:
        print(
            f"SIGNAL DETECTOR FAILED: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
