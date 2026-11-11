import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import math
from datetime import timezone
from supabase import create_client

# Import our certified engines
from research_engine import ResearchStateEngine, IN_VALUE
from futures_positioning import FuturesPositioning, valid_metric_value

st.set_page_config(page_title="BTC Combined Research", layout="wide")

st.title("🔬 BTC Combined Research Terminal")
st.caption("AMT Engine (v1.0) + Futures Positioning (v1.1) + Options Forensics")

# ==========================================
# 1. AMT DATA FETCHING & CALCULATIONS
# ==========================================

@st.cache_data(ttl=60)
def fetch_binance_data():
    url = "https://data-api.binance.vision/api/v3/klines?symbol=BTCUSDT&interval=30m&limit=500"
    res = requests.get(url, timeout=10).json()
    
    df = pd.DataFrame(res, columns=['Date', 'open', 'high', 'low', 'close', 'volume', 'close_time', 'qav', 'num_trades', 'tbbav', 'tbqav', 'ignore'])
    df['Date'] = pd.to_datetime(df['Date'], unit='ms', utc=True).dt.tz_convert('Asia/Kolkata')
    
    for col in ['open', 'high', 'low', 'close']:
        df[col] = df[col].astype(float)
        
    df['prev_close'] = df['close'].shift(1)
    df['tr0'] = abs(df['high'] - df['low'])
    df['tr1'] = abs(df['high'] - df['prev_close'])
    df['tr2'] = abs(df['low'] - df['prev_close'])
    df['tr'] = df[['tr0', 'tr1', 'tr2']].max(axis=1)
    df['atr'] = df['tr'].ewm(alpha=1/21, adjust=False).mean()
    
    df['ShiftedDate'] = df['Date'] - pd.Timedelta(hours=5, minutes=30)
    df = df.dropna(subset=['atr'])
    return df

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

# ==========================================
# 2. SUPABASE DATA FETCHING
# ==========================================

@st.cache_data(ttl=60, show_spinner=False)
def fetch_supabase_aggregate_oi():
    empty_result = {"current_oi_usd": None, "previous_oi_usd": None, "delta_usd": None, "delta_pct": None, "status": "DATA_UNAVAILABLE"}
    try:
        supabase_url = st.secrets["SUPABASE_URL"]
        supabase_key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(supabase_url, supabase_key)

        response = client.table("futures_oi_snapshots").select("created_at,oi_usd").eq("exchange", "AGGREGATE").order("created_at", desc=True).limit(100).execute()
        rows = response.data or []

        if not rows: return {**empty_result, "status": "NO_DATABASE_HISTORY"}

        parsed_rows = []
        for row in rows:
            timestamp = pd.to_datetime(row.get("created_at"), utc=True, errors="coerce")
            raw_oi = row.get("oi_usd")
            try: oi_value = float(raw_oi) if raw_oi is not None else None
            except (TypeError, ValueError): oi_value = None
            if pd.isna(timestamp): continue
            parsed_rows.append({"time": timestamp, "oi": oi_value})

        if not parsed_rows: return {**empty_result, "status": "NO_VALID_TIMESTAMPS"}

        parsed_rows.sort(key=lambda item: item["time"])
        latest = parsed_rows[-1]

        if latest["oi"] is None or not np.isfinite(latest["oi"]) or latest["oi"] <= 0:
            return {**empty_result, "status": "LATEST_AGGREGATE_OI_UNAVAILABLE"}

        target_time = latest["time"] - pd.Timedelta(minutes=30)
        previous_candidates = [item for item in parsed_rows if item["time"] < latest["time"] and item["oi"] is not None and np.isfinite(item["oi"]) and item["oi"] > 0]

        if not previous_candidates:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "PENDING_BASELINE"}

        previous = min(previous_candidates, key=lambda item: abs((item["time"] - target_time).total_seconds()))
        offset_seconds = abs((previous["time"] - target_time).total_seconds())

        if offset_seconds > 600:
            return {**empty_result, "current_oi_usd": latest["oi"], "status": "NO_30M_MATCH_WITHIN_10M"}

        delta_usd = latest["oi"] - previous["oi"]
        delta_pct = (delta_usd / previous["oi"]) * 100.0

        return {"current_oi_usd": latest["oi"], "previous_oi_usd": previous["oi"], "delta_usd": delta_usd, "delta_pct": delta_pct, "status": "OK"}
    except Exception as exc:
        return {**empty_result, "status": f"ERROR: {exc}"}

