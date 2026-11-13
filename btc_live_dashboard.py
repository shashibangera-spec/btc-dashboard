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
# CONFIGURATION & STATE
# ==========================================
CLUSTER_ABSORPTION_PCT = 0.30
MAX_CLUSTER_WIDTH = 5
OI_CLUSTER_MIN_USD = 5_000_000
MARK_TOLERANCE_PCT = 0.02 
IST_TZ = timezone(timedelta(hours=5, minutes=30))

state_names = {
    0: "BUILDING", 1: "DEVELOPMENT", 2: "DISCOVERY ABOVE", 3: "DISCOVERY BELOW",
    4: "ACCEPTANCE UP", 5: "ACCEPTANCE DOWN", 6: "POC BREAK ABOVE",
    7: "POC BREAK BELOW", 8: "NEW AREA UP", 9: "NEW AREA DOWN", 10: "FAILED DISC UP",
    11: "FAILED DISC DOWN", 12: "ACC FAILURE UP", 13: "ACC FAILURE DOWN",
    14: "RE-ENTRY CONFIRMED", 15: "STALLED ABOVE", 16: "STALLED BELOW",
    17: "FAILED UP (CAND)", 18: "FAILED DOWN (CAND)"
}

@st.cache_resource
def get_persistent_state():
    # Stripped down to only what is needed for UI tracking
    return {
        "session_start_straddle": None,
        "session_start_expiry": None,
        "inverted_gamma_history": [], 
        "active_inverted_event": None, 
        "last_inverted_status": False, 
        "trade_journal": ""
    }
p_state = get_persistent_state()

# ==========================================
# SIDEBAR
# ==========================================
st.sidebar.header("🛠️ Manual AMT Overrides")
try:
    default_vah = float(st.query_params.get("vah", 0.0))
    default_val = float(st.query_params.get("val", 0.0))
    default_poc = float(st.query_params.get("poc", 0.0))
except (TypeError, ValueError):
    default_vah = default_val = default_poc = 0.0

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
journal_input = st.sidebar.text_area("Log Trade Details", value=p_state["trade_journal"], height=200)
if st.sidebar.button("Save Journal"):
    p_state["trade_journal"] = journal_input
    st.sidebar.success("Journal saved!")

st.title("⚡ BTC AMT & Delta Options Terminal")
manual_values_valid = (input_vah > 0 and input_val > 0 and input_poc > 0 and input_vah >= input_poc >= input_val)
final_vah, final_val, final_poc = np.nan, np.nan, np.nan

