import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import math
import streamlit.components.v1 as components
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone, timedelta

st.set_page_config(page_title="BTC Data Terminal", layout="wide")

# ==========================================
# CONFIGURATION (OPTIONS CLUSTERING)
# ==========================================
CLUSTER_ABSORPTION_PCT = 0.30
MAX_CLUSTER_WIDTH = 5
OI_CLUSTER_MIN_USD = 5_000_000
MARK_TOLERANCE_PCT = 0.02 

# ==========================================
# PERSISTENT STATE (Flight Recorder)
# ==========================================
@st.cache_resource
def get_persistent_state():
    return {
        "prev_pcr": 0.0,
        "prev_max_pain": 0.0,
        "prev_call_wall": 0.0,
        "prev_put_wall": 0.0,
        "prev_amt_state": -1,
        "prev_price": 0.0,
        "session_start_straddle": None,
        "alert_history": [],
        "options_flow_history": [],
        "wall_status_log": [],
        "trade_journal": "",

        # Gamma Break State Machine
        "last_closed_candle_time": 0,
        "previous_candle_time": 0,
        "put_gamma_break_state": "SAFE",
        "active_broken_put_wall": 0.0
    }

p_state = get_persistent_state()

# Backward compatibility with an existing cached state
p_state.setdefault("last_closed_candle_time", 0)
p_state.setdefault("previous_candle_time", 0)
p_state.setdefault("put_gamma_break_state", "SAFE")
p_state.setdefault("active_broken_put_wall", 0.0)

# ==========================================
# TELEGRAM ALERT FUNCTION (UPDATED FOR RETRY)
# ==========================================
def send_telegram_message(message):
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
    except KeyError:
        st.error("Telegram secrets not found.")
        return False
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        response = requests.post(url, json=payload, timeout=5)
        if response.status_code == 200:
            return True
        else:
            st.error(f"Telegram API error: {response.status_code}")
            return False
    except Exception as e:
        st.error(f"Telegram connection error: {e}")
        return False

# ==========================================
# SIDEBAR: MANUAL AMT OVERRIDES & JOURNAL
# ==========================================
qp = st.query_params
default_vah = float(qp.get("vah", 0.0))
default_val = float(qp.get("val", 0.0))
default_poc = float(qp.get("poc", 0.0))

st.sidebar.header("🛠️ Manual AMT Overrides")
input_vah = st.sidebar.number_input("Prior VAH", value=default_vah, step=50.0, format="%.2f")
input_val = st.sidebar.number_input("Prior VAL", value=default_val, step=50.0, format="%.2f")
input_poc = st.sidebar.number_input("Prior POC", value=default_poc, step=50.0, format="%.2f")

if st.sidebar.button("Apply Overrides"):
    st.query_params["vah"] = str(input_vah)
    st.query_params["val"] = str(input_val)
    st.query_params["poc"] = str(input_poc)
    st.rerun()

if st.sidebar.button("Clear Overrides"):
    st.query_params.clear()
    st.rerun()

st.sidebar.markdown("---")
st.sidebar.header("📝 Trade Journal")
st.sidebar.caption("RESEARCH ONLY - Saved across reloads")
journal_input = st.sidebar.text_area("Log Trade Details", value=p_state["trade_journal"], height=200)
if st.sidebar.button("Save Journal"):
    p_state["trade_journal"] = journal_input
    st.sidebar.success("Journal saved!")

st.title("⚡ BTC AMT & Delta Options Terminal")

manual_values_valid = (input_vah > 0 and input_val > 0 and input_poc > 0 and input_vah >= input_poc >= input_val)
final_vah, final_val, final_poc = np.nan, np.nan, np.nan

alert_triggered = False
alert_message = ""

# ==========================================
# SECTION 1: BINANCE DATA & AMT MATH (UNTOUCHED)
# ==========================================
@st.cache_data(ttl=180)
def fetch_amt_data():
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    endpoints = ["https://api.binance.com", "https://api1.binance.com", "https://api2.binance.com", "https://api3.binance.com", "https://data-api.binance.vision"]
    
    df = None
    for base in endpoints:
        try:
            url = f"{base}/api/v3/klines?symbol=BTCUSDT&interval=30m&limit=1000"
            response = session.get(url, timeout=10)
            response.raise_for_status()
            res = response.json()
            if not res or not isinstance(res, list): continue
                
            df = pd.DataFrame(
                res,
                columns=[
                    'Date', 'open', 'high', 'low', 'close',
                    'volume', 'close_time', 'qav', 'num_trades',
                    'tbbav', 'tbqav', 'ignore'
                ]
            )

            # Candle opening time in IST
            df['Date'] = pd.to_datetime(
                df['Date'], unit='ms', utc=True
            ).dt.tz_convert('Asia/Kolkata')

            # Preserve actual Binance candle closing time in UTC
            df['CloseTimeUTC'] = pd.to_datetime(
                df['close_time'], unit='ms', utc=True
            )

            # Preserve the original close timestamp in milliseconds
            df['CloseTimeMs'] = pd.to_numeric(
                df['close_time'], errors='coerce'
            )

            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            
            df['ShiftedDate'] = df['Date'] - pd.Timedelta(hours=5, minutes=30)
            df = df[['Date', 'CloseTimeUTC', 'CloseTimeMs', 'open', 'high', 'low', 'close', 'ShiftedDate']]
            df = df.sort_values('Date').reset_index(drop=True)
            return df
        except Exception:
            continue
    return pd.DataFrame()

@st.cache_data(ttl=180)
def fetch_global_funding():
    try:
        url = "https://api.india.delta.exchange/v2/tickers?contract_types=perpetual_futures"
        res = requests.get(url, timeout=10).json()
        for ticker in res.get("result", []):
            if ticker.get("symbol") == "BTCUSD":
                funding_rate = float(ticker.get("funding_rate", 0)) * 100
                mark_price = float(ticker.get("mark_price", 0))
                return funding_rate, mark_price
        return None, None
    except Exception:
        return None, None

def calculate_tpo(session_df):
    if session_df.empty: return np.nan, np.nan, np.nan
    row_size = 50.0
    value_pct = 0.70
    high = session_df['high'].max()
    low = session_df['low'].min()
    if high - low <= 0: return np.nan, np.nan, np.nan
    
    base_bucket = math.floor(low / row_size)
    num_buckets = math.floor(high / row_size) - base_bucket + 1
    tpo = np.zeros(num_buckets)
    
    for _, row in session_df.iterrows():
        lo_idx = max(0, int(math.floor(row['low'] / row_size) - base_bucket))
        hi_idx = min(num_buckets - 1, int(math.floor(row['high'] / row_size) - base_bucket))
        for i in range(lo_idx, hi_idx + 1): tpo[i] += 1
            
    poc_idx = np.argmax(tpo)
    max_val = np.max(tpo)
    tied_indices = np.where(tpo == max_val)[0]
    if len(tied_indices) > 1:
        profile_mid = (num_buckets - 1) / 2.0
        distances = np.abs(tied_indices - profile_mid)
        poc_idx = tied_indices[np.argmin(distances)]
        
    target = math.ceil(np.sum(tpo) * value_pct)
    covered = tpo[poc_idx]
    up, dn = poc_idx, poc_idx
    
    while covered < target:
        can_down, can_up = dn > 0, up < num_buckets - 1
        if not can_down and not can_up: break
        d = tpo[dn - 1] if can_down else -1
        u = tpo[up + 1] if can_up else -1
        if u > d: up += 1; covered += u
        elif d > u: dn -= 1; covered += d
        else:
            if can_down: dn -= 1; covered += d
            if covered < target and can_up: up += 1; covered += u
                
    return (base_bucket + up) * row_size + row_size/2, (base_bucket + dn) * row_size + row_size/2, (base_bucket + poc_idx) * row_size + row_size/2