@st.cache_data(ttl=60, show_spinner=False)
def fetch_options_forensics():
    empty_result = {"latest_time": None, "baseline_time": None, "status": "DATA_UNAVAILABLE", "df": pd.DataFrame()}
    
    try:
        supabase_url = st.secrets["SUPABASE_URL"]
        supabase_key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(supabase_url, supabase_key)

        # 1. Get all SUCCESS runs
        runs = client.table("options_collection_runs").select("snapshot_id, started_at").eq("status", "SUCCESS").order("started_at", desc=True).limit(10).execute().data
        if not runs: return {**empty_result, "status": "NO_SUCCESSFUL_RUNS"}

        latest_run = runs[0]
        latest_snapshot_id = latest_run["snapshot_id"]
        latest_time = pd.to_datetime(latest_run["started_at"], utc=True)

        # 2. Find the baseline run (strictly 25-35 mins old)
        baseline_run = None
        for run in runs[1:]:
            r_time = pd.to_datetime(run["started_at"], utc=True)
            age_mins = (latest_time - r_time).total_seconds() / 60.0
            if 25 <= age_mins <= 35:
                baseline_run = run
                break
        
        baseline_snapshot_id = baseline_run["snapshot_id"] if baseline_run else None
        baseline_time = pd.to_datetime(baseline_run["started_at"], utc=True) if baseline_run else None

        # 3. Fetch latest data
        latest_data = client.table("options_oi_snapshots").select("product_id, expiry_date, strike_price, option_type, oi_contracts, oi_value_usd, mark_iv, spot_price").eq("snapshot_id", latest_snapshot_id).execute().data
        if not latest_data: return {**empty_result, "status": "NO_CONTRACTS_IN_LATEST_RUN"}
        
        df_latest = pd.DataFrame(latest_data)

        # 4. Fetch baseline data if available
        df_baseline = pd.DataFrame()
        if baseline_snapshot_id:
            baseline_data = client.table("options_oi_snapshots").select("product_id, oi_contracts").eq("snapshot_id", baseline_snapshot_id).execute().data
            if baseline_data: df_baseline = pd.DataFrame(baseline_data)

        # 5. Merge and Calculate
        df_merged = df_latest.copy()
        
        if not df_baseline.empty:
            df_baseline = df_baseline.rename(columns={"oi_contracts": "prev_oi_contracts"})
            df_merged = pd.merge(df_latest, df_baseline, on="product_id", how="left")
            df_merged["oi_change"] = df_merged["oi_contracts"] - df_merged["prev_oi_contracts"]
            status = "OK"
        else:
            df_merged["oi_change"] = np.nan
            status = "PENDING_BASELINE"

        # Format for display
        df_merged["Mark IV (%)"] = df_merged["mark_iv"] * 100
        df_merged = df_merged.sort_values(["strike_price", "option_type"])

        return {
            "latest_time": latest_time.strftime("%H:%M UTC"),
            "baseline_time": baseline_time.strftime("%H:%M UTC") if baseline_time else None,
            "status": status,
            "df": df_merged
        }
    except Exception as exc:
        return {**empty_result, "status": f"ERROR: {exc}"}

# ==========================================
# 3. BUILD STATE ENGINES
# ==========================================

# --- AMT ENGINE ---
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
    st.error("Could not calculate TPO Profile. Insufficient data.")
    st.stop()

frozen_profile = {"profile_id": "P1", "vah": vah, "poc": poc, "val": val, "frozen": True}
config = {"version": "v1.0", "atr_period": 21, "atr_multiplier": 0.50, "minimum_buffer": 50, "discovery_closes": 2, "reentry_closes": 1, "balance_crossings": 2, "balance_window": 5}

