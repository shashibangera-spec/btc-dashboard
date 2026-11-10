"""
BTC AMT RESEARCH DASHBOARD
AMT + Price Action + Futures OI + Options Positioning

PRIMARY BTC AMT CYCLE:
00:00 UTC to 00:00 UTC
05:30 IST to 05:30 IST

DELTA OPTIONS EXPIRY:
17:30 IST (independent contract expiry reference)

Primary timeframe: 30 minutes
Timezone: Asia/Kolkata
"""

import re
import time
import logging
import sqlite3
import os

from datetime import datetime, date, time as dt_time, timedelta
from zoneinfo import ZoneInfo

import requests
import pandas as pd
import numpy as np
import streamlit as st

from streamlit_autorefresh import st_autorefresh

# ============================================================
# 1. APPLICATION CONFIGURATION
# ============================================================

st.set_page_config(
    page_title="BTC AMT Research",
    page_icon="₿",
    layout="wide",
    initial_sidebar_state="collapsed"
)

IST = ZoneInfo("Asia/Kolkata")

BINANCE_BASE = "https://data-api.binance.vision"
DELTA_BASE = "https://api.india.delta.exchange"

BINANCE_KLINES_URL = f"{BINANCE_BASE}/api/v3/klines"
DELTA_TICKERS_URL = f"{DELTA_BASE}/v2/tickers"
DELTA_PRODUCTS_URL = f"{DELTA_BASE}/v2/products"

TIMEFRAME = "30m"
CANDLE_LIMIT = 1000

TPO_ROW_SIZE = 50.0
VALUE_AREA_PERCENT = 0.70

REFRESH_INTERVAL_MS = 300000
REQUEST_TIMEOUT = 20

OPTIONS_EXPIRY_HOUR = 17
OPTIONS_EXPIRY_MINUTE = 30

AMT_CYCLE_HOUR = 5
AMT_CYCLE_MINUTE = 30

logging.basicConfig(level=logging.ERROR)

DB_PATH = "oi_forensics.db"

# ============================================================
# SERVER-SIDE HISTORY STORAGE & SQLITE DB
# ============================================================
@st.cache_resource
def get_history():
    """Stores general history on the server, so it survives browser closure."""
    return []

def init_db():
    """Initialize SQLite database for Options OI Forensics."""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS oi_snapshots (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT,
            symbol TEXT,
            strike REAL,
            option_type TEXT,
            oi_contracts REAL,
            oi_usd REAL,
            volume REAL,
            iv REAL,
            premium REAL
        )
    ''')
    conn.commit()
    conn.close()

def save_oi_snapshot(options_df, now_ist):
    """Save OI snapshot to SQLite every ~5 minutes."""
    if options_df.empty:
        return
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Check last save time to prevent spamming
    c.execute("SELECT MAX(timestamp) FROM oi_snapshots")
    last_ts_str = c.fetchone()[0]
    
    if last_ts_str:
        last_ts = datetime.fromisoformat(last_ts_str)
        # If less than 4 minutes have passed, skip saving
        if (now_ist - last_ts).total_seconds() < 240:
            conn.close()
            return
            
    current_ts = now_ist.isoformat()
    
    for _, row in options_df.iterrows():
        c.execute('''
            INSERT INTO oi_snapshots 
            (timestamp, symbol, strike, option_type, oi_contracts, oi_usd, volume, iv, premium)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            current_ts,
            row['symbol'],
            row['strike'],
            row['option_type'],
            row.get('oi_contracts', 0),
            row.get('oi_usd', 0),
            row.get('volume', 0),
            row.get('iv', 0),
            row.get('option_price', 0)
        ))
        
    conn.commit()
    conn.close()

def get_oi_change(symbol, current_oi, now_ist):
    """Query DB to find OI change over the last 30 minutes."""
    if not current_oi or np.isnan(current_oi):
        return "Unavailable"
        
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    target_time = now_ist - timedelta(minutes=30)
    
    # Find snapshot closest to 30 mins ago (with a 10 min tolerance)
    c.execute('''
        SELECT timestamp, oi_contracts FROM oi_snapshots 
        WHERE symbol = ? 
        ORDER BY ABS(strftime('%s', timestamp) - strftime('%s', ?)) ASC LIMIT 1
    ''', (symbol, target_time.isoformat()))
    
    result = c.fetchone()
    conn.close()
    
    if not result:
        return "Insufficient history"
        
    past_ts_str, past_oi = result
    past_ts = datetime.fromisoformat(past_ts_str)
    
    # If the closest snapshot is too far (e.g., app was closed for hours), ignore
    if abs((past_ts - target_time).total_seconds()) > 600:
        return "Insufficient history"
        
    if past_oi == 0:
        return "Insufficient history"
        
    change = current_oi - past_oi
    pct_change = (change / past_oi) * 100
    
    # Define threshold for "Approximately unchanged" (e.g., +/- 2%)
    if pct_change > 2.0:
        return f"Growing (+{pct_change:.1f}%)"
    elif pct_change < -2.0:
        return f"Shrinking ({pct_change:.1f}%)"
    else:
        return "Approximately unchanged"

init_db()

# ============================================================
# 2. CUSTOM STYLING
# ============================================================

st.markdown(
    """
    <style>
    .main { background-color: #0e1117; }
    .block-container { padding-top: 1.5rem; padding-bottom: 2rem; }
    div[data-testid="stMetric"] {
        background-color: #161b22;
        border: 1px solid #30363d;
        padding: 15px;
        border-radius: 10px;
    }
    div[data-testid="stMetricLabel"] { color: #aeb8c4; }
    div[data-testid="stMetricValue"] { color: #ffffff; }
    .section-header { font-size: 19px; font-weight: 600; margin-top: 12px; margin-bottom: 12px; }
    .info-text { color: #9da7b3; font-size: 12px; }
    </style>
    """,
    unsafe_allow_html=True
)

# ============================================================
# 3. HTTP SESSION AND HELPERS
# ============================================================

@st.cache_resource
def get_session():
    session = requests.Session()
    session.headers.update({
        "Accept": "application/json",
        "User-Agent": "BTC-AMT-Research-Dashboard/2.0"
    })
    return session

SESSION = get_session()

