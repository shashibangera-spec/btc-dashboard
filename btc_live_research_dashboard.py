import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
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
            
            # MATCHED CONTRIBUTOR FIX: Extract contributors from status JSON
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

        # MATCHED CONTRIBUTOR FIX: Only compare if contributors are identical
        if latest["contributors"] != previous["contributors"]:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "COVERAGE_MISMATCH"}

        delta_usd = latest["oi"] - previous["oi"]
        delta_pct = (delta_usd / previous["oi"]) * 100.0

        return {"current_oi_usd": latest["oi"], "previous_oi_usd": previous["oi"], "delta_usd": delta_usd, "delta_pct": delta_pct, "status": "OK"}
    except Exception as exc:
        return {**empty_result, "status": f"ERROR: {exc}"}

@st.cache_data(ttl=60, show_spinner=False)
def fetch_options_forensics():
    # (Keep the options fetcher from the previous code here - it is correct)
    # ... For brevity, assume the options fetcher is identical to before ...
    pass # (Replace this with your existing options fetch logic)

# --- DATA FETCHING ---
df = fetch_binance_data()
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
# We read the latest state directly from Supabase instead of calculating it live.
@st.cache_data(ttl=60, show_spinner=False)
def fetch_amt_shadow_state():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        res = client.table("amt_snapshots").select("open_time_utc, result").order("open_time_utc", desc=True).limit(1).execute()
        if res.data:
            return res.data[0]
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
    except Exception as e:
        fut_snapshot = None

supabase_oi = fetch_supabase_aggregate_oi()
# opt_forensics = fetch_options_forensics() # Uncomment when defined

# --- RENDER UI ---
col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Price", f"${completed_df.iloc[-1]['close']:,.2f}")
col2.metric("AMT Live", "See Below")
col3.metric("AMT Certified", shadow_state["result"].get("confirmed_state") if shadow_state else "N/A")
col4.metric("ATR (21)", f"${completed_df.iloc[-1]['atr']:,.2f}")

st.markdown("---")
st.subheader("🗺️ Layer 1: Auction Market Theory")
p1, p2, p3 = st.columns(3)
p1.metric("Prior VAH", f"${vah:,.2f}")
p2.metric("Prior POC", f"${poc:,.2f}")
p3.metric("Prior VAL", f"${val:,.2f}")

if shadow_state:
    st.info(f"**Certified AMT State (from Supabase):** {shadow_state['result'].get('confirmed_state')} (Last Updated: {shadow_state['open_time_utc']})")
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