import os
import json
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

def parse_json_safely(data):
    if isinstance(data, str):
        try:
            return json.loads(data)
        except:
            return {}
    return data if isinstance(data, dict) else {}

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {"chat_id": TELEGRAM_CHAT_ID, "text": message, "parse_mode": "Markdown"}
    try:
        res = requests.post(url, json=payload, timeout=10)
        if res.status_code == 200:
            return True, res.json().get("result", {}).get("message_id")
        else:
            return False, res.text
    except Exception as e:
        return False, str(e)

def check_event_exists(event_key):
    res = supabase.table("telegram_alert_events").select("event_key").eq("event_key", event_key).execute()
    return len(res.data) > 0

def log_event(event_key, event_type, event_time, payload):
    try:
        supabase.table("telegram_alert_events").insert({
            "event_key": event_key,
            "event_type": event_type,
            "event_time": event_time,
            "payload": payload,
            "delivery_status": "SENT"
        }).execute()
    except Exception as e:
        print(f"Failed to log event: {e}")

def evaluate_amt_transition():
    # Fetch last 2 completed candles
    res = supabase.table("amt_snapshots").select("*").eq("data_status", "VALID").order("candle_close_time", desc=True).limit(2).execute()
    if len(res.data) < 2: return None
    
    curr, prev = res.data[0], res.data[1]
    if curr["profile_id"] != prev["profile_id"]: return None
    
    curr_state = curr["confirmed_state"]
    prev_state = prev["confirmed_state"]
    
    if curr_state != prev_state:
        event_key = f"AMT_STATE_CHANGE_{curr['candle_close_time']}"
        if check_event_exists(event_key): return None
        
        msg = f"🚨 *AMT STRUCTURAL CHANGE*\n\n{prev_state} → {curr_state}\nCandle: {curr['candle_open_time']}\nClose: ${curr['close_price']:,.0f}"
        return {"key": event_key, "type": "AMT_STATE_CHANGE", "time": curr["candle_close_time"], "msg": msg, "payload": curr}
    return None

def evaluate_gex_breach():
    # Fetch last 2 completed AMT candles
    res_amt = supabase.table("amt_snapshots").select("*").eq("data_status", "VALID").order("candle_close_time", desc=True).limit(2).execute()
    if len(res_amt.data) < 2: return None
    curr, prev = res_amt.data[0], res_amt.data[1]
    
    # Use today's UTC date as the expiry floor. Never evaluate expired contracts.
    today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")

    # Select the nearest unexpired expiry first, then the freshest snapshot
    # available for that expiry. Chained order() calls create a multi-column
    # PostgREST order: expiry_date ASC, source_collected_at DESC.
    res_opt = (
        supabase.table("options_derived_snapshots")
        .select("*")
        .gte("expiry_date", today_str)
        .order("expiry_date", desc=False)
        .order("source_collected_at", desc=True)
        .limit(1)
        .execute()
    )
    if not res_opt.data: return None
    opt = res_opt.data[0]

    # Require a successful collection run for this exact options snapshot.
    snapshot_id = opt.get("snapshot_id")
    if not snapshot_id:
        print("GEX check skipped: options snapshot has no snapshot_id.")
        return None

    res_run = (
        supabase.table("options_collection_runs")
        .select("snapshot_id,status")
        .eq("snapshot_id", snapshot_id)
        .eq("status", "SUCCESS")
        .limit(1)
        .execute()
    )
    if not res_run.data:
        print(f"GEX check skipped: no SUCCESS collection run for snapshot {snapshot_id}.")
        return None

    # The options data must be contemporaneous with the AMT candle being tested.
    try:
        source_time = datetime.fromisoformat(
            str(opt["source_collected_at"]).replace("Z", "+00:00")
        )
        candle_time = datetime.fromisoformat(
            str(curr["candle_close_time"]).replace("Z", "+00:00")
        )
        if source_time.tzinfo is None:
            source_time = source_time.replace(tzinfo=timezone.utc)
        if candle_time.tzinfo is None:
            candle_time = candle_time.replace(tzinfo=timezone.utc)

        age = candle_time - source_time
        if age.total_seconds() < 0 or age > timedelta(minutes=35):
            print(
                "GEX check skipped: options snapshot is outside the allowed "
                f"freshness window (age={age})."
            )
            return None
    except (KeyError, TypeError, ValueError) as exc:
        print(f"GEX check skipped: invalid options/AMT timestamp: {exc}")
        return None

    # Check metric status (Handle string or dict)
    metric_status = parse_json_safely(opt.get("metric_status"))
    if metric_status.get("call_gex") != "OK" or metric_status.get("put_gex") != "OK":
        return None
        
    call_wall = float(opt.get("call_gex_wall_strike", 0))
    put_wall = float(opt.get("put_gex_wall_strike", 0))
    
    if call_wall == 0 or put_wall == 0: return None
    
    c_close, p_close = float(curr["close_price"]), float(prev["close_price"])
    
    # 2 Consecutive closes above Call Wall
    if c_close > call_wall and p_close > call_wall:
        event_key = f"GEX_CALL_BREACH_{curr['candle_close_time']}"
        if check_event_exists(event_key): return None
        msg = f"⚠️ *PRICE CLOSED ABOVE CALL GEX CONCENTRATION*\n\nPrice: ${c_close:,.0f}\nCall Wall: ${call_wall:,.0f}\nExpiry: {opt['expiry_date']}"
        return {"key": event_key, "type": "GEX_WALL_BREACH", "time": curr["candle_close_time"], "msg": msg, "payload": curr}
        
    # 2 Consecutive closes below Put Wall
    if c_close < put_wall and p_close < put_wall:
        event_key = f"GEX_PUT_BREACH_{curr['candle_close_time']}"
        if check_event_exists(event_key): return None
        msg = f"⚠️ *PRICE CLOSED BELOW PUT GEX CONCENTRATION*\n\nPrice: ${c_close:,.0f}\nPut Wall: ${put_wall:,.0f}\nExpiry: {opt['expiry_date']}"
        return {"key": event_key, "type": "GEX_WALL_BREACH", "time": curr["candle_close_time"], "msg": msg, "payload": curr}
    return None

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
        msg = f"🌊 *OI SHOCK: {direction}*\n\nChange: {pct_change:+.2f}%\nPrevious OI: ${oi_p/1e6:.1f}M\nCurrent OI: ${oi_c/1e6:.1f}M\nCoverage: {len(contrib_c)}/4 ({', '.join(contrib_c)})"
        return {"key": event_key, "type": "OI_SHOCK", "time": curr["created_at"], "msg": msg, "payload": curr}
    return None

def main():
    print("Evaluating alerts...")
    alerts = []
    
    amt = evaluate_amt_transition()
    if amt: alerts.append(amt)
    
    gex = evaluate_gex_breach()
    if gex: alerts.append(gex)
    
    oi = evaluate_oi_shock()
    if oi: alerts.append(oi)
    
    if alerts:
        combined_msg = "\n\n".join([a["msg"] for a in alerts])
        print(f"Sending alert:\n{combined_msg}")
        success, msg_id = send_telegram(combined_msg)
        
        if success:
            for a in alerts:
                log_event(a["key"], a["type"], a["time"], a["payload"])
            print("Alert sent and logged successfully.")
        else:
            print(f"Telegram delivery failed: {msg_id}")
    else:
        print("No qualifying alerts.")

if __name__ == "__main__":
    main()