def api_get(url, params=None):
    response = SESSION.get(url, params=params, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()
    data = response.json()
    if isinstance(data, dict):
        if data.get("success") is False:
            raise RuntimeError(str(data.get("error", "API returned unsuccessful response")))
    return data

def to_float(value, default=np.nan):
    try:
        if value is None: return default
        result = float(value)
        if not np.isfinite(result): return default
        return result
    except (ValueError, TypeError):
        return default

def get_result(data):
    if isinstance(data, dict): return data.get("result", [])
    return data

def finite(value):
    return value is not None and np.isfinite(to_float(value))

def fmt_price(value):
    return f"${float(value):,.2f}" if finite(value) else "Unavailable"

def fmt_usd(value):
    return f"${float(value):,.0f}" if finite(value) else "Unavailable"

# ============================================================
# 4. BINANCE OHLC DATA
# ============================================================

def fetch_binance_klines():
    params = {"symbol": "BTCUSDT", "interval": TIMEFRAME, "limit": CANDLE_LIMIT}
    data = api_get(BINANCE_KLINES_URL, params=params)
    
    if not isinstance(data, list) or not data:
        raise RuntimeError("No Binance candle data returned.")

    columns = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume", "trades", "taker_buy_volume", "taker_buy_quote_volume", "ignore"]
    df = pd.DataFrame(data, columns=columns)

    for column in ["open", "high", "low", "close", "volume"]:
        df[column] = pd.to_numeric(df[column], errors="coerce")

    df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True).dt.tz_convert(IST)
    df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True).dt.tz_convert(IST)
    
    df = df.dropna(subset=["open", "high", "low", "close", "volume"])
    return df.sort_values("open_time").reset_index(drop=True)

# ============================================================
# 5. DELTA PAGINATED DATA
# ============================================================

def fetch_paginated_delta(url, params, page_size=1000, max_pages=30):
    results = []
    current_params = params.copy()
    current_params["page_size"] = page_size
    cursor_history = set()

    for page in range(max_pages):
        data = api_get(url, params=current_params)
        page_results = get_result(data)
        if isinstance(page_results, dict): page_results = [page_results]
        if not isinstance(page_results, list): break
        results.extend(page_results)
        
        meta = data.get("meta", {}) if isinstance(data, dict) else {}
        next_cursor = meta.get("after")
        if not next_cursor or next_cursor in cursor_history: break
        cursor_history.add(next_cursor)
        current_params["after"] = next_cursor
        time.sleep(0.15)

    return results

# ============================================================
# 6. DELTA PRODUCTS
# ============================================================

def fetch_delta_products():
    params = {"states": "live", "page_size": 1000}
    try:
        return fetch_paginated_delta(DELTA_PRODUCTS_URL, params, page_size=1000)
    except requests.HTTPError:
        return fetch_paginated_delta(DELTA_PRODUCTS_URL, {"states": "live", "page_size": 500}, page_size=500)

# ============================================================
# 7. DELTA FUTURES
# ============================================================

def fetch_delta_futures():
    params = {"contract_types": "perpetual_futures"}
    data = api_get(DELTA_TICKERS_URL, params=params)
    tickers = get_result(data)
    if isinstance(tickers, dict): tickers = [tickers]

    btc_ticker = next((t for t in tickers if t.get("symbol") == "BTCUSD"), None)
    if btc_ticker is None:
        raise RuntimeError("BTCUSD perpetual ticker not found on Delta.")

    return {
        "symbol": btc_ticker.get("symbol"),
        "funding_rate": to_float(btc_ticker.get("funding_rate")),
        "mark_price": to_float(btc_ticker.get("mark_price")),
        "spot_price": to_float(btc_ticker.get("spot_price")),
        "oi_value_usd": to_float(btc_ticker.get("oi_value_usd")),
        "timestamp": btc_ticker.get("timestamp")
    }

# ============================================================
# 8. OPTIONS EXPIRY PARSER
# ============================================================

def parse_option_symbol(symbol):
    if not symbol: return None
    pattern = r"^(C|P)-BTC-([0-9]+(?:\.[0-9]+)?)-(\d{6})$"
    match = re.match(pattern, symbol)
    if not match: return None

    try:
        expiry_date = datetime.strptime(match.group(3), "%d%m%y").date()
    except ValueError:
        return None

    return {
        "symbol": symbol,
        "option_type": match.group(1),
        "strike": float(match.group(2)),
        "expiry_date": expiry_date
    }

def get_options_expiry_datetime(expiry_date):
    return datetime.combine(expiry_date, dt_time(OPTIONS_EXPIRY_HOUR, OPTIONS_EXPIRY_MINUTE), tzinfo=IST)

def get_nearest_expiry(products, tickers, now_ist):
    expiry_dates = set()
    for product in products:
        parsed = parse_option_symbol(product.get("symbol", ""))
        if parsed: expiry_dates.add(parsed["expiry_date"])
    for ticker in tickers:
        parsed = parse_option_symbol(ticker.get("symbol", ""))
        if parsed: expiry_dates.add(parsed["expiry_date"])

    valid_expiries = [e for e in expiry_dates if get_options_expiry_datetime(e) > now_ist]
    return min(valid_expiries) if valid_expiries else None

# ============================================================
# 9. DELTA OPTIONS CHAIN
# ============================================================

def fetch_delta_options():
    products = fetch_delta_products()
    params = {"contract_types": "call_options,put_options", "page_size": 1000}
    tickers = fetch_paginated_delta(DELTA_TICKERS_URL, params, page_size=1000)
    
    now_ist = datetime.now(IST)
    nearest_expiry = get_nearest_expiry(products, tickers, now_ist)
    if nearest_expiry is None:
        raise RuntimeError("No upcoming BTC options expiry found.")

    option_rows = []
    for ticker in tickers:
        parsed = parse_option_symbol(ticker.get("symbol", ""))
        if not parsed or parsed["expiry_date"] != nearest_expiry:
            continue

        quotes = ticker.get("quotes") or {}
        greeks = ticker.get("greeks") or {}

        bid = to_float(quotes.get("best_bid"))
        ask = to_float(quotes.get("best_ask"))
        mark = to_float(ticker.get("mark_price"))
        last = to_float(ticker.get("close"))

        if finite(bid) and finite(ask) and bid > 0 and ask >= bid:
            mid_price = (bid + ask) / 2
        elif finite(mark) and mark > 0:
            mid_price = mark
        elif finite(last) and last > 0:
            mid_price = last
        else:
            mid_price = np.nan

        option_rows.append({
            "symbol": parsed["symbol"],
            "option_type": parsed["option_type"],
            "strike": parsed["strike"],
            "expiry_date": parsed["expiry_date"],
            "oi_usd": to_float(ticker.get("oi_value_usd"), default=0.0),
            "oi_contracts": to_float(ticker.get("oi"), default=0.0), # NEW RAW OI
            "volume": to_float(ticker.get("volume"), default=0.0),  # NEW VOLUME
            "iv": to_float(quotes.get("mark_iv") or quotes.get("bid_iv") or quotes.get("ask_iv"), default=0.0), # NEW IV
            "gamma": to_float(greeks.get("gamma")),
            "bid": bid,
            "ask": ask,
            "mark_price": mark,
            "option_price": mid_price,
            "spot_price": to_float(ticker.get("spot_price"))
        })

    options_df = pd.DataFrame(option_rows)
    if options_df.empty:
        raise RuntimeError("No option tickers found for nearest expiry.")

    return nearest_expiry, options_df

