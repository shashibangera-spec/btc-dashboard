import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import math
import streamlit.components.v1 as components
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="BTC Data Terminal", layout="wide")

# ==========================================
# TELEGRAM ALERT FUNCTION
# ==========================================
def send_telegram_message(message):
    try:
        token = st.secrets["TELEGRAM_TOKEN"]
        chat_id = st.secrets["TELEGRAM_CHAT_ID"]
    except KeyError:
        return
    
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    payload = {"chat_id": chat_id, "text": message, "parse_mode": "HTML"}
    try:
        requests.post(url, json=payload, timeout=5)
    except Exception:
        pass

# ==========================================
# SIDEBAR: MANUAL AMT OVERRIDES
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

st.title("⚡ BTC AMT & Delta Options Terminal")

# Initialize Session State
if 'prev_pcr' not in st.session_state: st.session_state.prev_pcr = 0.0
if 'prev_max_pain' not in st.session_state: st.session_state.prev_max_pain = 0.0
if 'prev_call_wall' not in st.session_state: st.session_state.prev_call_wall = 0.0
if 'prev_put_wall' not in st.session_state: st.session_state.prev_put_wall = 0.0
if 'prev_amt_state' not in st.session_state: st.session_state.prev_amt_state = -1

manual_values_valid = (input_vah > 0 and input_val > 0 and input_poc > 0 and input_vah >= input_poc >= input_val)
final_vah, final_val, final_poc = np.nan, np.nan, np.nan

alert_triggered = False
alert_message = ""

