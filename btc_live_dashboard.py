import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import streamlit.components.v1 as components
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

st.set_page_config(page_title="BTC Data Terminal", layout="wide")

# ==========================================
# SIDEBAR: MANUAL AMT OVERRIDES
# ==========================================
qp = st.query_params
default_vah = float(qp.get("vah", 0.0))
default_val = float(qp.get("val", 0.0))
default_poc = float(qp.get("poc", 0.0))

st.sidebar.header("🛠️ Manual AMT Overrides")
st.sidebar.caption("Type exact values from TradingView to override Python's math.")
st.sidebar.caption("⚠️ Leave as 0 to use auto-calculation.")

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

# Initialize session state for tracking changes
if 'prev_pcr' not in st.session_state:
    st.session_state.prev_pcr = 0.0
if 'prev_max_pain' not in st.session_state:
    st.session_state.prev_max_pain = 0.0
if 'prev_call_wall' not in st.session_state:
    st.session_state.prev_call_wall = 0.0
if 'prev_put_wall' not in st.session_state:
    st.session_state.prev_put_wall = 0.0

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
            url = f"{base}/api/v3/klines?symbol=BTCUSDT&interval=30m&limit=100"
            response = session.get(url, timeout=10)
            response.raise_for_status()
            res = response.json()
            if not res or not isinstance(res, list): continue
                
            df = pd.DataFrame(res, columns=['Date', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
            df['Date'] = pd.to_datetime(df['Date'], unit='ms', utc=True)
            df = df[['Date', 'open', 'high', 'low', 'close']]
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            df = df.sort_values('Date').set_index('Date')
            return df
        except Exception:
            continue
    return pd.DataFrame()

def calculate_amt(df):
    row_size = 50.0
    value_pct = 0.70
    profiles = {}
    dates = df.index.strftime('%Y-%m-%d').unique()
    
    for date in dates:
        day_df = df[df.index.strftime('%Y-%m-%d') == date]
        if day_df.empty: continue
        high = day_df['high'].max()
        low = day_df['low'].min()
        if high - low <= 0: continue
        
        base_bucket = np.floor(low / row_size)
        num_buckets = int(np.floor(high / row_size) - base_bucket + 1)
        tpo = np.zeros(num_buckets)
        
        for _, row in day_df.iterrows():
            lo_idx = max(0, int(np.floor(row['low'] / row_size) - base_bucket))
            hi_idx = min(num_buckets - 1, int(np.floor(row['high'] / row_size) - base_bucket))
            for i in range(lo_idx, hi_idx + 1): tpo[i] += 1
                
        poc_idx = np.argmax(tpo)
        target = tpo.sum() * value_pct
        covered = tpo[poc_idx]
        up, dn = poc_idx, poc_idx
        
        while covered < target:
            if up >= num_buckets - 1 and dn <= 0: break
            up_val = tpo[up + 1] if up < num_buckets - 1 else -1
            dn_val = tpo[dn - 1] if dn > 0 else -1
            if up_val >= dn_val: up += 1; covered += up_val
            else: dn -= 1; covered += dn_val
                
        profiles[date] = {
            'VAH': (base_bucket + up) * row_size + row_size/2,
            'VAL': (base_bucket + dn) * row_size + row_size/2,
            'POC': (base_bucket + poc_idx) * row_size + row_size/2
        }
    return profiles

# ==========================================
# SECTION 2: DELTA OPTIONS DATA
# ==========================================
@st.cache_data(ttl=180)
def fetch_delta_options():
    BASE_URL = "https://api.india.delta.exchange/v2"
    
    def fetch_paginated_data(url):
        results = []
        while url:
            try:
                response = requests.get(url, timeout=10)
                response.raise_for_status()
                data = response.json()
                results.extend(data.get("result", []))
                meta = data.get("meta", {})
                after = meta.get("after")
                if after:
                    base_url = url.split("?")[0]
                    query_params = url.split("?")[1]
                    params = [p for p in query_params.split("&") if not p.startswith("after=")]
                    params.append(f"after={after}")
                    url = f"{base_url}?{'&'.join(params)}"
                else:
                    url = None
            except Exception:
                break
        return results

    try:
        products = fetch_paginated_data(f"{BASE_URL}/products?states=live&page_size=1000")
        btc_options = []
        for p in products:
            if p.get("underlying_asset", {}).get("symbol") == "BTC" and p.get("contract_type") in ("call_options", "put_options"):
                btc_options.append(p)

        if not btc_options: return pd.DataFrame(), "No Options"

        expiries = set()
        for opt in btc_options:
            parts = opt.get("symbol", "").split("-")
            if len(parts) >= 4: expiries.add(parts[3])
        
        nearest_expiry = sorted(list(expiries))[0]
        
        filtered_options = []
        for opt in btc_options:
            parts = opt.get("symbol", "").split("-")
            if len(parts) >= 4 and parts[3] == nearest_expiry:
                filtered_options.append(opt)

        tickers = fetch_paginated_data(f"{BASE_URL}/tickers?contract_types=call_options,put_options&page_size=1000")
        ticker_map = {t.get("symbol"): t for t in tickers if t.get("symbol")}

        chain = []
        for opt in filtered_options:
            sym = opt.get("symbol")
            ticker = ticker_map.get(sym)
            if not ticker: continue

            strike = opt.get("strike_price")
            if strike is None:
                parts = sym.split("-")
                if len(parts) >= 3:
                    raw_strike = parts[2].replace(",", "").strip().upper()
                    strike = float(raw_strike.replace("K", "")) * 1000 if "K" in raw_strike else float(raw_strike)
            
            opt_type = "Call" if opt.get("contract_type") == "call_options" else "Put"
            oi_usd = ticker.get("oi_value_usd") or ticker.get("oi", 0)
            oi_contracts = ticker.get("oi_contracts") or ticker.get("oi", 0)
            
            quotes = ticker.get("quotes", {})
            bid = quotes.get("best_bid") or ticker.get("best_bid", 0)
            ask = quotes.get("best_ask") or ticker.get("best_ask", 0)

            greeks = ticker.get("greeks") or {}
            gamma_str = greeks.get("gamma") or "0"
            gamma = float(gamma_str)
            
            iv_str = quotes.get("mark_iv") or "0"
            iv = float(iv_str) * 100

            try:
                chain.append({
                    "Strike": float(strike),
                    "Type": opt_type,
                    "Bid": float(bid) if bid else 0.0,
                    "Ask": float(ask) if ask else 0.0,
                    "OI_USD": float(oi_usd) if oi_usd else 0.0,
                    "OI_Contracts": float(oi_contracts) if oi_contracts else 0.0,
                    "Gamma": gamma,
                    "IV": iv
                })
            except (ValueError, TypeError):
                continue

        df = pd.DataFrame(chain)
        if df.empty: return pd.DataFrame(), nearest_expiry

        df_pivot = df.pivot_table(index="Strike", columns="Type", values=["Bid", "Ask", "OI_USD", "OI_Contracts", "Gamma", "IV"], aggfunc="first").reset_index().fillna(0)

        new_cols = []
        for col in df_pivot.columns:
            if isinstance(col, tuple):
                new_cols.append("_".join([str(c) for c in col if c != ""]).strip("_"))
            else:
                new_cols.append(str(col))
        df_pivot.columns = new_cols

        return df_pivot, nearest_expiry

    except Exception as e:
        return pd.DataFrame(), "Error"

# ==========================================
# SECTION 3: UI RENDERING & ALERTS
# ==========================================
df = fetch_amt_data()
if not df.empty:
    profiles = calculate_amt(df)
    dates_list = sorted(profiles.keys())
    
    if len(dates_list) >= 2:
        prior_date_str = dates_list[-2]
        calc_vah = profiles[prior_date_str]['VAH']
        calc_val = profiles[prior_date_str]['VAL']
        calc_poc = profiles[prior_date_str]['POC']
    else:
        calc_vah, calc_val, calc_poc = np.nan, np.nan, np.nan

    final_vah = input_vah if input_vah > 0 else calc_vah
    final_val = input_val if input_val > 0 else calc_val
    final_poc = input_poc if input_poc > 0 else calc_poc

    latest_price = df['close'].iloc[-1]
    
    st.subheader("📊 Live AMT Data")
    if input_vah > 0:
        st.info("Manual AMT Overrides are ACTIVE.")
        
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Current Price", f"${latest_price:,.2f}")
    col2.metric("Prior VAH", f"${final_vah:,.2f}" if not pd.isna(final_vah) else "N/A")
    col3.metric("Prior VAL", f"${final_val:,.2f}" if not pd.isna(final_val) else "N/A")
    col4.metric("Prior POC", f"${final_poc:,.2f}" if not pd.isna(final_poc) else "N/A")
else:
    latest_price = 0
    st.warning("⚠️ Could not fetch AMT Data from Binance. Displaying Options Data only.")

st.markdown("---")

options_df, loaded_expiry = fetch_delta_options()

if not options_df.empty:
    st.subheader(f"📉 Delta Options Chain (Expiry: {loaded_expiry})")
    
    if latest_price > 0:
        lower_bound = latest_price * 0.90
        upper_bound = latest_price * 1.10
        filtered_options = options_df[(options_df['Strike'] >= lower_bound) & (options_df['Strike'] <= upper_bound)].copy()
    else:
        filtered_options = options_df.copy()

    # TRUE MAX PAIN
    strikes = filtered_options['Strike'].tolist()
    call_oi = dict(zip(filtered_options['Strike'], filtered_options['OI_Contracts_Call']))
    put_oi = dict(zip(filtered_options['Strike'], filtered_options['OI_Contracts_Put']))
    
    pain_dict = {}
    for settle in strikes:
        total_pain = 0
        for strike in strikes:
            if settle > strike: total_pain += call_oi.get(strike, 0) * (settle - strike)
            if settle < strike: total_pain += put_oi.get(strike, 0) * (strike - settle)
        pain_dict[settle] = total_pain
        
    true_max_pain = min(pain_dict, key=pain_dict.get)
    
    # WALLS & PCR
    call_wall_row = filtered_options.loc[filtered_options['OI_USD_Call'].idxmax()]
    put_wall_row = filtered_options.loc[filtered_options['OI_USD_Put'].idxmax()]
    
    total_calls_usd = filtered_options['OI_USD_Call'].sum()
    total_puts_usd = filtered_options['OI_USD_Put'].sum()
    pcr = total_puts_usd / total_calls_usd if total_calls_usd > 0 else 0

    # Calculate GEX
    contract_size = 0.001
    call_gex = call_wall_row['Gamma_Call'] * call_wall_row['OI_Contracts_Call'] * latest_price * contract_size
    put_gex = put_wall_row['Gamma_Put'] * put_wall_row['OI_Contracts_Put'] * latest_price * contract_size
    
    # ==========================================
    # 🚨 ALERTS ENGINE 🚨
    # ==========================================
    alert_triggered = False
    alert_message = ""
    
    # 1. PCR Extreme Alert
    if pcr > 1.20:
        alert_triggered = True
        alert_message += f"⚠️ EXTREME GREED: PCR is {pcr:.2f} (Retail buying heavy Puts. Watch for a bounce!)<br>"
    elif pcr < 0.30:
        alert_triggered = True
        alert_message += f"⚠️ EXTREME FEAR: PCR is {pcr:.2f} (Retail buying heavy Calls. Watch for a dump!)<br>"
        
    # 2. Max Pain Migration (Moved more than $500)
    if st.session_state.prev_max_pain != 0 and abs(true_max_pain - st.session_state.prev_max_pain) >= 500:
        alert_triggered = True
        alert_message += f"🔄 MAX PAIN SHIFT: Moved from ${st.session_state.prev_max_pain:,.0f} to ${true_max_pain:,.0f}!<br>"
        
    # 3. Wall Shift (Call or Put wall jumped by at least $200)
    if st.session_state.prev_call_wall != 0 and abs(call_wall_row['Strike'] - st.session_state.prev_call_wall) >= 200:
        alert_triggered = True
        alert_message += f"🚧 CALL WALL SHIFT: Moved from ${st.session_state.prev_call_wall:,.0f} to ${call_wall_row['Strike']:,.0f}!<br>"
        
    if st.session_state.prev_put_wall != 0 and abs(put_wall_row['Strike'] - st.session_state.prev_put_wall) >= 200:
        alert_triggered = True
        alert_message += f"🚧 PUT WALL SHIFT: Moved from ${st.session_state.prev_put_wall:,.0f} to ${put_wall_row['Strike']:,.0f}!<br>"

    # Display Alert
    if alert_triggered:
        st.error(f"🚨 **TRADING ALERT FIRED** 🚨\n\n{alert_message}")
        # Play a ding sound
        components.html("""
            <audio autoplay>
                <source src="https://actions.google.com/sounds/v1/alarms/beep_short.ogg" type="audio/ogg">
            </audio>
        """, height=0, width=0)

    # Update the State for the next refresh
    st.session_state.prev_pcr = pcr
    st.session_state.prev_max_pain = true_max_pain
    st.session_state.prev_call_wall = call_wall_row['Strike']
    st.session_state.prev_put_wall = put_wall_row['Strike']

    # ==========================================
    # RENDER METRICS
    # ==========================================
    st.subheader("🎯 Key Options Walls & Greeks")
    w1, w2, w3, w4 = st.columns(4)
    w1.metric("🔴 Call Wall (Resistance)", f"${call_wall_row['Strike']:,.0f}", f"GEX: ${call_gex:,.0f}")
    w2.metric("🟢 Put Wall (Support)", f"${put_wall_row['Strike']:,.0f}", f"GEX: ${put_gex:,.0f}")
    w3.metric("⚖️ True Max Pain", f"${true_max_pain:,.0f}", "Min Payout to Buyers")
    w4.metric("📊 Put/Call Ratio", f"{pcr:.2f}", "Bearish" if pcr > 1 else "Bullish", delta_color="inverse")
    
    st.markdown("---")

    display_df = filtered_options[['Bid_Call', 'Ask_Call', 'OI_USD_Call', 'Gamma_Call', 'IV_Call', 'Strike', 'IV_Put', 'Gamma_Put', 'OI_USD_Put', 'Bid_Put', 'Ask_Put']].copy()
    
    st.dataframe(
        display_df, 
        use_container_width=True, 
        hide_index=True,
        column_config={
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
        }
    )

    csv = display_df.to_csv(index=False).encode('utf-8')
    st.download_button(
        label="📥 Download Options Data as CSV",
        data=csv,
        file_name=f"delta_options_{loaded_expiry}.csv",
        mime="text/csv",
    )

else:
    st.error("Could not load Options Chain data from Delta API.")

if st.button("🔄 Refresh All Data"):
    st.cache_data.clear()
    st.rerun()

components.html("<script>setTimeout(function(){ window.parent.location.reload(); }, 180000);</script>", height=0, width=0)