# ==========================================
# FETCH DATA FUNCTIONS
# ==========================================
@st.cache_data(ttl=180)
def fetch_amt_data():
    session = requests.Session()
    retry_strategy = Retry(total=3, backoff_factor=1, status_forcelist=[429, 500, 502, 503, 504], allowed_methods=["GET"])
    adapter = HTTPAdapter(max_retries=retry_strategy)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    endpoints = ["https://api.binance.com", "https://api1.binance.com", "https://api2.binance.com", "https://api3.binance.com", "https://data-api.binance.vision"]
    
    for base in endpoints:
        try:
            url = f"{base}/api/v3/klines?symbol=BTCUSDT&interval=30m&limit=1000"
            response = session.get(url, timeout=10)
            response.raise_for_status()
            res = response.json()
            if not res or not isinstance(res, list): continue
                
            df = pd.DataFrame(res, columns=['Date', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
            df['Date'] = pd.to_datetime(df['Date'], unit='ms', utc=True).dt.tz_convert('Asia/Kolkata')
            df['CloseTimeUTC'] = pd.to_datetime(df['close_time'], unit='ms', utc=True)
            df['CloseTimeMs'] = pd.to_numeric(df['close_time'], errors='coerce')
            
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            
            df['ShiftedDate'] = df['Date'] - pd.Timedelta(hours=5, minutes=30)
            df = df[['Date', 'CloseTimeUTC', 'CloseTimeMs', 'open', 'high', 'low', 'close', 'ShiftedDate']]
            return df.sort_values('Date').reset_index(drop=True)
        except Exception:
            continue
    return pd.DataFrame()

@st.cache_data(ttl=60)
def fetch_futures_context():
    try:
        url = "https://api.india.delta.exchange/v2/tickers?contract_types=perpetual_futures"
        res = requests.get(url, timeout=10).json()
        for ticker in res.get("result", []):
            if ticker.get("symbol") == "BTCUSD":
                funding_rate = float(ticker.get("funding_rate", 0)) 
                mark_price = float(ticker.get("mark_price", 0))
                spot_price = float(ticker.get("spot_price", 0))
                oi_usd = float(ticker.get("oi_value_usd", 0))
                sp_prem = ((mark_price - spot_price) / spot_price) * 100 if spot_price > 0 else None
                return funding_rate, mark_price, oi_usd, sp_prem
        return None, None, None, None
    except Exception:
        return None, None, None, None

def calculate_tpo(session_df):
    if session_df.empty: return np.nan, np.nan, np.nan
    row_size = 50.0; value_pct = 0.70
    high = session_df['high'].max(); low = session_df['low'].min()
    if high - low <= 0: return np.nan, np.nan, np.nan
    base_bucket = math.floor(low / row_size)
    num_buckets = math.floor(high / row_size) - base_bucket + 1
    tpo = np.zeros(num_buckets)
    for _, row in session_df.iterrows():
        lo_idx = max(0, int(math.floor(row['low'] / row_size) - base_bucket))
        hi_idx = min(num_buckets - 1, int(math.floor(row['high'] / row_size) - base_bucket))
        for i in range(lo_idx, hi_idx + 1): tpo[i] += 1
    poc_idx = np.argmax(tpo); max_val = np.max(tpo); tied_indices = np.where(tpo == max_val)[0]
    if len(tied_indices) > 1:
        profile_mid = (num_buckets - 1) / 2.0
        distances = np.abs(tied_indices - profile_mid)
        poc_idx = tied_indices[np.argmin(distances)]
    target = math.ceil(np.sum(tpo) * value_pct); covered = tpo[poc_idx]; up, dn = poc_idx, poc_idx
    while covered < target:
        can_down, can_up = dn > 0, up < num_buckets - 1
        if not can_down and not can_up: break
        d = tpo[dn - 1] if can_down else -1; u = tpo[up + 1] if can_up else -1
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
    sessions = []
    
    for name, session_data in [
        ("Asia (05:30-13:30)", df[(df['Date'] >= cycle_start) & (df['Date'] < asia_end)]),
        ("Europe (13:30-17:30)", df[(df['Date'] >= asia_end) & (df['Date'] < europe_end)]),
        ("US Cash (17:30-01:30)", df[(df['Date'] >= europe_end) & (df['Date'] < us_cash_end)]),
        ("US Overnight (Current)", df[(df['Date'] >= us_cash_end) & (df['Date'] < cycle_end)])
    ]:
        if session_data.empty: continue
        vah, val, poc = calculate_tpo(session_data)
        close = session_data['close'].iloc[-1]; high = session_data['high'].max(); low = session_data['low'].min()
        
        macro_bias = "⚪ Inside Prior Value"
        if not pd.isna(prev_vah) and not pd.isna(prev_val):
            if close > prev_vah: macro_bias = "🟢 Bullish (Above Prior VAH)"
            elif close < prev_val: macro_bias = "🔴 Bearish (Below Prior VAL)"
            elif high > prev_vah and close < prev_vah: macro_bias = "⚠️ Bull Trap"
            elif low < prev_val and close > prev_val: macro_bias = "⚠️ Bear Trap"
                
        micro_bias = "⚪ Balanced (Inside Value)"
        if not pd.isna(vah) and not pd.isna(val):
            if close > vah: micro_bias = "🟢 Strong Close"
            elif close < val: micro_bias = "🔴 Weak Close"

        sessions.append({"Session": name, "Open": session_data['open'].iloc[0], "High": high, "Low": low, "Close": close, "Macro Bias": macro_bias, "Micro Bias": micro_bias, "POC": poc, "VAH": vah, "VAL": val})
    return pd.DataFrame(sessions)

def generate_state_journal(df, manual_valid, manual_vah, manual_val, manual_poc):
    if df.empty: return pd.DataFrame(), manual_valid, {}
    df['prev_close'] = df['close'].shift(1)
    df['tr0'] = abs(df['high'] - df['low']); df['tr1'] = abs(df['high'] - df['prev_close']); df['tr2'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1); df['atr20'] = df['tr'].ewm(alpha=1/20, adjust=False).mean()
    
    today_str = df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    today_df = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') == today_str].copy()
    yesterday_df = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str].copy()
    if yesterday_df.empty or today_df.empty: return pd.DataFrame(), manual_valid, {}

    last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
    
    if manual_valid: prevVAH, prevVAL, prevPOC = manual_vah, manual_val, manual_poc
    else: prevVAH, prevVAL, prevPOC = calculate_tpo(prev_day_df)
    if pd.isna(prevVAH) or pd.isna(prevVAL) or pd.isna(prevPOC): return pd.DataFrame(), manual_valid, {}

    journal = []; candle_state_map = {}; auctionState = 0; discoveryStartBar = 0; cycleBars = 0
    aboveValueBars = 0; belowValueBars = 0; insideValueBars = 0; acceptancePullbackBarsDown = 0
    acceptanceConfirmedDown = False; pocAboveBars = 0; pocBelowBars = 0; reentryInsideBars = 0
    prev_currVAH, prev_currVAL, prev_currPOC = np.nan, np.nan, np.nan

    for i in range(len(today_df)):
        bar = today_df.iloc[i]; current_bar_df = today_df.iloc[:i+1]; cycleBars += 1
        currVAH, currVAL, currPOC = calculate_tpo(current_bar_df); close = bar['close']
        
        if close > prevVAH: aboveValueBars += 1; belowValueBars = 0; insideValueBars = 0
        elif close < prevVAL: belowValueBars += 1; aboveValueBars = 0; insideValueBars = 0
        else: aboveValueBars = 0; belowValueBars = 0; insideValueBars += 1

        if not pd.isna(currPOC):
            if currPOC > prevVAH: pocAboveBars += 1; pocBelowBars = 0
            elif currPOC < prevVAL: pocBelowBars += 1; pocAboveBars = 0
            else: pocAboveBars = 0; pocBelowBars = 0

        barRange = bar['high'] - bar['low']
        wideBar = not pd.isna(bar['atr20']) and barRange > bar['atr20'] * 1.5
        effectiveTimeout = 6
        if not pd.isna(bar['atr20']): effectiveTimeout = max(3, min(20, round(6 * (bar['atr20'] / 250.0))))
        discoveryTimedOut = (i - discoveryStartBar) >= effectiveTimeout

        if belowValueBars >= 2:
            if bar['high'] >= prevVAL and close < prevVAL and wideBar:
                acceptancePullbackBarsDown += 1
                if acceptancePullbackBarsDown >= 2: acceptanceConfirmedDown = True
            elif close > prevVAL: acceptancePullbackBarsDown = 0; acceptanceConfirmedDown = False
        else: acceptancePullbackBarsDown = 0; acceptanceConfirmedDown = False

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
                if reentryInsideBars >= 3 and cycleBars >= 3: auctionState = 1; reentryInsideBars = 0
        elif auctionState == 16:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close >= prevVAL and close <= prevVAH: auctionState = 11

        if auctionState != 14: reentryInsideBars = 0
        prev_currVAH, prev_currVAL, prev_currPOC = currVAH, currVAL, currPOC
        candle_state_map[bar['Date'].strftime('%H:%M')] = auctionState
        
        if i == 0 or auctionState != journal[-1]['Code'] or i == len(today_df) - 1:
            journal.append({"Time": bar['Date'].strftime('%H:%M'), "Close": close, "Code": auctionState, "State Name": state_names.get(auctionState, "UNKNOWN"), "Dev VAH": currVAH, "Dev POC": currPOC, "Dev VAL": currVAL})
    return pd.DataFrame(journal), manual_valid, candle_state_map

def parse_timestamp(ts):
    if ts is None: return None
    try:
        if isinstance(ts, (int, float)): return datetime.datetime.fromtimestamp(float(ts)/1000 if float(ts)>1e11 else float(ts), tz=timezone.utc)
        if isinstance(ts, str):
            ts = ts.strip()
            if not ts: return None
            dt = datetime.datetime.fromisoformat(ts.replace("Z", "+00:00"))
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except: pass
    return None

def find_oi_clusters(strike_ladder, df_opt, opt_type, spot_price, straddle_price):
    if not strike_ladder or pd.isna(straddle_price) or straddle_price <= 0: return [], [], []
    df_type = df_opt[df_opt['Type'] == opt_type].dropna(subset=['OI_USD'])
    if df_type.empty: return [], [], []
    df_sorted = df_type.sort_values('OI_USD', ascending=False); claimed = set(); clusters = []
    for _, row in df_sorted.iterrows():
        peak_strike = row['Strike']; peak_oi = row['OI_USD']
        if peak_strike in claimed or peak_oi < OI_CLUSTER_MIN_USD or peak_strike not in strike_ladder: continue
        peak_idx = strike_ladder.index(peak_strike); current_cluster = [peak_strike]; claimed.add(peak_strike)
        
        left = peak_idx - 1
        while left >= 0 and len(current_cluster) < MAX_CLUSTER_WIDTH:
            test_strike = strike_ladder[left]
            if test_strike in claimed: break
            row_data = df_type[df_type['Strike'] == test_strike]
            if row_data.empty or row_data['OI_USD'].values[0] < (peak_oi * CLUSTER_ABSORPTION_PCT): break
            current_cluster.append(test_strike); claimed.add(test_strike); left -= 1
                
        right = peak_idx + 1
        while right < len(strike_ladder) and len(current_cluster) < MAX_CLUSTER_WIDTH:
            test_strike = strike_ladder[right]
            if test_strike in claimed: break
            row_data = df_type[df_type['Strike'] == test_strike]
            if row_data.empty or row_data['OI_USD'].values[0] < (peak_oi * CLUSTER_ABSORPTION_PCT): break
            current_cluster.append(test_strike); claimed.add(test_strike); right += 1
                
        cluster_df = df_type[df_type['Strike'].isin(current_cluster)]; total_oi = cluster_df['OI_USD'].sum()
        if total_oi == 0: continue
        vwas = (cluster_df['Strike'] * cluster_df['OI_USD']).sum() / total_oi
        if opt_type == "Call" and vwas <= spot_price: continue
        if opt_type == "Put" and vwas >= spot_price: continue
        clusters.append({"Type": opt_type, "VWAS": vwas, "OI_USD": total_oi, "Dist": abs(vwas - spot_price) / straddle_price, "Strikes": current_cluster})
        
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
        results = []; seen_cursors = set(); page_count = 0; complete = False
        while url:
            if page_count >= 50: break
            try:
                response = requests.get(url, timeout=10); response.raise_for_status()
                data = response.json()
                if not isinstance(data, dict): break
                page_results = data.get("result", [])
                if not isinstance(page_results, list): break
                results.extend(page_results); page_count += 1
                meta = data.get("meta") or {}; after = meta.get("after")
                if after is None or after == "": complete = True; break
                cursor = str(after)
                if cursor in seen_cursors: break
                seen_cursors.add(cursor)
                base_url, query_params = url.split("?", 1)
                params = [p for p in query_params.split("&") if not p.startswith("after=")]
                params.append(f"after={cursor}")
                url = f"{base_url}?{'&'.join(params)}"
            except Exception: complete = False; break
        return results, complete

    try:
        products, prod_complete = fetch_paginated_data(f"{BASE_URL}/products?states=live&page_size=1000")
        btc_options = [p for p in products if p.get("underlying_asset", {}).get("symbol") == "BTC" and p.get("contract_type") in ("call_options", "put_options")]
        if not btc_options: return pd.DataFrame(), None, None, None, None, None, None, False
        now_utc = datetime.datetime.now(timezone.utc); valid_expiries = []
        for opt in btc_options:
            sym = opt.get("symbol", ""); parts = sym.split("-")
            if len(parts) >= 4:
                exp_str = parts[3]
                try:
                    settlement_time_str = opt.get("settlement_time") or opt.get("expiry_time")
                    dt_utc = parse_timestamp(settlement_time_str); source = "metadata"
                    if dt_utc is None:
                        dt_naive = datetime.datetime.strptime(exp_str, "%d%m%y")
                        dt_utc = dt_naive.replace(hour=12, minute=0, second=0, tzinfo=timezone.utc); source = "fallback"
                    if dt_utc > now_utc: valid_expiries.append({"expiry_str": exp_str, "dt_utc": dt_utc, "source": source})
                except Exception: pass
                    
        if not valid_expiries: return pd.DataFrame(), None, None, None, None, None, None, False
        nearest = min(valid_expiries, key=lambda x: x["dt_utc"])
        nearest_expiry = nearest["expiry_str"]; expiry_dt = nearest["dt_utc"]; expiry_source = nearest["source"]
        
        filtered_options = [opt for opt in btc_options if len(opt.get("symbol", "").split("-")) >= 4 and opt.get("symbol", "").split("-")[3] == nearest_expiry]
        strike_ladder = sorted(list(set(float(p.get("strike_price")) for p in filtered_options if p.get("strike_price") is not None)))
        contract_size = float(filtered_options[0].get("contract_value", 0.001)) if filtered_options else 0.001

        tickers, tick_complete = fetch_paginated_data(f"{BASE_URL}/tickers?contract_types=call_options,put_options&page_size=1000")
        ticker_map = {t.get("symbol"): t for t in tickers if t.get("symbol")}
        expected_symbols = [p.get("symbol") for p in filtered_options]
        chain = []
        
        for opt in filtered_options:
            sym = opt.get("symbol"); ticker = ticker_map.get(sym)
            if not ticker: continue
            try:
                strike = opt.get("strike_price")
                if strike is None:
                    parts = sym.split("-"); raw_strike = parts[2].replace(",", "").strip().upper()
                    strike = float(raw_strike.replace("K", "")) * 1000 if "K" in raw_strike else float(raw_strike)
                
                strike = float(strike); opt_type = "Call" if opt.get("contract_type") == "call_options" else "Put"
                oi_usd = float(ticker.get("oi_value_usd")) if ticker.get("oi_value_usd") is not None else None
                oi_contracts = float(ticker.get("oi_contracts") or ticker.get("oi")) if (ticker.get("oi_contracts") or ticker.get("oi")) is not None else None
                volume = float(ticker.get("volume", 0)) if ticker.get("volume") is not None else 0.0
                quotes = ticker.get("quotes", {})
                bid = float(quotes.get("best_bid") or ticker.get("best_bid")) if (quotes.get("best_bid") or ticker.get("best_bid")) is not None else None
                ask = float(quotes.get("best_ask") or ticker.get("best_ask")) if (quotes.get("best_ask") or ticker.get("best_ask")) is not None else None
                mark = float(ticker.get("mark_price")) if ticker.get("mark_price") is not None else None
                greeks = ticker.get("greeks") or {}; gamma = float(greeks.get("gamma") or "0")
                iv = float(quotes.get("mark_iv") or quotes.get("ask_iv") or "0") * 100
                ts_val = ticker.get("timestamp") or ticker.get("updated_at")
                
                chain.append({"Symbol": sym, "Strike": strike, "Type": opt_type, "Bid": bid, "Ask": ask, "Mark": mark, "OI_USD": oi_usd, "OI_Contracts": oi_contracts, "Gamma": gamma, "IV": iv, "Volume": volume, "Timestamp": ts_val})
            except (ValueError, TypeError): continue

        df = pd.DataFrame(chain)
        if df.empty: return pd.DataFrame(), nearest_expiry, expiry_dt, expiry_source, contract_size, None, None, False

        expected_symbol_set = set(str(sym) for sym in expected_symbols)
        received_symbol_set = set(df["Symbol"].dropna().astype(str)) if not df.empty else set()
        oi_values = pd.to_numeric(df["OI_Contracts"], errors="coerce") if not df.empty else pd.Series()
        is_chain_complete = (prod_complete and tick_complete and expected_symbol_set == received_symbol_set and len(df) == len(expected_symbol_set) and oi_values.notna().all() and np.isfinite(oi_values.to_numpy(dtype=float)).all() and (oi_values >= 0).all())

        df_pivot = df.pivot_table(index="Strike", columns="Type", values=["Bid", "Ask", "Mark", "OI_USD", "OI_Contracts", "Gamma", "IV", "Volume"], aggfunc="first").reset_index()
        new_cols = []
        for col in df_pivot.columns:
            if isinstance(col, tuple): new_cols.append("_".join([str(c) for c in col if c != ""]).strip("_"))
            else: new_cols.append(str(col))
        df_pivot.columns = new_cols

        expected_columns = ['Bid_Call', 'Ask_Call', 'Mark_Call', 'OI_USD_Call', 'OI_Contracts_Call', 'Gamma_Call', 'IV_Call', 'Volume_Call', 'Bid_Put', 'Ask_Put', 'Mark_Put', 'OI_USD_Put', 'OI_Contracts_Put', 'Gamma_Put', 'IV_Put', 'Volume_Put']
        for col in expected_columns:
            if col not in df_pivot.columns: df_pivot[col] = np.nan

        return df_pivot, nearest_expiry, expiry_dt, expiry_source, contract_size, df, strike_ladder, is_chain_complete
    except Exception:
        return pd.DataFrame(), None, None, None, None, None, None, False

# ==========================================
# CALCULATION & PROCESSING
# ==========================================
df = fetch_amt_data()
latest_price = float(df['close'].iloc[-1]) if not df.empty else 0.0
funding_rate, mark_price, oi_usd, sp_prem = fetch_futures_context()

if not df.empty:
    today_str = df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    yesterday_df = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str]
    
    if not yesterday_df.empty:
        last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
        prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
        if manual_values_valid: final_vah, final_val, final_poc = input_vah, input_val, input_poc
        else: final_vah, final_val, final_poc = calculate_tpo(prev_day_df)
    elif manual_values_valid: final_vah, final_val, final_poc = input_vah, input_val, input_poc