# ============================================================
# 10. PRIMARY BTC UTC DAILY AMT CYCLE
# ============================================================

def get_current_cycle_start(now=None):
    if now is None: now = datetime.now(IST)
    boundary = now.replace(hour=AMT_CYCLE_HOUR, minute=AMT_CYCLE_MINUTE, second=0, microsecond=0)
    if now < boundary: boundary -= timedelta(days=1)
    return boundary

def get_cycle_candles(df, cycle_start):
    cycle_end = cycle_start + timedelta(days=1)
    cycle_df = df[(df["open_time"] >= cycle_start) & (df["open_time"] < cycle_end)].copy()
    return cycle_df.reset_index(drop=True)

# ============================================================
# 11. TPO PRICE ROWS
# ============================================================

def get_price_rows(low, high, row_size=TPO_ROW_SIZE):
    lower = int(np.floor(low / row_size))
    upper = int(np.floor(high / row_size))
    return list(range(lower, upper + 1))

def row_price(row, row_size=TPO_ROW_SIZE):
    return row * row_size + row_size / 2

# ============================================================
# 12. TPO PROFILE AND 70% VALUE AREA
# ============================================================

def calculate_tpo_profile(cycle_df, row_size=TPO_ROW_SIZE, value_area_percent=VALUE_AREA_PERCENT):
    if cycle_df.empty: return None

    tpo_counts = {}
    profile_low = cycle_df["low"].min()
    profile_high = cycle_df["high"].max()

    for _, candle in cycle_df.iterrows():
        for row in get_price_rows(candle["low"], candle["high"], row_size):
            tpo_counts[row] = tpo_counts.get(row, 0) + 1

    if not tpo_counts: return None

    rows = sorted(tpo_counts.keys())
    max_tpo = max(tpo_counts.values())
    candidates = [row for row, count in tpo_counts.items() if count == max_tpo]
    
    profile_midpoint = (profile_low + profile_high) / 2
    midpoint_row = profile_midpoint / row_size
    poc_row = min(candidates, key=lambda row: (abs(row - midpoint_row), row))

    total_tpo = sum(tpo_counts.values())
    target_tpo = total_tpo * value_area_percent
    
    included_rows = {poc_row}
    accumulated = tpo_counts[poc_row]
    lower_row = poc_row - 1
    upper_row = poc_row + 1

    while accumulated < target_tpo:
        lower_count = tpo_counts.get(lower_row, 0)
        upper_count = tpo_counts.get(upper_row, 0)

        if lower_count == 0 and upper_count == 0: break

        if lower_count > upper_count:
            included_rows.add(lower_row)
            accumulated += lower_count
            lower_row -= 1
        elif upper_count > lower_count:
            included_rows.add(upper_row)
            accumulated += upper_count
            upper_row += 1
        else:
            lower_distance = abs(row_price(lower_row, row_size) - profile_midpoint)
            upper_distance = abs(row_price(upper_row, row_size) - profile_midpoint)

            if lower_distance <= upper_distance:
                included_rows.add(lower_row)
                accumulated += lower_count
                lower_row -= 1
            else:
                included_rows.add(upper_row)
                accumulated += upper_count
                upper_row += 1

    value_area_low_row = min(included_rows)
    value_area_high_row = max(included_rows)

    profile_table = [{
        "Price Level": row_price(row, row_size),
        "TPO Count": tpo_counts[row],
        "In Value Area": (row in included_rows),
        "POC": row == poc_row
    } for row in rows]

    return {
        "poc": row_price(poc_row, row_size),
        "vah": row_price(value_area_high_row, row_size),
        "val": row_price(value_area_low_row, row_size),
        "tpo_counts": tpo_counts,
        "table": pd.DataFrame(profile_table),
        "total_tpo": total_tpo
    }

# ============================================================
# 13. OHLCV VOLUME PROFILE APPROXIMATION
# ============================================================

def calculate_volume_profile(cycle_df, row_size=TPO_ROW_SIZE):
    if cycle_df.empty: return None

    volume_distribution = {}
    for _, candle in cycle_df.iterrows():
        rows = get_price_rows(candle["low"], candle["high"], row_size)
        if not rows: continue
        allocated_volume = candle["volume"] / len(rows)
        for row in rows:
            volume_distribution[row] = volume_distribution.get(row, 0.0) + allocated_volume

    if not volume_distribution: return None

    poc_row = max(volume_distribution, key=volume_distribution.get)
    result = pd.DataFrame([{
        "Price Level": row_price(row, row_size),
        "Estimated Volume": volume,
        "POC": row == poc_row
    } for row, volume in sorted(volume_distribution.items())])

    return {"poc": row_price(poc_row, row_size), "table": result}

# ============================================================
# 14. INITIAL BALANCE
# ============================================================

def calculate_initial_balance(cycle_df, cycle_start):
    ib_start = cycle_start
    ib_end = cycle_start + timedelta(hours=1)
    ib_df = cycle_df[(cycle_df["open_time"] >= ib_start) & (cycle_df["open_time"] < ib_end)]

    if len(ib_df) < 2:
        return {"high": np.nan, "low": np.nan, "complete": False}

    return {"high": ib_df["high"].max(), "low": ib_df["low"].min(), "complete": True}

# ============================================================
# 15. SINGLE PRINTS
# ============================================================

def calculate_single_prints(cycle_df, row_size=TPO_ROW_SIZE):
    if cycle_df.empty: return pd.DataFrame(columns=["Price Level", "Candle Count"])

    candle_counts = {}
    for _, candle in cycle_df.iterrows():
        rows = set(get_price_rows(candle["low"], candle["high"], row_size))
        for row in rows:
            candle_counts[row] = candle_counts.get(row, 0) + 1

    single_rows = [row for row, count in candle_counts.items() if count == 1]
    return pd.DataFrame([{
        "Price Level": row_price(row, row_size),
        "Candle Count": candle_counts[row]
    } for row in sorted(single_rows)])

# ============================================================
# 16. OPTIONS ANALYTICS
# ============================================================

