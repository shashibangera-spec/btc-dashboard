import os
import json
import math
import requests
from datetime import datetime, timedelta, timezone
from supabase import create_client

SUPABASE_URL = os.environ.get("SUPABASE_URL")
SUPABASE_SERVICE_ROLE_KEY = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
TELEGRAM_CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID")

if not all([SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID]):
    print("Missing environment variables.")
    exit(1)

supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
IST = timezone(timedelta(hours=5, minutes=30))

# Must stay synchronized with the CHECK constraint in the Supabase migration.
ALLOWED_EVENT_TYPES = frozenset({
    "AMT_STATE_CHANGE",
    "AMT_PROFILE_INITIALIZATION",
    "GEX_WALL_BREACH",
    "OI_SHOCK",
    "TEST_ALERT",
})

# Final merge commit must replace this marker with its UTC commit timestamp.
# NOTE: Update this to the exact current UTC time before committing the final merge.
GEX_ALERT_CUTOVER_UTC = "2026-11-16T15:00:00Z"

def parse_json_safely(data):
    if isinstance(data, str):
        try:
            return json.loads(data)
        except:
            return {}
    return data if isinstance(data, dict) else {}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "HTML",
    }
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            message_id = res.json().get("result", {}).get("message_id")
            if isinstance(message_id, int) and message_id > 0:
                return True, message_id
            return False, "Telegram returned HTTP 200 without a valid message_id"
        else:
            return False, res.text
    except Exception as e:
        return False, str(e)

def check_event_exists(event_key):
    res = (
        supabase.table("telegram_alert_events")
        .select("event_key,delivery_status,telegram_message_id")
        .eq("event_key", event_key)
        .execute()
    )
    # Only a confirmed Telegram delivery suppresses a retry.
    return any(
        row.get("delivery_status") == "SENT"
        and isinstance(row.get("telegram_message_id"), int)
        and row.get("telegram_message_id") > 0
        for row in res.data
    )

def begin_event_delivery(alert):
    """Durably create/update an outbox row as PENDING before attempting Telegram."""
    event_type = alert.get("type")
    if event_type not in ALLOWED_EVENT_TYPES:
        raise ValueError(f"Unsupported Telegram outbox event_type: {event_type!r}")
    key = alert["key"]
    existing = (
        supabase.table("telegram_alert_events")
        .select("attempt_count,delivery_status,telegram_message_id")
        .eq("event_key", key)
        .limit(1)
        .execute()
    )
    rows = existing.data or []
    if rows and rows[0].get("delivery_status") == "SENT" and rows[0].get("telegram_message_id"):
        return False
    attempts = int(rows[0].get("attempt_count") or 0) if rows else 0
    record = {
        "event_key": key,
        "event_type": alert["type"],
        "event_time": alert["time"],
        "payload": alert.get("payload", {}),
        "delivery_status": "PENDING",
        "telegram_message_id": None,
        "sent_at": None,
        "attempt_count": attempts + 1,
        "last_error": None,
    }
    # This write must succeed before any network send is attempted.
    supabase.table("telegram_alert_events").upsert(
        record, on_conflict="event_key"
    ).execute()
    return True


def finish_event_delivery(alert, status, message_id=None, error=None):
    """Finalize the already-persisted outbox attempt."""
    if status not in ("SENT", "FAILED"):
        raise ValueError("delivery status must be SENT or FAILED")
    record = {
        "delivery_status": status,
        "telegram_message_id": message_id if status == "SENT" else None,
        "sent_at": datetime.now(timezone.utc).isoformat() if status == "SENT" else None,
        "last_error": str(error)[:1000] if error else None,
    }
    # PostgREST may return an empty representation for a successful UPDATE;
    # execute() raises on database errors, so do not mistake an empty response
    # body for a failed status transition.
    (
        supabase.table("telegram_alert_events")
        .update(record)
        .eq("event_key", alert["key"])
        .execute()
    )