session_df = calculate_session_journey(df, final_vah, final_val)
journal_res = generate_state_journal(df, manual_values_valid, input_vah, input_val, input_poc)
current_amt_state = int(journal_res[0].iloc[-1]['Code']) if (journal_res and not journal_res[0].empty) else 0
current_amt_name = state_names.get(current_amt_state, "UNKNOWN")
journal_df_check = journal_res[0] if journal_res else pd.DataFrame()

options_df, loaded_expiry, expiry_dt, expiry_source, dynamic_contract_size, raw_chain_df, strike_ladder, is_chain_complete = fetch_delta_options()

true_max_pain, straddle_price, atm_strike, true_max_pain = np.nan, np.nan, 0.0, np.nan
call_oi_wall, put_oi_wall, call_gamma_wall, put_gamma_wall = None, None, None, None
pcr, pcr_volume, atm_row = 0.0, 0.0, None
near_call, mid_call, far_call, near_put, mid_put, far_put = [], [], [], [], [], []
straddle_valid = False
filtered_options = pd.DataFrame()

if not options_df.empty and latest_price > 0:
    options_df['GEX_Call'] = options_df['Gamma_Call'] * options_df['OI_Contracts_Call'] * latest_price * dynamic_contract_size
    options_df['GEX_Put'] = options_df['Gamma_Put'] * options_df['OI_Contracts_Put'] * latest_price * dynamic_contract_size
    options_df['Dist_to_Spot'] = abs(options_df['Strike'] - latest_price)
    
    atm_strike = options_df.loc[options_df['Dist_to_Spot'].idxmin(), 'Strike']
    atm_row = options_df[options_df['Strike'] == atm_strike].iloc[0]
    cb, ca, cm = atm_row['Bid_Call'], atm_row['Ask_Call'], atm_row['Mark_Call']
    pb, pa, pm = atm_row['Bid_Put'], atm_row['Ask_Put'], atm_row['Mark_Put']
    
    if not any(pd.isna(x) for x in [cb, ca, pb, pa]) and cb > 0 and ca > 0 and pb > 0 and pa > 0 and cb <= ca and pb <= pa:
        straddle_price = cm + pm
        straddle_valid = True

    lower_bound = latest_price * 0.90; upper_bound = latest_price * 1.10
    filtered_options = options_df[(options_df['Strike'] >= lower_bound) & (options_df['Strike'] <= upper_bound)].copy()
    
    if not filtered_options.empty:
        temp_filt = filtered_options.fillna(0)
        if temp_filt['OI_USD_Call'].max() > 0: call_oi_wall = temp_filt.loc[temp_filt['OI_USD_Call'].idxmax()]
        if temp_filt['OI_USD_Put'].max() > 0: put_oi_wall = temp_filt.loc[temp_filt['OI_USD_Put'].idxmax()]
        if temp_filt['GEX_Call'].max() > 0: call_gamma_wall = temp_filt.loc[temp_filt['GEX_Call'].idxmax()]
        if temp_filt['GEX_Put'].max() > 0: put_gamma_wall = temp_filt.loc[temp_filt['GEX_Put'].idxmax()]
        
        total_calls_usd = temp_filt['OI_USD_Call'].sum(); total_puts_usd = temp_filt['OI_USD_Put'].sum()
        pcr = total_puts_usd / total_calls_usd if total_calls_usd > 0 else 0
        total_calls_vol = temp_filt['Volume_Call'].sum(); total_puts_vol = temp_filt['Volume_Put'].sum()
        pcr_volume = total_puts_vol / total_calls_vol if total_calls_vol > 0 else 0

        if is_chain_complete and not options_df.empty:
            all_strikes = options_df['Strike'].tolist()
            all_call_oi = dict(zip(options_df['Strike'], options_df['OI_Contracts_Call'].fillna(0)))
            all_put_oi = dict(zip(options_df['Strike'], options_df['OI_Contracts_Put'].fillna(0)))
            pain_dict = {}
            for settle in all_strikes:
                pain_dict[settle] = sum((all_call_oi.get(k, 0) * (settle - k) if settle > k else 0) + (all_put_oi.get(k, 0) * (k - settle) if settle < k else 0) for k in all_strikes)
            true_max_pain = min(pain_dict, key=pain_dict.get) if pain_dict else np.nan

        if straddle_valid and raw_chain_df is not None and strike_ladder is not None:
            base_straddle = p_state["session_start_straddle"] if p_state["session_start_straddle"] else straddle_price
            near_call, mid_call, far_call = find_oi_clusters(strike_ladder, raw_chain_df, "Call", latest_price, base_straddle)
            near_put, mid_put, far_put = find_oi_clusters(strike_ladder, raw_chain_df, "Put", latest_price, base_straddle)