def calculate_session_journey(df, prev_vah, prev_val):
    if df.empty: return pd.DataFrame()
    
    cycle_date = df['ShiftedDate'].iloc[-1].date()
    cycle_start = pd.Timestamp(cycle_date).tz_localize('Asia/Kolkata') + pd.Timedelta(hours=5, minutes=30)
    
    asia_end = cycle_start + pd.Timedelta(hours=8)
    europe_end = cycle_start + pd.Timedelta(hours=12)
    us_cash_end = cycle_start + pd.Timedelta(hours=20)
    cycle_end = cycle_start + pd.Timedelta(hours=24)

    prev_us_over_start = cycle_start - pd.Timedelta(hours=4)
    prev_us_over_end = cycle_start
    
    sessions = []
    
    prev_us_df = df[(df['Date'] >= prev_us_over_start) & (df['Date'] < prev_us_over_end)]
    if not prev_us_df.empty:
        vah, val, poc = calculate_tpo(prev_us_df)
        close = prev_us_df['close'].iloc[-1]
        high = prev_us_df['high'].max()
        low = prev_us_df['low'].min()
        
        macro_bias = "⚪ Inside Prior Value"
        if not pd.isna(prev_vah) and not pd.isna(prev_val):
            if close > prev_vah: macro_bias = "🟢 Bullish (Above Prior VAH)"
            elif close < prev_val: macro_bias = "🔴 Bearish (Below Prior VAL)"
            elif high > prev_vah and close < prev_vah: macro_bias = "⚠️ Bull Trap (Rejected Prior VAH)"
            elif low < prev_val and close > prev_val: macro_bias = "⚠️ Bear Trap (Rejected Prior VAL)"
                
        micro_bias = "⚪ Balanced (Inside Value)"
        if not pd.isna(vah) and not pd.isna(val):
            if close > vah: micro_bias = "🟢 Strong Close (Above Session VAH)"
            elif close < val: micro_bias = "🔴 Weak Close (Below Session VAL)"

        sessions.append({
            "Session": "US Overnight (Prev)", "Open": prev_us_df['open'].iloc[0], "High": high, "Low": low, "Close": close,
            "Macro Bias": macro_bias, "Micro Bias": micro_bias, "POC": poc, "VAH": vah, "VAL": val
        })

    for name, session_data in [
        ("Asia (05:30-13:30)", df[(df['Date'] >= cycle_start) & (df['Date'] < asia_end)]),
        ("Europe (13:30-17:30)", df[(df['Date'] >= asia_end) & (df['Date'] < europe_end)]),
        ("US Cash (17:30-01:30)", df[(df['Date'] >= europe_end) & (df['Date'] < us_cash_end)]),
        ("US Overnight (Current)", df[(df['Date'] >= us_cash_end) & (df['Date'] < cycle_end)])
    ]:
        if session_data.empty: continue
        vah, val, poc = calculate_tpo(session_data)
        
        close = session_data['close'].iloc[-1]
        high = session_data['high'].max()
        low = session_data['low'].min()
        
        macro_bias = "⚪ Inside Prior Value"
        if not pd.isna(prev_vah) and not pd.isna(prev_val):
            if close > prev_vah: macro_bias = "🟢 Bullish (Above Prior VAH)"
            elif close < prev_val: macro_bias = "🔴 Bearish (Below Prior VAL)"
            elif high > prev_vah and close < prev_vah: macro_bias = "⚠️ Bull Trap (Rejected Prior VAH)"
            elif low < prev_val and close > prev_val: macro_bias = "⚠️ Bear Trap (Rejected Prior VAL)"
                
        micro_bias = "⚪ Balanced (Inside Value)"
        if not pd.isna(vah) and not pd.isna(val):
            if close > vah: micro_bias = "🟢 Strong Close (Above Session VAH)"
            elif close < val: micro_bias = "🔴 Weak Close (Below Session VAL)"

        sessions.append({
            "Session": name, "Open": session_data['open'].iloc[0], "High": high, "Low": low, "Close": close,
            "Macro Bias": macro_bias, "Micro Bias": micro_bias, "POC": poc, "VAH": vah, "VAL": val
        })
        
    return pd.DataFrame(sessions)

# ==========================================
# SECTION 2: PROJECT ECHO STATE ENGINE (UNTOUCHED)
# ==========================================
def generate_state_journal(df, manual_valid, manual_vah, manual_val, manual_poc):
    if df.empty: return pd.DataFrame(), manual_valid
    
    df['prev_close'] = df['close'].shift(1)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['prev_close'])
    df['tr2'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr20'] = df['tr'].ewm(alpha=1/20, adjust=False).mean()
    
    today_str = df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    today_df = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') == today_str].copy()
    yesterday_df = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str].copy()
    
    if yesterday_df.empty or today_df.empty: return pd.DataFrame(), manual_valid

    last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
    
    if manual_valid:
        prevVAH, prevVAL, prevPOC = manual_vah, manual_val, manual_poc
    else:
        prevVAH, prevVAL, prevPOC = calculate_tpo(prev_day_df)
    
    if pd.isna(prevVAH) or pd.isna(prevVAL) or pd.isna(prevPOC): return pd.DataFrame(), manual_valid

    journal = []
    auctionState = 0
    discoveryStartBar = 0
    cycleBars = 0
    aboveValueBars = 0
    belowValueBars = 0
    insideValueBars = 0
    acceptancePullbackBarsDown = 0
    acceptanceConfirmedDown = False
    pocAboveBars = 0
    pocBelowBars = 0
    reentryInsideBars = 0
    prev_currVAH, prev_currVAL, prev_currPOC = np.nan, np.nan, np.nan
    
    state_names = {
        0: "BUILDING", 1: "DEVELOPMENT", 2: "DISCOVERY ABOVE", 3: "DISCOVERY BELOW",
        4: "ACCEPTANCE UP", 5: "ACCEPTANCE DOWN", 6: "POC BREAK ABOVE",
        7: "POC BREAK BELOW", 8: "NEW AREA UP", 9: "NEW AREA DOWN", 10: "FAILED DISC UP",
        11: "FAILED DISC DOWN", 12: "ACC FAILURE UP", 13: "ACC FAILURE DOWN",
        14: "RE-ENTRY CONFIRMED", 15: "STALLED ABOVE", 16: "STALLED BELOW",
        17: "FAILED UP (CAND)", 18: "FAILED DOWN (CAND)"
    }

    for i in range(len(today_df)):
        bar = today_df.iloc[i]
        current_bar_df = today_df.iloc[:i+1]
        cycleBars += 1
        
        currVAH, currVAL, currPOC = calculate_tpo(current_bar_df)
        close = bar['close']
        
        if close > prevVAH: aboveValueBars += 1; belowValueBars = 0; insideValueBars = 0
        elif close < prevVAL: belowValueBars += 1; aboveValueBars = 0; insideValueBars = 0
        else: aboveValueBars = 0; belowValueBars = 0; insideValueBars += 1

        if not pd.isna(currPOC):
            if currPOC > prevVAH: pocAboveBars += 1; pocBelowBars = 0
            elif currPOC < prevVAL: pocBelowBars += 1; pocAboveBars = 0
            else: pocAboveBars = 0; pocBelowBars = 0

        barRange = bar['high'] - bar['low']
        wideBar = not pd.isna(bar['atr20']) and barRange > bar['atr20'] * 1.5
        significantBar = wideBar
        
        effectiveTimeout = 6
        if not pd.isna(bar['atr20']):
            effectiveTimeout = max(3, min(20, round(6 * (bar['atr20'] / 250.0))))
        discoveryTimedOut = (i - discoveryStartBar) >= effectiveTimeout

        if belowValueBars >= 2:
            if bar['high'] >= prevVAL and close < prevVAL and significantBar:
                acceptancePullbackBarsDown += 1
                if acceptancePullbackBarsDown >= 2: acceptanceConfirmedDown = True
            elif close > prevVAL:
                acceptancePullbackBarsDown = 0; acceptanceConfirmedDown = False
        else:
            acceptancePullbackBarsDown = 0; acceptanceConfirmedDown = False

        vaShiftDown = not pd.isna(prev_currVAH) and currVAH < prev_currVAH and currVAL < prev_currVAL
        pocShiftDown = not pd.isna(prev_currPOC) and (prev_currPOC - currPOC) >= (50.0 * 1.0)
        valueMigrationDown = vaShiftDown and pocShiftDown
        newValueDevelopingDown = pocBelowBars >= 2 and cycleBars >= 3

        if auctionState == 0:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i
            elif cycleBars >= 3: auctionState = 1
        elif auctionState == 1:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i
        elif auctionState == 2:
            if close < prevVAL: auctionState = 3; discoveryStartBar = i
            elif close <= prevVAH: auctionState = 17
            elif discoveryTimedOut: auctionState = 15
        elif auctionState == 3:
            if acceptanceConfirmedDown: auctionState = 5
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close >= prevVAL: auctionState = 18
            elif discoveryTimedOut: auctionState = 16
        elif auctionState == 5:
            if valueMigrationDown: auctionState = 7
            elif close >= prevVAL: auctionState = 13
        elif auctionState == 7:
            if newValueDevelopingDown: auctionState = 9
            elif close >= prevVAL: auctionState = 13
        elif auctionState == 17:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i
            elif insideValueBars >= 2: auctionState = 10
        elif auctionState == 18:
            if close < prevVAL: auctionState = 3; discoveryStartBar = i
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif insideValueBars >= 2: auctionState = 11
        elif auctionState == 10:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i
            elif insideValueBars >= 2: auctionState = 14
        elif auctionState == 11:
            if close < prevVAL: auctionState = 3; discoveryStartBar = i
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif insideValueBars >= 2: auctionState = 14
        elif auctionState == 14:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i
            else:
                reentryInsideBars += 1
                if reentryInsideBars >= 3 and cycleBars >= 3:
                    auctionState = 1
                    reentryInsideBars = 0
        elif auctionState == 16:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close >= prevVAL and close <= prevVAH: auctionState = 11
            
        if auctionState != 14:
            reentryInsideBars = 0
            
        prev_currVAH, prev_currVAL, prev_currPOC = currVAH, currVAL, currPOC
        
        if i == 0 or auctionState != journal[-1]['Code'] or i == len(today_df) - 1:
            journal.append({
                "Time": bar['Date'].strftime('%H:%M'), "Close": close, "Code": auctionState,
                "State Name": state_names.get(auctionState, "UNKNOWN"),
                "Dev VAH": currVAH, "Dev POC": currPOC, "Dev VAL": currVAL,
            })
    return pd.DataFrame(journal), manual_valid