amt_engine = ResearchStateEngine(configuration=config, initial_state=IN_VALUE)
today_df = completed_df[completed_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == today_str]

for _, row in today_df.iterrows():
    candle = {
        "exchange": "BINANCE", "symbol": "BTCUSDT", "timeframe": "30",
        "open_time_utc": row['Date'].isoformat(), "open": row['open'], "high": row['high'], "low": row['low'], "close": row['close'], "atr": row['atr'], "profile": frozen_profile, "completed": True
    }
    amt_engine.process_candle(candle, {})

live_state = "PROVISIONAL_OBSERVATION"
if not live_df.empty:
    live_row = live_df.iloc[-1]
    live_candle = {
        "exchange": "BINANCE", "symbol": "BTCUSDT", "timeframe": "30",
        "open_time_utc": live_row['Date'].isoformat(), "open": live_row['open'], "high": live_row['high'], "low": live_row['low'], "close": live_row['close'], "atr": live_row['atr'], "profile": frozen_profile, "completed": False
    }
    live_result = amt_engine.process_candle(live_candle, {})
    live_state = live_result.get("developing_state", "PROVISIONAL_OBSERVATION")

amt_output = amt_engine.output()
confirmed_state = amt_output["final_confirmed_state"]
last_completed = completed_df.iloc[-1]
buffer = max(50, last_completed['atr'] * 0.50)

# --- FUTURES ENGINE ---
@st.cache_resource
def get_futures_engine():
    return FuturesPositioning()

with st.spinner("Fetching Futures Positioning Data..."):
    try:
        fut_engine = get_futures_engine()
        fut_snapshot = fut_engine.collect()
    except Exception as e:
        fut_snapshot = None

# --- SUPABASE FETCHES ---
supabase_oi = fetch_supabase_aggregate_oi()
opt_forensics = fetch_options_forensics()

# ==========================================
# 4. RENDER UI
# ==========================================

col1, col2, col3, col4 = st.columns(4)
col1.metric("Current Price", f"${last_completed['close']:,.2f}")
col2.metric("AMT Confirmed", confirmed_state)
col3.metric("AMT Developing", live_state)
col4.metric("ATR (21)", f"${last_completed['atr']:,.2f}")

st.markdown("---")

# LAYER 1: AMT
st.subheader("🗺️ Layer 1: Auction Market Theory")
p1, p2, p3 = st.columns(3)
p1.metric("Prior VAH", f"${vah:,.2f}")
p2.metric("Prior POC", f"${poc:,.2f}")
p3.metric("Prior VAL", f"${val:,.2f}")

st.info(f"**Buffer:** ${buffer:,.2f} | **Disc Above:** ${vah + buffer:,.2f} | **Disc Below:** ${val - buffer:,.2f}")

st.markdown("---")

# LAYER 2: FUTURES POSITIONING
st.subheader("📈 Layer 2: Futures Positioning")