# ==========================================
# RENDER UI TABS
# ==========================================
tab_cockpit, tab_amt, tab_options, tab_raw = st.tabs(["🚦 Cockpit", "📖 AMT Structure", "🔍 Options", "📋 Raw Data"])

# --- TAB 1: COCKPIT ---
with tab_cockpit:
    st.subheader("🚦 Vital Signs")
    v1, v2, v3, v4 = st.columns(4)
    v1.metric("Current Price", f"${latest_price:,.2f}")
    v2.metric("AMT State", f"{current_amt_name}", f"Code {current_amt_state}")
    
    if funding_rate is not None: v4.metric("Funding Rate (8h)", f"{funding_rate:+.4f}%")
    else: v4.metric("Funding Rate", "N/A")
        
    st.markdown("---")
    st.subheader("🧠 Tape Read & Confluence")
    
    tc1, tc2, tc3 = st.columns(3)
    tc2.metric("Derivatives Flow", f"{'OI Rising' if oi_usd else 'N/A'} + {'Perp Discount' if sp_prem and sp_prem < 0 else 'Perp Premium' if sp_prem and sp_prem > 0 else 'N/A'}")
    
    if pd.isna(final_vah) or pd.isna(final_val): loc = "Value Area Unavailable"
    else: loc = "Above VAH" if latest_price > final_vah else "Below VAL" if latest_price < final_val else "Inside VA"
    tc3.metric("Price Location", loc, f"${latest_price:,.0f}")
    tc1.metric("AMT State", current_amt_name, f"Code {current_amt_state}")
    
    st.markdown("---")
    st.subheader("📐 Quantitative Confluence")
    qc1, qc2, qc3 = st.columns(3)
    
    latest_dev_poc = journal_df_check.iloc[-1]['Dev POC'] if not journal_df_check.empty else np.nan
    prior_va_width = final_vah - final_val if not pd.isna(final_vah) and not pd.isna(final_val) else np.nan
    straddle_ratio = straddle_price / prior_va_width if not pd.isna(prior_va_width) and prior_va_width > 0 and not pd.isna(straddle_price) and straddle_valid else np.nan
    mp_poc_distance = true_max_pain - latest_dev_poc if not pd.isna(true_max_pain) and not pd.isna(latest_dev_poc) else np.nan
    
    if not pd.isna(straddle_ratio):
        qc1.metric("Straddle Ratio (vs Prior VA)", f"{straddle_ratio:.2f}x", "Expected Move > Prior Value" if straddle_ratio > 1.0 else "Expected Move < Prior Value", delta_color="normal" if straddle_ratio > 1.0 else "off")
    else: qc1.metric("Straddle Ratio (vs Prior VA)", "N/A")
    
    if not pd.isna(mp_poc_distance): qc2.metric("Max Pain vs Dev POC", f"${mp_poc_distance:,.0f}")
    else: qc2.metric("Max Pain vs Dev POC", "N/A")
    
    if not pd.isna(prior_va_width) and not pd.isna(straddle_price) and straddle_valid:
        qc3.metric("VA Width vs Straddle", f"${prior_va_width:,.0f} / ${straddle_price:,.0f}")
    else: qc3.metric("VA Width vs Straddle", "N/A")