# ==========================================
# SECTION 3: DELTA OPTIONS DATA (UPDATED)
# ==========================================
def parse_timestamp(ts):
    if ts is None: return None
    try:
        if isinstance(ts, (int, float)):
            if ts > 1e11: ts = ts / 1000.0
            return datetime.datetime.fromtimestamp(ts, tz=timezone.utc)
        if isinstance(ts, str):
            if ts.isdigit():
                val = float(ts)
                if val > 1e11: val = val / 1000.0
                return datetime.datetime.fromtimestamp(val, tz=timezone.utc)
            return datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except Exception:
        pass
    return None

def find_oi_clusters(strike_ladder, df_opt, opt_type, spot_price, straddle_price):
    if not strike_ladder or pd.isna(straddle_price) or straddle_price <= 0:
        return [], [], []

    df_type = df_opt[df_opt['Type'] == opt_type].copy()
    if df_type.empty: return [], [], []
    
    df_type = df_type.dropna(subset=['OI_USD'])
    if df_type.empty: return [], [], []
    
    df_sorted = df_type.sort_values('OI_USD', ascending=False)
    claimed = set()
    clusters = []
    
    for _, row in df_sorted.iterrows():
        peak_strike = row['Strike']
        if peak_strike in claimed: continue
        
        peak_oi = row['OI_USD']
        if peak_oi < OI_CLUSTER_MIN_USD: continue
        
        if peak_strike not in strike_ladder: continue
        peak_idx = strike_ladder.index(peak_strike)
        
        current_cluster = [peak_strike]
        claimed.add(peak_strike)
        
        left = peak_idx - 1
        while left >= 0 and len(current_cluster) < MAX_CLUSTER_WIDTH:
            test_strike = strike_ladder[left]
            if test_strike in claimed: break
            row_data = df_type[df_type['Strike'] == test_strike]
            if row_data.empty: break
            test_oi = row_data['OI_USD'].values[0]
            if pd.isna(test_oi) or test_oi < (peak_oi * CLUSTER_ABSORPTION_PCT): break
            current_cluster.append(test_strike); claimed.add(test_strike); left -= 1
                
        right = peak_idx + 1
        while right < len(strike_ladder) and len(current_cluster) < MAX_CLUSTER_WIDTH:
            test_strike = strike_ladder[right]
            if test_strike in claimed: break
            row_data = df_type[df_type['Strike'] == test_strike]
            if row_data.empty: break
            test_oi = row_data['OI_USD'].values[0]
            if pd.isna(test_oi) or test_oi < (peak_oi * CLUSTER_ABSORPTION_PCT): break
            current_cluster.append(test_strike); claimed.add(test_strike); right += 1
                
        cluster_df = df_type[df_type['Strike'].isin(current_cluster)]
        total_oi = cluster_df['OI_USD'].sum()
        if total_oi == 0: continue
        
        vwas = (cluster_df['Strike'] * cluster_df['OI_USD']).sum() / total_oi
        
        if opt_type == "Call" and vwas <= spot_price: continue
        if opt_type == "Put" and vwas >= spot_price: continue

        dist = abs(vwas - spot_price) / straddle_price
        
        clusters.append({"Type": opt_type, "VWAS": vwas, "OI_USD": total_oi, "Dist": dist, "Strikes": current_cluster})
        
    near, mid, far = [], [], []
    for c in clusters:
        if c['Dist'] < 0.5: near.append(c)
        elif c['Dist'] < 1.0: mid.append(c)
        else: far.append(c)
        
    near.sort(key=lambda x: x['Dist']); mid.sort(key=lambda x: x['Dist']); far.sort(key=lambda x: x['Dist'])
    return near, mid, far

