import os
import sys
import math
from datetime import datetime, timezone

from supabase import create_client
from amt_core import fetch_binance_data, calculate_tpo
from research_engine import ResearchStateEngine, IN_VALUE, ENGINE_VERSION

PROJECT_ECHO_NAMES = {
    "IN_VALUE": "Inside Value",
    "DISCOVERY_CANDIDATE_BELOW": "Downside Discovery Candidate",
    "DISCOVERY_CANDIDATE_ABOVE": "Upside Discovery Candidate",
    "DISCOVERY_CONFIRMED_BELOW": "Downside Discovery Confirmed",
    "DISCOVERY_CONFIRMED_ABOVE": "Upside Discovery Confirmed",
    "REENTRY_CONFIRMED": "Re-entry Confirmed",
    "BALANCE_CONFIRMED": "Balance Confirmed",
    "DATA_UNAVAILABLE": "Data Unavailable",
    "PIN_HYPOTHESIS": "Pin Hypothesis",
    "VALUE_EXCURSION": "Value Excursion",
}


def project_echo_fields(result):
    # Project Echo is a versioned label over the existing ResearchStateEngine output.
    # It does not change or replace confirmed_state, developing_state, or state_output.
    code = result.get("developing_state") or result.get("confirmed_state")
    if not code:
        return None, None, ENGINE_VERSION
    return str(code), PROJECT_ECHO_NAMES.get(str(code), str(code).replace("_", " ").title()), ENGINE_VERSION


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
    df["is_live"] = df["close_time"] > int(now_utc.timestamp() * 1000)
    completed_df = df[~df["is_live"]].copy()
    if completed_df.empty:
        print("No completed candles.", file=sys.stderr)
        return 1

    today_str = completed_df["ShiftedDate"].iloc[-1].strftime("%Y-%m-%d")
    yesterday_df = completed_df[
        completed_df["ShiftedDate"].dt.strftime("%Y-%m-%d") < today_str
    ]
    if yesterday_df.empty:
        print("Not enough history.", file=sys.stderr)
        return 1

    last_prev_day = yesterday_df["ShiftedDate"].iloc[-1].strftime("%Y-%m-%d")
    prev_day_df = yesterday_df[
        yesterday_df["ShiftedDate"].dt.strftime("%Y-%m-%d") == last_prev_day
    ]
    vah, val, poc = calculate_tpo(prev_day_df)

    frozen_profile = {
        "profile_id": f"P_{last_prev_day}",
        "vah": vah,
        "poc": poc,
        "val": val,
        "frozen": True,
    }
    config = {
        "version": "v1.0",
        "atr_period": 21,
        "atr_multiplier": 0.50,
        "minimum_buffer": 50,
        "discovery_closes": 2,
        "reentry_closes": 1,
        "balance_crossings": 2,
        "balance_window": 5,
    }
    amt_engine = ResearchStateEngine(configuration=config, initial_state=IN_VALUE)

    # Explicit deterministic chronological ordering.
    today_df = (
        completed_df[
            completed_df["ShiftedDate"].dt.strftime("%Y-%m-%d") == today_str
        ]
        .sort_values("Date")
        .reset_index(drop=True)
    )

    client = create_client(supabase_url, supabase_key)

    # Process each completed candle and save to Supabase.
    for candle_index, (_, row) in enumerate(today_df.iterrows()):
        # Recalculate the developing profile from today's first completed candle
        # through the current candle (inclusive). This is observational data only;
        # the ResearchStateEngine inputs and state transitions remain unchanged.
        developing_slice = today_df.iloc[:candle_index + 1]
        developing_vah, developing_val, developing_poc = calculate_tpo(developing_slice)

        candle = {
            "exchange": "BINANCE",
            "symbol": "BTCUSDT",
            "timeframe": "30",
            "open_time_utc": row["Date"].isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "atr": row["atr"],
            "profile": frozen_profile,
            "completed": True,
        }
        result = amt_engine.process_candle(candle, {})
        if result.get("ignored"):
            print(f"Skipped duplicate candle: {row['Date'].isoformat()}")
            continue

        buffer_value = max(config["minimum_buffer"], float(row["atr"]) * config["atr_multiplier"])
        close_dt = datetime.fromtimestamp(row["close_time"] / 1000.0, tz=timezone.utc)
        echo_code, echo_name, echo_version = project_echo_fields(result)

        # Existing AMT columns and state_output remain unchanged.
        db_row = {
            "exchange": "BINANCE",
            "symbol": "BTCUSDT",
            "timeframe": "30",
            "candle_open_time": row["Date"].isoformat(),
            "candle_close_time": close_dt.isoformat(),
            "open_price": float(row["open"]),
            "high_price": float(row["high"]),
            "low_price": float(row["low"]),
            "close_price": float(row["close"]),
            "volume": float(row["volume"]),
            "profile_date": last_prev_day,
            "profile_id": frozen_profile["profile_id"],
            "prior_vah": float(vah),
            "prior_poc": float(poc),
            "prior_val": float(val),
            "developing_poc": float(developing_poc) if math.isfinite(float(developing_poc)) else None,
            "developing_vah": float(developing_vah) if math.isfinite(float(developing_vah)) else None,
            "developing_val": float(developing_val) if math.isfinite(float(developing_val)) else None,
            "tpo_row_size": 50.0,
            "value_area_percent": 0.70,
            "atr_21": float(row["atr"]),
            "atr_buffer": float(buffer_value),
            "discovery_above": result.get("upper_discovery_threshold"),
            "discovery_below": result.get("lower_discovery_threshold"),
            "engine_version": "v1.0",
            "confirmed_state": result.get("confirmed_state"),
            "developing_state": result.get("developing_state"),
            "state_output": result,
            "data_status": result.get("data_status", "OK"),
            "project_echo_code": echo_code,
            "project_echo_name": echo_name,
            "project_echo_version": echo_version,
        }

        client.table("amt_snapshots").upsert(
            db_row,
            on_conflict="exchange,symbol,timeframe,candle_open_time",
        ).execute()
        print(
            f"Saved candle: {row['Date'].strftime('%H:%M')} -> "
            f"State: {result.get('confirmed_state')} | Project Echo: {echo_code}"
        )

    return 0


if __name__ == "__main__":
    sys.exit(main())