# --- TAB 2: AMT STRUCTURE ---
with tab_amt:
    st.subheader("🗺️ AMT Map & Context")
    c1, c2, c3 = st.columns(3)
    c1.metric("Prior VAH", f"${final_vah:,.2f}" if not pd.isna(final_vah) else "N/A")
    c2.metric("Prior VAL", f"${final_val:,.2f}" if not pd.isna(final_val) else "N/A")
    c3.metric("Prior POC", f"${final_poc:,.2f}" if not pd.isna(final_poc) else "N/A")
    
    st.markdown("### Session Journey")
    if not session_df.empty:
        st.dataframe(session_df, use_container_width=True, hide_index=True, column_config={
            "Session": st.column_config.TextColumn("Session"),
            "Open": st.column_config.NumberColumn("Open", format="$%.0f"),
            "High": st.column_config.NumberColumn("High", format="$%.0f"),
            "Low": st.column_config.NumberColumn("Low", format="$%.0f"),
            "Close": st.column_config.NumberColumn("Close", format="$%.0f"),
            "POC": st.column_config.NumberColumn("POC", format="$%.0f"),
            "VAH": st.column_config.NumberColumn("VAH", format="$%.0f"),
            "VAL": st.column_config.NumberColumn("VAL", format="$%.0f"),
        })
        
    st.markdown("### Project Echo Journal")
    if not journal_df_check.empty:
        st.dataframe(journal_df_check, use_container_width=True, hide_index=True, column_config={
            "Time": st.column_config.TextColumn("Time (IST)"),
            "Close": st.column_config.NumberColumn("Close", format="$%.0f"),
            "Code": st.column_config.NumberColumn("Code"),
            "State Name": st.column_config.TextColumn("State Name"),
            "Dev VAH": st.column_config.NumberColumn("Dev VAH", format="$%.0f"),
            "Dev POC": st.column_config.NumberColumn("Dev POC", format="$%.0f"),
            "Dev VAL": st.column_config.NumberColumn("Dev VAL", format="$%.0f"),
        })

