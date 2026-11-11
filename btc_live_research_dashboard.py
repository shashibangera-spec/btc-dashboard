import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import json
from datetime import timezone
from supabase import create_client
from amt_core import fetch_binance_data, calculate_tpo
from futures_positioning import FuturesPositioning, valid_metric_value

st.set_page_config(page_title="BTC Combined Research", layout="wide")
st.title("🔬 BTC Combined Research Terminal")
st.caption("AMT Engine (v1.0) + Futures Positioning (v1.2) + Options Forensics")

# --- SUPABASE FETCHERS ---
@st.cache_data(ttl=60, show_spinner=False)
def fetch_supabase_aggregate_oi():
    empty_result = {"current_oi_usd": None, "previous_oi_usd": None, "delta_usd": None, "delta_pct": None, "status": "DATA_UNAVAILABLE"}
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)

        response = client.table("futures_oi_snapshots").select("created_at,oi_usd,status").eq("exchange", "AGGREGATE").order("created_at", desc=True).limit(100).execute()
        rows = response.data or []

        if not rows: return {**empty_result, "status": "NO_DATABASE_HISTORY"}

        parsed_rows = []
        for row in rows:
            timestamp = pd.to_datetime(row.get("created_at"), utc=True, errors="coerce")
            raw_oi = row.get("oi_usd")
            try: oi_value = float(raw_oi) if raw_oi is not None else None
            except: oi_value = None
            if pd.isna(timestamp): continue
            
            contributors = []
            try:
                status_json = json.loads(row.get("status", "{}"))
                contributors = status_json.get("oi_contributors", [])
            except: pass
            
            parsed_rows.append({"time": timestamp, "oi": oi_value, "contributors": set(contributors)})

        if not parsed_rows: return {**empty_result, "status": "NO_VALID_TIMESTAMPS"}

        parsed_rows.sort(key=lambda item: item["time"])
        latest = parsed_rows[-1]

        if latest["oi"] is None or not np.isfinite(latest["oi"]) or latest["oi"] <= 0:
            return {**empty_result, "status": "LATEST_AGGREGATE_OI_UNAVAILABLE"}

        # AUDIT FIX #4: Strict 25-35 Minute Baseline Validation
        previous_candidates = [item for item in parsed_rows if item["time"] < latest["time"] and item["oi"] is not None]
        if not previous_candidates:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "PENDING_BASELINE"}

        # Find the closest candidate to exactly 30 mins ago
        target_time = latest["time"] - pd.Timedelta(minutes=30)
        previous = min(previous_candidates, key=lambda item: abs((item["time"] - target_time).total_seconds()))
        
        # Calculate exact minute gap
        diff_minutes = abs((latest["time"] - previous["time"]).total_seconds()) / 60.0
        
        if diff_minutes < 25.0 or diff_minutes > 35.0:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": f"INVALID_BASELINE_GAP_{diff_minutes:.1f}M"}

        if latest["contributors"] != previous["contributors"]:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "COVERAGE_MISMATCH"}

        delta_usd = latest["oi"] - previous["oi"]
        delta_pct = (delta_usd / previous["oi"]) * 100.0

        return {"current_oi_usd": latest["oi"], "previous_oi_usd": previous["oi"], "delta_usd": delta_usd, "delta_pct": delta_pct, "status": "OK"}
    except Exception as exc:
        return {**empty_result, "status": f"ERROR: {exc}"}

def process_options_snapshot(client, snapshot_id):
    chain = client.table("options_oi_snapshots").select("strike_price, option_type, oi_contracts, mark_iv, volume_24h, taker_buy_volume, taker_sell_volume").eq("snapshot_id", snapshot_id).execute()
    if not chain.data: return None
    
    df = pd.DataFrame(chain.data)
    if df.empty: return None
    
    pivot_oi = df.pivot_table(index='strike_price', columns='option_type', values='oi_contracts', aggfunc='sum').reset_index()
    pivot_iv = df.pivot_table(index='strike_price', columns='option_type', values='mark_iv', aggfunc='mean').reset_index()
    pivot_vol = df.pivot_table(index='strike_price', columns='option_type', values='volume_24h', aggfunc='sum').reset_index()
    
    pivot_oi.rename(columns={'CALL': 'Call OI', 'PUT': 'Put OI'}, inplace=True)
    pivot_iv.rename(columns={'CALL': 'Call IV', 'PUT': 'Put IV'}, inplace=True)
    pivot_vol.rename(columns={'CALL': 'Call Vol', 'PUT': 'Put Vol'}, inplace=True)
    
    merged = pd.merge(pivot_oi, pivot_iv, on='strike_price', how='outer')
    merged = pd.merge(merged, pivot_vol, on='strike_price', how='outer')
    
    if 'taker_buy_volume' in df.columns:
        taker_agg = df.groupby('strike_price')[['taker_buy_volume', 'taker_sell_volume']].sum().reset_index()
        merged = pd.merge(merged, taker_agg, on='strike_price', how='outer')
    
    return merged.sort_values('strike_price')

