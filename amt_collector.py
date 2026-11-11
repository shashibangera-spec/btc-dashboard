import os
import sys
import json
from datetime import datetime, timezone
from supabase import create_client
from amt_core import fetch_binance_data, calculate_tpo
from research_engine import ResearchStateEngine, IN_VALUE

def main():
    supabase_url = os.getenv("SUPABASE_URL")
    supabase_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not supabase_url or not supabase_key:
        print("Missing Supabase secrets.", file=sys.stderr)
        return 1

    print("Fetching Binance data...")
    df = fetch_binance_data()
    if df.empty:
        print("No data.", file=sys.stderr)
        return 1

    now_utc = datetime.now(timezone.utc)
    df['is_live'] = df['close_time'] > int(now_utc.timestamp() * 1000)
    completed_df = df[~df['is_live']].copy()

    today_str = completed_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    yesterday_df = completed_df[completed_df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str]
    
    if yesterday_df.empty:
        print("Not enough history.", file=sys.stderr)
        return 1

    last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
    vah, val, poc = calculate_tpo(prev_day_df)

    frozen_profile = {"profile_id": f"P_{last_prev_day}", "vah": vah, "poc": poc, "val": val, "frozen": True}
    config = {"version": "v1.0", "atr_period": 21, "atr_multiplier": 0.50, "minimum_buffer": 50, "discovery_closes": 2, "reentry_closes": 1, "balance_crossings": 2, "balance_window": 5}
    
    amt_engine = ResearchStateEngine(configuration=config, initial_state=IN_VALUE)
    today_df = completed_df[completed_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == today_str]

    client = create_client(supabase_url, supabase_key)
    
    # Process each candle and save to Supabase
    for _, row in today_df.iterrows():
        candle = {
            "exchange": "BINANCE", "symbol": "BTCUSDT", "timeframe": "30",
            "open_time_utc": row['Date'].isoformat(), "open": row['open'], "high": row['high'], "low": row['low'], "close": row['close'], 
            "atr": row['atr'], "profile": frozen_profile, "completed": True
        }
        result = amt_engine.process_candle(candle, {})
        
        # Build database row
        db_row = {
            "exchange": "BINANCE",
            "symbol": "BTCUSDT",
            "timeframe": "30",
            "open_time_utc": row['Date'].isoformat(),
            "profile_id": frozen_profile["profile_id"],
            "profile": frozen_profile,
            "atr": float(row['atr']),
            "engine_version": "v1.0",
            "result": result
        }
        
        # Upsert to avoid duplicates
        client.table("amt_snapshots").upsert(db_row, on_conflict="exchange,symbol,timeframe,open_time_utc").execute()
        print(f"Saved candle: {row['Date'].strftime('%H:%M')} -> State: {result.get('confirmed_state')}")

    return 0

if __name__ == "__main__":
    sys.exit(main())