def log_event(event_key, event_type, event_time, payload, status, message_id=None):
    """Compatibility helper for legacy callers; writes through the outbox lifecycle."""
    alert = {"key": event_key, "type": event_type, "time": event_time, "payload": payload}
    try:
        if not begin_event_delivery(alert):
            return
        finish_event_delivery(
            alert, status,
            message_id=message_id if status == "SENT" else None,
            error=None if status == "SENT" else "Delivery failed",
        )
    except Exception as exc:
        print("Failed to persist event lifecycle ({}): {}".format(event_key, exc))

def evaluate_amt_transition():
    # Read a bounded window so a delayed workflow can still detect the first candle
    # of the current profile instead of missing the boundary after later candles arrive.
    res = (
        supabase.table("amt_snapshots")
        .select("*")
        .eq("data_status", "VALID")
        .order("candle_close_time", desc=True)
        .limit(200)
        .execute()
    )
    rows = res.data or []
    if len(rows) < 2:
        return None

    curr = rows[0]
    curr_profile = curr.get("profile_id")
    if not curr_profile:
        print("AMT alert skipped: latest profile_id is missing; refusing to infer a profile boundary.")
        return None

    # Profile IDs are expected to be monotonic by candle time. Locate the first
    # row of the latest profile, even when this workflow runs several candles late.
    boundary_index = next(
        (index for index in range(len(rows) - 1)
         if rows[index].get("profile_id") == curr_profile
         and rows[index + 1].get("profile_id") != curr_profile),
        None,
    )
    if boundary_index is not None:
        first_profile_candle = rows[boundary_index]
        previous_profile = rows[boundary_index + 1].get("profile_id")
        if not previous_profile:
            print("AMT alert skipped: preceding profile_id is missing; refusing to infer a profile boundary.")
            return None
        event_key = (
            f"AMT_PROFILE_INITIALIZATION_{curr_profile}_"
            f"{first_profile_candle['candle_close_time']}"
        )
        # A delivered initialization is a one-off event. After it is recorded,
        # continue evaluating the newest intraday transition on every run.
        if not check_event_exists(event_key):
            initial_state = first_profile_candle.get("confirmed_state") or "STATE_UNAVAILABLE"
            close_price = first_profile_candle.get("close_price")
            try:
                close_text = f"USD {float(close_price):,.0f}" if close_price is not None else "Unavailable"
            except (TypeError, ValueError):
                close_text = "Unavailable"
            msg = (
                "🆕 AMT NEW PROFILE / STATE INITIALIZATION\n\n"
                f"Profile: {curr_profile}\n"
                f"Initial confirmed state: {initial_state}\n"
                f"Candle: {first_profile_candle.get('candle_open_time', 'Unavailable')}\n"
                f"Close: {close_text}"
            )
            payload = {
                "profile_id": curr_profile,
                "previous_profile_id": previous_profile,
                "initial_state": initial_state,
                "candle_open_time": first_profile_candle.get("candle_open_time"),
                "candle_close_time": first_profile_candle.get("candle_close_time"),
                "close_price": close_price,
            }
            return {
                "key": event_key,
                "type": "AMT_PROFILE_INITIALIZATION",
                "time": first_profile_candle["candle_close_time"],
                "msg": msg,
                "payload": payload,
            }

    # Once initialization is already delivered (or no boundary is in the window),
    # preserve the ordinary latest-candle same-profile transition behavior.
    prev = rows[1]
    if prev.get("profile_id") != curr_profile:
        return None
    curr_state = curr["confirmed_state"]
    prev_state = prev["confirmed_state"]
    if curr_state != prev_state:
        event_key = f"AMT_STATE_CHANGE_{curr['candle_close_time']}"
        if check_event_exists(event_key):
            return None
        raw_price = curr.get("close_price")
        try:
            close_price = float(raw_price)
            price_text = f"${close_price:,.0f}"
        except (TypeError, ValueError):
            close_price = None
            price_text = "Unavailable"

        if curr_state == "DISCOVERY_CONFIRMED_BELOW":
            msg = (
                "🔻 <b>BREAKDOWN CONFIRMED</b>\n"
                f"<b>Price:</b> {price_text}\n"
                "<b>What happened:</b> Price broke below the daily value area.\n"
                "<b>Next Step:</b> Watch for a retest of the VAL. Do not chase the low."
            )
        elif curr_state == "REENTRY_CONFIRMED":
            msg = (
                "↩️ <b>FAILED BREAKDOWN (Returned Inside Value)</b>\n"
                f"<b>Price:</b> {price_text}\n"
                "<b>What happened:</b> Sellers failed. Price reclaimed the value area.\n"
                "<b>Next Step:</b> Watch for a pullback to hold above the VAL for a long rotation."
            )
        else:
            msg = (
                f"🚨 AMT STRUCTURAL CHANGE\n\n{prev_state} → {curr_state}"
                f"\nCandle: {curr['candle_open_time']}\nClose: {price_text}"
            )
        return {
            "key": event_key,
            "type": "AMT_STATE_CHANGE",
            "time": curr["candle_close_time"],
            "msg": msg,
            "payload": curr,
        }
    return None