# ==========================================
# SECTION 1: BINANCE DATA & AMT MATH
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
                
            df = pd.DataFrame(res, columns=['Date', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
            df['Date'] = pd.to_datetime(df['Date'], unit='ms', utc=True).dt.tz_convert('Asia/Kolkata')
            df = df[['Date', 'open', 'high', 'low', 'close']]
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            
            df['ShiftedDate'] = df['Date'] - pd.Timedelta(hours=5, minutes=30)
            df = df.sort_values('Date').reset_index(drop=True)
            return df
        except Exception:
            continue
    return pd.DataFrame()

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
    
    asia_end = cycle_start + pd.Timedelta(hours=8)        # 13:30 IST
    europe_end = cycle_start + pd.Timedelta(hours=12)     # 17:30 IST
    us_cash_end = cycle_start + pd.Timedelta(hours=20)    # 01:30 IST
    cycle_end = cycle_start + pd.Timedelta(hours=24)      # 05:30 IST
    
    asia_df = df[(df['Date'] >= cycle_start) & (df['Date'] < asia_end)]
    eu_df = df[(df['Date'] >= asia_end) & (df['Date'] < europe_end)]
    us_cash_df = df[(df['Date'] >= europe_end) & (df['Date'] < us_cash_end)]
    us_overnight_df = df[(df['Date'] >= us_cash_end) & (df['Date'] < cycle_end)]
    
    sessions = []
    for name, session_data in [
        ("Asia (05:30-13:30)", asia_df),
        ("Europe (13:30-17:30)", eu_df),
        ("US Cash (17:30-01:30)", us_cash_df),
        ("US Overnight (01:30-05:30)", us_overnight_df)
    ]:
        if session_data.empty: continue
        vah, val, poc = calculate_tpo(session_data)
        
        close = session_data['close'].iloc[-1]
        high = session_data['high'].max()
        low = session_data['low'].min()
        
        # 1. MACRO BIAS (vs Prior 24h Value)
        macro_bias = "⚪ Inside Prior Value"
        if not pd.isna(prev_vah) and not pd.isna(prev_val):
            if close > prev_vah: macro_bias = "🟢 Bullish (Above Prior VAH)"
            elif close < prev_val: macro_bias = "🔴 Bearish (Below Prior VAL)"
            elif high > prev_vah and close < prev_vah: macro_bias = "⚠️ Bull Trap (Rejected Prior VAH)"
            elif low < prev_val and close > prev_val: macro_bias = "⚠️ Bear Trap (Rejected Prior VAL)"
                
        # 2. MICRO BIAS (vs Session's Own Value Area)
        micro_bias = "⚪ Balanced (Inside Value)"
        if not pd.isna(vah) and not pd.isna(val):
            if close > vah: micro_bias = "🟢 Strong Close (Above Session VAH)"
            elif close < val: micro_bias = "🔴 Weak Close (Below Session VAL)"

        sessions.append({
            "Session": name, 
            "Open": session_data['open'].iloc[0], 
            "High": high,
            "Low": low, 
            "Close": close,
            "Macro Bias": macro_bias,
            "Micro Bias": micro_bias,
            "POC": poc, 
            "VAH": vah, 
            "VAL": val
        })
    return pd.DataFrame(sessions)

# ==========================================
# SECTION 2: PROJECT ECHO STATE ENGINE
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
        elif auctionState == 16:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i
            elif close >= prevVAL and close <= prevVAH: auctionState = 11
            
        prev_currVAH, prev_currVAL, prev_currPOC = currVAH, currVAL, currPOC
        
        if i == 0 or auctionState != journal[-1]['Code'] or i == len(today_df) - 1:
            journal.append({
                "Time": bar['Date'].strftime('%H:%M'),
                "Close": close,
                "Code": auctionState,
                "State Name": state_names.get(auctionState, "UNKNOWN"),
                "Dev VAH": currVAH,
                "Dev POC": currPOC,
                "Dev VAL": currVAL,
            })
    return pd.DataFrame(journal), manual_valid

# ==========================================
# SECTION 3: DELTA OPTIONS DATA
# ==========================================
@st.cache_data(ttl=180)
def fetch_delta_options():
    BASE_URL = "https://api.india.delta.exchange/v2"
    
    def fetch_paginated_data(url):
        results = []
        seen_cursors = set()
        page_count = 0
        max_pages = 20
        
        while url and page_count < max_pages:
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                results.extend(data.get("result", []))
                meta = data.get("meta", {})
                after = meta.get("after")
                
                if not after or after in seen_cursors: break
                seen_cursors.add(after)
                
                base_url = url.split("?")[0]
                query_params = url.split("?")[1]
                params = [p for p in query_params.split("&") if not p.startswith("after=")]
                params.append(f"after={after}")
                url = f"{base_url}?{'&'.join(params)}"
                page_count += 1
            except Exception:
                break
        return results

    try:
        products = fetch_paginated_data(f"{BASE_URL}/products?states=live&page_size=1000")
        btc_options = [p for p in products if p.get("underlying_asset", {}).get("symbol") == "BTC" and p.get("contract_type") in ("call_options", "put_options")]

        if not btc_options: return pd.DataFrame(), "No Options"

        expiries = set()
        for opt in btc_options:
            parts = opt.get("symbol", "").split("-")
            if len(parts) >= 4: expiries.add(parts[3])
        
        valid_expiries = []
        today = datetime.date.today()
        for exp in expiries:
            try:
                exp_date = datetime.datetime.strptime(exp, "%d%m%y").date()
                if exp_date >= today: valid_expiries.append((exp_date, exp))
            except ValueError: continue
                
        if not valid_expiries: return pd.DataFrame(), "No Valid Expiry"
        nearest_expiry = min(valid_expiries, key=lambda x: x[0])[1]
        
        filtered_options = [opt for opt in btc_options if len(opt.get("symbol", "").split("-")) >= 4 and opt.get("symbol", "").split("-")[3] == nearest_expiry]

        tickers = fetch_paginated_data(f"{BASE_URL}/tickers?contract_types=call_options,put_options&page_size=1000")
        ticker_map = {t.get("symbol"): t for t in tickers if t.get("symbol")}

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
                oi_usd = float(ticker.get("oi_value_usd") or 0.0)
                oi_contracts = float(ticker.get("oi_contracts") or ticker.get("oi") or 0.0)
                
                quotes = ticker.get("quotes", {})
                bid = float(quotes.get("best_bid") or ticker.get("best_bid", 0) or 0.0)
                ask = float(quotes.get("best_ask") or ticker.get("best_ask", 0) or 0.0)
                mark = float(ticker.get("mark_price") or 0.0)

                greeks = ticker.get("greeks") or {}
                gamma = float(greeks.get("gamma") or "0")
                iv = float(quotes.get("mark_iv") or "0") * 100
                
                chain.append({"Strike": strike, "Type": opt_type, "Bid": bid, "Ask": ask, "Mark": mark, "OI_USD": oi_usd, "OI_Contracts": oi_contracts, "Gamma": gamma, "IV": iv})
            except (ValueError, TypeError):
                continue

        df = pd.DataFrame(chain)
        if df.empty: return pd.DataFrame(), nearest_expiry

        df_pivot = df.pivot_table(index="Strike", columns="Type", values=["Bid", "Ask", "Mark", "OI_USD", "OI_Contracts", "Gamma", "IV"], aggfunc="first").reset_index().fillna(0)

        new_cols = []
        for col in df_pivot.columns:
            if isinstance(col, tuple): new_cols.append("_".join([str(c) for c in col if c != ""]).strip("_"))
            else: new_cols.append(str(col))
        df_pivot.columns = new_cols

        expected_columns = ['Bid_Call', 'Ask_Call', 'Mark_Call', 'OI_USD_Call', 'OI_Contracts_Call', 'Gamma_Call', 'IV_Call', 'Bid_Put', 'Ask_Put', 'Mark_Put', 'OI_USD_Put', 'OI_Contracts_Put', 'Gamma_Put', 'IV_Put']
        for col in expected_columns:
            if col not in df_pivot.columns: df_pivot[col] = 0.0

        return df_pivot, nearest_expiry

    except Exception as e:
        return pd.DataFrame(), "Error"

# ==========================================
# SECTION 4: UI RENDERING
# ==========================================
df = fetch_amt_data()
binance_status = "🟢 Connected"

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
    
    st.subheader("📊 Live AMT Data (24-Hour Prior)")
    if manual_values_valid: st.info("🟢 Reference Source: TRADINGVIEW MANUAL")
    elif not pd.isna(final_vah): st.info("🔵 Reference Source: PYTHON CALCULATED")
    else: st.warning("🔴 Reference Source: UNAVAILABLE")
        
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Current Price", f"${latest_price:,.2f}")
    col2.metric("Prior VAH", f"${final_vah:,.2f}" if not pd.isna(final_vah) else "N/A")
    col3.metric("Prior VAL", f"${final_val:,.2f}" if not pd.isna(final_val) else "N/A")
    col4.metric("Prior POC", f"${final_poc:,.2f}" if not pd.isna(final_poc) else "N/A")
    
    st.markdown("---")

    st.subheader("🗺️ Session Journey (05:30 AM to 05:30 AM)")
    session_df = calculate_session_journey(df, final_vah, final_val)
    
    if not session_df.empty:
        st.dataframe(
            session_df, 
            use_container_width=True, 
            hide_index=True,
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

    st.subheader("📖 PROJECT ECHO - State Journal")
    st.caption("⚠️ Experimental State Journal. Shows when the auction shifted.")
    
    journal_res = generate_state_journal(df, manual_values_valid, input_vah, input_val, input_poc)
    
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
            
            current_amt_state = journal_df.iloc[-1]['Code']
            current_amt_name = journal_df.iloc[-1]['State Name']
            
            if st.session_state.prev_amt_state != -1 and current_amt_state != st.session_state.prev_amt_state:
                if current_amt_state in [2, 3, 10, 11, 14]: 
                    alert_triggered = True
                    alert_message += f"📖 AMT STATE SHIFT: {current_amt_name}<br>"
            
            st.session_state.prev_amt_state = current_amt_state
        else:
            st.info("Building State Journal...")
    st.markdown("---")

else:
    latest_price = 0
    binance_status = "🔴 Failed"
    st.warning("⚠️ Could not fetch AMT Data from Binance.")

# --- OPTIONS FLOW ---
options_df, loaded_expiry = fetch_delta_options()
delta_status = "🟢 Connected"

if not options_df.empty:
    st.subheader(f"📉 Delta Options Chain (Expiry: {loaded_expiry})")
    
    contract_size = 0.001
    options_df['GEX_Call'] = options_df['Gamma_Call'] * options_df['OI_Contracts_Call'] * latest_price * contract_size
    options_df['GEX_Put'] = options_df['Gamma_Put'] * options_df['OI_Contracts_Put'] * latest_price * contract_size
    
    if latest_price > 0:
        options_df['Dist_to_Spot'] = abs(options_df['Strike'] - latest_price)
        atm_strike = options_df.loc[options_df['Dist_to_Spot'].idxmin(), 'Strike']
        atm_row = options_df[options_df['Strike'] == atm_strike].iloc[0]
        
        call_premium = atm_row['Mark_Call']
        put_premium = atm_row['Mark_Put']
        straddle_price = call_premium + put_premium
        atm_iv = atm_row['IV_Call']
        
        dist_to_vah = final_vah - latest_price if not pd.isna(final_vah) else 0
        dist_to_val = latest_price - final_val if not pd.isna(final_val) else 0
        
        st.markdown(f"#### 🧠 Market Expectations (ATM Straddle at ${atm_strike:,.0f})")
        e1, e2, e3, e4, e5 = st.columns(5)
        e1.metric("ATM Straddle Price", f"${straddle_price:,.2f}", "Breakeven Distance")
        e2.metric("ATM Implied Volatility", f"{atm_iv:.2f}%", "Expected Volatility")
        
        if dist_to_vah > 0: e3.metric("To Prior VAH", f"${dist_to_vah:,.2f}", "Distance")
        else: e3.metric("To Prior VAH", f"${dist_to_vah:,.2f}", "Above VAH")

        if dist_to_val > 0: e4.metric("To Prior VAL", f"${dist_to_val:,.2f}", "Distance")
        else: e4.metric("To Prior VAL", f"${dist_to_val:,.2f}", "Below VAL")
            
        e5.metric("ATM Call Premium", f"${call_premium:,.2f}")
        st.markdown("---")

    all_strikes = options_df['Strike'].tolist()
    all_call_oi = dict(zip(options_df['Strike'], options_df['OI_Contracts_Call']))
    all_put_oi = dict(zip(options_df['Strike'], options_df['OI_Contracts_Put']))
    
    pain_dict = {}
    for settle in all_strikes:
        total_pain = 0
        for strike in all_strikes:
            if settle > strike: total_pain += all_call_oi.get(strike, 0) * (settle - strike)
            if settle < strike: total_pain += all_put_oi.get(strike, 0) * (strike - settle)
        pain_dict[settle] = total_pain
        
    true_max_pain = min(pain_dict, key=pain_dict.get)

    if latest_price > 0:
        lower_bound = latest_price * 0.90
        upper_bound = latest_price * 1.10
        filtered_options = options_df[(options_df['Strike'] >= lower_bound) & (options_df['Strike'] <= upper_bound)].copy()
    else:
        filtered_options = options_df.copy()

    if filtered_options.empty:
        st.warning("⚠️ No option strikes available inside the selected spot range (90%-110%).")
        st.stop()

    call_oi_wall = filtered_options.loc[filtered_options['OI_USD_Call'].idxmax()]
    put_oi_wall = filtered_options.loc[filtered_options['OI_USD_Put'].idxmax()]
    call_gamma_wall = filtered_options.loc[filtered_options['GEX_Call'].idxmax()]
    put_gamma_wall = filtered_options.loc[filtered_options['GEX_Put'].idxmax()]
    
    total_calls_usd = filtered_options['OI_USD_Call'].sum()
    total_puts_usd = filtered_options['OI_USD_Put'].sum()
    pcr = total_puts_usd / total_calls_usd if total_calls_usd > 0 else 0

    if pcr > 1.20 or pcr < 0.30:
        alert_triggered = True
        alert_message += f"⚠️ PCR EXTREME: OI PCR is {pcr:.2f}<br>"
    if st.session_state.prev_max_pain != 0 and abs(true_max_pain - st.session_state.prev_max_pain) >= 500:
        alert_triggered = True
        alert_message += f"🔄 MAX PAIN SHIFT: Moved to ${true_max_pain:,.0f}!<br>"
    if st.session_state.prev_call_wall != 0 and abs(call_oi_wall['Strike'] - st.session_state.prev_call_wall) >= 200:
        alert_triggered = True
        alert_message += f"🚧 CALL OI WALL SHIFT: Moved to ${call_oi_wall['Strike']:,.0f}!<br>"
    if st.session_state.prev_put_wall != 0 and abs(put_oi_wall['Strike'] - st.session_state.prev_put_wall) >= 200:
        alert_triggered = True
        alert_message += f"🚧 PUT OI WALL SHIFT: Moved to ${put_oi_wall['Strike']:,.0f}!<br>"

    st.session_state.prev_pcr = pcr
    st.session_state.prev_max_pain = true_max_pain
    st.session_state.prev_call_wall = call_oi_wall['Strike']
    st.session_state.prev_put_wall = put_oi_wall['Strike']

    st.subheader("📊 Options Structure")
    w1, w2, w3, w4 = st.columns(4)
    w1.metric("🔴 Highest Call OI", f"${call_oi_wall['Strike']:,.0f}", f"OI: ${call_oi_wall['OI_USD_Call']/1e6:.2f}M")
    w2.metric("🟢 Highest Put OI", f"${put_oi_wall['Strike']:,.0f}", f"OI: ${put_oi_wall['OI_USD_Put']/1e6:.2f}M")
    w3.metric("⚖️ True Max Pain", f"${true_max_pain:,.0f}", "Min Payout")
    w4.metric("📊 OI PCR (USD)", f"{pcr:.2f}", "")

    w5, w6, w7, w8 = st.columns(4)
    w5.metric("🔴 Call Gamma Wall", f"${call_gamma_wall['Strike']:,.0f}", f"Est. GEX: ${call_gamma_wall['GEX_Call']:,.0f}")
    w6.metric("🟢 Put Gamma Wall", f"${put_gamma_wall['Strike']:,.0f}", f"Est. GEX: ${put_gamma_wall['GEX_Put']:,.0f}")
    st.caption("⚠️ GEX is calculated within the ±10% spot band and uses a fixed 0.001 contract multiplier.")
    
    st.markdown("---")

    st.subheader("🎯 Premium at AMT Levels")
    if not pd.isna(final_vah) and not pd.isna(final_val) and not pd.isna(final_poc):
        vah_strike = filtered_options.loc[(filtered_options['Strike'] - final_vah).abs().idxmin()]
        val_strike = filtered_options.loc[(filtered_options['Strike'] - final_val).abs().idxmin()]
        poc_strike = filtered_options.loc[(filtered_options['Strike'] - final_poc).abs().idxmin()]
        
        c1, c2, c3 = st.columns(3)
        c1.metric(f"Prior VAH (${vah_strike['Strike']:,.0f})", f"Call: ${vah_strike['Mark_Call']:,.2f}", f"Put: ${vah_strike['Mark_Put']:,.2f}")
        c2.metric(f"Prior POC (${poc_strike['Strike']:,.0f})", f"Call: ${poc_strike['Mark_Call']:,.2f}", f"Put: ${poc_strike['Mark_Put']:,.2f}")
        c3.metric(f"Prior VAL (${val_strike['Strike']:,.0f})", f"Call: ${val_strike['Mark_Call']:,.2f}", f"Put: ${val_strike['Mark_Put']:,.2f}")
    else:
        st.info("Manual Overrides required.")
    st.markdown("---")

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
    delta_status = "🔴 Failed"
    st.error("Could not load Options Chain data from Delta API.")

# ==========================================
# FIRE ALERTS
# ==========================================
if alert_triggered:
    clean_telegram_msg = alert_message.replace("<br>", "\n")
    send_telegram_message(f"🚨 BTC TERMINAL ALERT 🚨\n\n{clean_telegram_msg}")
    st.warning(f"🚨 **DATA UPDATE** 🚨\n\n{alert_message}")
    components.html("<audio autoplay><source src='https://actions.google.com/sounds/v1/alarms/beep_short.ogg' type='audio/ogg'></audio>", height=0, width=0)

if st.button("🔄 Refresh All Data"):
    st.cache_data.clear()
    st.rerun()

st.markdown("---")
now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
st.caption(f"**System Health:** Binance {binance_status} | Delta {delta_status} | Last Fetched: {now}")

components.html("<script>setTimeout(function(){ window.parent.location.reload(); }, 180000);</script>", height=0, width=0)