# --- TAB 3: OPTIONS ---
with tab_options:
    if not options_df.empty and atm_row is not None:
        st.subheader(f"📉 Options Snapshot (Expiry: {loaded_expiry})")
        o1, o2, o3, o4 = st.columns(4)
        o1.metric("ATM Strike", f"${atm_strike:,.0f}")
        if straddle_valid: o2.metric("ATM Straddle", f"${straddle_price:,.2f}")
        else: o2.metric("ATM Straddle", "⚠️ INVALID")
        o3.metric("Max Pain", f"${true_max_pain:,.0f}" if not pd.isna(true_max_pain) else "N/A")
        o4.metric("Local OI PCR", f"{pcr:.2f}")
        
        st.markdown("---")
        st.subheader("🎯 OI Concentrations & Walls")
        
        c_near = near_call[0] if near_call else None; p_near = near_put[0] if near_put else None
        c_mid = mid_call[0] if mid_call else None; p_mid = mid_put[0] if mid_put else None
        c_far = far_call[0] if far_call else None; p_far = far_put[0] if far_put else None

        raw_clusters = [
            {"Tier": "📍 Near (<0.5x)", "Call": c_near, "Put": p_near},
            {"Tier": "📍 Mid (0.5 - 1.0x)", "Call": c_mid, "Put": p_mid},
            {"Tier": "🌍 Distant (>1.0x)", "Call": c_far, "Put": p_far}
        ]
        cluster_rows = []
        for r in raw_clusters:
            c_data = r['Call']; p_data = r['Put']
            cluster_rows.append({
                "Tier": r['Tier'],
                "Call Strike": f"${c_data['VWAS']:,.0f}" if c_data else "N/A",
                "Call OI": f"${c_data['OI_USD']/1e6:.2f}M" if c_data else "N/A",
                "Put Strike": f"${p_data['VWAS']:,.0f}" if p_data else "N/A",
                "Put OI": f"${p_data['OI_USD']/1e6:.2f}M" if p_data else "N/A",
            })
        st.dataframe(pd.DataFrame(cluster_rows), use_container_width=True, hide_index=True)
        
        st.markdown("##### Context Metrics")
        w1, w2 = st.columns(2)
        w1.metric("Gross Gamma Wall Estimates", f"C: ${call_gamma_wall['Strike']:,.0f} / P: ${put_gamma_wall['Strike']:,.0f}" if call_gamma_wall is not None and put_gamma_wall is not None else "N/A")
        w2.metric("Local Structural Extreme", f"C: ${call_oi_wall['Strike']:,.0f} / P: ${put_oi_wall['Strike']:,.0f}" if call_oi_wall is not None and put_oi_wall is not None else "N/A")