def parse_utc_timestamp(value):
    if not isinstance(value, str) or not value.strip():
        raise ValueError("timestamp is missing")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def validate_gex_event_details(details):
    """Validate the detector's complete GEX_WALL_BREACH evidence payload."""
    if isinstance(details, str):
        try:
            details = json.loads(details)
        except (TypeError, ValueError) as exc:
            raise ValueError("event_details is not valid JSON") from exc
    if not isinstance(details, dict):
        raise ValueError("event_details must be an object")
    required = (
        "wall_side", "wall_identity", "expiry_date", "breached_level",
        "first_close_timestamp", "first_close_price",
        "confirmation_close_timestamp", "confirmation_close_price",
        "wall_source_snapshot_id", "wall_source_collected_at",
        "consecutive_confirmation", "rearm_rule",
    )
    missing = [key for key in required if key not in details or details[key] is None]
    if missing:
        raise ValueError("missing required event_details keys: " + ", ".join(missing))
    normalized = dict(details)
    if normalized["wall_side"] not in ("CALL", "PUT"):
        raise ValueError("wall_side must be CALL or PUT")
    for key in ("wall_identity", "wall_source_snapshot_id", "rearm_rule"):
        if not isinstance(normalized[key], str) or not normalized[key].strip():
            raise ValueError(f"{key} must be a non-empty string")
    try:
        expiry = datetime.strptime(str(normalized["expiry_date"]), "%Y-%m-%d").date()
    except (TypeError, ValueError) as exc:
        raise ValueError("expiry_date must be YYYY-MM-DD") from exc
    normalized["expiry_date"] = expiry.isoformat()
    for key in ("breached_level", "first_close_price", "confirmation_close_price"):
        value = normalized[key]
        if isinstance(value, bool):
            raise ValueError(f"{key} must be a finite number")
        try:
            number = float(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{key} must be a finite number") from exc
        if not math.isfinite(number):
            raise ValueError(f"{key} must be a finite number")
        normalized[key] = number
    for key in ("first_close_timestamp", "confirmation_close_timestamp", "wall_source_collected_at"):
        normalized[key] = parse_utc_timestamp(normalized[key]).isoformat()
    if normalized["consecutive_confirmation"] is not True:
        raise ValueError("consecutive_confirmation must be exactly true")
    return normalized


def gex_row_is_eligible(row, cutover_utc):
    """Pure eligibility gate; detected_at controls cutover, event_timestamp is market-event time."""
    if not isinstance(row, dict) or row.get("event_type") != "GEX_WALL_BREACH" or row.get("id") is None:
        return False
    try:
        detected_at = parse_utc_timestamp(row.get("detected_at"))
        cutover = parse_utc_timestamp(cutover_utc)
        parse_utc_timestamp(row.get("event_timestamp"))
        validate_gex_event_details(row.get("event_details"))
    except (TypeError, ValueError):
        return False
    return detected_at >= cutover


def build_gex_alert(row):
    details = validate_gex_event_details(row["event_details"])
    side = details["wall_side"]
    confirmation_price = details["confirmation_close_price"]
    breached_level = details["breached_level"]
    direction = "ABOVE CALL" if side == "CALL" else "BELOW PUT"
    message = (
        f"⚠️ GEX WALL BREACH — PRICE CLOSED {direction} CONCENTRATION\n\n"
        f"Confirmation close: ${confirmation_price:,.2f}\n"
        f"Breached wall: ${breached_level:,.2f}\n"
        f"Expiry: {details['expiry_date']}\n"
        f"Confirmed: {details['confirmation_close_timestamp']}"
    )
    event_key = f"SYSTEM_SIGNAL_GEX_WALL_BREACH_{row['id']}"
    return {
        "key": event_key,
        "type": "GEX_WALL_BREACH",
        "time": row["event_timestamp"],
        "msg": message,
        "payload": {
            "journal_id": row["id"],
            "event_key": row.get("event_key"),
            "event_timestamp": row["event_timestamp"],
            "detected_at": row["detected_at"],
            "event_details": details,
        },
    }


def collect_alerts(amt, gex_alerts, oi):
    """Keep alert types independent; a same-candle AMT and GEX pair both survive."""
    alerts = []
    if amt:
        alerts.append(amt)
    alerts.extend(gex_alerts or [])
    if oi:
        alerts.append(oi)
    return alerts


def log_gex_event(alert, status, message_id=None, error=None):
    """GEX-specific outbox write with retry attempt accounting; shared legacy helper stays unchanged."""
    key = alert["key"]
    try:
        existing = (
            supabase.table("telegram_alert_events")
            .select("attempt_count")
            .eq("event_key", key)
            .limit(1)
            .execute()
        )
        prior_attempts = int(existing.data[0].get("attempt_count") or 0) if existing.data else 0
        record = {
            "event_key": key,
            "event_type": alert["type"],
            "event_time": alert["time"],
            "payload": alert["payload"],
            "delivery_status": status,
            "telegram_message_id": message_id if status == "SENT" else None,
            "sent_at": datetime.now(timezone.utc).isoformat() if status == "SENT" else None,
            "attempt_count": prior_attempts + 1,
            "last_error": str(error)[:1000] if error else None,
        }
        supabase.table("telegram_alert_events").upsert(
            record, on_conflict="event_key"
        ).execute()
    except Exception as exc:
        print(f"Failed to log GEX event ({status}): {exc}")


def evaluate_gex_breach():
    """Read detector-confirmed journal events; never recalculate walls or apply snapshot-age gates."""
    try:
        cutover = parse_utc_timestamp(GEX_ALERT_CUTOVER_UTC)
    except ValueError:
        print("GEX alert skipped: GEX_ALERT_CUTOVER_UTC has not been finalized.")
        return []
    response = (
        supabase.table("system_signal_journal")
        .select("id,event_timestamp,event_type,event_key,event_details,detected_at")
        .eq("event_type", "GEX_WALL_BREACH")
        .gte("detected_at", cutover.isoformat())
        .order("detected_at", desc=False)
        .limit(200)
        .execute()
    )
    alerts = []
    for row in response.data or []:
        if not gex_row_is_eligible(row, cutover.isoformat()):
            print(f"GEX journal row skipped: invalid or pre-cutover row id={row.get('id')}.")
            continue
        try:
            alert = build_gex_alert(row)
        except (TypeError, ValueError) as exc:
            print(f"GEX journal row skipped: invalid event_details id={row.get('id')}: {exc}")
            continue
        if check_event_exists(alert["key"]):
            continue
        alerts.append(alert)
    return alerts

def classify_amt_price_location(row):
    """Classify the completed AMT candle close against its Prior Value area."""
    if not isinstance(row, dict):
        return "LOCATION_UNAVAILABLE"
    try:
        price = float(row.get("close_price"))
        vah = float(row.get("prior_vah"))
        val = float(row.get("prior_val"))
    except (TypeError, ValueError):
        return "LOCATION_UNAVAILABLE"
    if not all(math.isfinite(value) for value in (price, vah, val)) or vah < val:
        return "LOCATION_UNAVAILABLE"
    if price > vah:
        return "ABOVE_VAH"
    if price < val:
        return "BELOW_VAL"
    return "INSIDE_VALUE"


def load_amt_context_asof(event_time):
    """Fetch the latest valid completed AMT candle available at the OI event time."""
    unavailable = {
        "price_location": "LOCATION_UNAVAILABLE",
        "amt_state": "AMT_STATE_UNAVAILABLE",
        "amt_candle_close_time": None,
        "prior_vah": None,
        "prior_val": None,
    }
    try:
        response = (
            supabase.table("amt_snapshots")
            .select("candle_close_time,close_price,prior_vah,prior_val,confirmed_state")
            .eq("exchange", "BINANCE")
            .eq("symbol", "BTCUSDT")
            .eq("timeframe", "30")
            .eq("data_status", "VALID")
            .lte("candle_close_time", event_time)
            .order("candle_close_time", desc=True)
            .limit(1)
            .execute()
        )
        rows = response.data or []
        if not rows:
            return unavailable
        row = rows[0]
        return {
            "price_location": classify_amt_price_location(row),
            "amt_state": row.get("confirmed_state") or "AMT_STATE_UNAVAILABLE",
            "amt_candle_close_time": row.get("candle_close_time"),
            "prior_vah": row.get("prior_vah"),
            "prior_val": row.get("prior_val"),
        }
    except Exception as exc:
        print(f"OI AMT context lookup failed; context marked unavailable: {exc}")
        return unavailable


def evaluate_oi_shock():
    # Fetch last 2 aggregate OI records
    res = supabase.table("futures_oi_snapshots").select("*").eq("exchange", "AGGREGATE").order("created_at", desc=True).limit(2).execute()
    if len(res.data) < 2: return None
    
    curr, prev = res.data[0], res.data[1]
    
    t1 = datetime.fromisoformat(curr["created_at"].replace("Z", "+00:00"))
    t0 = datetime.fromisoformat(prev["created_at"].replace("Z", "+00:00"))
    
    delta_mins = (t1 - t0).total_seconds() / 60
    if not (25 <= delta_mins <= 35): return None
    
    # Strict contributor matching
    stat_c = parse_json_safely(curr.get("status"))
    stat_p = parse_json_safely(prev.get("status"))
    
    contrib_c = sorted(stat_c.get("coverage", {}).get("contributors", []))
    contrib_p = sorted(stat_p.get("coverage", {}).get("contributors", []))
    
    if not contrib_c or contrib_c != contrib_p or len(contrib_c) < 2:
        return None
        
    oi_c, oi_p = float(curr["oi_usd"]), float(prev["oi_usd"])
    pct_change = ((oi_c - oi_p) / oi_p) * 100
    
    if abs(pct_change) >= 3.0:
        direction = "EXPANDED" if pct_change > 0 else "CONTRACTED"
        event_key = f"OI_SHOCK_{curr['created_at']}"
        if check_event_exists(event_key): return None
        amt_context = load_amt_context_asof(curr["created_at"])
        msg = (
            f"🌊 OI SHOCK: {direction}\n\n"
            f"Change: {pct_change:+.2f}%\n"
            f"Previous OI: ${oi_p/1e6:.1f}M\n"
            f"Current OI: ${oi_c/1e6:.1f}M\n"
            f"Coverage: {len(contrib_c)}/4 ({', '.join(contrib_c)})\n"
            f"Price Location: {amt_context['price_location']}\n"
            f"AMT State: {amt_context['amt_state']}"
        )
        payload = {**curr, **amt_context}
        return {"key": event_key, "type": "OI_SHOCK", "time": curr["created_at"], "msg": msg, "payload": payload}
    return None

def main():
    if os.environ.get("TEST_MODE", "false").lower() == "true":
        print("TEST MODE: Sending test Telegram message...")
        test_msg = (
            "🧪 TEST ALERT\n\n"
            "This is a verification message from the BTC alert system.\n"
            "If you see this, delivery works."
        )
        event_time = datetime.now(timezone.utc).isoformat()
        test_alert = {
            "key": f"TEST_{event_time}",
            "type": "TEST_ALERT",
            "time": event_time,
            "payload": {"test": True},
        }
        begin_event_delivery(test_alert)
        success, msg_id = send_telegram(test_msg)
        delivery_status = (
            "SENT" if success and isinstance(msg_id, int) and msg_id > 0 else "FAILED"
        )
        finish_event_delivery(
            test_alert, delivery_status,
            message_id=msg_id if delivery_status == "SENT" else None,
            error=None if delivery_status == "SENT" else msg_id,
        )
        if delivery_status == "SENT":
            print(f"TEST SUCCESS: Message ID {msg_id}")
            return
        print(f"TEST FAILED: {msg_id}")
        raise SystemExit(1)

    print("Evaluating alerts...")
    alerts = []
    
    amt = evaluate_amt_transition()
    if amt: alerts.append(amt)
    
    try:
        gex_alerts = evaluate_gex_breach()
    except Exception as exc:
        # GEX journal failures must not stop independent AMT/OI alert evaluation.
        # No GEX alert is created or marked SENT on this path; journal events remain retryable.
        print(f"GEX evaluation failed; continuing with AMT/OI alerts: {exc}")
        gex_alerts = []

    oi = evaluate_oi_shock()
    alerts = collect_alerts(amt, gex_alerts, oi)
    
    if alerts:
        # Persist every event before sending the combined Telegram message. If
        # any PENDING write fails, abort delivery rather than send untracked alerts.
        pending_alerts = []
        try:
            for alert in alerts:
                if begin_event_delivery(alert):
                    pending_alerts.append(alert)
        except Exception as exc:
            print(f"Outbox PENDING write failed; Telegram send aborted: {exc}")
            raise SystemExit(1)

        if not pending_alerts:
            print("All qualifying alerts already have confirmed SENT outbox records.")
            return

        combined_msg = "\n\n".join([a["msg"] for a in pending_alerts])
        print(f"Sending alert:\n{combined_msg}")
        success, msg_id = send_telegram(combined_msg)

        delivery_status = "SENT" if success and isinstance(msg_id, int) and msg_id > 0 else "FAILED"
        valid_message_id = msg_id if delivery_status == "SENT" else None
        finalization_errors = []
        for alert in pending_alerts:
            try:
                finish_event_delivery(
                    alert, delivery_status,
                    message_id=valid_message_id,
                    error=None if delivery_status == "SENT" else msg_id,
                )
            except Exception as exc:
                finalization_errors.append("{}: {}".format(alert["key"], exc))

        if delivery_status == "SENT" and not finalization_errors:
            print(f"Alert sent and outbox finalized successfully (message_id={valid_message_id}).")
        elif delivery_status == "SENT":
            # Telegram accepted the message; PENDING rows are intentionally left
            # recoverable/auditable if their SENT update failed.
            print("Telegram accepted message_id={}; outbox finalization failed: {}".format(
                valid_message_id, "; ".join(finalization_errors)
            ))
        else:
            print(f"Telegram delivery failed: {msg_id}")
            if finalization_errors:
                print("Outbox FAILED update errors: " + "; ".join(finalization_errors))
    else:
        print("No qualifying alerts.")

if __name__ == "__main__":
    main()
