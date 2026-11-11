import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import math
from datetime import timezone

# Import our certified engines
from research_engine import ResearchStateEngine, IN_VALUE
from futures_positioning import FuturesPositioning, valid_metric_value

st.set_page_config(page_title="BTC Combined Research", layout="wide")

st.title("🔬 BTC Combined Research Terminal")
st.caption("AMT Engine (v1.0) + Futures Positioning (v1.1)")

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
# 2. BUILD STATE ENGINES
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

# Wrap the network call in a spinner so the UI doesn't just sit blank
with st.spinner("Fetching Futures Positioning Data (this can take 10-15s if fallbacks are used)..."):
    try:
        fut_engine = get_futures_engine()
        fut_snapshot = fut_engine.collect()
    except Exception as e:
        st.error(f"Failed to fetch Futures data: {e}")
        fut_snapshot = None

# ==========================================
# 3. RENDER UI
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
    oi_val = agg["oi_usd"]
    f1.metric("Aggregate OI", f"${oi_val/1e9:.2f}B" if oi_val else "N/A", f"Coverage: {cov['label']}")

    basis_val = agg["basis_pct_oi_weighted"]
    f2.metric("Weighted Basis", f"{basis_val:+.4f}%" if basis_val else "N/A")

    funding_val = agg["funding_rate_oi_weighted"]
    f3.metric("Weighted Funding", f"{funding_val:+.4f}%" if funding_val else "N/A")

    delta = fut_snapshot["oi_30m_delta"]
    if delta["status"] == "OK":
        f4.metric("30m OI Delta", f"{delta['change_pct']:+.2f}%", f"{delta['change_usd']/1e6:+.1f}M USD")
    elif delta["status"] == "PENDING_BASELINE":
        f4.metric("30m OI Delta", "PENDING", "Building history...")
    else:
        f4.metric("30m OI Delta", "N/A", delta.get("message", ""))

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

st.markdown("""
<script>
    setTimeout(function(){
        window.parent.location.reload();
    }, 60000);
</script>
""", unsafe_allow_html=True)