def calculate_options_metrics(options_df, current_price):
    if options_df.empty: return None

    calls = options_df[options_df["option_type"] == "C"].copy()
    puts = options_df[options_df["option_type"] == "P"].copy()
    all_strikes = sorted(options_df["strike"].unique())

    if not all_strikes: return None

    atm_strike = min(all_strikes, key=lambda strike: abs(strike - current_price))
    
    atm_calls = calls[calls["strike"] == atm_strike]
    atm_puts = puts[puts["strike"] == atm_strike]

    call_price = atm_calls.iloc[0]["option_price"] if not atm_calls.empty else np.nan
    put_price = atm_puts.iloc[0]["option_price"] if not atm_puts.empty else np.nan

    straddle = np.nan
    if finite(call_price) and finite(put_price):
        straddle = call_price + put_price

    call_wall = calls.loc[calls["oi_usd"].idxmax()] if not calls.empty else None
    put_wall = puts.loc[puts["oi_usd"].idxmax()] if not puts.empty else None

    # Find Neighboring Strikes
    def get_neighbors(wall_row):
        if wall_row is None: return [], []
        strike = wall_row["strike"]
        idx = all_strikes.index(strike)
        
        lower = all_strikes[idx - 1] if idx > 0 else None
        upper = all_strikes[idx + 1] if idx < len(all_strikes) - 1 else None
        return lower, upper

    call_neighbors = get_neighbors(call_wall)
    put_neighbors = get_neighbors(put_wall)

    return {
        "atm_strike": atm_strike,
        "call_price": call_price,
        "put_price": put_price,
        "straddle": straddle,
        "call_wall": call_wall,
        "put_wall": put_wall,
        "call_neighbors": call_neighbors,
        "put_neighbors": put_neighbors,
        "calls": calls,
        "puts": puts
    }

# ============================================================
# 17. OPTIONS EXPIRY COUNTDOWN
# ============================================================

def expiry_countdown(expiry_date, now=None):
    if now is None: now = datetime.now(IST)
    expiry_datetime = get_options_expiry_datetime(expiry_date)
    remaining = expiry_datetime - now

    if remaining.total_seconds() <= 0: return "Expired"

    total_seconds = int(remaining.total_seconds())
    days, remainder = divmod(total_seconds, 86400)
    hours, remainder = divmod(remainder, 3600)
    minutes, seconds = divmod(remainder, 60)

    return f"{days}d {hours}h {minutes}m {seconds}s"

# ============================================================
# 18. DATA FETCH CONTROLLER
# ============================================================

def fetch_all_data():
    results = {}
    errors = []
    successful_fetch_times = []

    try:
        results["candles"] = fetch_binance_klines()
        successful_fetch_times.append(datetime.now(IST))
    except Exception as exc:
        errors.append(f"Binance OHLC: {exc}")
        results["candles"] = None

    try:
        results["futures"] = fetch_delta_futures()
        successful_fetch_times.append(datetime.now(IST))
    except Exception as exc:
        errors.append(f"Delta Futures: {exc}")
        results["futures"] = None

    try:
        expiry, options = fetch_delta_options()
        results["expiry"] = expiry
        results["options"] = options
        successful_fetch_times.append(datetime.now(IST))
    except Exception as exc:
        errors.append(f"Delta Options: {exc}")
        results["expiry"] = None
        results["options"] = None

    results["errors"] = errors
    results["last_successful_fetch"] = max(successful_fetch_times) if successful_fetch_times else None
    return results

# ============================================================
# 19. AUTOMATIC HISTORY AND MARKET ASSESSMENT
# ============================================================

def row_value(row, key):
    if row is None: return np.nan
    try: return to_float(row.get(key))
    except Exception: return np.nan

def make_market_snapshot(now_ist, current_price, tpo, ib, futures_data, option_metrics, expiry):
    call_wall = option_metrics.get("call_wall") if option_metrics else None
    put_wall = option_metrics.get("put_wall") if option_metrics else None

    premium = np.nan
    if futures_data and finite(futures_data.get("mark_price")) and finite(futures_data.get("spot_price")):
        spot = float(futures_data["spot_price"])
        if spot:
            premium = ((float(futures_data["mark_price"]) - spot) / spot) * 100

    return {
        "timestamp": now_ist.isoformat(),
        "cycle": get_current_cycle_start(now_ist).strftime("%Y-%m-%d %H:%M"),
        "price": to_float(current_price),
        "poc": to_float(tpo.get("poc")) if tpo else np.nan,
        "vah": to_float(tpo.get("vah")) if tpo else np.nan,
        "val": to_float(tpo.get("val")) if tpo else np.nan,
        "ib_high": to_float(ib.get("high")) if ib else np.nan,
        "ib_low": to_float(ib.get("low")) if ib else np.nan,
        "ib_complete": bool(ib.get("complete")) if ib else False,
        "oi_usd": to_float(futures_data.get("oi_value_usd")) if futures_data else np.nan,
        "funding": to_float(futures_data.get("funding_rate")) if futures_data else np.nan,
        "premium_pct": premium,
        "atm_strike": to_float(option_metrics.get("atm_strike")) if option_metrics else np.nan,
        "straddle": to_float(option_metrics.get("straddle")) if option_metrics else np.nan,
        "call_wall_strike": row_value(call_wall, "strike"),
        "call_wall_oi": row_value(call_wall, "oi_usd"),
        "put_wall_strike": row_value(put_wall, "strike"),
        "put_wall_oi": row_value(put_wall, "oi_usd"),
        "expiry": expiry.isoformat() if expiry else None
    }

def add_snapshot_to_history(snapshot):
    history = get_history()
    current_time = datetime.fromisoformat(snapshot["timestamp"])

    if not history:
        history.append(snapshot)
    else:
        last_time = datetime.fromisoformat(history[-1]["timestamp"])
        elapsed = (current_time - last_time).total_seconds()
        if elapsed >= 240:
            history.append(snapshot)
        elif elapsed < 0:
            history.clear()
            history.append(snapshot)

    cutoff = current_time - timedelta(days=7)
    history[:] = [item for item in history if datetime.fromisoformat(item["timestamp"]) >= cutoff]
    if len(history) > 2200: del history[:-2200]
    return history

def find_comparison_snapshot(history, now_ist, minutes, tolerance=12):
    target = now_ist - timedelta(minutes=minutes)
    candidates = []
    for item in history:
        stamp = datetime.fromisoformat(item["timestamp"])
        age_minutes = (now_ist - stamp).total_seconds() / 60
        if age_minutes > 0 and abs((stamp - target).total_seconds()) <= tolerance * 60:
            candidates.append((abs((stamp - target).total_seconds()), item))
    return min(candidates, key=lambda pair: pair[0])[1] if candidates else None