@st.cache_data(ttl=180)
def fetch_delta_options():
    BASE_URL = "https://api.india.delta.exchange/v2"
    
    def fetch_paginated_data(url):
        results = []
        seen_cursors = set()
        page_count = 0
        max_pages = 20
        is_complete = False
        
        while url and page_count < max_pages:
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                results.extend(data.get("result", []))
                meta = data.get("meta", {})
                after = meta.get("after")
                
                if not after or after in seen_cursors:
                    is_complete = True
                    break
                    
                seen_cursors.add(after)
                base_url = url.split("?")[0]
                query_params = url.split("?")[1]
                params = [p for p in query_params.split("&") if not p.startswith("after=")]
                params.append(f"after={after}")
                url = f"{base_url}?{'&'.join(params)}"
                page_count += 1
            except Exception:
                break
        return results, is_complete

    try:
        products, prod_complete = fetch_paginated_data(f"{BASE_URL}/products?states=live&page_size=1000")
        btc_options = [p for p in products if p.get("underlying_asset", {}).get("symbol") == "BTC" and p.get("contract_type") in ("call_options", "put_options")]

        if not btc_options: return pd.DataFrame(), None, None, None, None, None, None, False

        now_utc = datetime.datetime.now(timezone.utc)
        valid_expiries = []
        
        for opt in btc_options:
            sym = opt.get("symbol", "")
            parts = sym.split("-")
            if len(parts) >= 4:
                exp_str = parts[3]
                try:
                    settlement_time_str = opt.get("settlement_time") or opt.get("expiry_time")
                    dt_utc = parse_timestamp(settlement_time_str)
                    source = "metadata"
                    
                    if dt_utc is None:
                        dt_naive = datetime.datetime.strptime(exp_str, "%d%m%y")
                        dt_utc = dt_naive.replace(hour=12, minute=0, second=0, tzinfo=timezone.utc)
                        source = "fallback_12utc"
                        
                    if dt_utc > now_utc:
                        valid_expiries.append({"expiry_str": exp_str, "dt_utc": dt_utc, "source": source})
                except Exception:
                    pass
                    
        if not valid_expiries: return pd.DataFrame(), None, None, None, None, None, None, False
        
        nearest = min(valid_expiries, key=lambda x: x["dt_utc"])
        nearest_expiry = nearest["expiry_str"]
        expiry_dt = nearest["dt_utc"]
        expiry_source = nearest["source"]
        
        filtered_options = [opt for opt in btc_options if len(opt.get("symbol", "").split("-")) >= 4 and opt.get("symbol", "").split("-")[3] == nearest_expiry]

        strike_ladder = sorted(list(set(float(p.get("strike_price")) for p in filtered_options if p.get("strike_price") is not None)))

        contract_size = 0.001
        if filtered_options:
            try: contract_size = float(filtered_options[0].get("contract_value", 0.001))
            except: pass

        tickers, tick_complete = fetch_paginated_data(f"{BASE_URL}/tickers?contract_types=call_options,put_options&page_size=1000")
        ticker_map = {t.get("symbol"): t for t in tickers if t.get("symbol")}
        is_chain_complete = prod_complete and tick_complete

        chain = []
        
        for opt in filtered_options:
            sym = opt.get("symbol")
            ticker = ticker_map.get(sym)
            if not ticker: continue

            try:
                strike = opt.get("strike_price")
                if strike is None:
                    parts = sym.split("-")
                    if len(parts) >= 3:
                        raw_strike = parts[2].replace(",", "").strip().upper()
                        strike = float(raw_strike.replace("K", "")) * 1000 if "K" in raw_strike else float(raw_strike)
                
                strike = float(strike)
                opt_type = "Call" if opt.get("contract_type") == "call_options" else "Put"
                oi_usd = float(ticker.get("oi_value_usd")) if ticker.get("oi_value_usd") is not None else None
                oi_contracts = float(ticker.get("oi_contracts") or ticker.get("oi")) if (ticker.get("oi_contracts") or ticker.get("oi")) is not None else None
                
                quotes = ticker.get("quotes", {})
                bid = float(quotes.get("best_bid") or ticker.get("best_bid")) if (quotes.get("best_bid") or ticker.get("best_bid")) is not None else None
                ask = float(quotes.get("best_ask") or ticker.get("best_ask")) if (quotes.get("best_ask") or ticker.get("best_ask")) is not None else None
                mark = float(ticker.get("mark_price")) if ticker.get("mark_price") is not None else None

                greeks = ticker.get("greeks") or {}
                gamma = float(greeks.get("gamma") or "0")
                iv = float(quotes.get("mark_iv") or quotes.get("ask_iv") or "0") * 100
                ts_val = ticker.get("timestamp") or ticker.get("updated_at")
                
                chain.append({
                    "Strike": strike, "Type": opt_type, 
                    "Bid": bid, "Ask": ask, "Mark": mark, 
                    "OI_USD": oi_usd, "OI_Contracts": oi_contracts, 
                    "Gamma": gamma, "IV": iv, "Timestamp": ts_val
                })
            except (ValueError, TypeError):
                continue

        df = pd.DataFrame(chain)
        if df.empty: return pd.DataFrame(), nearest_expiry, expiry_dt, expiry_source, contract_size, None, None, False

        df_pivot = df.pivot_table(index="Strike", columns="Type", values=["Bid", "Ask", "Mark", "OI_USD", "OI_Contracts", "Gamma", "IV"], aggfunc="first").reset_index()

        new_cols = []
        for col in df_pivot.columns:
            if isinstance(col, tuple): new_cols.append("_".join([str(c) for c in col if c != ""]).strip("_"))
            else: new_cols.append(str(col))
        df_pivot.columns = new_cols

        expected_columns = ['Bid_Call', 'Ask_Call', 'Mark_Call', 'OI_USD_Call', 'OI_Contracts_Call', 'Gamma_Call', 'IV_Call', 'Bid_Put', 'Ask_Put', 'Mark_Put', 'OI_USD_Put', 'OI_Contracts_Put', 'Gamma_Put', 'IV_Put']
        for col in expected_columns:
            if col not in df_pivot.columns: df_pivot[col] = np.nan

        return df_pivot, nearest_expiry, expiry_dt, expiry_source, contract_size, df, strike_ladder, is_chain_complete

    except Exception as e:
        return pd.DataFrame(), None, None, None, None, None, None, False

# ==========================================
# SECTION 4: PRE-CALCULATE DATA & ALERTS
# ==========================================
df = fetch_amt_data()
binance_status = "🟢 Connected"
latest_price = 0
options_df = pd.DataFrame()
filtered_options = pd.DataFrame()
call_oi_wall, put_oi_wall, call_gamma_wall, put_gamma_wall = None, None, None, None
true_max_pain = 0
pcr = 0
atm_row, atm_strike = None, 0
straddle_valid = False
straddle_reason = ""
straddle_price = np.nan
quote_freshness = "Unverified"
atm_call_age = "N/A"
atm_put_age = "N/A"
time_str = "Unknown"
settlement_ist = "Unknown"

options_bias_score = 0
nearest_call_dist = None
nearest_put_dist = None

# Pending flags for state machine
pending_gamma_breach = False
pending_broken_wall = 0.0

funding_rate, funding_mark_price = fetch_global_funding()

if not df.empty:
    today_str = df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    yesterday_df = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str]
    
    if not yesterday_df.empty:
        last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
        prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
        
        if manual_values_valid:
            final_vah, final_val, final_poc = input_vah, input_val, input_poc
        else:
            final_vah, final_val, final_poc = calculate_tpo(prev_day_df)
    else:
        if manual_values_valid:
            final_vah, final_val, final_poc = input_vah, input_val, input_poc

    latest_price = df['close'].iloc[-1]

journal_res = generate_state_journal(df, manual_values_valid, input_vah, input_val, input_poc)
current_amt_state = 0
current_amt_name = "UNKNOWN"
if journal_res is not None:
    journal_df_check, _ = journal_res
    if not journal_df_check.empty:
        current_amt_state = journal_df_check.iloc[-1]['Code']
        current_amt_name = journal_df_check.iloc[-1]['State Name']

options_df, loaded_expiry, expiry_dt, expiry_source, dynamic_contract_size, raw_chain_df, strike_ladder, is_chain_complete = fetch_delta_options()

delta_status = "🟢 Connected" if not options_df.empty else "🔴 Failed"

# Time Calculations
if expiry_dt:
    time_remaining = expiry_dt - datetime.datetime.now(timezone.utc)
    time_str = str(time_remaining).split('.')[0]
    ist_tz = timezone(timedelta(hours=5, minutes=30))
    settlement_ist = expiry_dt.astimezone(ist_tz).strftime('%Y-%m-%d %H:%M:%S')

