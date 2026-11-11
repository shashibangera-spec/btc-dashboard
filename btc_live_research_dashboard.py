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
            
            # Extract contributors from status JSON
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

        target_time = latest["time"] - pd.Timedelta(minutes=30)
        previous_candidates = [item for item in parsed_rows if item["time"] < latest["time"] and item["oi"] is not None]

        if not previous_candidates:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "PENDING_BASELINE"}

        previous = min(previous_candidates, key=lambda item: abs((item["time"] - target_time).total_seconds()))
        offset_seconds = abs((previous["time"] - target_time).total_seconds())

        if offset_seconds > 600:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "NO_30M_MATCH_WITHIN_10M"}

        # Only compare if contributors are identical
        if latest["contributors"] != previous["contributors"]:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "COVERAGE_MISMATCH"}

        delta_usd = latest["oi"] - previous["oi"]
        delta_pct = (delta_usd / previous["oi"]) * 100.0

        return {"current_oi_usd": latest["oi"], "previous_oi_usd": previous["oi"], "delta_usd": delta_usd, "delta_pct": delta_pct, "status": "OK"}
    except Exception as exc:
        return {**empty_result, "status": f"ERROR: {exc}"}

@st.cache_data(ttl=60, show_spinner=False)
def fetch_options_forensics():
    # Queries the latest options snapshot from Delta Options Collector
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        
        # Get the latest snapshot_id
        res = client.table("options_oi_snapshots").select("snapshot_id, collected_at").order("collected_at", desc=True).limit(1).execute()
        if not res.data: return None
        
        latest_snapshot = res.data[0]
        snapshot_id = latest_snapshot["snapshot_id"]
        
        # Get all strikes for this snapshot
        chain = client.table("options_oi_snapshots").select("strike_price, option_type, oi_contracts, mark_iv").eq("snapshot_id", snapshot_id).execute()
        if not chain.data: return None
        
        df = pd.DataFrame(chain.data)
        if df.empty: return None
        
        # Pivot to get Call/Put side by side
        pivot_oi = df.pivot_table(index='strike_price', columns='option_type', values='oi_contracts', aggfunc='sum').reset_index()
        pivot_iv = df.pivot_table(index='strike_price', columns='option_type', values='mark_iv', aggfunc='mean').reset_index()
        
        # Rename columns for clarity
        pivot_oi.rename(columns={'CALL': 'Call OI', 'PUT': 'Put OI'}, inplace=True)
        pivot_iv.rename(columns={'CALL': 'Call IV', 'PUT': 'Put IV'}, inplace=True)
        
        # Merge
        merged = pd.merge(pivot_oi, pivot_iv, on='strike_price', how='outer').sort_values('strike_price')
        
        return {"timestamp": latest_snapshot["collected_at"], "data": merged}
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=60)
def get_cached_binance_data():
    return fetch_binance_data()

# --- DATA FETCHING ---
with st.spinner("Fetching Binance data..."):
    df = get_cached_binance_data()

now_utc = datetime.datetime.now(timezone.utc)
df['is_live'] = df['close_time'] > int(now_utc.timestamp() * 1000)
completed_df = df[~df['is_live']].copy()
live_df = df[df['is_live']].copy()

today_str = completed_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
yesterday_df = completed_df[completed_df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str]

if not yesterday_df.empty:
    last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
    vah, val, poc = calculate_tpo(prev_day_df)
else:
    vah, val, poc = np.nan, np.nan, np.nan

if pd.isna(vah):
    st.error("Could not calculate TPO Profile.")
    st.stop()

# --- AMT ENGINE (Shadow Mode via Supabase) ---
@st.cache_data(ttl=60, show_spinner=False)
def fetch_amt_shadow_state():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        # Query the correct columns: candle_open_time and confirmed_state
        res = client.table("amt_snapshots").select("candle_open_time, confirmed_state").order("candle_open_time", desc=True).limit(1).execute()
        if res.data:
            return res.data[0]
        return None
    except Exception as e: 
        print(f"Error fetching AMT shadow state: {e}")
        return None

shadow_state = fetch_amt_shadow_state()

# --- FUTURES ENGINE ---
@st.cache_resource
def get_futures_engine():
    return FuturesPositioning()

with st.spinner("Fetching Futures Positioning Data..."):
    try:
        fut_snapshot = get_futures_engine().collect()
    except Exception as e:
        fut_snapshot = None

supabase_oi = fetch_supabase_aggregate_oi()
opt_forensics = fetch_options_forensics()

# --- RENDER UI ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Price", f"${completed_df.iloc[-1]['close']:,.2f}")
col2.metric("AMT Live", "See Below")
# Use the correct dictionary key from our query
col3.metric("AMT Certified", shadow_state["confirmed_state"] if shadow_state else "N/A")
col4.metric("ATR (21)", f"${completed_df.iloc[-1]['atr']:,.2f}")

st.markdown("---")
st.subheader("🗺️ Layer 1: Auction Market Theory")
p1, p2, p3 = st.columns(3)
p1.metric("Prior VAH", f"${vah:,.2f}")
p2.metric("Prior POC", f"${poc:,.2f}")
p3.metric("Prior VAL", f"${val:,.2f}")

if shadow_state:
    # Use the correct dictionary keys for rendering
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
    elif supabase_oi["status"] == "COVERAGE_MISMATCH":
        f2.metric("30m OI Delta", "UNAVAILABLE", "Coverage changed", delta_color="off")
    else:
        f2.metric("30m OI Delta", "PENDING", supabase_oi["status"], delta_color="off")

    f3.metric("Weighted Basis", f"{agg['basis_pct_oi_weighted']:+.4f}%" if agg['basis_pct_oi_weighted'] else "N/A")
    f4.metric("Weighted Funding", f"{agg['funding_rate_oi_weighted']:+.4f}%" if agg['funding_rate_oi_weighted'] else "N/A")
else:
    st.warning("Futures data currently unavailable.")

st.markdown("---")
st.subheader("🔍 Layer 3: Options Forensics (Delta India)")

if opt_forensics and "data" in opt_forensics:
    st.caption(f"Latest Snapshot: {opt_forensics['timestamp']}")
    st.dataframe(
        opt_forensics["data"], 
        use_container_width=True, 
        hide_index=True,
        column_config={
            "strike_price": st.column_config.NumberColumn("Strike", format="$%d"),
            "Call OI": st.column_config.NumberColumn("Call OI"),
            "Put OI": st.column_config.NumberColumn("Put OI"),
            "Call IV": st.column_config.NumberColumn("Call IV", format="%.4f"),
            "Put IV": st.column_config.NumberColumn("Put IV", format="%.4f"),
        }
    )
elif opt_forensics and "error" in opt_forensics:
    st.error(f"Error loading options data: {opt_forensics['error']}")
else:
    st.info("PENDING: Waiting for options_collector.py to populate Supabase.")
