import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import json
from datetime import timezone
from supabase import create_client
from amt_core import fetch_binance_data, calculate_tpo

st.set_page_config(page_title="BTC Combined Research", layout="wide")
st.title("🔬 BTC Combined Research Terminal")
st.caption("AMT Engine (v1.0) + Futures Positioning (v1.2) + Options Forensics")

# --- SUPABASE FETCHERS ---
@st.cache_data(ttl=60, show_spinner=False)
def fetch_layer2_supabase_data():
    empty_result = {
        "current_oi_usd": None, "previous_oi_usd": None, "delta_usd": None, "delta_pct": None, "status": "DATA_UNAVAILABLE",
        "basis_pct": None, "funding_rate": None, "coverage": "UNKNOWN"
    }
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)

        response = client.table("futures_oi_snapshots").select("created_at,oi_usd,funding_rate,basis_pct,status").eq("exchange", "AGGREGATE").order("created_at", desc=True).limit(100).execute()
        rows = response.data or []

        if not rows: return {**empty_result, "status": "NO_DATABASE_HISTORY"}

        parsed_rows = []
        for row in rows:
            timestamp = pd.to_datetime(row.get("created_at"), utc=True, errors="coerce")
            if pd.isna(timestamp): continue
            
            status_json = {}
            try: status_json = json.loads(row.get("status", "{}"))
            except: pass
            
            parsed_rows.append({
                "time": timestamp, 
                "oi": float(row.get("oi_usd")) if row.get("oi_usd") else None,
                "funding": float(row.get("funding_rate")) if row.get("funding_rate") else None,
                "basis": float(row.get("basis_pct")) if row.get("basis_pct") else None,
                "contributors": set(status_json.get("oi_contributors", [])),
                "coverage": status_json.get("coverage", {}).get("label", "UNKNOWN")
            })

        if not parsed_rows: return {**empty_result, "status": "NO_VALID_TIMESTAMPS"}

        parsed_rows.sort(key=lambda item: item["time"])
        latest = parsed_rows[-1]

        result = {
            "current_oi_usd": latest["oi"], 
            "basis_pct": latest["basis"],
            "funding_rate": latest["funding"],
            "coverage": latest["coverage"],
            "status": "OK"
        }

        if latest["oi"] is None or latest["oi"] <= 0:
            return {**result, "status": "LATEST_AGGREGATE_OI_UNAVAILABLE"}

        previous_candidates = [item for item in parsed_rows if item["time"] < latest["time"] and item["oi"] is not None]
        if not previous_candidates:
            return {**result, "status": "PENDING_BASELINE"}

        target_time = latest["time"] - pd.Timedelta(minutes=30)
        previous = min(previous_candidates, key=lambda item: abs((item["time"] - target_time).total_seconds()))
        
        diff_minutes = abs((latest["time"] - previous["time"]).total_seconds()) / 60.0
        
        if diff_minutes < 25.0 or diff_minutes > 35.0:
            return {**result, "status": f"REJECTED_BASELINE_{diff_minutes:.0f}M"}

        if latest["contributors"] != previous["contributors"]:
            return {**result, "status": "COVERAGE_MISMATCH"}

        delta_usd = latest["oi"] - previous["oi"]
        delta_pct = (delta_usd / previous["oi"]) * 100.0

        return {**result, "previous_oi_usd": previous["oi"], "delta_usd": delta_usd, "delta_pct": delta_pct, "status": "OK"}
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
        
        runs = client.table("options_collection_runs").select("snapshot_id, completed_at").eq("status", "SUCCESS").order("completed_at", desc=True).limit(2).execute()
        
        if not runs.data or len(runs.data) < 2:
            latest_run = client.table("options_collection_runs").select("snapshot_id, completed_at").eq("status", "SUCCESS").order("completed_at", desc=True).limit(1).execute()
            snapshots = latest_run.data if latest_run.data else []
        else:
            snapshots = runs.data
            
        if not snapshots: return None
        
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