if not options_df.empty and latest_price > 0:
    options_df['GEX_Call'] = options_df['Gamma_Call'] * options_df['OI_Contracts_Call'] * latest_price * dynamic_contract_size
    options_df['GEX_Put'] = options_df['Gamma_Put'] * options_df['OI_Contracts_Put'] * latest_price * dynamic_contract_size
    
    options_df['Dist_to_Spot'] = abs(options_df['Strike'] - latest_price)
    atm_strike = options_df.loc[options_df['Dist_to_Spot'].idxmin(), 'Strike']
    atm_row = options_df[options_df['Strike'] == atm_strike].iloc[0]
    
    cb, ca, cm = atm_row['Bid_Call'], atm_row['Ask_Call'], atm_row['Mark_Call']
    pb, pa, pm = atm_row['Bid_Put'], atm_row['Ask_Put'], atm_row['Mark_Put']
    
    checks_passed = True
    if pd.isna(cb) or pd.isna(ca) or pd.isna(pb) or pd.isna(pa): 
        checks_passed = False; straddle_reason = "Missing Bid/Ask"
    elif cb <= 0 or ca <= 0 or pb <= 0 or pa <= 0: 
        checks_passed = False; straddle_reason = "Bid/Ask <= 0"
    elif cb > ca or pb > pa: 
        checks_passed = False; straddle_reason = "Crossed Market (Bid > Ask)"
    elif pd.isna(cm) or pd.isna(pm) or not (cb * (1-MARK_TOLERANCE_PCT) <= cm <= ca * (1+MARK_TOLERANCE_PCT)) or not (pb * (1-MARK_TOLERANCE_PCT) <= pm <= pa * (1+MARK_TOLERANCE_PCT)): 
        checks_passed = False; straddle_reason = "Mark/Quote Inconsistency"
    
    call_ts = raw_chain_df[(raw_chain_df['Strike'] == atm_strike) & (raw_chain_df['Type'] == 'Call')]['Timestamp'].values
    put_ts = raw_chain_df[(raw_chain_df['Strike'] == atm_strike) & (raw_chain_df['Type'] == 'Put')]['Timestamp'].values
    
    now_utc = datetime.datetime.now(timezone.utc)
    if len(call_ts) > 0:
        dt_c = parse_timestamp(call_ts[0])
        if dt_c:
            age_c = (now_utc - dt_c).total_seconds()
            atm_call_age = f"{age_c:.0f}s"
    if len(put_ts) > 0:
        dt_p = parse_timestamp(put_ts[0])
        if dt_p:
            age_p = (now_utc - dt_p).total_seconds()
            atm_put_age = f"{age_p:.0f}s"

    if checks_passed:
        straddle_price = cm + pm
        straddle_valid = True
        if p_state["session_start_straddle"] is None:
            p_state["session_start_straddle"] = straddle_price
    else:
        straddle_valid = False
        straddle_price = np.nan
        quote_freshness = "Unverified"

    lower_bound = latest_price * 0.90
    upper_bound = latest_price * 1.10
    filtered_options = options_df[(options_df['Strike'] >= lower_bound) & (options_df['Strike'] <= upper_bound)].copy()

    if not filtered_options.empty:
        temp_filt = filtered_options.fillna(0)
        call_oi_wall = temp_filt.loc[temp_filt['OI_USD_Call'].idxmax()]
        put_oi_wall = temp_filt.loc[temp_filt['OI_USD_Put'].idxmax()]
        call_gamma_wall = temp_filt.loc[temp_filt['GEX_Call'].idxmax()]
        put_gamma_wall = temp_filt.loc[temp_filt['GEX_Put'].idxmax()]
        
        total_calls_usd = temp_filt['OI_USD_Call'].sum()
        total_puts_usd = temp_filt['OI_USD_Put'].sum()
        pcr = total_puts_usd / total_calls_usd if total_calls_usd > 0 else 0

        if is_chain_complete:
            all_strikes = options_df['Strike'].tolist()
            all_call_oi = dict(zip(options_df['Strike'], options_df['OI_Contracts_Call'].fillna(0)))
            all_put_oi = dict(zip(options_df['Strike'], options_df['OI_Contracts_Put'].fillna(0)))
            pain_dict = {}
            for settle in all_strikes:
                total_pain = 0
                for strike in all_strikes:
                    if settle > strike: total_pain += all_call_oi.get(strike, 0) * (settle - strike)
                    if settle < strike: total_pain += all_put_oi.get(strike, 0) * (strike - settle)
                pain_dict[settle] = total_pain
            true_max_pain = min(pain_dict, key=pain_dict.get)
        else:
            true_max_pain = np.nan

        if straddle_valid and raw_chain_df is not None and strike_ladder is not None:
            near_call, mid_call, far_call = find_oi_clusters(strike_ladder, raw_chain_df, "Call", latest_price, straddle_price)
            near_put, mid_put, far_put = find_oi_clusters(strike_ladder, raw_chain_df, "Put", latest_price, straddle_price)
            
            if pcr > 0 and pcr < 0.70: options_bias_score += 1
            elif pcr > 1.20: options_bias_score -= 1
            
            call_iv_val = atm_row['IV_Call']
            put_iv_val = atm_row['IV_Put']
            if not pd.isna(call_iv_val) and not pd.isna(put_iv_val):
                if call_iv_val > put_iv_val: options_bias_score += 1
                elif put_iv_val > call_iv_val: options_bias_score -= 1
                
            all_calls = near_call + mid_call + far_call
            all_puts = near_put + mid_put + far_put
            if all_calls: nearest_call_dist = all_calls[0]['Dist']
            if all_puts: nearest_put_dist = all_puts[0]['Dist']
            
            if nearest_call_dist is not None and nearest_put_dist is not None:
                if nearest_call_dist < nearest_put_dist: options_bias_score -= 1
                elif nearest_put_dist < nearest_call_dist: options_bias_score += 1
        else:
            near_call, mid_call, far_call = [], [], []
            near_put, mid_put, far_put = [], [], []

        # ==========================================================
        # PUT GAMMA WALL BREAK STATE MACHINE
        # 30M COMPLETED-CANDLE CONFIRMATION
        # NOTIFICATION ONLY — DOES NOT MODIFY TRADING ENGINE
        # ==========================================================

        notification_put_wall = None
        notification_candidates = filtered_options.copy()
        required_cols = ['Strike', 'Gamma_Put', 'OI_Contracts_Put', 'GEX_Put']

        if all(col in notification_candidates.columns for col in required_cols):
            notification_candidates = notification_candidates.dropna(subset=required_cols)
            notification_candidates = notification_candidates[
                np.isfinite(notification_candidates['Strike']) &
                np.isfinite(notification_candidates['Gamma_Put']) &
                np.isfinite(notification_candidates['OI_Contracts_Put']) &
                np.isfinite(notification_candidates['GEX_Put']) &
                (notification_candidates['Gamma_Put'] > 0) &
                (notification_candidates['OI_Contracts_Put'] > 0) &
                (notification_candidates['GEX_Put'] > 0)
            ]
            if not notification_candidates.empty:
                notification_put_wall = float(
                    notification_candidates.loc[
                        notification_candidates['GEX_Put'].idxmax(),
                        'Strike'
                    ]
                )

        now_utc_ms = int(datetime.datetime.now(timezone.utc).timestamp() * 1000)
        completed_candles = df[df['CloseTimeMs'].notna() & (df['CloseTimeMs'] <= now_utc_ms)]

        if not completed_candles.empty:
            completed_candle = completed_candles.iloc[-1]
            completed_candle_time = int(completed_candle['CloseTimeMs'])

            # FIRST DEPLOYMENT / INITIALIZATION
            if not p_state["last_closed_candle_time"]:
                p_state["last_closed_candle_time"] = completed_candle_time
                if notification_put_wall is not None:
                    if completed_candle['close'] < notification_put_wall:
                        p_state["put_gamma_break_state"] = "BREACHED"
                        p_state["active_broken_put_wall"] = notification_put_wall
                    else:
                        p_state["put_gamma_break_state"] = "SAFE"
                        p_state["active_broken_put_wall"] = 0.0

            # PROCESS ONLY A NEW COMPLETED CANDLE
            elif completed_candle_time > p_state["last_closed_candle_time"]:
                p_state["previous_candle_time"] = p_state["last_closed_candle_time"]
                p_state["last_closed_candle_time"] = completed_candle_time
                
                # ONLY EVALUATE IF WALL IS VALID
                if notification_put_wall is not None:
                    completed_candle_close = float(completed_candle['close'])
                    wall_strike = notification_put_wall

                    if p_state["put_gamma_break_state"] == "SAFE":
                        if completed_candle_close < wall_strike:
                            alert_triggered = True
                            alert_message += f"🚨 GAMMA WALL BREAK — 30M CLOSE CONFIRMED: Price closed at ${completed_candle_close:,.2f}, below Put Wall (${wall_strike:,.0f}).<br>"
                            pending_gamma_breach = True
                            pending_broken_wall = wall_strike
                            
                    elif p_state["put_gamma_break_state"] == "BREACHED":
                        if completed_candle_close > wall_strike:
                            p_state["put_gamma_break_state"] = "SAFE"
                            p_state["active_broken_put_wall"] = 0.0

        # ==========================================================
        # END PUT GAMMA WALL BREAK STATE MACHINE
        # ==========================================================

        # Passive Logging: Wall Status Shifts
        current_call_status = "PRESENT" if near_call else "N/A"
        current_put_status = "PRESENT" if near_put else "N/A"
        
        if not p_state["wall_status_log"]:
            p_state["wall_status_log"].append({
                "time": datetime.datetime.now().strftime("%H:%M"),
                "call": current_call_status,
                "put": current_put_status
            })
        else:
            last_log = p_state["wall_status_log"][-1]
            if last_log["call"] != current_call_status or last_log["put"] != current_put_status:
                p_state["wall_status_log"].append({
                    "time": datetime.datetime.now().strftime("%H:%M"),
                    "call": current_call_status,
                    "put": current_put_status
                })
                if len(p_state["wall_status_log"]) > 10:
                    p_state["wall_status_log"].pop(0)

        # Options Flow History
        current_wall_state = (call_oi_wall['Strike'], put_oi_wall['Strike'], true_max_pain)
        if p_state["options_flow_history"]:
            last_state = p_state["options_flow_history"][-1]
            if last_state["call"] != current_wall_state[0] or last_state["put"] != current_wall_state[1] or last_state["mp"] != current_wall_state[2]:
                p_state["options_flow_history"].append({
                    "time": datetime.datetime.now().strftime("%H:%M"),
                    "call": current_wall_state[0],
                    "put": current_wall_state[1],
                    "mp": current_wall_state[2]
                })
                if len(p_state["options_flow_history"]) > 10:
                    p_state["options_flow_history"].pop(0)
        else:
            p_state["options_flow_history"].append({
                "time": datetime.datetime.now().strftime("%H:%M"),
                "call": current_wall_state[0],
                "put": current_wall_state[1],
                "mp": current_wall_state[2]
            })

        # SMART CONFLUENCE ALERTS
        if straddle_valid and near_put and current_amt_state in [3, 11, 14]:
            p_cluster = near_put[0]['VWAS'] if near_put else (mid_put[0]['VWAS'] if mid_put else 0)
            if p_cluster > 0 and latest_price <= p_cluster and options_bias_score <= -1:
                alert_triggered = True
                alert_message += f"🚨 TRAP DOOR: Price breaking below Put Cluster (${p_cluster:,.0f}) with Bearish Bias!<br>"
                
        if straddle_valid and near_call and current_amt_state in [2, 10]:
            c_cluster = near_call[0]['VWAS'] if near_call else (mid_call[0]['VWAS'] if mid_call else 0)
            if c_cluster > 0 and latest_price >= c_cluster and funding_rate is not None and funding_rate < -0.05:
                alert_triggered = True
                alert_message += f"🚨 SQUEEZE: Price breaking above Call Cluster (${c_cluster:,.0f}) with Negative Funding!<br>"

        if straddle_valid and pcr > 0.8 and pcr < 1.1 and abs(latest_price - true_max_pain) < 100:
            alert_triggered = True
            alert_message += f"⚠️ GAMMA PIN: Price trapped near Max Pain (${true_max_pain:,.0f}). Avoid chopping.<br>"

        # STANDARD ALERTS
        if p_state["prev_pcr"] != 0.0:
            if (p_state["prev_pcr"] >= 1.20 and pcr < 1.20) or (p_state["prev_pcr"] >= 0.30 and pcr < 0.30):
                alert_triggered = True
                alert_message += f"⚠️ PCR EXTREME CROSSED: OI PCR is now {pcr:.2f}<br>"
        
        if p_state["prev_max_pain"] != 0 and not pd.isna(true_max_pain) and abs(true_max_pain - p_state["prev_max_pain"]) >= 500:
            alert_triggered = True
            alert_message += f"🔄 MAX PAIN SHIFT: Moved to ${true_max_pain:,.0f}!<br>"
            
        if p_state["prev_call_wall"] != 0 and abs(call_oi_wall['Strike'] - p_state["prev_call_wall"]) >= 200:
            alert_triggered = True
            alert_message += f"🚧 CALL OI WALL SHIFT: Moved to ${call_oi_wall['Strike']:,.0f}!<br>"
            
        if p_state["prev_put_wall"] != 0 and abs(put_oi_wall['Strike'] - p_state["prev_put_wall"]) >= 200:
            alert_triggered = True
            alert_message += f"🚧 PUT OI WALL SHIFT: Moved to ${put_oi_wall['Strike']:,.0f}!<br>"

        if p_state["prev_price"] != 0.0:
            if p_state["prev_price"] < call_gamma_wall['Strike'] and latest_price >= call_gamma_wall['Strike']:
                alert_triggered = True
                alert_message += f"🔥 GAMMA BREAKOUT: Price crossed above Call Wall (${call_gamma_wall['Strike']:,.0f})!<br>"

        p_state["prev_pcr"] = pcr
        p_state["prev_max_pain"] = true_max_pain
        p_state["prev_call_wall"] = call_oi_wall['Strike']
        p_state["prev_put_wall"] = put_oi_wall['Strike']
        p_state["prev_price"] = latest_price