@st.cache_data(ttl=60, show_spinner=False)
def fetch_options_forensics():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        
        # AUDIT FIX #1: Strictly filter for SUCCESS runs only
        runs = client.table("options_collection_runs").select("snapshot_id, completed_at").eq("status", "SUCCESS").order("completed_at", desc=True).limit(2).execute()
        
        if not runs.data or len(runs.data) < 2:
            # If we don't have at least two SUCCESSFUL runs, we can't calculate migration
            # Fallback to just getting the single latest successful run
            latest_run = client.table("options_collection_runs").select("snapshot_id, completed_at").eq("status", "SUCCESS").order("completed_at", desc=True).limit(1).execute()
            if latest_run.data:
                snapshots = latest_run.data
            else:
                return None
        else:
            snapshots = runs.data
            
        latest_snapshot = snapshots[0]
        previous_snapshot = snapshots[1] if len(snapshots) > 1 else None
        
        current_df = process_options_snapshot(client, latest_snapshot["snapshot_id"])
        previous_df = process_options_snapshot(client, previous_snapshot["snapshot_id"]) if previous_snapshot else None
        
        return {
            "timestamp": latest_snapshot["completed_at"], 
            "previous_timestamp": previous_snapshot["completed_at"] if previous_snapshot else None,
            "data": current_df,
            "previous_data": previous_df
        }
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=60)
def get_cached_binance_data():
    return fetch_binance_data()

# --- OPTIONS CLASSIFIERS ---
def get_nearest_strikes(df, current_price, count=5):
    if df is None or df.empty: return pd.DataFrame()
    df_copy = df.copy()
    df_copy['distance'] = abs(df_copy['strike_price'] - current_price)
    return df_copy.nsmallest(count, 'distance').sort_values('strike_price')