if fut_snapshot:
    agg = fut_snapshot["aggregate"]
    cov = fut_snapshot["coverage"]

    f1, f2, f3, f4 = st.columns(4)
    
    if supabase_oi["current_oi_usd"] is not None:
        f1.metric("Aggregate OI (Supabase)", f"${supabase_oi['current_oi_usd']/1e9:.2f}B", f"Coverage: {cov['label']}")
    else:
        f1.metric("Aggregate OI (Live)", f"${agg['oi_usd']/1e9:.2f}B" if agg["oi_usd"] else "N/A", f"Coverage: {cov['label']}")

    if supabase_oi["status"] == "OK":
        f2.metric("30m OI Delta", f"{supabase_oi['delta_pct']:+.2f}%", f"${supabase_oi['delta_usd']:+,.0f}")
    else:
        f2.metric("30m OI Delta", "PENDING", supabase_oi["status"], delta_color="off")

    basis_val = agg["basis_pct_oi_weighted"]
    f3.metric("Weighted Basis", f"{basis_val:+.4f}%" if basis_val else "N/A")

    funding_val = agg["funding_rate_oi_weighted"]
    f4.metric("Weighted Funding", f"{funding_val:+.4f}%" if funding_val else "N/A")

    st.caption(f"**Interpretation:** {fut_snapshot['interpretation']}")

    with st.expander("View Exchange Breakdown"):
        ex_data = []
        for name in ["BINANCE", "BYBIT", "OKX"]:
            ex = fut_snapshot["exchanges"].get(name, {})
            oi = valid_metric_value(ex, "oi_usd")
            funding = valid_metric_value(ex, "funding_rate")
            basis = valid_metric_value(ex, "basis_pct")
            
            ex_data.append({
                "Exchange": name,
                "OI (USD)": f"${oi/1e9:.2f}B" if oi else "N/A",
                "Funding": f"{funding*100:.4f}%" if funding else "N/A",
                "Basis": f"{basis:+.4f}%" if basis else "N/A",
                "Status": ex["oi_usd"]["status"]
            })
        
        st.dataframe(pd.DataFrame(ex_data), use_container_width=True, hide_index=True)
else:
    st.warning("Futures positioning data is currently unavailable.")

st.markdown("---")

# LAYER 3: OPTIONS FORENSICS
st.subheader("🔬 Layer 3: Options Forensics")

st.caption(f"Latest Snapshot: {opt_forensics['latest_time'] or 'N/A'} | Baseline Snapshot: {opt_forensics['baseline_time'] or 'Pending 30m window'}")

if opt_forensics["status"] == "OK":
    st.success("Valid 30-minute baseline found. Showing OI migration.")
elif opt_forensics["status"] == "PENDING_BASELINE":
    st.warning("PENDING / NO VALID 30M BASELINE. Showing latest snapshot only. OI change will populate once a 25-35 minute baseline is available.")
else:
    st.error(f"Options Forensics Error: {opt_forensics['status']}")

if not opt_forensics["df"].empty:
    df_display = opt_forensics["df"]
    
    # Split into Calls and Puts for side-by-side display
    calls = df_display[df_display["option_type"] == "CALL"][["strike_price", "oi_contracts", "oi_value_usd", "Mark IV (%)", "oi_change"]].rename(
        columns={"oi_contracts": "Call OI", "oi_value_usd": "Call OI USD", "oi_change": "Call OI Change"}
    )
    puts = df_display[df_display["option_type"] == "PUT"][["strike_price", "oi_contracts", "oi_value_usd", "Mark IV (%)", "oi_change"]].rename(
        columns={"oi_contracts": "Put OI", "oi_value_usd": "Put OI USD", "Mark IV (%)": "Put IV (%)", "oi_change": "Put OI Change"}
    )

    # Merge on strike
    merged_display = pd.merge(calls, puts, on="strike_price", how="outer").sort_values("strike_price")
    
    # Format columns
    st.dataframe(merged_display, use_container_width=True, hide_index=True, column_config={
        "strike_price": st.column_config.NumberColumn("Strike", format="$%.0f"),
        "Call OI": st.column_config.NumberColumn("Call OI", format="%.0f"),
        "Call OI USD": st.column_config.NumberColumn("Call OI USD", format="$%.0f"),
        "Mark IV (%)": st.column_config.NumberColumn("Call IV", format="%.2f%%"),
        "Call OI Change": st.column_config.NumberColumn("Call Δ OI", format="%+.0f"),
        "Put OI": st.column_config.NumberColumn("Put OI", format="%.0f"),
        "Put OI USD": st.column_config.NumberColumn("Put OI USD", format="$%.0f"),
        "Put IV (%)": st.column_config.NumberColumn("Put IV", format="%.2f%%"),
        "Put OI Change": st.column_config.NumberColumn("Put Δ OI", format="%+.0f"),
    })
else:
    st.info("No option chain data available for the latest snapshot.")

st.markdown("""
<script>
    setTimeout(function(){
        window.parent.location.reload();
    }, 60000);
</script>
""", unsafe_allow_html=True)