if journal_res is not None and not journal_df_check.empty:
    if p_state["prev_amt_state"] != -1 and current_amt_state != p_state["prev_amt_state"]:
        is_extreme_neg = (funding_rate is not None and funding_rate < -0.05)
        is_extreme_pos = (funding_rate is not None and funding_rate > 0.05)
        
        if current_amt_state in [2, 3, 10, 11, 14]:
            if current_amt_state in [2, 10]:
                if is_extreme_neg:
                    alert_triggered = True
                    alert_message += f"🚨 POTENTIAL SHORT SQUEEZE: Breakout confirmed ({current_amt_name}) with EXTREME NEGATIVE Funding ({funding_rate:.3f}%)!<br>"
                else:
                    alert_triggered = True
                    alert_message += f"📖 AMT STATE SHIFT: {current_amt_name} (Upside breakout)<br>"
            elif current_amt_state in [3, 11]:
                if is_extreme_pos:
                    alert_triggered = True
                    alert_message += f"🚨 POTENTIAL LONG LIQUIDATION: Breakdown confirmed ({current_amt_name}) with EXTREME POSITIVE Funding ({funding_rate:.3f}%)!<br>"
                else:
                    alert_triggered = True
                    alert_message += f"📖 AMT STATE SHIFT: {current_amt_name} (Downside breakdown)<br>"
            else:
                alert_triggered = True
                alert_message += f"📖 AMT STATE SHIFT: {current_amt_name}<br>"
    
    p_state["prev_amt_state"] = current_amt_state

if alert_triggered:
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    p_state["alert_history"].insert(0, {"Time": timestamp, "Alert": alert_message.replace("<br>", " | ")})
    if len(p_state["alert_history"]) > 50:
        p_state["alert_history"] = p_state["alert_history"][:50]
    
    clean_telegram_msg = alert_message.replace("<br>", "\n")
    success = send_telegram_message(f"🚨 BTC TERMINAL ALERT 🚨\n\n{clean_telegram_msg}")
    
    if success:
        if pending_gamma_breach:
            p_state["put_gamma_break_state"] = "BREACHED"
            p_state["active_broken_put_wall"] = pending_broken_wall
        st.warning(f"🚨 **DATA UPDATE** 🚨\n\n{alert_message}")
        components.html("<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/beep_short.ogg' type='audio/ogg'></audio>", height=0, width=0)
    else:
        # Revert timestamp so it retries next time
        if "previous_candle_time" in p_state:
            p_state["last_closed_candle_time"] = p_state["previous_candle_time"]
        # Remove the unsent alert from history
        p_state["alert_history"].pop(0)
        st.error("⚠️ TELEGRAM DELIVERY FAILED. State reverted; will retry on next refresh.")