# --- OPTIONS CLASSIFIERS (With Semantic Guards) ---
def get_nearest_strikes(df, current_price, count=5):
    if df is None or df.empty: return pd.DataFrame()
    df_copy = df.copy()
    df_copy['distance'] = abs(df_copy['strike_price'] - current_price)
    return df_copy.nsmallest(count, 'distance').sort_values('strike_price')

def analyze_neighboring_strikes(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return {"status": "UNAVAILABLE", "reason": "NO_DATA"}
    if 'Call OI' not in closest.columns or 'Put OI' not in closest.columns:
        return {"status": "UNAVAILABLE", "reason": "MISSING_COLUMNS"}
    if closest['Call OI'].dropna().empty or closest['Put OI'].dropna().empty:
        return {"status": "INSUFFICIENT_DATA", "reason": "NULL_OI_SIDE"}
    
    usable = closest.dropna(subset=['strike_price', 'Call OI', 'Put OI'])
    if len(usable) < 3:
        return {"status": "INSUFFICIENT_DATA", "reason": "FEWER_THAN_3_USABLE_STRIKES"}
    
    closest = usable.sort_values('strike_price')
    walls = []
    if 'Call OI' in closest.columns:
        call_ois = closest['Call OI'].tolist()
        for i in range(1, len(call_ois) - 1):
            neighbor_avg = (call_ois[i-1] + call_ois[i+1]) / 2
            if call_ois[i] > (neighbor_avg * 1.5) and call_ois[i] > 500:
                walls.append({"strike": closest.iloc[i]['strike_price'], "type": "CALL", "oi": call_ois[i]})
    if 'Put OI' in closest.columns:
        put_ois = closest['Put OI'].tolist()
        for i in range(1, len(put_ois) - 1):
            neighbor_avg = (put_ois[i-1] + put_ois[i+1]) / 2
            if put_ois[i] > (neighbor_avg * 1.5) and put_ois[i] > 500:
                walls.append({"strike": closest.iloc[i]['strike_price'], "type": "PUT", "oi": put_ois[i]})
    return {"status": "OK", "walls": walls}

def analyze_iv_skew(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return {"status": "UNAVAILABLE", "reason": "NO_DATA"}
    if 'Call IV' not in closest.columns or 'Put IV' not in closest.columns:
        return {"status": "UNAVAILABLE", "reason": "MISSING_COLUMNS"}
    if closest['Call IV'].dropna().empty or closest['Put IV'].dropna().empty:
        return {"status": "INSUFFICIENT_DATA", "reason": "NULL_VALUES"}
        
    avg_call_iv = closest['Call IV'].mean()
    avg_put_iv = closest['Put IV'].mean()
    if pd.isna(avg_call_iv) or pd.isna(avg_put_iv): return {"status": "INSUFFICIENT_DATA", "reason": "CALCULATION_FAILED"}
    if avg_put_iv == 0: return {"status": "INSUFFICIENT_DATA", "reason": "ZERO_PUT_IV"}
    
    skew = avg_call_iv - avg_put_iv
    skew_pct = (skew / avg_put_iv) * 100
    status = "NEUTRAL"
    if skew_pct > 5.0: status = "BULLISH_URGENCY"
    elif skew_pct < -5.0: status = "BEARISH_URGENCY"
    return {"status": status, "skew_pct": skew_pct, "avg_call_iv": avg_call_iv, "avg_put_iv": avg_put_iv}

def analyze_volume_vs_oi(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return {"status": "UNAVAILABLE", "reason": "NO_DATA"}
    req_cols = ['Call Vol', 'Call OI', 'Put Vol', 'Put OI']
    if not all(col in closest.columns for col in req_cols):
        return {"status": "UNAVAILABLE", "reason": "MISSING_COLUMNS"}
        
    if closest['Call Vol'].dropna().empty or closest['Put Vol'].dropna().empty:
        return {"status": "INSUFFICIENT_DATA", "reason": "NULL_VOLUME"}
    
    total_call_vol = closest['Call Vol'].sum()
    total_call_oi = closest['Call OI'].sum()
    total_put_vol = closest['Put Vol'].sum()
    total_put_oi = closest['Put OI'].sum()
    
    if total_call_oi == 0 or total_put_oi == 0: return {"status": "INSUFFICIENT_DATA", "reason": "ZERO_OI"}
    
    call_ratio = total_call_vol / total_call_oi
    put_ratio = total_put_vol / total_put_oi
    status = "STALE_POSITIONING"
    direction = "NEUTRAL"
    if call_ratio > 0.5 or put_ratio > 0.5: status = "FRESH_MONEY_ENTERING"
    if call_ratio > (put_ratio * 1.5): direction = "BULLISH_ACTIVITY"
    elif put_ratio > (call_ratio * 1.5): direction = "BEARISH_ACTIVITY"
    return {"status": status, "call_ratio": call_ratio, "put_ratio": put_ratio, "direction": direction}

def analyze_oi_migration(current_df, previous_df):
    if current_df is None or previous_df is None or current_df.empty or previous_df.empty:
        return {"status": "UNAVAILABLE", "reason": "MISSING_DATA"}
    if 'Call OI' not in current_df.columns or 'Put OI' not in current_df.columns or 'Call OI' not in previous_df.columns or 'Put OI' not in previous_df.columns or 'strike_price' not in current_df.columns or 'strike_price' not in previous_df.columns:
        return {"status": "UNAVAILABLE", "reason": "MISSING_COLUMNS"}
    if current_df['Call OI'].dropna().empty or current_df['Put OI'].dropna().empty or previous_df['Call OI'].dropna().empty or previous_df['Put OI'].dropna().empty:
        return {"status": "INSUFFICIENT_DATA", "reason": "NULL_VALUES"}
        
    call_shift = current_df.loc[current_df['Call OI'].idxmax()]['strike_price'] - previous_df.loc[previous_df['Call OI'].idxmax()]['strike_price']
    put_shift = current_df.loc[current_df['Put OI'].idxmax()]['strike_price'] - previous_df.loc[previous_df['Put OI'].idxmax()]['strike_price']
    
    status = "STABLE"
    if call_shift > 0 and put_shift > 0: status = "BULLISH_MIGRATION"
    elif call_shift < 0 and put_shift < 0: status = "BEARISH_MIGRATION"
    elif call_shift > 0 and put_shift <= 0: status = "MILD_BULLISH"
    return {"status": status, "call_shift": call_shift, "put_shift": put_shift}

def analyze_aggressor_trades(df, current_price):
    closest = get_nearest_strikes(df, current_price, count=5)
    if closest.empty: return {"status": "UNAVAILABLE", "reason": "NO_DATA"}
    if 'taker_buy_volume' not in closest.columns or 'taker_sell_volume' not in closest.columns:
        return {"status": "UNAVAILABLE", "reason": "MISSING_COLUMNS"}
        
    total_taker_buy = closest['taker_buy_volume'].sum()
    total_taker_sell = closest['taker_sell_volume'].sum()
    
    if pd.isna(total_taker_buy) or pd.isna(total_taker_sell):
        return {"status": "INSUFFICIENT_DATA", "reason": "NULL_TAKER_VOLUME"}
    
    status = "NEUTRAL_FLOW"
    if total_taker_buy > (total_taker_sell * 1.3) and total_taker_buy > 10: status = "AGGRESSIVE_BUYING"
    elif total_taker_sell > (total_taker_buy * 1.3) and total_taker_sell > 10: status = "AGGRESSIVE_SELLING"
    return {"status": status, "buy_vol": total_taker_buy, "sell_vol": total_taker_sell}

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
layer2_data = fetch_layer2_supabase_data()
opt_forensics = fetch_options_forensics()

opt_df = opt_forensics["data"] if opt_forensics and "data" in opt_forensics else None
prev_opt_df = opt_forensics.get("previous_data") if opt_forensics else None

# Execute Analyzers
walls_res = analyze_neighboring_strikes(opt_df, current_price)
iv_analysis = analyze_iv_skew(opt_df, current_price)
vol_analysis = analyze_volume_vs_oi(opt_df, current_price)
migration_analysis = analyze_oi_migration(opt_df, prev_opt_df)
aggressor_analysis = analyze_aggressor_trades(opt_df, current_price)

# --- RENDER UI: CONSENSUS OVERVIEW ---
st.markdown("### 📊 Options Forensics Consensus")
st.caption("This is a multi-dimensional overview, not a predictive Buy/Sell signal. AMT determines structure; Options provide positioning context.")

c1, c2, c3 = st.columns(3)
c1.metric("AMT Structure", shadow_state["confirmed_state"] if shadow_state else "N/A")
c2.metric("Coverage", layer2_data["coverage"])
c3.metric("Current Price", f"${current_price:,.2f}")

cons1, cons2, cons3, cons4, cons5 = st.columns(5)
with cons1:
    if walls_res and walls_res.get("status") == "OK":
        if walls_res.get("walls"): st.warning("🧱 **Walls Detected**")
        else: st.info("⚖️ Unclassified")
    else:
        st.info("⚖️ Data Unavailable")
with cons2:
    if iv_analysis and iv_analysis.get('status') == "BULLISH_URGENCY": st.success("🌪️ **Bullish IV**")
    elif iv_analysis and iv_analysis.get('status') == "BEARISH_URGENCY": st.error("🌪️ **Bearish IV**")
    elif iv_analysis and iv_analysis.get('status') in ["UNAVAILABLE", "INSUFFICIENT_DATA"]: st.info("🌪️ Data Unavailable")
    else: st.info("🌪️ Neutral IV")
with cons3:
    if vol_analysis and vol_analysis.get('direction') == "BULLISH_ACTIVITY": st.success("🐋 **Bullish Vol**")
    elif vol_analysis and vol_analysis.get('direction') == "BEARISH_ACTIVITY": st.error("🐋 **Bearish Vol**")
    elif vol_analysis and vol_analysis.get('status') in ["UNAVAILABLE", "INSUFFICIENT_DATA"]: st.info("🐋 Data Unavailable")
    else: st.info("🐋 Stale/Neutral")
with cons4:
    if migration_analysis and migration_analysis.get('status') == "BULLISH_MIGRATION": st.success("🚚 **Bullish Shift**")
    elif migration_analysis and migration_analysis.get('status') == "BEARISH_MIGRATION": st.error("🚚 **Bearish Shift**")
    elif migration_analysis and migration_analysis.get('status') in ["UNAVAILABLE", "INSUFFICIENT_DATA"]: st.info("🚚 Data Unavailable")
    else: st.info("🚚 Stable OI")
with cons5:
    if aggressor_analysis and aggressor_analysis.get('status') == "AGGRESSIVE_BUYING": st.success("⚔️ **Taker Buys**")
    elif aggressor_analysis and aggressor_analysis.get('status') == "AGGRESSIVE_SELLING": st.error("⚔️ **Taker Sells**")
    elif aggressor_analysis and aggressor_analysis.get('status') in ["UNAVAILABLE", "INSUFFICIENT_DATA"]: st.info("⚔️ Data Unavailable")
    else: st.info("⚔️ Neutral Flow")

st.markdown("---")
st.subheader("🗺️ Layer 1: Auction Market Theory")
p1, p2, p3, p4 = st.columns(4)
p1.metric("Prior VAH", f"${vah:,.2f}" if not pd.isna(vah) else "N/A")
p2.metric("Prior POC", f"${poc:,.2f}" if not pd.isna(poc) else "N/A")
p3.metric("Prior VAL", f"${val:,.2f}" if not pd.isna(val) else "N/A")
p4.metric("ATR (21)", f"${completed_df.iloc[-1]['atr']:,.2f}")

if shadow_state:
    st.info(f"**Certified AMT State:** {shadow_state['confirmed_state']} (Last Updated: {shadow_state['candle_open_time']})")

st.markdown("---")
st.subheader("📈 Layer 2: Futures Positioning (Historical Snapshot)")

f1, f2, f3, f4 = st.columns(4)
f1.metric("Aggregate OI", f"${layer2_data['current_oi_usd']/1e9:.2f}B" if layer2_data["current_oi_usd"] else "N/A")

if layer2_data["status"] == "OK":
    f2.metric("30m OI Delta", f"{layer2_data['delta_pct']:+.2f}%", f"${layer2_data['delta_usd']:+,.0f}")
elif "REJECTED_BASELINE" in layer2_data["status"]:
    f2.metric("30m OI Delta", "REJECTED", "Gap not 25-35m", delta_color="inverse")
elif layer2_data["status"] == "COVERAGE_MISMATCH":
    f2.metric("30m OI Delta", "UNAVAILABLE", "Coverage changed", delta_color="off")
else:
    f2.metric("30m OI Delta", "PENDING", layer2_data["status"], delta_color="off")

f3.metric("Weighted Basis", f"{layer2_data['basis_pct']:+.4f}%" if layer2_data['basis_pct'] is not None else "N/A")
f4.metric("Weighted Funding", f"{layer2_data['funding_rate']:+.4f}%" if layer2_data['funding_rate'] is not None else "N/A")

st.markdown("---")
st.subheader("🔍 Layer 3: Options Forensics (Delta India)")

if opt_forensics and "data" in opt_forensics:
    st.caption(f"Latest Snapshot: {opt_forensics['timestamp']}")
    
    st.markdown("#### 🧱 Filter 1: Neighboring Strike Analysis (OI Walls)")
    if walls_res and walls_res.get("status") == "OK":
        walls = walls_res.get("walls", [])
        if walls:
            for wall in walls:
                st.warning(f"**{wall['type']} WALL DETECTED at ${wall['strike']:,.0f}** (OI: {wall['oi']:,.0f})")
        else:
            st.success("**Unclassified Concentration:** No distinct OI walls found near current price.")
    else:
        st.info("**UNAVAILABLE:** Not enough data to detect OI walls.")

    st.markdown("#### 🌪️ Filter 2: Implied Volatility Skew (Urgency)")
    if iv_analysis and iv_analysis.get('status') not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
        c1, c2, c3 = st.columns(3)
        c1.metric("Avg Call IV (Nearby)", f"{iv_analysis['avg_call_iv']*100:.2f}%")
        c2.metric("Avg Put IV (Nearby)", f"{iv_analysis['avg_put_iv']*100:.2f}%")
        delta_color = "normal" if iv_analysis['skew_pct'] > 0 else "inverse"
        c3.metric("IV Skew (Call - Put)", f"{iv_analysis['skew_pct']:+.2f}%", delta_color=delta_color)
        if iv_analysis['status'] == "BULLISH_URGENCY": st.error(f"**BULLISH_URGENCY:** Call IV is significantly higher than Put IV. Traders are paying a premium for upside.")
        elif iv_analysis['status'] == "BEARISH_URGENCY": st.warning(f"**BEARISH_URGENCY:** Put IV is significantly higher than Call IV. Traders are paying a premium for downside protection.")
        else: st.info(f"**NEUTRAL:** Volatility is balanced. Market is pricing a neutral outcome.")
    else: st.info("**UNAVAILABLE:** Cannot calculate IV skew due to missing data.")

    st.markdown("#### 🐋 Filter 3: Volume vs OI (Fresh Money Detector)")
    if vol_analysis and vol_analysis.get('status') not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
        v1, v2 = st.columns(2)
        v1.metric("Call Vol/OI Ratio", f"{vol_analysis['call_ratio']:.2f}x")
        v2.metric("Put Vol/OI Ratio", f"{vol_analysis['put_ratio']:.2f}x")
        if vol_analysis['status'] == "FRESH_MONEY_ENTERING":
            if vol_analysis['direction'] == "BULLISH_ACTIVITY": st.success(f"**FRESH MONEY: BULLISH** - Traders are actively opening new Call positions today.")
            elif vol_analysis['direction'] == "BEARISH_ACTIVITY": st.error(f"**FRESH MONEY: BEARISH** - Traders are actively opening new Put positions today.")
            else: st.info(f"**FRESH MONEY: NEUTRAL** - Traders are actively opening new positions today.")
        else: st.info(f"**STALE POSITIONING:** Volume is low relative to OI. Existing positions are being held, but no significant new money is entering.")
    else: st.info("**UNAVAILABLE:** Cannot calculate Volume vs OI due to missing data.")

    st.markdown("#### 🚚 Filter 4: OI Migration (Sentiment Shift)")
    if migration_analysis and migration_analysis.get('status') not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
        if migration_analysis['status'] == "BULLISH_MIGRATION": st.success(f"**BULLISH MIGRATION:** Both Call and Put walls moved HIGHER. Sentiment is shifting Bullish.")
        elif migration_analysis['status'] == "BEARISH_MIGRATION": st.error(f"**BEARISH MIGRATION:** Both Call and Put walls moved LOWER. Sentiment is shifting Bearish.")
        elif migration_analysis['status'] == "MILD_BULLISH": st.info(f"**MILD BULLISH MIGRATION:** Call wall moved higher while Put wall stayed/lowered.")
        else: st.info(f"**STABLE MIGRATION:** Peak OI strikes have not moved significantly.")
    elif migration_analysis and migration_analysis.get('status') == "INSUFFICIENT_DATA":
        st.warning("**PENDING:** Waiting for a second historical successful snapshot to calculate migration.")
    else: st.info("**UNAVAILABLE:** Missing required data to calculate migration.")

    st.markdown("#### ⚔️ Filter 5: Aggressor Trades (Directional Taker Flow)")
    if aggressor_analysis and aggressor_analysis.get('status') not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
        a1, a2 = st.columns(2)
        a1.metric("Taker Buy Vol", f"{aggressor_analysis['buy_vol']:,.0f}")
        a2.metric("Taker Sell Vol", f"{aggressor_analysis['sell_vol']:,.0f}")
        
        if aggressor_analysis['status'] == "AGGRESSIVE_BUYING": st.success(f"**AGGRESSIVE BUYING:** Buyers are lifting the ask. (Buys: {aggressor_analysis['buy_vol']:,.0f} vs Sells: {aggressor_analysis['sell_vol']:,.0f})")
        elif aggressor_analysis['status'] == "AGGRESSIVE_SELLING": st.error(f"**AGGRESSIVE SELLING:** Sellers are hitting the bid. (Sells: {aggressor_analysis['sell_vol']:,.0f} vs Buys: {aggressor_analysis['buy_vol']:,.0f})")
        else: st.info(f"**NEUTRAL FLOW:** Taker buys and sells are relatively balanced.")
    else: st.info("**UNAVAILABLE:** Missing required data to calculate aggressor flow.")

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

# --- TEXT EXPORT FOR AI SUPPORT ---
st.markdown("---")
with st.expander("📋 Copy/Paste Text Report for AI Support"):
    st.write("Click the copy icon in the top right of the box below to copy this for support.")
    
    report_lines = []
    report_lines.append(f"--- BTC TERMINAL REPORT ---")
    report_lines.append(f"Time (UTC): {datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
    report_lines.append(f"Price: ${current_price:,.2f}")
    report_lines.append(f"AMT State: {shadow_state['confirmed_state'] if shadow_state else 'N/A'}")
    report_lines.append(f"Coverage: {layer2_data['coverage']}")
    
    report_lines.append(f"\n--- LAYER 2 (FUTURES) ---")
    report_lines.append(f"OI: ${layer2_data['current_oi_usd']/1e9:.2f}B" if layer2_data['current_oi_usd'] else "OI: N/A")
    report_lines.append(f"30m Delta: {layer2_data['status']} {layer2_data.get('delta_pct', '')}")
    report_lines.append(f"Basis: {layer2_data['basis_pct']}")
    report_lines.append(f"Funding: {layer2_data['funding_rate']}")
    
    report_lines.append(f"\n--- LAYER 3 (OPTIONS) ---")
    report_lines.append(f"Walls: {walls_res}")
    report_lines.append(f"IV Skew: {iv_analysis}")
    report_lines.append(f"Vol vs OI: {vol_analysis}")
    report_lines.append(f"Migration: {migration_analysis}")
    report_lines.append(f"Aggressor Flow: {aggressor_analysis}")
    
    final_report = "\n".join(report_lines)
    st.code(final_report, language="text")