def delta_text(current, previous, suffix="", money=False):
    if not (finite(current) and finite(previous)):
        return "Not enough historical readings"
    change = float(current) - float(previous)
    if money:
        if previous:
            percentage = (change / float(previous)) * 100
            return f"{change:+,.0f} USD ({percentage:+.2f}%)"
        return f"{change:+,.0f} USD"
    return f"{change:+.2f}{suffix}"

# ============================================================
# 20. COMBINED MARKET BIAS
# ============================================================

def assess_market_bias(snapshot, history, now_ist):
    price, poc, vah, val = snapshot["price"], snapshot["poc"], snapshot["vah"], snapshot["val"]
    bullish, bearish, neutral = [], [], []

    auction_location = "Unavailable"
    if all(finite(x) for x in (price, poc, vah, val)):
        if price > vah:
            auction_location = "Above developing value"
            bullish.append("Price is above developing VAH.")
        elif price < val:
            auction_location = "Below developing value"
            bearish.append("Price is below developing VAL.")
        else:
            auction_location = "Inside developing value"
            neutral.append("Price is inside developing value; auction remains rotational unless acceptance changes.")
            
        if price > poc: neutral.append("Price is above developing POC.")
        elif price < poc: neutral.append("Price is below developing POC.")

    prior_30m = find_comparison_snapshot(history, now_ist, 30)
    prior_1h = find_comparison_snapshot(history, now_ist, 60)
    prior_4h = find_comparison_snapshot(history, now_ist, 240)
    reference = prior_1h or prior_30m

    price_change = np.nan
    oi_change = np.nan
    if reference:
        if finite(price) and finite(reference.get("price")):
            price_change = price - reference["price"]
        if finite(snapshot["oi_usd"]) and finite(reference.get("oi_usd")):
            oi_change = snapshot["oi_usd"] - reference["oi_usd"]

    oi_context = "Insufficient history to classify OI build-up or reduction."
    if finite(price_change) and finite(oi_change):
        if price_change > 0 and oi_change > 0:
            oi_context = "Price and OI both increased: participation expanded alongside the rise."
            bullish.append("Futures OI increased alongside rising price.")
        elif price_change > 0 and oi_change < 0:
            oi_context = "Price rose while OI fell: position reduction or short covering is possible."
            neutral.append("Rising price with falling OI may be short covering rather than fresh participation.")
        elif price_change < 0 and oi_change > 0:
            oi_context = "Price fell while OI increased: participation expanded alongside the decline."
            bearish.append("Futures OI increased alongside falling price.")
        elif price_change < 0 and oi_change < 0:
            oi_context = "Price and OI both fell: position reduction or long liquidation is possible."
            neutral.append("Falling price with falling OI may reflect liquidation rather than fresh short participation.")
        else:
            oi_context = "Price/OI changes are mixed or nearly unchanged."

    if reference and reference.get("cycle") == snapshot.get("cycle"):
        old_poc = reference.get("poc")
        if finite(poc) and finite(old_poc):
            if poc > old_poc: neutral.append("Developing POC has migrated higher since the comparison reading.")
            elif poc < old_poc: neutral.append("Developing POC has migrated lower since the comparison reading.")

    funding = snapshot.get("funding")
    if finite(funding): neutral.append(f"Funding is {float(funding):+.5f}% as supplied by Delta; positioning context only.")
    if finite(snapshot.get("premium_pct")): neutral.append(f"Perpetual mark premium/discount versus spot is {snapshot['premium_pct']:+.4f}%.")

    if finite(snapshot.get("call_wall_strike")) and finite(snapshot.get("put_wall_strike")):
        neutral.append(f"Nearest-expiry largest OI concentrations: call {snapshot['call_wall_strike']:,.0f}, put {snapshot['put_wall_strike']:,.0f}; these do not establish dealer direction or defended levels.")

    bull_score = len(bullish)
    bear_score = len(bearish)

    if bull_score > 0 and bear_score == 0: bias = "Bullish evidence only"
    elif bear_score > 0 and bull_score == 0: bias = "Bearish evidence only"
    elif bull_score == 0 and bear_score == 0: bias = "Neutral / insufficient directional evidence"
    else: bias = "Mixed / conflicting"

    dominant = max(bull_score, bear_score)
    difference = abs(bull_score - bear_score)

    if dominant >= 4 and difference >= 3: confidence = "Moderate"
    elif dominant >= 3 and difference >= 2: confidence = "Low–Moderate"
    else: confidence = "Low"
    if not reference and bias in ("Bullish evidence only", "Bearish evidence only"): confidence = "Low"

    comparisons = {}
    for label, minutes in (("30m", 30), ("1h", 60), ("4h", 240)):
        old = find_comparison_snapshot(history, now_ist, minutes)
        if old:
            comparisons[label] = {
                "time": old["timestamp"],
                "price": delta_text(snapshot["price"], old.get("price"), money=True),
                "oi": delta_text(snapshot["oi_usd"], old.get("oi_usd"), money=True),
                "poc": delta_text(snapshot["poc"], old.get("poc"), money=True)
            }
        else: comparisons[label] = None

    return {
        "bias": bias, "confidence": confidence, "bullish": bullish, "bearish": bearish, "neutral": neutral,
        "oi_context": oi_context, "auction_location": auction_location, "comparisons": comparisons,
        "history_count": len(history), "price_change_reference": price_change, "oi_change_reference": oi_change,
        "reference_label": "1h" if prior_1h else "30m" if prior_30m else "none", "prior_4h": prior_4h
    }

# ============================================================
# 21. COPY-READY MARKET REPORT
# ============================================================