def analyze_neighboring_strikes(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return None
    walls = []
    if 'Call OI' in closest.columns:
        call_ois = closest['Call OI'].fillna(0).tolist()
        for i in range(1, len(call_ois) - 1):
            neighbor_avg = (call_ois[i-1] + call_ois[i+1]) / 2
            if call_ois[i] > (neighbor_avg * 1.5) and call_ois[i] > 500:
                walls.append({"strike": closest.iloc[i]['strike_price'], "type": "CALL", "oi": call_ois[i]})
    if 'Put OI' in closest.columns:
        put_ois = closest['Put OI'].fillna(0).tolist()
        for i in range(1, len(put_ois) - 1):
            neighbor_avg = (put_ois[i-1] + put_ois[i+1]) / 2
            if put_ois[i] > (neighbor_avg * 1.5) and put_ois[i] > 500:
                walls.append({"strike": closest.iloc[i]['strike_price'], "type": "PUT", "oi": put_ois[i]})
    return walls

def analyze_iv_skew(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return None
    avg_call_iv = closest['Call IV'].mean()
    avg_put_iv = closest['Put IV'].mean()
    if pd.isna(avg_call_iv) or pd.isna(avg_put_iv): return None
    skew = avg_call_iv - avg_put_iv
    skew_pct = (skew / avg_put_iv) * 100
    status = "NEUTRAL"
    interpretation = "Volatility is balanced. Market is pricing a neutral outcome."
    if skew_pct > 5.0:
        status = "BULLISH_URGENCY"
        interpretation = "Call IV is significantly higher than Put IV. Traders are paying a premium for upside."
    elif skew_pct < -5.0:
        status = "BEARISH_URGENCY"
        interpretation = "Put IV is significantly higher than Call IV. Traders are paying a premium for downside protection."
    return {"avg_call_iv": avg_call_iv, "avg_put_iv": avg_put_iv, "skew_pct": skew_pct, "status": status, "interpretation": interpretation}

def analyze_volume_vs_oi(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return None
    total_call_vol = closest['Call Vol'].sum()
    total_call_oi = closest['Call OI'].sum()
    total_put_vol = closest['Put Vol'].sum()
    total_put_oi = closest['Put OI'].sum()
    if total_call_oi == 0 or total_put_oi == 0: return None
    call_ratio = total_call_vol / total_call_oi
    put_ratio = total_put_vol / total_put_oi
    status = "STALE_POSITIONING"
    interpretation = "Volume is low relative to OI. Existing positions are being held, but no significant new money is entering."
    if call_ratio > 0.5 or put_ratio > 0.5:
        status = "FRESH_MONEY_ENTERING"
        interpretation = "High Volume relative to OI detected. Traders are actively opening new positions today."
    direction = "NEUTRAL"
    if call_ratio > (put_ratio * 1.5): direction = "BULLISH_ACTIVITY"
    elif put_ratio > (call_ratio * 1.5): direction = "BEARISH_ACTIVITY"
    return {"call_ratio": call_ratio, "put_ratio": put_ratio, "status": status, "direction": direction, "interpretation": interpretation}

def analyze_oi_migration(current_df, previous_df):
    if current_df is None or previous_df is None or current_df.empty or previous_df.empty: return None
    
    # AUDIT FIX #5: Crash protection for idxmax() if data is missing
    if current_df['Call OI'].dropna().empty or current_df['Put OI'].dropna().empty: return None
    if previous_df['Call OI'].dropna().empty or previous_df['Put OI'].dropna().empty: return None
        
    peak_call_current = current_df.loc[current_df['Call OI'].idxmax()]['strike_price']
    peak_put_current = current_df.loc[current_df['Put OI'].idxmax()]['strike_price']
    peak_call_previous = previous_df.loc[previous_df['Call OI'].idxmax()]['strike_price']
    peak_put_previous = previous_df.loc[previous_df['Put OI'].idxmax()]['strike_price']
    
    call_shift = peak_call_current - peak_call_previous
    put_shift = peak_put_current - peak_put_previous
    
    status = "STABLE"
    interpretation = "Peak OI strikes have not moved significantly."
    
    if call_shift > 0 and put_shift > 0:
        status = "BULLISH_MIGRATION"
        interpretation = f"Both Call and Put walls moved HIGHER. Sentiment is shifting Bullish."
    elif call_shift < 0 and put_shift < 0:
        status = "BEARISH_MIGRATION"
        interpretation = f"Both Call and Put walls moved LOWER. Sentiment is shifting Bearish."
    elif call_shift > 0 and put_shift <= 0:
        status = "MILD_BULLISH"
        interpretation = f"Call wall moved higher while Put wall stayed/lowered."
    return {"call_shift": call_shift, "put_shift": put_shift, "status": status, "interpretation": interpretation}

def analyze_aggressor_trades(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty or 'taker_buy_volume' not in closest.columns: return None
    
    total_taker_buy = closest['taker_buy_volume'].sum()
    total_taker_sell = closest['taker_sell_volume'].sum()
    
    if pd.isna(total_taker_buy) and pd.isna(total_taker_sell): return None
        
    total_taker_buy = total_taker_buy if not pd.isna(total_taker_buy) else 0
    total_taker_sell = total_taker_sell if not pd.isna(total_taker_sell) else 0
    
    status = "NEUTRAL_FLOW"
    interpretation = "Taker buys and sells are relatively balanced."
    
    if total_taker_buy > (total_taker_sell * 1.3) and total_taker_buy > 10:
        status = "AGGRESSIVE_BUYING"
        interpretation = f"Heavy Taker Buying detected. Buyers are lifting the ask. (Buys: {total_taker_buy:,.0f} vs Sells: {total_taker_sell:,.0f})"
    elif total_taker_sell > (total_taker_buy * 1.3) and total_taker_sell > 10:
        status = "AGGRESSIVE_SELLING"
        interpretation = f"Heavy Taker Selling detected. Sellers are hitting the bid. (Sells: {total_taker_sell:,.0f} vs Buys: {total_taker_buy:,.0f})"
        
    return {"buy_vol": total_taker_buy, "sell_vol": total_taker_sell, "status": status, "interpretation": interpretation}

# --- DATA FETCHING ---
with st.spinner("Fetching Binance data..."):
    df = get_cached_binance_data()

now_utc = datetime.datetime.now(timezone.utc)
df['is_live'] = df['close_time'] > int(now_utc.timestamp() * 1000)
completed_df = df[~df['is_live']].copy()

today_str = completed_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
yesterday_df = completed_df[completed_df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str]

if not yesterday_df.empty:
    last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
    vah, val, poc = calculate_tpo(prev_day_df)
else:
    vah, val, poc = np.nan, np.nan, np.nan

current_price = completed_df.iloc[-1]['close']

# --- AMT ENGINE ---
@st.cache_data(ttl=60, show_spinner=False)
def fetch_amt_shadow_state():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        res = client.table("amt_snapshots").select("candle_open_time, confirmed_state").order("candle_open_time", desc=True).limit(1).execute()
        if res.data: return res.data[0]
        return None
    except: return None

shadow_state = fetch_amt_shadow_state()

# --- FUTURES ENGINE ---
@st.cache_resource
def get_futures_engine():
    return FuturesPositioning()

with st.spinner("Fetching Futures Positioning Data..."):
    try:
        fut_snapshot = get_futures_engine().collect()
    except: fut_snapshot = None

supabase_oi = fetch_supabase_aggregate_oi()
opt_forensics = fetch_options_forensics()

# --- RENDER UI ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Price", f"${current_price:,.2f}")
col2.metric("AMT Live", "See Below")
col3.metric("AMT Certified", shadow_state["confirmed_state"] if shadow_state else "N/A")
col4.metric("ATR (21)", f"${completed_df.iloc[-1]['atr']:,.2f}")

st.markdown("---")
st.subheader("🗺️ Layer 1: Auction Market Theory")
p1, p2, p3 = st.columns(3)
p1.metric("Prior VAH", f"${vah:,.2f}")
p2.metric("Prior POC", f"${poc:,.2f}")
p3.metric("Prior VAL", f"${val:,.2f}")

if shadow_state:
    st.info(f"**Certified AMT State (from Supabase):** {shadow_state['confirmed_state']} (Last Updated: {shadow_state['candle_open_time']})")
else:
    st.warning("Certified AMT State: PENDING (Waiting for first GitHub Action run)")

st.markdown("---")
st.subheader("📈 Layer 2: Futures Positioning")

if fut_snapshot:
    agg = fut_snapshot["aggregate"]
    cov = fut_snapshot["coverage"]

    f1, f2, f3, f4 = st.columns(4)
    f1.metric("Aggregate OI", f"${supabase_oi['current_oi_usd']/1e9:.2f}B" if supabase_oi["current_oi_usd"] else "N/A", f"Coverage: {cov['label']}")

    if supabase_oi["status"] == "OK":
        f2.metric("30m OI Delta", f"{supabase_oi['delta_pct']:+.2f}%", f"${supabase_oi['delta_usd']:+,.0f}")
    elif "INVALID_BASELINE_GAP" in supabase_oi["status"]:
        f2.metric("30m OI Delta", "REJECTED", "Baseline not 25-35m", delta_color="inverse")
    elif supabase_oi["status"] == "COVERAGE_MISMATCH":
        f2.metric("30m OI Delta", "UNAVAILABLE", "Coverage changed", delta_color="off")
    else:
        f2.metric("30m OI Delta", "PENDING", supabase_oi["status"], delta_color="off")

    f3.metric("Weighted Basis", f"{agg['basis_pct_oi_weighted']:+.4f}%" if agg['basis_pct_oi_weighted'] else "N/A")
    f4.metric("Weighted Funding", f"{agg['funding_rate_oi_weighted']:+.4f}%" if agg['funding_rate_oi_weighted'] else "N/A")

st.markdown("---")
st.subheader("🔍 Layer 3: Options Forensics (Delta India)")

if opt_forensics and "data" in opt_forensics:
    st.caption(f"Latest Snapshot: {opt_forensics['timestamp']}")
    
    opt_df = opt_forensics["data"]
    prev_opt_df = opt_forensics.get("previous_data")
    
    # --- FILTER 1 ---
    walls = analyze_neighboring_strikes(opt_df, current_price)
    st.markdown("#### 🧱 Filter 1: Neighboring Strike Analysis (OI Walls)")
    if walls:
        for wall in walls:
            st.warning(f"**{wall['type']} WALL DETECTED at ${wall['strike']:,.0f}** (OI: {wall['oi']:,.0f})")
    else:
        st.success("**Unclassified Concentration:** No distinct OI walls found near current price.")

    # --- FILTER 2 ---
    iv_analysis = analyze_iv_skew(opt_df, current_price)
    st.markdown("#### 🌪️ Filter 2: Implied Volatility Skew (Urgency)")
    if iv_analysis:
        c1, c2, c3 = st.columns(3)
        c1.metric("Avg Call IV (Nearby)", f"{iv_analysis['avg_call_iv']*100:.2f}%")
        c2.metric("Avg Put IV (Nearby)", f"{iv_analysis['avg_put_iv']*100:.2f}%")
        delta_color = "normal" if iv_analysis['skew_pct'] > 0 else "inverse"
        c3.metric("IV Skew (Call - Put)", f"{iv_analysis['skew_pct']:+.2f}%", delta_color=delta_color)
        if iv_analysis['status'] == "BULLISH_URGENCY": st.error(f"**{iv_analysis['status']}:** {iv_analysis['interpretation']}")
        elif iv_analysis['status'] == "BEARISH_URGENCY": st.warning(f"**{iv_analysis['status']}:** {iv_analysis['interpretation']}")
        else: st.info(f"**{iv_analysis['status']}:** {iv_analysis['interpretation']}")

    # --- FILTER 3 ---
    vol_analysis = analyze_volume_vs_oi(opt_df, current_price)
    st.markdown("#### 🐋 Filter 3: Volume vs OI (Fresh Money Detector)")
    if vol_analysis:
        v1, v2 = st.columns(2)
        v1.metric("Call Vol/OI Ratio", f"{vol_analysis['call_ratio']:.2f}x")
        v2.metric("Put Vol/OI Ratio", f"{vol_analysis['put_ratio']:.2f}x")
        if vol_analysis['status'] == "FRESH_MONEY_ENTERING":
            if vol_analysis['direction'] == "BULLISH_ACTIVITY": st.success(f"**FRESH MONEY: BULLISH** - {vol_analysis['interpretation']}")
            elif vol_analysis['direction'] == "BEARISH_ACTIVITY": st.error(f"**FRESH MONEY: BEARISH** - {vol_analysis['interpretation']}")
            else: st.info(f"**FRESH MONEY: NEUTRAL** - {vol_analysis['interpretation']}")
        else: st.info(f"**STALE POSITIONING:** {vol_analysis['interpretation']}")

    # --- FILTER 4 ---
    migration_analysis = analyze_oi_migration(opt_df, prev_opt_df)
    st.markdown("#### 🚚 Filter 4: OI Migration (Sentiment Shift)")
    if migration_analysis:
        if migration_analysis['status'] == "BULLISH_MIGRATION": st.success(f"**BULLISH MIGRATION:** {migration_analysis['interpretation']}")
        elif migration_analysis['status'] == "BEARISH_MIGRATION": st.error(f"**BEARISH MIGRATION:** {migration_analysis['interpretation']}")
        elif migration_analysis['status'] == "MILD_BULLISH": st.info(f"**MILD BULLISH MIGRATION:** {migration_analysis['interpretation']}")
        else: st.info(f"**STABLE MIGRATION:** {migration_analysis['interpretation']}")
    else: st.warning("**PENDING:** Waiting for a second historical successful snapshot to calculate migration.")

    # --- FILTER 5 ---
    aggressor_analysis = analyze_aggressor_trades(opt_df, current_price)
    st.markdown("#### ⚔️ Filter 5: Aggressor Trades (Directional Taker Flow)")
    if aggressor_analysis:
        a1, a2 = st.columns(2)
        a1.metric("Taker Buy Vol", f"{aggressor_analysis['buy_vol']:,.0f}")
        a2.metric("Taker Sell Vol", f"{aggressor_analysis['sell_vol']:,.0f}")
        
        if aggressor_analysis['status'] == "AGGRESSIVE_BUYING":
            st.success(f"**AGGRESSIVE BUYING:** {aggressor_analysis['interpretation']}")
        elif aggressor_analysis['status'] == "AGGRESSIVE_SELLING":
            st.error(f"**AGGRESSIVE SELLING:** {aggressor_analysis['interpretation']}")
        else:
            st.info(f"**NEUTRAL FLOW:** {aggressor_analysis['interpretation']}")
    else:
        st.info("**PENDING:** Awaiting Aggressor Trade Data. We need to wait for the updated options_collector.py to run.")

    st.markdown("#### Raw Options Chain (Closest 10 Strikes)")
    closest_df = get_nearest_strikes(opt_df, current_price, count=10)
    st.dataframe(
        closest_df.drop(columns=['distance'], errors='ignore'), 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "strike_price": st.column_config.NumberColumn("Strike", format="$%d"),
            "Call OI": st.column_config.NumberColumn("Call OI", format="%d"),
            "Put OI": st.column_config.NumberColumn("Put OI", format="%d"),
            "Call Vol": st.column_config.NumberColumn("Call Vol", format="%d"),
            "Put Vol": st.column_config.NumberColumn("Put Vol", format="%d"),
            "Call IV": st.column_config.NumberColumn("Call IV", format="%.4f"),
            "Put IV": st.column_config.NumberColumn("Put IV", format="%.4f"),
        }
    )
elif opt_forensics and "error" in opt_forensics:
    st.error(f"Error loading options data: {opt_forensics['error']}")
else:
    st.info("PENDING: Waiting for options_collector.py to populate Supabase.")