# ==========================================
# SECTION 5: RENDER UI TABS (REORGANIZED + SNAPSHOT)
# ==========================================
tab_dashboard, tab_history = st.tabs(["📊 Live Dashboard", "📜 Alert History"])

with tab_history:
    st.subheader("📜 Alert History Log")
    st.caption("⚠️ This log persists across page reloads.")
    history_data = p_state["alert_history"]
    if history_data:
        st.dataframe(pd.DataFrame(history_data), use_container_width=True, hide_index=True)
    else:
        st.info("No alerts have fired yet during this session.")

with tab_dashboard:
    if not df.empty:
        # --- TOP VITAL SIGNS ---
        st.subheader("🚦 Vital Signs")
        v1, v2, v3, v4 = st.columns(4)
        v1.metric("Current Price", f"${latest_price:,.2f}")
        v2.metric("AMT State", f"{current_amt_name}", f"Code {current_amt_state}")
        
        if options_bias_score >= 2:
            b_color, b_text = "normal", "🟢 BULLISH"
        elif options_bias_score <= -2:
            b_color, b_text = "inverse", "🔴 BEARISH"
        else:
            b_color, b_text = "off", "⚪ NEUTRAL"
        v3.metric("Options Bias", b_text, f"Score: {options_bias_score:+d}", delta_color=b_color)
        
        if funding_rate is not None:
            f_color = "inverse" if funding_rate > 0.01 else "normal" if funding_rate < -0.01 else "off"
            v4.metric("Funding Rate (8h)", f"{funding_rate:+.4f}%", "Ext Neg" if funding_rate < -0.05 else "Ext Pos" if funding_rate > 0.05 else "Neutral", delta_color=f_color)
            
            if funding_rate > 0.15:
                st.warning("⚠️ EXTREME POSITIVE FUNDING: Crowded long-side positioning / elevated squeeze risk. (Informational only, not an entry/exit signal).")
            elif funding_rate < -0.15:
                st.warning("⚠️ EXTREME NEGATIVE FUNDING: Crowded short-side positioning / elevated squeeze risk. (Informational only, not an entry/exit signal).")
        else:
            v4.metric("Funding Rate", "N/A")
            
        st.markdown("---")

        # --- SECTION 1: AMT MAP ---
        st.subheader("🗺️ AMT Map & Context")
        if manual_values_valid: st.info("🟢 Reference Source: TRADINGVIEW MANUAL")
        elif not pd.isna(final_vah): st.info("🔵 Reference Source: PYTHON CALCULATED")
        else: st.warning("🔴 Reference Source: UNAVAILABLE")
            
        c1, c2, c3 = st.columns(3)
        c1.metric("Prior VAH", f"${final_vah:,.2f}" if not pd.isna(final_vah) else "N/A")
        c2.metric("Prior VAL", f"${final_val:,.2f}" if not pd.isna(final_val) else "N/A")
        c3.metric("Prior POC", f"${final_poc:,.2f}" if not pd.isna(final_poc) else "N/A")
        
        session_df = calculate_session_journey(df, final_vah, final_val)
        if not session_df.empty:
            st.dataframe(
                session_df, use_container_width=True, hide_index=True,
                column_config={
                    "Session": st.column_config.TextColumn("Session"),
                    "Open": st.column_config.NumberColumn("Open", format="$%.0f"),
                    "High": st.column_config.NumberColumn("High", format="$%.0f"),
                    "Low": st.column_config.NumberColumn("Low", format="$%.0f"),
                    "Close": st.column_config.NumberColumn("Close", format="$%.0f"),
                    "Macro Bias": st.column_config.TextColumn("Macro Bias (vs Prior)"),
                    "Micro Bias": st.column_config.TextColumn("Micro Bias (vs Session)"),
                    "POC": st.column_config.NumberColumn("POC", format="$%.0f"),
                    "VAH": st.column_config.NumberColumn("VAH", format="$%.0f"),
                    "VAL": st.column_config.NumberColumn("VAL", format="$%.0f"),
                }
            )
        st.markdown("---")

        # --- SECTION 2: OPTIONS SNAPSHOT ---
        if not options_df.empty and atm_row is not None:
            st.subheader(f"📉 Options Snapshot (Expiry: {loaded_expiry})")
            
            o1, o2, o3, o4 = st.columns(4)
            if straddle_valid:
                straddle_delta = straddle_price - p_state["session_start_straddle"] if p_state["session_start_straddle"] else 0
                straddle_delta_str = f"{straddle_delta:+,.0f}" if straddle_delta != 0 else "Unchanged"
                o1.metric("ATM Straddle", f"${straddle_price:,.2f}", straddle_delta_str)
            else:
                o1.metric("ATM Straddle", "⚠️ INVALID", straddle_reason)

            o2.metric("Max Pain (Expiry Ref)", f"${true_max_pain:,.0f}" if not pd.isna(true_max_pain) else "N/A")
            o3.metric("Settlement Time", settlement_ist)
            o4.metric("Remaining Time", time_str)
            
            st.caption(f"Expiry Source: {expiry_source} | Quote Freshness: {quote_freshness} | Call Ticker Age: {atm_call_age} | Put Ticker Age: {atm_put_age}")
            st.markdown("---")

        if not filtered_options.empty:
            st.subheader("🎯 OI Concentrations & Walls")
            
            # Build the compact table data
            cluster_rows = []
            
            # Near
            c_near = near_call[0] if near_call else None
            p_near = near_put[0] if near_put else None
            cluster_rows.append({
                "Tier": "📍 Near (<0.5x)",
                "Call Strike": f"${c_near['VWAS']:,.0f}" if c_near else "N/A",
                "Call OI": f"${c_near['OI_USD']/1e6:.2f}M" if c_near else "N/A",
                "Call Dist": f"{c_near['Dist']:.1f}x" if c_near else "N/A",
                "Put Strike": f"${p_near['VWAS']:,.0f}" if p_near else "N/A",
                "Put OI": f"${p_near['OI_USD']/1e6:.2f}M" if p_near else "N/A",
                "Put Dist": f"{p_near['Dist']:.1f}x" if p_near else "N/A",
            })
            
            # Mid
            c_mid = mid_call[0] if mid_call else None
            p_mid = mid_put[0] if mid_put else None
            cluster_rows.append({
                "Tier": "📍 Mid (0.5 - 1.0x)",
                "Call Strike": f"${c_mid['VWAS']:,.0f}" if c_mid else "N/A",
                "Call OI": f"${c_mid['OI_USD']/1e6:.2f}M" if c_mid else "N/A",
                "Call Dist": f"{c_mid['Dist']:.1f}x" if c_mid else "N/A",
                "Put Strike": f"${p_mid['VWAS']:,.0f}" if p_mid else "N/A",
                "Put OI": f"${p_mid['OI_USD']/1e6:.2f}M" if p_mid else "N/A",
                "Put Dist": f"{p_mid['Dist']:.1f}x" if p_mid else "N/A",
            })
            
            # Far
            c_far = far_call[0] if far_call else None
            p_far = far_put[0] if far_put else None
            cluster_rows.append({
                "Tier": "🌍 Distant (>1.0x)",
                "Call Strike": f"${c_far['VWAS']:,.0f}" if c_far else "N/A",
                "Call OI": f"${c_far['OI_USD']/1e6:.2f}M" if c_far else "N/A",
                "Call Dist": f"{c_far['Dist']:.1f}x" if c_far else "N/A",
                "Put Strike": f"${p_far['VWAS']:,.0f}" if p_far else "N/A",
                "Put OI": f"${p_far['OI_USD']/1e6:.2f}M" if p_far else "N/A",
                "Put Dist": f"{p_far['Dist']:.1f}x" if p_far else "N/A",
            })

            st.dataframe(pd.DataFrame(cluster_rows), use_container_width=True, hide_index=True)
            st.caption("Context only — not support, resistance or price targets. Normalized distance based on ATM Straddle.")

            st.markdown("##### 📊 Context Metrics")
            w1, w2, w3 = st.columns(3)
            w1.metric("Local OI PCR (±10%)", f"{pcr:.2f}", "Put OI / Call OI (USD)")
            w2.metric("Gross Gamma Concentration", f"C: ${call_gamma_wall['Strike']:,.0f} / P: ${put_gamma_wall['Strike']:,.0f}", "Unsigned OI Estimation")
            w3.metric("Structural Extreme (OI)", f"C: ${call_oi_wall['Strike']:,.0f} / P: ${put_oi_wall['Strike']:,.0f}")
            st.caption("ℹ️ Context only — not support, resistance or price targets. Gross Gamma estimated using unsigned OI.")
            st.markdown("---")

        # --- SECTION 3: PROJECT ECHO ---
        st.subheader("📖 PROJECT ECHO - State Journal")
        if journal_res is not None:
            journal_df, echo_used_manual = journal_res
            if not journal_df.empty:
                if echo_used_manual: st.caption("Echo Reference: 🟢 TRADINGVIEW MANUAL")
                else: st.caption("Echo Reference: 🔵 PYTHON CALCULATED")
                st.dataframe(
                    journal_df, use_container_width=True, hide_index=True,
                    column_config={
                        "Time": st.column_config.TextColumn("Time (IST)"),
                        "Close": st.column_config.NumberColumn("Close", format="$%.0f"),
                        "Code": st.column_config.NumberColumn("Code"),
                        "State Name": st.column_config.TextColumn("State Name"),
                        "Dev VAH": st.column_config.NumberColumn("Dev VAH", format="$%.0f"),
                        "Dev POC": st.column_config.NumberColumn("Dev POC", format="$%.0f"),
                        "Dev VAL": st.column_config.NumberColumn("Dev VAL", format="$%.0f"),
                    }
                )
        st.markdown("---")

        # --- SECTION 4: RAW DATA ---
        st.subheader("📋 Raw Options Data")
        if not filtered_options.empty:
            if 'Dist_to_Spot' in filtered_options.columns:
                filtered_options = filtered_options.drop(columns=['Dist_to_Spot'])
            display_df = filtered_options[['Bid_Call', 'Ask_Call', 'OI_USD_Call', 'Gamma_Call', 'IV_Call', 'Strike', 'IV_Put', 'Gamma_Put', 'OI_USD_Put', 'Bid_Put', 'Ask_Put']].copy()
            st.dataframe(display_df, use_container_width=True, hide_index=True, column_config={
                "Bid_Call": st.column_config.NumberColumn("Call Bid", format="$%.2f"),
                "Ask_Call": st.column_config.NumberColumn("Call Ask", format="$%.2f"),
                "OI_USD_Call": st.column_config.NumberColumn("Call OI", format="$%,.0f"),
                "Gamma_Call": st.column_config.NumberColumn("Call Gamma", format="%.8f"),
                "IV_Call": st.column_config.NumberColumn("Call IV", format="%.2f%%"),
                "Strike": st.column_config.NumberColumn("Strike", format="$%.0f"),
                "IV_Put": st.column_config.NumberColumn("Put IV", format="%.2f%%"),
                "Gamma_Put": st.column_config.NumberColumn("Put Gamma", format="%.8f"),
                "OI_USD_Put": st.column_config.NumberColumn("Put OI", format="$%,.0f"),
                "Bid_Put": st.column_config.NumberColumn("Put Bid", format="$%.2f"),
                "Ask_Put": st.column_config.NumberColumn("Put Ask", format="$%.2f"),
            })
            csv = display_df.to_csv(index=False).encode('utf-8')
            st.download_button("📥 Download Options Data as CSV", data=csv, file_name=f"delta_options_{loaded_expiry}.csv", mime="text/csv")
    else:
        binance_status = "🔴 Failed"
        st.warning("⚠️ Could not fetch AMT Data from Binance.")

    # --- MOVED TO BOTTOM: ONE-CLICK SNAPSHOT GENERATION (ENHANCED) ---
    st.markdown("---")
    
    # Build Options Flow History Text
    flow_text = ""
    for entry in p_state["options_flow_history"][-5:]:
        flow_text += f"{entry['time']} | C: {entry['call']:.0f} | P: {entry['put']:.0f} | MP: {entry['mp']:.0f}\n"

    # Build Recent Alerts Text
    alert_text = ""
    for entry in p_state["alert_history"][:5]:
        alert_text += f"{entry['Time'][11:16]} | {entry['Alert'][:60]}...\n"

    # Build Extended Echo Text
    echo_text = ""
    if journal_res is not None and not journal_df_check.empty:
        for idx, row in journal_df_check.tail(10).iterrows():
            echo_text += f"{row['Time']} | ${row['Close']:,.0f} | {row['State Name']}\n"
            
    # Build Wall Status Log Text
    wall_text = ""
    for entry in p_state["wall_status_log"][-5:]:
        wall_text += f"{entry['time']} | Call: {entry['call']} | Put: {entry['put']}\n"

    snapshot_text = f"""
=== BTC TERMINAL SNAPSHOT ===
Time: {datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')} IST

[RESEARCH OBSERVATIONS — NOT USED FOR TRADE DECISIONS]

[VITAL SIGNS]
Price: ${latest_price:,.2f}
AMT State: {current_amt_name} (Code {current_amt_state})
Options Bias: {b_text} (Score: {options_bias_score:+d})
Funding Rate: {f"{funding_rate:+.4f}%" if funding_rate is not None else "N/A"}

[AMT MAP]
Prior VAH: ${final_vah:,.2f} | Prior POC: ${final_poc:,.2f} | Prior VAL: ${final_val:,.2f}
Source: {"TradingView Manual" if manual_values_valid else "Python Calculated"}

[OPTIONS SNAPSHOT]
Expiry: {loaded_expiry} | Time Left: {time_str}
ATM Straddle: {"$"+f"{straddle_price:,.2f}" if straddle_valid else "INVALID ("+straddle_reason+")"}
Max Pain: ${true_max_pain:,.0f}
Local PCR: {pcr:.2f}
Gamma Walls: Call ${call_gamma_wall['Strike']:,.0f} / Put ${put_gamma_wall['Strike']:,.0f}

[OI CLUSTERS]
Near-Term (<0.5x): Call {"$"+f"{near_call[0]['VWAS']:,.0f}" if near_call else "N/A"} | Put {"$"+f"{near_put[0]['VWAS']:,.0f}" if near_put else "N/A"}
Mid-Term (0.5-1.0x): Call {"$"+f"{mid_call[0]['VWAS']:,.0f}" if mid_call else "N/A"} | Put {"$"+f"{mid_put[0]['VWAS']:,.0f}" if mid_put else "N/A"}
Distant (>=1.0x): Call {"$"+f"{far_call[0]['VWAS']:,.0f}" if far_call else "N/A"} | Put {"$"+f"{far_put[0]['VWAS']:,.0f}" if far_put else "N/A"}

[WALL STATUS LOG (Passive Tracking)]
{wall_text}

[OPTIONS FLOW JOURNAL (Last 5 Shifts)]
{flow_text}

[RECENT ALERTS (Last 5)]
{alert_text}

[PROJECT ECHO (Last 10 States)]
{echo_text}

[TRADE JOURNAL]
{p_state["trade_journal"] if p_state["trade_journal"] else "Empty"}
"""

    with st.expander("📋 Copy Dashboard Snapshot (Click to expand)"):
        st.code(snapshot_text, language="text")

    if st.button("🔄 Refresh All Data"):
        st.cache_data.clear()
        st.rerun()

    st.markdown("---")
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    st.caption(f"**System Health:** Binance {binance_status} | Delta {delta_status} | Last Fetched: {now}")

components.html("<script>setTimeout(function(){ window.parent.location.reload(); }, 180000);</script>", height=0, width=0)