def build_copy_report(snapshot, assessment, options_metrics, initial_balance, single_prints, cycle_df, now_ist):
    lines = [
        "BTC AMT + FUTURES + OPTIONS MARKET REPORT",
        f"Generated: {now_ist.strftime('%d-%m-%Y %H:%M:%S IST')}",
        f"Primary BTC AMT cycle: {snapshot.get('cycle')} IST (05:30 to 05:30)",
        "Cycle basis: 00:00 UTC daily boundary",
        "Options expiry reference: 17:30 IST",
        f"Bias: {assessment['bias']} | Confidence: {assessment['confidence']}",
        "",
        "1) AMT STRUCTURE",
        f"Price (Binance latest candle close): {fmt_price(snapshot.get('price'))}",
        f"Developing POC: {fmt_price(snapshot.get('poc'))}",
        f"VAH: {fmt_price(snapshot.get('vah'))} | VAL: {fmt_price(snapshot.get('val'))}",
        f"Price location: {assessment['auction_location']}",
        f"IB High: {fmt_price(snapshot.get('ib_high'))} | IB Low: {fmt_price(snapshot.get('ib_low'))} | Complete: {snapshot.get('ib_complete')}",
        f"30m candles in current cycle: {len(cycle_df)}",
        f"Single-print price rows: {len(single_prints)}",
        "Volume profile: OHLCV range-distributed approximation, not trade-level volume-at-price.",
        "",
        "2) FUTURES PARTICIPATION (DELTA BTC PERPETUAL)",
        f"Open interest: {fmt_usd(snapshot.get('oi_usd'))}",
        f"Funding rate: {snapshot.get('funding'):.6f}%" if finite(snapshot.get("funding")) else "Funding rate: Unavailable",
        f"Perpetual mark premium/discount vs spot: {snapshot.get('premium_pct'):+.5f}%" if finite(snapshot.get("premium_pct")) else "Perpetual premium/discount: Unavailable",
        f"OI interpretation: {assessment['oi_context']}",
        "",
        "3) OPTIONS POSITIONING (NEAREST EXPIRY)",
        f"Expiry: {snapshot.get('expiry') or 'Unavailable'} (17:30 IST contract reference)",
        f"ATM strike: {fmt_price(snapshot.get('atm_strike'))}",
        f"ATM straddle premium: {fmt_price(snapshot.get('straddle'))}",
        f"Largest call OI: strike {fmt_price(snapshot.get('call_wall_strike'))}, OI {fmt_usd(snapshot.get('call_wall_oi'))}",
        f"Largest put OI: strike {fmt_price(snapshot.get('put_wall_strike'))}, OI {fmt_usd(snapshot.get('put_wall_oi'))}",
        "Options OI concentrations are not guaranteed support/resistance; gamma is not dealer net gamma exposure.",
        "",
        "4) AUTOMATIC COMBINED ASSESSMENT",
        f"Bias: {assessment['bias']}",
        f"Confidence: {assessment['confidence']}",
        "Bullish evidence:"
    ]

    lines += [f"- {item}" for item in assessment["bullish"]] or ["- None identified"]
    lines.append("Bearish evidence:")
    lines += [f"- {item}" for item in assessment["bearish"]] or ["- None identified"]
    lines.append("Context / cautions:")
    lines += [f"- {item}" for item in assessment["neutral"]] or ["- None"]

    lines += ["", "5) AUTOMATIC HISTORICAL COMPARISON"]
    for label in ("30m", "1h", "4h"):
        comp = assessment["comparisons"].get(label)
        if comp:
            lines.append(f"{label}: Price {comp['price']} | OI {comp['oi']} | POC {comp['poc']} (reference {comp['time']})")
        else:
            lines.append(f"{label}: Not enough stored readings yet.")

    lines += [
        "", f"Stored readings in server memory: {assessment['history_count']}",
        "History is stored on the server and survives browser closure.",
        "A single OI snapshot cannot establish OI build-up or reduction.",
        "Please assess observed facts separately from interpretation. Do not treat OI walls, funding or gamma as standalone directional proof."
    ]

    return "\n".join(lines)

# ============================================================
# 22. DASHBOARD HEADER
# ============================================================

now = datetime.now(IST)
cycle_start = get_current_cycle_start(now)
cycle_end = cycle_start + timedelta(days=1)

st.title("₿ BTC Auction Market Observer")
st.caption("AMT | Price Action | Futures Participation | Options Positioning")

col1, col2, col3 = st.columns(3)
with col1: st.metric("Primary BTC AMT Cycle", "05:30 → 05:30 IST")
with col2: st.metric("Primary Timeframe", "30 Minutes")
with col3: st.metric("TPO Row Size", "$50")

st.caption(f"Current UTC daily cycle: {cycle_start.strftime('%d %b %Y %H:%M')} to {cycle_end.strftime('%d %b %Y %H:%M')} IST")
st.caption("00:00 UTC daily boundary | Options expiry remains independently referenced to 17:30 IST.")
st.divider()

# ============================================================
# 23. FETCH MARKET DATA
# ============================================================

with st.spinner("Fetching public market data..."):
    market_data = fetch_all_data()

candles = market_data["candles"]
futures = market_data["futures"]
options_df = market_data["options"]
expiry_date = market_data["expiry"]

for error in market_data["errors"]: st.warning(error)
if candles is None:
    st.error("Binance candle data is unavailable. AMT calculations cannot be completed.")
    st.stop()

# ============================================================
# 24. CALCULATE CURRENT AMT CYCLE
# ============================================================

cycle_df = get_cycle_candles(candles, cycle_start)
current_price = float(candles.iloc[-1]["close"])
tpo_profile = calculate_tpo_profile(cycle_df)
volume_profile = calculate_volume_profile(cycle_df)
initial_balance = calculate_initial_balance(cycle_df, cycle_start)
single_prints = calculate_single_prints(cycle_df)

# ============================================================
# 25. OPTIONS CALCULATIONS & FORENSICS
# ============================================================

options_metrics = None
if options_df is not None:
    options_metrics = calculate_options_metrics(options_df, current_price)
    # SAVE TO SQLITE DB
    save_oi_snapshot(options_df, now)

# ============================================================
# 26. AUTOMATIC SNAPSHOT AND BIAS
# ============================================================

bias_snapshot = make_market_snapshot(now, current_price, tpo_profile, initial_balance, futures, options_metrics, expiry_date)
bias_history = add_snapshot_to_history(bias_snapshot)
bias_assessment = assess_market_bias(bias_snapshot, bias_history, now)

# ============================================================
# 27. DASHBOARD TABS
# ============================================================

tab_amt, tab_futures, tab_options, tab_bias = st.tabs([
    "AMT Structure", "Futures Participation", "Options Positioning", "Market Bias & Copy Report"
])

# ============================================================
# TAB 1: AMT STRUCTURE
# ============================================================