# --- TAB 4: RAW DATA ---
with tab_raw:
    if not filtered_options.empty:
        st.subheader("Raw Options Chain — 10 Nearest Strikes")
        
        # Create a copy for display to avoid modifying the original
        display_df = filtered_options.copy()
        
        # Convert USD OI to Millions for clean display
        display_df['Call_OI_M'] = display_df['OI_USD_Call'] / 1e6
        display_df['Put_OI_M'] = display_df['OI_USD_Put'] / 1e6
        
        # Select and reorder columns using actual variable names
        display_df = display_df[[
            'Strike', 
            'Mark_Call', 
            'Call_OI_M', 
            'Volume_Call', 
            'IV_Call', 
            'IV_Put', 
            'Volume_Put', 
            'Put_OI_M', 
            'Mark_Put'
        ]]
        
        # Styling function (Purple anchor for CMP)
        def highlight_cmp_and_walls(row):
            if row['Strike'] == atm_strike:
                return ['background-color: #6A1B9A; color: white; font-weight: bold'] * len(row)
            return [''] * len(row)

        styled_df = display_df.style.apply(highlight_cmp_and_walls, axis=1)
        
        # Render with proper formatting
        st.dataframe(styled_df, use_container_width=True, hide_index=True, column_config={
            "Strike": st.column_config.NumberColumn("Strike", format="$%.0f"),
            "Mark_Call": st.column_config.NumberColumn("Call Prem", format="$%.2f"),
            "Call_OI_M": st.column_config.NumberColumn("Call OI (USD)", format="$%.2fM"),
            "Volume_Call": st.column_config.NumberColumn("Call Vol", format="%d"),
            "IV_Call": st.column_config.NumberColumn("Call IV", format="%.1f%%"),
            "IV_Put": st.column_config.NumberColumn("Put IV", format="%.1f%%"),
            "Volume_Put": st.column_config.NumberColumn("Put Vol", format="%d"),
            "Put_OI_M": st.column_config.NumberColumn("Put OI (USD)", format="$%.2fM"),
            "Mark_Put": st.column_config.NumberColumn("Put Prem", format="$%.2f"),
        })

    st.markdown("---")
    with st.expander("📋 Copy Dashboard Snapshot"):
        snapshot_text = f"""
=== BTC TERMINAL SNAPSHOT ===
Time: {datetime.datetime.now(IST_TZ).strftime('%Y-%m-%d %H:%M:%S')} IST

[VITAL SIGNS]
Price: ${latest_price:,.2f}
AMT State: {current_amt_name} (Code {current_amt_state})
Funding Rate: {funding_rate if funding_rate else 'N/A'}

[AMT MAP]
Prior VAH: ${final_vah:,.2f} | Prior POC: ${final_poc:,.2f} | Prior VAL: ${final_val:,.2f}
"""
        st.code(snapshot_text, language="text")

components.html("<script>setTimeout(function(){ window.parent.location.reload(); }, 180000);</script>", height=0, width=0)