with tab_amt:
    st.subheader("BTC Auction Structure")
    st.metric("Current BTC Price (Binance BTCUSDT)", f"${current_price:,.2f}")
    st.caption(f"30-minute candles available in current cycle: {len(cycle_df)}")
    st.divider()

    st.markdown('<div class="section-header">Developing TPO Profile</div>', unsafe_allow_html=True)
    if tpo_profile is not None:
        c1, c2, c3 = st.columns(3)
        c1.metric("Developing POC", f"${tpo_profile['poc']:,.2f}")
        c2.metric("VAH", f"${tpo_profile['vah']:,.2f}")
        c3.metric("VAL", f"${tpo_profile['val']:,.2f}")
        st.caption(f"70% TPO Value Area | Total TPO observations: {tpo_profile['total_tpo']}")

        with st.expander("View TPO Price Distribution"):
            st.dataframe(tpo_profile["table"].sort_values("Price Level", ascending=False), use_container_width=True, hide_index=True)
    else: st.warning("Insufficient candles for TPO calculation.")
    st.divider()

    st.markdown('<div class="section-header">Initial Balance</div>', unsafe_allow_html=True)
    ib1, ib2 = st.columns(2)
    if initial_balance["complete"]:
        ib1.metric("Initial Balance High", f"${initial_balance['high']:,.2f}")
        ib2.metric("Initial Balance Low", f"${initial_balance['low']:,.2f}")
    else:
        ib1.metric("Initial Balance High", "Pending")
        ib2.metric("Initial Balance Low", "Pending")
        st.info("IB requires both the 05:30 and 06:00 IST candles of the current UTC daily cycle.")
    st.divider()

    st.markdown('<div class="section-header">Single Prints</div>', unsafe_allow_html=True)
    if not single_prints.empty:
        st.dataframe(single_prints.sort_values("Price Level", ascending=False), use_container_width=True, hide_index=True)
        st.caption("Price rows touched by exactly one 30-minute candle in the current cycle.")
    else: st.info("No single-print levels identified.")
    st.divider()

    st.markdown('<div class="section-header">Volume Profile (OHLCV Approximation)</div>', unsafe_allow_html=True)
    if volume_profile is not None:
        st.metric("Estimated Volume POC", f"${volume_profile['poc']:,.2f}")
        st.dataframe(volume_profile["table"].sort_values("Price Level", ascending=False), use_container_width=True, hide_index=True)
        st.caption("Candle volume is equally allocated across the $50 rows touched by each candle. This is not trade-level volume-at-price.")
    else: st.info("Volume Profile unavailable.")


# ============================================================
# TAB 2: FUTURES PARTICIPATION
# ============================================================

with tab_futures:
    st.subheader("Delta BTC Perpetual Futures")
    if futures is not None:
        oi = futures["oi_value_usd"]
        funding = futures["funding_rate"]
        mark_price = futures["mark_price"]
        spot_price = futures["spot_price"]

        col1, col2 = st.columns(2)
        col1.metric("Total OI (USD)", f"${oi:,.2f}" if finite(oi) else "Unavailable")
        col2.metric("Current Funding Rate", f"{funding:.4f}%" if finite(funding) else "Unavailable")
        st.divider()

        st.markdown('<div class="section-header">Spot / Perpetual Premium</div>', unsafe_allow_html=True)
        if finite(mark_price) and finite(spot_price) and spot_price != 0:
            premium = ((mark_price - spot_price) / spot_price) * 100
            p1, p2, p3 = st.columns(3)
            p1.metric("Perpetual Mark Price", f"${mark_price:,.2f}")
            p2.metric("Spot Price", f"${spot_price:,.2f}")
            p3.metric("Spot / Perp Premium", f"{premium:+.5f}%")
        else: st.warning("Spot or perpetual price unavailable.")
        st.divider()

        st.markdown('<div class="section-header">Participation Interpretation Reference</div>', unsafe_allow_html=True)
        interpretation = pd.DataFrame([
            {"Price": "Rising", "OI": "Rising", "Context": "New participation with upward movement"},
            {"Price": "Rising", "OI": "Falling", "Context": "Possible short covering / position reduction"},
            {"Price": "Falling", "OI": "Rising", "Context": "New participation with downward movement"},
            {"Price": "Falling", "OI": "Falling", "Context": "Possible long liquidation / position reduction"},
            {"Price": "Sideways", "OI": "Rising", "Context": "Position build-up during balance"},
            {"Price": "Sideways", "OI": "Falling", "Context": "Position reduction during balance"}
        ])
        st.dataframe(interpretation, use_container_width=True, hide_index=True)
        st.caption("OI is aggregate open contract value. It does not independently identify whether buyers or sellers initiated positions.")
    else: st.warning("Delta perpetual futures data unavailable.")


# ============================================================
# TAB 3: OPTIONS POSITIONING & FORENSICS
# ============================================================

with tab_options:
    st.subheader("Delta BTC Options Positioning")
    st.caption("⚠️ Default classification: **Unclassified Concentration**. Neighboring strikes are shown as observed data, not confirmed spreads.")

    if options_metrics is not None and expiry_date is not None:
        st.markdown('<div class="section-header">Nearest Options Expiry</div>', unsafe_allow_html=True)
        now_options = datetime.now(IST)
        countdown = expiry_countdown(expiry_date, now_options)
        
        c1, c2 = st.columns(2)
        c1.metric("Nearest Expiry Date", expiry_date.strftime("%d %B %Y"))
        c2.metric("Time Remaining to 17:30 IST", countdown)
        st.caption("Options expiry reference: 17:30 IST. This is independent of the BTC AMT cycle.")
        st.divider()

        st.markdown('<div class="section-header">ATM Strike and Straddle</div>', unsafe_allow_html=True)
        atm1, atm2 = st.columns(2)
        atm1.metric("ATM Strike", f"${options_metrics['atm_strike']:,.2f}")
        straddle = options_metrics["straddle"]
        atm2.metric("ATM Straddle Price", f"${straddle:,.2f}" if finite(straddle) else "Unavailable")
        st.caption("Straddle = ATM call premium + ATM put premium.")
        st.divider()

        # --- FORENSIC: CALL WALL ---
        st.markdown('<div class="section-header">🔍 Call Wall Forensics (Largest OI)</div>', unsafe_allow_html=True)
        call_wall = options_metrics["call_wall"]
        if call_wall is not None:
            cw1, cw2, cw3 = st.columns(3)
            cw1.metric("Call Strike", f"${call_wall['strike']:,.2f}")
            cw2.metric("Call OI (USD)", f"${call_wall['oi_usd']:,.2f}")
            
            # Fetch 30m OI change from SQLite
            oi_status = get_oi_change(call_wall["symbol"], call_wall["oi_contracts"], now)
            cw3.metric("30m OI Trend", oi_status)
            
            lower_strike, upper_strike = options_metrics["call_neighbors"]
            st.caption(f"**Neighbor Strikes:** Lower: {lower_strike if lower_strike else 'N/A'} | Upper: {upper_strike if upper_strike else 'N/A'}")
            st.info(f"**Classification:** Unclassified Concentration. (Raw OI: {call_wall['oi_contracts']:,.0f} contracts | Volume: {call_wall['volume']:,.0f})")
        else: st.info("Call OI data unavailable.")
        st.divider()

        # --- FORENSIC: PUT WALL ---
        st.markdown('<div class="section-header">🔍 Put Wall Forensics (Largest OI)</div>', unsafe_allow_html=True)
        put_wall = options_metrics["put_wall"]
        if put_wall is not None:
            pw1, pw2, pw3 = st.columns(3)
            pw1.metric("Put Strike", f"${put_wall['strike']:,.2f}")
            pw2.metric("Put OI (USD)", f"${put_wall['oi_usd']:,.2f}")
            
            # Fetch 30m OI change from SQLite
            oi_status = get_oi_change(put_wall["symbol"], put_wall["oi_contracts"], now)
            pw3.metric("30m OI Trend", oi_status)
            
            lower_strike, upper_strike = options_metrics["put_neighbors"]
            st.caption(f"**Neighbor Strikes:** Lower: {lower_strike if lower_strike else 'N/A'} | Upper: {upper_strike if upper_strike else 'N/A'}")
            st.info(f"**Classification:** Unclassified Concentration. (Raw OI: {put_wall['oi_contracts']:,.0f} contracts | Volume: {put_wall['volume']:,.0f})")
        else: st.info("Put OI data unavailable.")
        st.divider()

        st.markdown('<div class="section-header">Nearest Expiry Options Chain</div>', unsafe_allow_html=True)
        display_columns = ["symbol", "option_type", "strike", "oi_usd", "oi_contracts", "volume", "gamma", "bid", "ask", "option_price"]
        display_df = options_df[display_columns].copy()
        display_df.columns = ["Symbol", "Type", "Strike", "OI USD", "OI Contracts", "Volume", "Gamma", "Bid", "Ask", "Premium"]
        display_df = display_df.sort_values(["Strike", "Type"])
        st.dataframe(display_df, use_container_width=True, hide_index=True)
        st.caption("Gamma is displayed as supplied by Delta. It is not an estimate of dealer net gamma exposure.")
    else: st.warning("Options positioning data is currently unavailable.")


# ============================================================
# TAB 4: MARKET BIAS AND COPY REPORT
# ============================================================

with tab_bias:
    st.subheader("Automatic Combined Market Assessment")
    st.caption("Assessment combines AMT structure with Delta futures and nearest-expiry options context. Historical comparisons are collected automatically during this app session.")

    b1, b2, b3 = st.columns(3)
    b1.metric("Combined Bias", bias_assessment["bias"])
    b2.metric("Confidence", bias_assessment["confidence"])
    b3.metric("Stored Readings", str(bias_assessment["history_count"]))
    st.markdown("#### Auction and participation evidence")

    left, right = st.columns(2)
    with left:
        st.markdown("**Bullish evidence**")
        if bias_assessment["bullish"]:
            for item in bias_assessment["bullish"]: st.write(f"• {item}")
        else: st.write("No bullish evidence identified.")
        
        st.markdown("**Bearish evidence**")
        if bias_assessment["bearish"]:
            for item in bias_assessment["bearish"]: st.write(f"• {item}")
        else: st.write("No bearish evidence identified.")

    with right:
        st.markdown("**Context and cautions**")
        if bias_assessment["neutral"]:
            for item in bias_assessment["neutral"]: st.write(f"• {item}")
        else: st.write("No additional context available.")
            
        st.markdown("**Futures OI interpretation**")
        st.write(bias_assessment["oi_context"])

    st.divider()
    st.markdown("#### Automatic historical comparison")
    comparison_rows = []
    for label in ("30m", "1h", "4h"):
        item = bias_assessment["comparisons"].get(label)
        comparison_rows.append({
            "Lookback": label,
            "Price change": (item["price"] if item else "Collecting readings"),
            "OI change": (item["oi"] if item else "Collecting readings"),
            "POC change": (item["poc"] if item else "Collecting readings"),
            "Reference time": (item["time"] if item else "—")
        })

    st.dataframe(pd.DataFrame(comparison_rows), use_container_width=True, hide_index=True)
    st.caption("The dashboard stores a reading approximately every five minutes.")

    if bias_history:
        history_df = pd.DataFrame(bias_history)
        csv = history_df.to_csv(index=False).encode('utf-8')
        st.download_button(
            label="📥 Download Full History as CSV",
            data=csv,
            file_name=f"btc_amt_history_{now.strftime('%Y-%m-%d_%H-%M')}.csv",
            mime="text/csv",
            help="Download the full snapshot history collected so far."
        )
    else:
        st.info("No history collected yet to download.")

    report_text = build_copy_report(bias_snapshot, bias_assessment, options_metrics, initial_balance, single_prints, cycle_df, now)
    st.divider()
    st.markdown("#### Copy-ready report")
    st.caption("Use the copy icon on the report block, then paste it into ChatGPT.")
    st.code(report_text, language="text")

    with st.expander("History and data-quality notes"):
        st.write("Primary AMT profile uses the UTC calendar-day boundary (00:00 UTC / 05:30 IST). Options expiry remains a separate 17:30 IST contract reference.")
        st.write("History is stored on the server (st.cache_resource) and OI Snapshots are stored in a local SQLite database (oi_forensics.db).")
        st.write("The bias is a structured interpretation, not a prediction.")
        st.write("Options OI concentrations are not confirmed support/resistance. Delta gamma is not dealer net gamma exposure.")


# ============================================================
# 28. DATA STATUS
# ============================================================

st.divider()
st.markdown("### Data Status")

status1, status2, status3 = st.columns(3)
with status1: st.metric("Binance OHLC", "Available" if candles is not None else "Unavailable")
with status2: st.metric("Delta Futures", "Available" if futures is not None else "Unavailable")
with status3: st.metric("Delta Options", "Available" if options_df is not None else "Unavailable")

last_fetch = market_data["last_successful_fetch"]
if last_fetch is not None:
    st.caption("Last successful data fetch: " + last_fetch.strftime("%d-%m-%Y %H:%M:%S %Z"))
else:
    st.warning("No successful API fetch was completed.")

st.caption("BTC AMT Research | Public market data | Visual research only | Not an execution system")

# ============================================================
# 29. AUTOMATIC REFRESH
# ============================================================

st_autorefresh(
    interval=REFRESH_INTERVAL_MS,
    key="btc_amt_auto_refresh"
)
