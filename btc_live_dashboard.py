import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import requests
import datetime
import math
import streamlit.components.v1 as components
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone, timedelta

st.set_page_config(page_title="BTC Data Terminal", page_icon="₿", layout="wide")

# ==========================================================================
# TERMINAL SKIN — UI ONLY (no data / calculation logic lives in this block)
# NOTE: for native widgets (st.dataframe grids) to also render dark, add a
# file  .streamlit/config.toml  next to this script containing:
#     [theme]
#     base = "dark"
#     backgroundColor = "#0E1117"
#     secondaryBackgroundColor = "#1A1E24"
# ==========================================================================
st.markdown("""
<style>
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&family=JetBrains+Mono:wght@400;500;600;700&display=swap');

:root {
  --bg: #0E1117;  --panel: #1A1E24;  --panel-2: #14181E;  --border: #2A313A;
  --text: #E6EDF3;  --muted: #8B96A5;
  --green: #00E676;  --red: #FF5252;  --amber: #FFC107;  --blue: #4FC3F7;
  --mono: 'JetBrains Mono', 'SF Mono', Menlo, Consolas, monospace;
  --sans: 'Inter', -apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif;
}

/* ---------- Canvas ---------- */
html, body, .stApp { background: var(--bg) !important; color: var(--text); font-family: var(--sans); color-scheme: dark; }
[data-testid="stHeader"] { background: transparent !important; }
#MainMenu, footer { visibility: hidden; }
.block-container { padding-top: 1.0rem !important; padding-bottom: 2rem !important; max-width: 1500px; }
[data-testid="stVerticalBlock"] { gap: 0.65rem !important; }
[data-testid="stHorizontalBlock"] { gap: 0.65rem !important; }

/* ---------- Sidebar ---------- */
[data-testid="stSidebar"] { background: #0A0D12 !important; border-right: 1px solid var(--border); }
[data-testid="stSidebar"] h2, [data-testid="stSidebar"] h3 {
  font-size: 0.78rem !important; letter-spacing: 0.12em; text-transform: uppercase; color: var(--muted) !important; font-weight: 700;
}
[data-testid="stSidebar"] label p { color: var(--muted) !important; font-size: 0.75rem !important; }
div[data-baseweb="input"], div[data-baseweb="base-input"], div[data-baseweb="textarea"], textarea {
  background: #12161B !important; color: var(--text) !important; border-color: var(--border) !important;
}
.stButton > button {
  background: var(--panel); color: var(--text); border: 1px solid var(--border); border-radius: 8px;
  font-weight: 600; font-size: 0.8rem; transition: all .15s ease;
}
.stButton > button:hover { border-color: var(--green); color: var(--green); background: #12261C; }

/* ---------- Native st.metric cards ---------- */
div[data-testid="stMetric"] {
  background: var(--panel); border: 1px solid var(--border); border-radius: 10px;
  padding: 10px 14px; box-shadow: inset 0 1px 0 rgba(255,255,255,.03);
}
div[data-testid="stMetricLabel"], div[data-testid="stMetricLabel"] p {
  color: var(--muted) !important; font-size: 0.66rem !important; font-weight: 600 !important;
  letter-spacing: 0.09em; text-transform: uppercase;
}
div[data-testid="stMetricValue"], div[data-testid="stMetricValue"] div {
  font-family: var(--mono) !important; font-weight: 700 !important; font-size: 1.3rem !important;
  color: var(--text) !important; font-variant-numeric: tabular-nums; letter-spacing: -0.02em;
}
div[data-testid="stMetricDelta"] { font-family: var(--mono) !important; font-size: 0.74rem !important; }

/* ---------- Alerts / Expanders / Captions ---------- */
div[data-testid="stAlert"] { background: #16202B !important; border: 1px solid #25405A !important; border-radius: 10px; color: var(--text) !important; }
/* ---------- Expanders → terminal "drawers" ---------- */
div[data-testid="stExpander"] {
  background: linear-gradient(145deg, #1A1F26 0%, #13171D 100%);
  border: 1px solid var(--border) !important; border-left: 3px solid #3A424D !important;
  border-radius: 12px; overflow: hidden;
  box-shadow: inset 0 1px 0 rgba(255,255,255,.03);
  transition: border-color .18s ease, box-shadow .18s ease, background .18s ease;
}
div[data-testid="stExpander"]:hover {
  border-left-color: var(--amber) !important; border-color: #3A424D !important;
  box-shadow: 0 0 22px -12px rgba(255,193,7,.55), inset 0 1px 0 rgba(255,255,255,.04);
}
div[data-testid="stExpander"]:has(details[open]) {
  border-left-color: var(--green) !important; border-color: #2F3A44 !important;
  box-shadow: 0 0 26px -14px rgba(0,230,118,.55), inset 0 1px 0 rgba(255,255,255,.04);
}
div[data-testid="stExpander"] details { border: 0 !important; background: transparent !important; }
div[data-testid="stExpander"] summary {
  display: flex !important; align-items: center; justify-content: space-between;
  padding: 0.7rem 1rem !important; cursor: pointer; list-style: none;
}
div[data-testid="stExpander"] summary::-webkit-details-marker { display: none; }
div[data-testid="stExpander"] summary:hover { background: rgba(255,255,255,.025); }
div[data-testid="stExpander"] summary p {
  font-family: var(--sans); font-size: 0.74rem !important; font-weight: 700 !important;
  letter-spacing: 0.12em; text-transform: uppercase; color: #C9D1D9 !important; margin: 0;
}
div[data-testid="stExpander"] summary:hover p { color: #FFFFFF !important; }
div[data-testid="stExpander"]:has(details[open]) summary p { color: var(--green) !important; }
div[data-testid="stExpander"] summary [data-testid="stIconMaterial"] { color: var(--muted) !important; font-size: 1.25rem !important; transition: transform .18s ease, color .18s ease; }
div[data-testid="stExpander"] summary:hover [data-testid="stIconMaterial"] { color: var(--amber) !important; }
div[data-testid="stExpander"]:has(details[open]) summary [data-testid="stIconMaterial"] { color: var(--green) !important; }
/* right-aligned state tag */
div[data-testid="stExpander"] summary::after {
  content: "EXPAND"; font-family: var(--mono); font-size: 0.6rem; font-weight: 600; letter-spacing: 0.14em;
  color: #6B7683; border: 1px solid var(--border); border-radius: 999px; padding: 2px 10px; background: rgba(10,13,18,.6);
}
div[data-testid="stExpander"]:has(details[open]) summary::after { content: "COLLAPSE"; color: var(--green); border-color: rgba(0,230,118,.35); }
/* drawer body */
div[data-testid="stExpanderDetails"] {
  background: #0F1318; border-top: 1px solid var(--border);
  padding: 1rem 1.1rem 1.1rem 1.1rem !important;
}
div[data-testid="stExpanderDetails"] h4 {
  font-size: 0.72rem !important; font-weight: 800 !important; letter-spacing: 0.16em; text-transform: uppercase;
  color: var(--text) !important; padding: 0 0 0 10px !important; border-left: 3px solid var(--amber); line-height: 1.2; margin-bottom: 0.2rem;
}
div[data-testid="stExpanderDetails"] strong { color: var(--text); letter-spacing: 0.04em; }
div[data-testid="stExpanderDetails"] [data-testid="stMetric"] { background: var(--panel); }
/* tables inside drawers: frame only — canvas grid itself is untouched */
div[data-testid="stExpanderDetails"] [data-testid="stDataFrame"] {
  border: 1px solid var(--border); border-radius: 8px; overflow: hidden;
}
div[data-testid="stExpanderDetails"] [data-testid="stCode"], div[data-testid="stExpanderDetails"] pre {
  background: #0A0D12 !important; border: 1px solid var(--border); border-radius: 8px;
  font-family: var(--mono) !important; font-size: 0.78rem !important; color: #C9D1D9 !important;
}
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] p { color: var(--muted) !important; font-size: 0.72rem !important; }
hr { border-color: var(--border) !important; margin: 0.6rem 0 !important; }

/* ---------- Top bar ---------- */
.topbar { display: flex; justify-content: space-between; align-items: center; padding: 2px 2px 10px 2px; border-bottom: 1px solid var(--border); }
.topbar .brand { font-family: var(--sans); font-weight: 800; font-size: 1.35rem; letter-spacing: 0.06em; color: #FFFFFF; }
.topbar .brand em { font-style: normal; color: var(--amber); }
.topbar .tag { color: var(--muted); font-size: 0.7rem; letter-spacing: 0.08em; text-transform: uppercase; margin-top: 2px; }
.topbar .live { font-family: var(--mono); font-size: 0.72rem; color: var(--muted); white-space: nowrap; display: flex; align-items: center; gap: 8px; }
.topbar .dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); box-shadow: 0 0 10px var(--green); animation: pulse 2s infinite; }
@keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: .35; } }

/* ---------- Tone system ---------- */
.t-up   { --accent: #00E676; --tint: rgba(0,230,118,.11);  --glow: rgba(0,230,118,.38); }
.t-down { --accent: #FF5252; --tint: rgba(255,82,82,.11);  --glow: rgba(255,82,82,.38); }
.t-warn { --accent: #FFC107; --tint: rgba(255,193,7,.09);  --glow: rgba(255,193,7,.32); }
.t-flat { --accent: #8B96A5; --tint: rgba(139,150,165,.07); --glow: rgba(139,150,165,.15); }
.t-info { --accent: #4FC3F7; --tint: rgba(79,195,247,.08); --glow: rgba(79,195,247,.28); }
.t-poc  { --accent: #B388FF; --tint: rgba(179,136,255,.09); --glow: rgba(179,136,255,.30); }

/* ---------- Hero cards (top header) ---------- */
.hero {
  background: linear-gradient(145deg, #1D222A 0%, #161A20 100%);
  border: 1px solid var(--border); border-left: 3px solid var(--accent, var(--border)); border-radius: 12px;
  padding: 12px 16px; min-height: 104px; display: flex; flex-direction: column; justify-content: center;
}
.hero .k { color: var(--muted); font-size: 0.66rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; }
.hero .v { margin-top: 4px; line-height: 1.15; }
.hero .v.price { font-family: var(--mono); font-weight: 700; font-size: 2.15rem; color: #FFFFFF; letter-spacing: -0.03em; font-variant-numeric: tabular-nums; }
.hero .v.state { font-family: var(--sans); font-weight: 800; font-size: 1.5rem; text-transform: uppercase; letter-spacing: 0.02em; color: var(--accent); text-shadow: 0 0 22px var(--glow); }
.hero .s { margin-top: 5px; font-family: var(--mono); font-size: 0.72rem; color: var(--muted); }

/* ---------- Auction story banner ---------- */
.story {
  background: linear-gradient(90deg, var(--tint) 0%, rgba(26,30,36,.92) 65%);
  border: 1px solid var(--border); border-left: 5px solid var(--accent); border-radius: 12px;
  padding: 14px 20px; box-shadow: 0 0 28px -10px var(--glow);
}
.story .k { color: var(--accent); font-size: 0.66rem; font-weight: 800; letter-spacing: 0.18em; text-transform: uppercase; }
.story .t { margin-top: 4px; font-family: var(--sans); font-size: 1.45rem; font-weight: 800; letter-spacing: 0.01em; line-height: 1.25; }
.story .t .prev { color: var(--muted); font-weight: 600; }
.story .t .arr  { color: var(--accent); margin: 0 10px; }
.story .t .cur  { color: #FFFFFF; }
.chips { display: flex; flex-wrap: wrap; gap: 8px; margin-top: 10px; }
.chip { display: inline-flex; align-items: center; gap: 8px; background: rgba(10,13,18,.65); border: 1px solid var(--border); border-radius: 999px; padding: 4px 12px; }
.chip .ck { color: var(--muted); font-size: 0.62rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; }
.chip .cv { font-family: var(--mono); font-size: 0.78rem; font-weight: 600; color: var(--text); }
.chip .cv.up { color: #00E676; }  .chip .cv.down { color: #FF5252; }

/* ---------- Section headers ---------- */
.sec { display: flex; align-items: center; gap: 10px; margin: 0.9rem 0 0.3rem 0; }
.sec .bar { width: 3px; height: 14px; background: var(--amber); border-radius: 2px; }
.sec .st { font-size: 0.74rem; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; color: var(--text); }
.sec .hint { margin-left: auto; font-size: 0.66rem; color: var(--muted); text-align: right; }

/* ---------- Generic stat cards + grids ---------- */
.grid { display: grid; gap: 10px; }
.g2 { grid-template-columns: repeat(2, 1fr); }
.g4 { grid-template-columns: repeat(4, 1fr); }
.g5 { grid-template-columns: 1.55fr repeat(4, 1fr); }
@media (max-width: 900px) { .g4, .g5 { grid-template-columns: repeat(2, 1fr); } }
.card {
  background: var(--panel); border: 1px solid var(--border); border-top: 2px solid var(--accent, var(--border));
  border-radius: 10px; padding: 10px 14px; box-shadow: inset 0 1px 0 rgba(255,255,255,.03);
}
.card .k { color: var(--muted); font-size: 0.64rem; font-weight: 600; letter-spacing: 0.09em; text-transform: uppercase; }
.card .v { margin-top: 3px; font-family: var(--mono); font-weight: 700; font-size: 1.3rem; color: var(--text); letter-spacing: -0.02em; font-variant-numeric: tabular-nums; }
.card .v.sm { font-family: var(--sans); font-size: 1.02rem; font-weight: 800; letter-spacing: 0.02em; text-transform: uppercase; }
.card.tint .v { color: var(--accent); }
.card .s { margin-top: 3px; font-family: var(--mono); font-size: 0.7rem; color: var(--accent, var(--muted)); font-weight: 600; }
.mini { color: var(--muted); font-size: 0.64rem; font-weight: 700; letter-spacing: 0.14em; text-transform: uppercase; margin: 2px 0 6px 2px; }

/* ---------- Value-area ladder ---------- */
.panel { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 12px 18px 14px 18px; }
.lad { position: relative; height: 62px; margin: 4px 10px 6px 10px; }
.lad .track { position: absolute; left: 0; right: 0; top: 38px; height: 8px; border-radius: 4px; background: #232932; }
.lad .band  { position: absolute; top: 38px; height: 8px; border-radius: 4px;
  background: linear-gradient(90deg, rgba(255,82,82,.70), rgba(79,195,247,.32) 50%, rgba(0,230,118,.70)); }
.lad .edge  { position: absolute; top: 32px; width: 2px; height: 20px; border-radius: 1px; }
.lad .edge.val { background: #FF5252; } .lad .edge.vah { background: #00E676; }
.lad .edge.poc { background: #FFC107; width: 3px; box-shadow: 0 0 8px rgba(255,193,7,.6); }
.lad .pin { position: absolute; top: 0; transform: translateX(-50%); display: flex; flex-direction: column; align-items: center; }
.lad .pin .pl { background: var(--accent); color: #0E1117; font-family: var(--mono); font-weight: 700; font-size: 0.72rem; padding: 2px 9px; border-radius: 5px; white-space: nowrap; box-shadow: 0 0 14px var(--glow); }
.lad .pin i { width: 2px; height: 28px; background: var(--accent); box-shadow: 0 0 8px var(--glow); }
.nums { display: grid; grid-template-columns: repeat(4, 1fr); gap: 10px; margin-top: 6px; }
.nums .n { border-left: 2px solid var(--accent, var(--border)); padding-left: 10px; }
.nums .n .k { color: var(--muted); font-size: 0.62rem; font-weight: 700; letter-spacing: 0.12em; text-transform: uppercase; }
.nums .n .v { font-family: var(--mono); font-weight: 700; font-size: 1.2rem; color: var(--text); font-variant-numeric: tabular-nums; letter-spacing: -0.02em; }

.foot { color: #6B7683; font-size: 0.68rem; line-height: 1.45; margin-top: 2px; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# CONFIGURATION & STATE
# ==========================================
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
    return {
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
            now_ms = int(datetime.datetime.now(timezone.utc).timestamp() * 1000)
            df['IsClosed'] = df['CloseTimeMs'] < now_ms
            
            for col in ['open', 'high', 'low', 'close']:
                df[col] = df[col].astype(float)
            
            df['ShiftedDate'] = df['Date'] - pd.Timedelta(hours=5, minutes=30)
            df = df[['Date', 'CloseTimeUTC', 'CloseTimeMs', 'IsClosed', 'open', 'high', 'low', 'close', 'ShiftedDate']]
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
    
    # ACTIVE PROGRESS TRACKING VARIABLES
    lowestSinceDiscDown = np.nan
    highestSinceDiscUp = np.nan
    barsWithoutNewLow = 0
    barsWithoutNewHigh = 0

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

        if auctionState in (3, 16): # Discovery Below or Stalled Below
            if pd.isna(lowestSinceDiscDown) or bar['low'] < lowestSinceDiscDown:
                lowestSinceDiscDown = bar['low']
                barsWithoutNewLow = 0
            else:
                barsWithoutNewLow += 1
        else:
            lowestSinceDiscDown = np.nan
            barsWithoutNewLow = 0

        if auctionState in (2, 15): # Discovery Above or Stalled Above
            if pd.isna(highestSinceDiscUp) or bar['high'] > highestSinceDiscUp:
                highestSinceDiscUp = bar['high']
                barsWithoutNewHigh = 0
            else:
                barsWithoutNewHigh += 1
        else:
            highestSinceDiscUp = np.nan
            barsWithoutNewHigh = 0

        timed_out_down = barsWithoutNewLow >= effectiveTimeout
        timed_out_up = barsWithoutNewHigh >= effectiveTimeout

        if auctionState == 0:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif cycleBars >= 3: auctionState = 1
        elif auctionState == 1:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
        elif auctionState == 2:
            if close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif close <= prevVAH: auctionState = 17
            elif timed_out_up: auctionState = 15
        elif auctionState == 3:
            if acceptanceConfirmedDown: auctionState = 5
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close >= prevVAL: auctionState = 18
            elif timed_out_down: auctionState = 16
        elif auctionState == 5:
            if valueMigrationDown: auctionState = 7
            elif close >= prevVAL: auctionState = 13
        elif auctionState == 7:
            if newValueDevelopingDown: auctionState = 9
            elif close >= prevVAL: auctionState = 13
        elif auctionState == 15: # STALLED ABOVE
            if close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif close >= prevVAL and close <= prevVAH: auctionState = 17
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
        elif auctionState == 16: # STALLED BELOW
            if close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close >= prevVAL and close <= prevVAH: auctionState = 11
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
        elif auctionState == 17:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif insideValueBars >= 2: auctionState = 10
        elif auctionState == 18:
            if close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif insideValueBars >= 2: auctionState = 11
        elif auctionState == 10:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif insideValueBars >= 2: auctionState = 14
        elif auctionState == 11:
            if close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            elif close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif insideValueBars >= 2: auctionState = 14
        elif auctionState == 14:
            if close > prevVAH: auctionState = 2; discoveryStartBar = i; highestSinceDiscUp = bar['high']; barsWithoutNewHigh = 0
            elif close < prevVAL: auctionState = 3; discoveryStartBar = i; lowestSinceDiscDown = bar['low']; barsWithoutNewLow = 0
            else:
                reentryInsideBars += 1
                if reentryInsideBars >= 3 and cycleBars >= 3: auctionState = 1; reentryInsideBars = 0

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

@st.cache_data(ttl=180)
def fetch_delta_options():
    BASE_URL = "https://api.india.delta.exchange/v2"
    def fetch_paginated_data(url):
        results = []; seen_cursors = set(); page_count = 0; complete = False
        while url:
            if page_count >= 50:
                break
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
        if not btc_options: return pd.DataFrame(), None, None, None, None, None, False
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
                    
        if not valid_expiries: return pd.DataFrame(), None, None, None, None, None, False
        nearest = min(valid_expiries, key=lambda x: x["dt_utc"])
        nearest_expiry = nearest["expiry_str"]; expiry_dt = nearest["dt_utc"]; expiry_source = nearest["source"]
        
        filtered_options = [opt for opt in btc_options if len(opt.get("symbol", "").split("-")) >= 4 and opt.get("symbol", "").split("-")[3] == nearest_expiry]
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
                greeks = ticker.get("greeks") or {}
                gamma_raw = greeks.get("gamma")
                gamma = float(gamma_raw) if gamma_raw not in (None, "") else np.nan
                iv = float(quotes.get("mark_iv") or quotes.get("ask_iv") or "0") * 100
                ts_val = ticker.get("timestamp") or ticker.get("updated_at")
                
                chain.append({"Symbol": sym, "Strike": strike, "Type": opt_type, "Bid": bid, "Ask": ask, "Mark": mark, "OI_USD": oi_usd, "OI_Contracts": oi_contracts, "Gamma": gamma, "IV": iv, "Volume": volume, "Timestamp": ts_val})
            except (ValueError, TypeError): continue

        df = pd.DataFrame(chain)
        if df.empty: return pd.DataFrame(), nearest_expiry, expiry_dt, expiry_source, contract_size, None, False

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

        return df_pivot, nearest_expiry, expiry_dt, expiry_source, contract_size, df, is_chain_complete
    except Exception:
        return pd.DataFrame(), None, None, None, None, None, False

# ==========================================
# CALCULATION & PROCESSING
# ==========================================
df = fetch_amt_data()

completed_df = df[df['IsClosed']].copy() if not df.empty else pd.DataFrame()
live_df = df[~df['IsClosed']].copy() if not df.empty else pd.DataFrame()

if not live_df.empty:
    latest_price = float(live_df['close'].iloc[-1])
elif not completed_df.empty:
    latest_price = float(completed_df['close'].iloc[-1])
else:
    latest_price = 0.0

funding_rate, mark_price, oi_usd, sp_prem = fetch_futures_context()

if not completed_df.empty:
    today_str = completed_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    yesterday_df = completed_df[completed_df['ShiftedDate'].dt.strftime('%Y-%m-%d') < today_str]
    if not yesterday_df.empty:
        last_prev_day = yesterday_df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
        prev_day_df = yesterday_df[yesterday_df['ShiftedDate'].dt.strftime('%Y-%m-%d') == last_prev_day]
        if manual_values_valid:
            final_vah, final_val, final_poc = input_vah, input_val, input_poc
        else:
            final_vah, final_val, final_poc = calculate_tpo(prev_day_df)
    elif manual_values_valid:
        final_vah, final_val, final_poc = input_vah, input_val, input_poc

session_df = calculate_session_journey(completed_df, final_vah, final_val)
journal_res = generate_state_journal(completed_df, manual_values_valid, input_vah, input_val, input_poc)
current_amt_state = int(journal_res[0].iloc[-1]['Code']) if (journal_res and not journal_res[0].empty) else 0
current_amt_name = state_names.get(current_amt_state, "UNKNOWN")
journal_df_check = journal_res[0] if journal_res else pd.DataFrame()

provisional_vah = provisional_val = provisional_poc = np.nan
if not df.empty:
    provisional_today_str = df['ShiftedDate'].iloc[-1].strftime('%Y-%m-%d')
    provisional_today = df[df['ShiftedDate'].dt.strftime('%Y-%m-%d') == provisional_today_str].copy()
    if not provisional_today.empty:
        provisional_vah, provisional_val, provisional_poc = calculate_tpo(provisional_today)

options_df, loaded_expiry, expiry_dt, expiry_source, dynamic_contract_size, raw_chain_df, is_chain_complete = fetch_delta_options()

true_max_pain, straddle_price, atm_strike, true_max_pain = np.nan, np.nan, 0.0, np.nan
call_oi_wall, put_oi_wall, call_gamma_wall, put_gamma_wall = None, None, None, None
pcr, pcr_volume, atm_row = 0.0, 0.0, None
straddle_valid = False
call_strike_text = "N/A"
put_strike_text = "N/A"
filtered_options = pd.DataFrame()

if not options_df.empty and latest_price > 0:
    valid_contract_size = dynamic_contract_size if dynamic_contract_size and dynamic_contract_size > 0 else np.nan
    options_df['GEX_Call'] = options_df['Gamma_Call'] * options_df['OI_Contracts_Call'] * valid_contract_size * (latest_price ** 2) * 0.01
    options_df['GEX_Put'] = options_df['Gamma_Put'] * options_df['OI_Contracts_Put'] * valid_contract_size * (latest_price ** 2) * 0.01
    options_df['Dist_to_Spot'] = abs(options_df['Strike'] - latest_price)
    
    atm_strike = options_df.loc[options_df['Dist_to_Spot'].idxmin(), 'Strike']
    atm_row = options_df[options_df['Strike'] == atm_strike].iloc[0]
    cb, ca, cm = atm_row['Bid_Call'], atm_row['Ask_Call'], atm_row['Mark_Call']
    pb, pa, pm = atm_row['Bid_Put'], atm_row['Ask_Put'], atm_row['Mark_Put']
    
    if (
        not any(pd.isna(x) for x in [cb, ca, cm, pb, pa, pm])
        and all(np.isfinite(float(x)) for x in [cb, ca, cm, pb, pa, pm])
        and cb > 0 and ca > 0 and cm > 0 and pb > 0 and pa > 0 and pm > 0
        and cb <= ca and pb <= pa
    ):
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
        
        total_calls_contracts = temp_filt['OI_Contracts_Call'].sum()
        total_puts_contracts = temp_filt['OI_Contracts_Put'].sum()
        pcr = total_puts_contracts / total_calls_contracts if total_calls_contracts > 0 else np.nan
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

    call_strike_text = "$" + format(call_gamma_wall["Strike"], ",.0f") if call_gamma_wall is not None else "N/A"
    put_strike_text = "$" + format(put_gamma_wall["Strike"], ",.0f") if put_gamma_wall is not None else "N/A"

# --- DEFINE UI VARIABLES ---
if pd.isna(final_vah) or pd.isna(final_val): loc = "Value Area Unavailable"
else: loc = "Above VAH" if latest_price > final_vah else "Below VAL" if latest_price < final_val else "Inside VA"

latest_dev_poc = journal_df_check.iloc[-1]['Dev POC'] if not journal_df_check.empty else np.nan
prior_va_width = final_vah - final_val if not pd.isna(final_vah) and not pd.isna(final_val) else np.nan
straddle_ratio = straddle_price / prior_va_width if not pd.isna(prior_va_width) and prior_va_width > 0 and not pd.isna(straddle_price) and straddle_valid else np.nan
mp_poc_distance = true_max_pain - latest_dev_poc if not pd.isna(true_max_pain) and not pd.isna(latest_dev_poc) else np.nan


# ==========================================
# RENDER UI — SINGLE-PAGE FOUR-LAYER COCKPIT (TERMINAL SKIN)
# ==========================================
_render_stamp = datetime.datetime.now(IST_TZ).strftime("%d %b %Y · %H:%M:%S IST")
st.markdown(
    '<div class="topbar"><div>'
    '<div class="brand"><em>₿</em> BTC TRADING COCKPIT</div>'
    '<div class="tag">AMT first · current auction behaviour · derivatives as context</div>'
    '</div>'
    '<div class="live"><span class="dot"></span>RENDERED ' + _render_stamp + '</div></div>',
    unsafe_allow_html=True,
)

story_context = {
    0: "BUILDING INITIAL VALUE", 1: "CURRENTLY DEVELOPING",
    2: "DISCOVERY ABOVE VALUE", 3: "DISCOVERY BELOW VALUE",
    4: "ACCEPTANCE DEVELOPING UP", 5: "ACCEPTANCE DEVELOPING DOWN",
    6: "POC BREAK ABOVE", 7: "POC BREAK BELOW",
    8: "NEW AREA DEVELOPING ABOVE", 9: "NEW AREA DEVELOPING BELOW",
    10: "FAILED DISCOVERY UP", 11: "FAILED DISCOVERY DOWN",
    12: "ACCEPTANCE FAILURE UP", 13: "ACCEPTANCE FAILURE DOWN",
    14: "RE-ENTRY CONFIRMED", 15: "STALLED ABOVE", 16: "STALLED BELOW",
    17: "FAILED UP DISCOVERY CANDIDATE", 18: "FAILED DOWN DISCOVERY CANDIDATE",
}
previous_ui_state = st.session_state.get("fast_prev_amt_state")
previous_ui_name = state_names.get(previous_ui_state) if previous_ui_state is not None else None
current_story = story_context.get(current_amt_state, current_amt_name)
auction_story = (
    previous_ui_name + " → " + current_story
    if previous_ui_state is not None and previous_ui_state != current_amt_state
    else current_story
)

dev_vah_ui = journal_df_check.iloc[-1]["Dev VAH"] if not journal_df_check.empty else np.nan
dev_poc_ui = journal_df_check.iloc[-1]["Dev POC"] if not journal_df_check.empty else np.nan
dev_val_ui = journal_df_check.iloc[-1]["Dev VAL"] if not journal_df_check.empty else np.nan

def signed_distance_text(value, reference):
    if pd.isna(value) or pd.isna(reference):
        return "N/A"
    delta_value = value - reference
    sign = "+" if delta_value >= 0 else "-"
    return sign + "$" + format(abs(delta_value), ",.0f")

prior_val_distance = signed_distance_text(latest_price, final_val)
dev_vah_distance = signed_distance_text(latest_price, dev_vah_ui)

wall_options = []
if call_gamma_wall is not None:
    wall_options.append(("Call", float(call_gamma_wall["Strike"])))
if put_gamma_wall is not None:
    wall_options.append(("Put", float(put_gamma_wall["Strike"])))
nearest_wall = "N/A"
if wall_options:
    wall_name, wall_price = min(wall_options, key=lambda x: abs(x[1] - latest_price))
    nearest_wall = "$" + format(wall_price, ",.0f") + " (" + wall_name + ")"


# ---------- UI helpers (formatting / HTML only) ----------
def _ui_num(value, fmt=",.0f", prefix="$", suffix=""):
    if value is None or pd.isna(value):
        return "N/A"
    return prefix + format(value, fmt) + suffix

def _ui_section(title, hint=""):
    st.markdown(
        '<div class="sec"><span class="bar"></span><span class="st">' + title + '</span>'
        '<span class="hint">' + hint + '</span></div>',
        unsafe_allow_html=True,
    )

def _ui_hero(label, value, sub="", tone="flat", kind="state"):
    sub_html = '<div class="s">' + sub + '</div>' if sub else ""
    return ('<div class="hero t-' + tone + '"><div class="k">' + label + '</div>'
            '<div class="v ' + kind + '">' + value + '</div>' + sub_html + '</div>')

def _ui_card(label, value, sub="", tone="flat", tint=False, small=False):
    sub_html = '<div class="s">' + sub + '</div>' if sub else ""
    return ('<div class="card t-' + tone + (' tint' if tint else '') + '"><div class="k">' + label + '</div>'
            '<div class="v' + (' sm' if small else '') + '">' + value + '</div>' + sub_html + '</div>')

def _ui_grid(cards, cls):
    return '<div class="grid ' + cls + '">' + "".join(cards) + '</div>'

# Presentation-only colour mapping for the existing auction state codes.
_UI_STATE_TONE = {2: "up", 4: "up", 6: "up", 8: "up", 3: "down", 5: "down", 7: "down", 9: "down"}
def _ui_state_tone(code):
    if code in (0, 1):
        return "flat"
    return _UI_STATE_TONE.get(code, "warn")

state_tone = _ui_state_tone(current_amt_state)
loc_tone = {"Above VAH": "up", "Below VAL": "down", "Inside VA": "warn"}.get(loc, "flat")

# ---------- TOP HEADER (3 columns) ----------
if loc == "Above VAH":
    loc_sub = signed_distance_text(latest_price, final_vah) + " vs prior VAH"
elif loc == "Below VAL":
    loc_sub = signed_distance_text(latest_price, final_val) + " vs prior VAL"
elif loc == "Inside VA":
    loc_sub = "Between prior VAL and VAH"
else:
    loc_sub = ""

h1, h2, h3 = st.columns([1, 1.5, 1])
h1.markdown(_ui_hero("BTC / USDT", _ui_num(latest_price, ",.2f"), "BINANCE · SPOT · 30M", "info", "price"), unsafe_allow_html=True)
h2.markdown(_ui_hero("AUCTION NOW", current_amt_name, "Independent code " + str(current_amt_state), state_tone, "state"), unsafe_allow_html=True)
h3.markdown(_ui_hero("LOCATION", loc, loc_sub, loc_tone, "state"), unsafe_allow_html=True)

# ---------- AUCTION STORY CALLOUT ----------
_story_parts = auction_story.split(" → ")
if len(_story_parts) == 2:
    story_html = ('<span class="prev">' + _story_parts[0] + '</span><span class="arr">→</span>'
                  '<span class="cur">' + _story_parts[1] + '</span>')
else:
    story_html = '<span class="cur">' + auction_story + '</span>'

def _ui_chip(label, value):
    cls = "up" if value.startswith("+") else "down" if value.startswith("-") else ""
    return ('<span class="chip"><span class="ck">' + label + '</span><span class="cv ' + cls + '">'
            + value + '</span></span>')

st.markdown(
    '<div class="story t-' + state_tone + '"><div class="k">AUCTION STORY</div>'
    '<div class="t">' + story_html + '</div>'
    '<div class="chips">'
    + _ui_chip("Price vs Prior VAL", prior_val_distance)
    + _ui_chip("Price vs Developing VAH", dev_vah_distance)
    + _ui_chip("Nearest GEX Wall", nearest_wall)
    + '</div></div>',
    unsafe_allow_html=True,
)

# ---------- WHAT CHANGED — UI ONLY ----------
_ui_section("WHAT CHANGED", "vs previous rendered snapshot in this browser session")
current_ui_snapshot = {
    "state": current_amt_state,
    "dev_vah": float(dev_vah_ui) if not pd.isna(dev_vah_ui) else None,
    "dev_poc": float(dev_poc_ui) if not pd.isna(dev_poc_ui) else None,
    "dev_val": float(dev_val_ui) if not pd.isna(dev_val_ui) else None,
}
previous_ui_snapshot = st.session_state.get("fast_previous_snapshot")

if previous_ui_snapshot is None:
    st.info("Baseline captured. Changes will appear after the next dashboard refresh.")
else:
    c1, c2, c3, c4 = st.columns(4)

    def show_level_change(column, label, key):
        current_value = current_ui_snapshot[key]
        previous_value = previous_ui_snapshot.get(key)
        if current_value is None or previous_value is None:
            column.metric(label, "N/A")
            return
        delta_value = current_value - previous_value
        if delta_value == 0:
            column.metric(label, "$" + format(current_value, ",.0f"), "UNCHANGED", delta_color="off")
        else:
            delta_text = "$" + format(delta_value, ",+.0f")
            column.metric(label, "$" + format(current_value, ",.0f"), delta_text)

    state_change = "UNCHANGED"
    if previous_ui_snapshot.get("state") != current_amt_state:
        state_change = "FROM " + state_names.get(previous_ui_snapshot.get("state"), "UNKNOWN")

    c1.metric("AUCTION STATE", current_amt_name, state_change, delta_color="off")
    show_level_change(c2, "DEV VAH", "dev_vah")
    show_level_change(c3, "DEV POC", "dev_poc")
    show_level_change(c4, "DEV VAL", "dev_val")

st.session_state["fast_previous_snapshot"] = current_ui_snapshot
st.session_state["fast_prev_amt_state"] = current_amt_state

# ---------- 1 · PRICE ACTION (prior VA + POC combined into one value ladder) ----------
_ui_section("1 · PRICE ACTION", "GEX walls are options-derived reference levels, not guaranteed support or resistance")

if pd.isna(final_val) or pd.isna(final_vah):
    st.markdown(
        '<div class="panel"><div class="mini">PRIOR VALUE AREA</div>'
        '<div class="card t-flat"><div class="v">N/A</div></div></div>',
        unsafe_allow_html=True,
    )
else:
    _lo = min(final_val, latest_price) if latest_price > 0 else final_val
    _hi = max(final_vah, latest_price) if latest_price > 0 else final_vah
    _pad = max((_hi - _lo) * 0.12, 1.0)
    _lo -= _pad; _hi += _pad
    _span = _hi - _lo

    def _ui_pos(x):
        return max(0.0, min(100.0, (x - _lo) / _span * 100.0))

    _p_val, _p_vah = _ui_pos(final_val), _ui_pos(final_vah)
    _poc_html = (
        '<div class="edge poc" style="left:calc(' + format(_ui_pos(final_poc), ".2f") + '% - 1px)"></div>'
        if not pd.isna(final_poc) else ""
    )
    _pin_html = (
        '<div class="pin t-' + loc_tone + '" style="left:' + format(_ui_pos(latest_price), ".2f") + '%">'
        '<span class="pl">CMP ' + _ui_num(latest_price, ",.0f") + '</span><i></i></div>'
        if latest_price > 0 else ""
    )
    st.markdown(
        '<div class="panel">'
        '<div class="mini">PRIOR VALUE AREA · VAL → POC → VAH</div>'
        '<div class="lad"><div class="track"></div>'
        '<div class="band" style="left:' + format(_p_val, ".2f") + '%;width:' + format(_p_vah - _p_val, ".2f") + '%"></div>'
        '<div class="edge val" style="left:calc(' + format(_p_val, ".2f") + '% - 1px)"></div>'
        '<div class="edge vah" style="left:calc(' + format(_p_vah, ".2f") + '% - 1px)"></div>'
        + _poc_html + _pin_html +
        '</div>'
        '<div class="nums">'
        '<div class="n t-down"><div class="k">Prior VAL</div><div class="v">' + _ui_num(final_val) + '</div></div>'
        '<div class="n t-warn"><div class="k">Prior POC</div><div class="v">' + _ui_num(final_poc) + '</div></div>'
        '<div class="n t-up"><div class="k">Prior VAH</div><div class="v">' + _ui_num(final_vah) + '</div></div>'
        '<div class="n t-flat"><div class="k">VA Width</div><div class="v">' + _ui_num(prior_va_width) + '</div></div>'
        '</div></div>',
        unsafe_allow_html=True,
    )

# ---------- 2 · AMT STRUCTURE ----------
_ui_section("2 · AMT STRUCTURE", "Classification basis: completed 30-minute candles")

prior_width = final_vah - final_val
current_width = provisional_vah - provisional_val
delta = current_width - prior_width
if any(pd.isna(value) for value in [prior_width, current_width, delta]):
    va_expansion_value = "N/A"
    va_expansion_label = None
elif delta > 0:
    va_expansion_value = "+$" + format(delta, ",.0f")
    if provisional_val < final_val:
        va_expansion_label = "Downward"
    elif provisional_vah > final_vah:
        va_expansion_label = "Upward"
    else:
        va_expansion_label = None
else:
    va_expansion_value = "Contained"
    va_expansion_label = None

if va_expansion_label == "Downward":
    _va_sub, _va_tone = "▼ Downward", "down"
elif va_expansion_label == "Upward":
    _va_sub, _va_tone = "▲ Upward", "up"
else:
    _va_sub, _va_tone = "", "flat"

st.markdown(
    _ui_grid([
        _ui_card("Fast Auction Classification", current_amt_name, "Independent code " + str(current_amt_state), state_tone, tint=True, small=True),
        _ui_card("Developing VAH", _ui_num(dev_vah_ui), tone="up"),
        _ui_card("Developing POC", _ui_num(dev_poc_ui), tone="warn"),
        _ui_card("Developing VAL", _ui_num(dev_val_ui), tone="down"),
        _ui_card("VA Expansion", va_expansion_value, _va_sub, _va_tone),
    ], "g5"),
    unsafe_allow_html=True,
)
st.markdown('<div class="foot">Independent dashboard classification; not a prediction or a replacement for the locked Pine AMT reference.</div>', unsafe_allow_html=True)

with st.expander("Prior value reference and provisional / forming profile"):
    x1, x2, x3 = st.columns(3)
    x1.metric("Prior VAH", "$" + format(final_vah, ",.0f") if not pd.isna(final_vah) else "N/A")
    x2.metric("Prior POC", "$" + format(final_poc, ",.0f") if not pd.isna(final_poc) else "N/A")
    x3.metric("Prior VAL", "$" + format(final_val, ",.0f") if not pd.isna(final_val) else "N/A")
    st.markdown("**Provisional / forming profile**")
    y1, y2, y3 = st.columns(3)
    y1.metric("Provisional VAH", "$" + format(provisional_vah, ",.0f") if not pd.isna(provisional_vah) else "N/A")
    y2.metric("Provisional POC", "$" + format(provisional_poc, ",.0f") if not pd.isna(provisional_poc) else "N/A")
    y3.metric("Provisional VAL", "$" + format(provisional_val, ",.0f") if not pd.isna(provisional_val) else "N/A")
    st.caption("Includes the currently forming 30-minute candle; informational only and excluded from completed-candle classification.")

# ---------- 3 · CONTEXT ----------
_ui_section("3 · CONTEXT", "Options · Futures · nearest-strike open interest")

ctx_left, ctx_right = st.columns([1.6, 1])

with ctx_left:
    # Visual map of the ten strikes nearest to spot, using the existing
    # nearest-expiry chain and USD open interest only.
    if raw_chain_df is not None and not raw_chain_df.empty and latest_price > 0:
        gex_view = raw_chain_df.copy()
        gex_view["Strike"] = pd.to_numeric(gex_view["Strike"], errors="coerce")
        gex_view["OI_USD"] = pd.to_numeric(gex_view["OI_USD"], errors="coerce")
        gex_view = gex_view.dropna(subset=["Strike", "OI_USD"])
        nearest_strikes = (
            gex_view[["Strike"]].drop_duplicates()
            .assign(Distance=lambda frame: (frame["Strike"] - latest_price).abs())
            .nsmallest(10, "Distance")["Strike"]
            .tolist()
        )
        chart_data = gex_view[gex_view["Strike"].isin(nearest_strikes)].copy()
        if not chart_data.empty:
            call_data = chart_data[chart_data["Type"] == "Call"].set_index("Strike")["OI_USD"]
            put_data = chart_data[chart_data["Type"] == "Put"].set_index("Strike")["OI_USD"]
            strikes_for_chart = sorted(nearest_strikes)
            fig = go.Figure()
            fig.add_trace(go.Bar(
                name="Call OI",
                y=strikes_for_chart,
                x=[float(call_data.get(strike, 0.0)) / 1e6 for strike in strikes_for_chart],
                orientation="h",
                marker=dict(color="#00E676", line=dict(width=0)),
                hovertemplate="Strike: $%{y:,.0f}<br>Call OI: $%{x:.2f}M<extra></extra>",
            ))
            fig.add_trace(go.Bar(
                name="Put OI",
                y=strikes_for_chart,
                x=[float(put_data.get(strike, 0.0)) / 1e6 for strike in strikes_for_chart],
                orientation="h",
                marker=dict(color="#FF5252", line=dict(width=0)),
                hovertemplate="Strike: $%{y:,.0f}<br>Put OI: $%{x:.2f}M<extra></extra>",
            ))
            fig.add_hline(
                y=latest_price,
                line_dash="dash",
                line_color="#FFD600",
                line_width=2.5,
                annotation_text="CMP $" + format(latest_price, ",.0f"),
                annotation_position="top right",
                annotation_font=dict(color="#0E1117", size=12, family="JetBrains Mono, Menlo, Consolas, monospace"),
                annotation_bgcolor="#FFD600",
                annotation_borderpad=3,
            )
            fig.update_layout(
                template="plotly_dark",
                barmode="group",
                bargap=0.28,
                bargroupgap=0.06,
                height=400,
                margin=dict(l=10, r=20, t=40, b=10),
                paper_bgcolor="rgba(0,0,0,0)",
                plot_bgcolor="rgba(0,0,0,0)",
                font=dict(color="#C9D1D9", family="Inter, -apple-system, Segoe UI, sans-serif", size=12),
                title=dict(text="OPEN INTEREST BY STRIKE · NEAREST 10", x=0.0, xanchor="left",
                           font=dict(size=11, color="#8B96A5")),
                legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1,
                            font=dict(size=11), bgcolor="rgba(0,0,0,0)"),
                hoverlabel=dict(bgcolor="#1A1E24", bordercolor="#2A313A", font=dict(color="#E6EDF3")),
                xaxis=dict(title=dict(text="Open Interest (USD millions)", font=dict(size=11, color="#8B96A5")),
                           gridcolor="rgba(255,255,255,0.06)", zeroline=False,
                           linecolor="rgba(255,255,255,0.10)"),
                yaxis=dict(title=dict(text="Strike (USD)", font=dict(size=11, color="#8B96A5")),
                           tickmode="array", tickvals=strikes_for_chart, ticktext=["$" + format(s_, ",.0f") for s_ in strikes_for_chart],
                           gridcolor="rgba(255,255,255,0.06)", linecolor="rgba(255,255,255,0.10)",
                           tickfont=dict(family="JetBrains Mono, Menlo, Consolas, monospace", size=11)),
            )
            st.plotly_chart(fig, use_container_width=True, theme=None, config={"displayModeBar": False})
        else:
            st.caption("No valid strike / open-interest rows available for the chart.")
    else:
        st.caption("Options chain unavailable; nearest-strike OI chart cannot be displayed.")

with ctx_right:
    st.markdown('<div class="mini">OPTIONS' + ((" · EXPIRY " + str(loaded_expiry)) if loaded_expiry else "") + '</div>', unsafe_allow_html=True)
    if not options_df.empty and atm_row is not None:
        st.markdown(
            _ui_grid([
                _ui_card("Max Pain", _ui_num(true_max_pain)),
                _ui_card("Local OI PCR · ±10%", format(pcr, ".2f") if not pd.isna(pcr) else "N/A", tone="info"),
                _ui_card("Call GEX Wall", call_strike_text, tone="up", tint=True),
                _ui_card("Put GEX Wall", put_strike_text, tone="down", tint=True),
            ], "g2"),
            unsafe_allow_html=True,
        )
    else:
        st.info("Options context unavailable or ATM quote validation did not pass. Missing values are not treated as zero.")

    st.markdown('<div class="mini" style="margin-top:8px">FUTURES</div>', unsafe_allow_html=True)
    basis_status = "Discount" if sp_prem is not None and sp_prem < 0 else "Premium" if sp_prem is not None and sp_prem > 0 else "Unavailable"
    _basis_tone = {"Discount": "down", "Premium": "up"}.get(basis_status, "flat")
    st.markdown(
        _ui_grid([
            _ui_card("Delta OI", "$" + format(oi_usd / 1e6, ".2f") + "M" if oi_usd is not None else "N/A"),
            _ui_card("Perp Basis", format(sp_prem, ".4f") + "%" if sp_prem is not None else "N/A", basis_status, _basis_tone),
        ], "g2"),
        unsafe_allow_html=True,
    )

st.markdown('<div class="foot">Futures OI and basis are context, not directional signals. OI direction is unavailable without a comparable prior observation.</div>', unsafe_allow_html=True)

_ui_section("DIAGNOSTICS AND RAW DATA", "expand for full tables")
with st.expander("Session OHLC history"):
    if not session_df.empty:
        st.dataframe(session_df, use_container_width=True, hide_index=True)
    else:
        st.caption("Session history unavailable.")

with st.expander("Fast Auction Classification journal"):
    st.caption("Independent dashboard classification; completed candles only.")
    if not journal_df_check.empty:
        st.dataframe(journal_df_check, use_container_width=True, hide_index=True)
    else:
        st.caption("Classification journal unavailable.")

with st.expander("Options OI clusters"):
    st.markdown("#### Top 3 Strikes by OI")
    st.caption("Largest individual Call and Put OI concentrations within ±15% of spot. OI is shown in USD value; distance is measured from current spot.")
    if raw_chain_df is not None and not raw_chain_df.empty and latest_price > 0:
        top_band = raw_chain_df.copy()
        top_band["Strike"] = pd.to_numeric(top_band["Strike"], errors="coerce")
        top_band["OI_USD"] = pd.to_numeric(top_band["OI_USD"], errors="coerce")
        top_band["OI_Contracts"] = pd.to_numeric(
            top_band["OI_Contracts"], errors="coerce"
        )
        top_band = top_band[
            top_band["Strike"].between(latest_price * 0.85, latest_price * 1.15)
            & top_band["OI_USD"].notna()
            & (top_band["OI_USD"] > 0)
        ].copy()
        top_band["Distance ($)"] = top_band["Strike"] - latest_price
        top_band["Distance (%)"] = (top_band["Distance ($)"] / latest_price) * 100
        top_cols = st.columns(2)
        for col, opt_type, heading in [
            (top_cols[0], "Call", "Top 3 Calls"),
            (top_cols[1], "Put", "Top 3 Puts"),
        ]:
            with col:
                st.markdown(f"**{heading}**")
                top_rows = top_band[top_band["Type"] == opt_type].nlargest(3, "OI_USD")[
                    ["Strike", "OI_USD", "OI_Contracts", "Distance ($)", "Distance (%)"]
                ].copy()
                top_rows = top_rows.sort_values(
                    "Strike",
                    ascending=(opt_type == "Put"),
                )
                if not top_rows.empty:
                    top_rows["OI_USD"] = top_rows["OI_USD"] / 1e6
                    st.dataframe(
                        top_rows,
                        column_config={
                            "Strike": st.column_config.NumberColumn("Strike", format="$%.0f"),
                            "OI_USD": st.column_config.NumberColumn("OI (USD M)", format="$%.2fM"),
                            "OI_Contracts": st.column_config.NumberColumn(
                                "OI (Contracts)", format="%.0f"
                            ),
                            "Distance ($)": st.column_config.NumberColumn("Distance ($)", format="$%+.0f"),
                            "Distance (%)": st.column_config.NumberColumn("Distance (%)", format="%+.2f%%"),
                        },
                        use_container_width=True,
                        hide_index=True,
                    )
                else:
                    st.caption("No qualifying strikes in the ±15% band.")
    else:
        st.caption("Raw options chain unavailable; Top 3 ranking cannot be calculated.")

with st.expander("Raw options chain — nearest strikes"):
    if not filtered_options.empty:
        display_df = filtered_options.copy()
        display_df["Strike"] = pd.to_numeric(display_df["Strike"], errors="coerce")
        display_df["Dist_to_Spot"] = display_df["Strike"] - latest_price
        # Keep the signed distance readable while marking the ATM strike explicitly.
        display_df["Dist_to_Spot"] = display_df["Dist_to_Spot"].map(
            lambda distance: "N/A" if pd.isna(distance) else "$" + format(distance, "+.0f")
        )
        if pd.notna(atm_strike):
            display_df.loc[
                display_df["Strike"] == float(atm_strike), "Dist_to_Spot"
            ] = "ATM"
        display_df["Call_OI_M"] = display_df["OI_USD_Call"] / 1e6
        display_df["Put_OI_M"] = display_df["OI_USD_Put"] / 1e6

        dev_vah = float(provisional_vah) if pd.notna(provisional_vah) else np.nan
        dev_val = float(provisional_val) if pd.notna(provisional_val) else np.nan
        dev_poc = float(provisional_poc) if pd.notna(provisional_poc) else np.nan
        strike_values = display_df["Strike"]

        amt_conditions = [
            strike_values > dev_vah,
            strike_values < dev_val,
            (strike_values - dev_poc).abs() <= 50,
        ]
        amt_labels = ["Above Value", "Below Value", "At Dev POC"]
        display_df["AMT Location"] = np.select(
            amt_conditions,
            amt_labels,
            default="Inside Value",
        )

        display_df = display_df.sort_values(
            by="Strike",
            ascending=False,
            kind="stable",
            na_position="last",
        )
        display_df = display_df[
            ["Mark_Call", "Call_OI_M", "Volume_Call", "IV_Call", "Strike",
             "Dist_to_Spot", "AMT Location", "IV_Put", "Volume_Put", "Put_OI_M", "Mark_Put"]
        ]

        atm_call_iv = (
            pd.to_numeric(atm_row.get("IV_Call"), errors="coerce")
            if atm_row is not None else np.nan
        )
        atm_put_iv = (
            pd.to_numeric(atm_row.get("IV_Put"), errors="coerce")
            if atm_row is not None else np.nan
        )

        def highlight_amt_gex_and_relative_iv(row):
            styles = pd.Series("", index=row.index, dtype="object")
            strike = pd.to_numeric(row["Strike"], errors="coerce")

            if pd.notna(strike) and pd.notna(dev_poc) and abs(strike - dev_poc) <= 50:
                styles.loc[:] = "background-color: #6A1B9A; color: white; font-weight: bold"

            # ATM strike overrides the purple AMT POC row on the Strike cell.
            # GEX wall styling below remains highest priority for that cell.
            if pd.notna(strike) and pd.notna(atm_strike) and strike == float(atm_strike):
                styles.loc["Strike"] = "background-color: #1E88E5; color: white; font-weight: bold"

            if call_gamma_wall is not None and pd.notna(strike) and strike == float(call_gamma_wall["Strike"]):
                styles.loc["Strike"] = "background-color: #2E7D32; color: white; font-weight: bold"
            if put_gamma_wall is not None and pd.notna(strike) and strike == float(put_gamma_wall["Strike"]):
                styles.loc["Strike"] = "background-color: #C62828; color: white; font-weight: bold"

            call_iv = pd.to_numeric(row["IV_Call"], errors="coerce")
            if pd.notna(call_iv) and pd.notna(atm_call_iv):
                call_iv_diff = call_iv - atm_call_iv
                if call_iv_diff > 5:
                    styles.loc["IV_Call"] = "background-color: #C62828; color: white"
                elif call_iv_diff < -5:
                    styles.loc["IV_Call"] = "background-color: #2E7D32; color: white"

            put_iv = pd.to_numeric(row["IV_Put"], errors="coerce")
            if pd.notna(put_iv) and pd.notna(atm_put_iv):
                put_iv_diff = put_iv - atm_put_iv
                if put_iv_diff > 5:
                    styles.loc["IV_Put"] = "background-color: #C62828; color: white"
                elif put_iv_diff < -5:
                    styles.loc["IV_Put"] = "background-color: #2E7D32; color: white"

            return styles

        raw_options_config = {
            "Strike": st.column_config.NumberColumn("Strike", format="$%.0f"),
            "Dist_to_Spot": st.column_config.TextColumn("Dist to Spot"),
            "Mark_Call": st.column_config.NumberColumn("Mark_Call", format="$%.2f"),
            "Mark_Put": st.column_config.NumberColumn("Mark_Put", format="$%.2f"),
            "Call_OI_M": st.column_config.NumberColumn("Call_OI_M", format="$%.2fM"),
            "Put_OI_M": st.column_config.NumberColumn("Put_OI_M", format="$%.2fM"),
            "Volume_Call": st.column_config.NumberColumn("Volume_Call", format="%.0f"),
            "Volume_Put": st.column_config.NumberColumn("Volume_Put", format="%.0f"),
            "IV_Call": st.column_config.NumberColumn("IV_Call", format="%.1f%%"),
            "IV_Put": st.column_config.NumberColumn("IV_Put", format="%.1f%%"),
        }
        st.caption("Relative IV vs ATM · Red: more than +5 vol points · Green: less than -5 vol points · Neutral: within ±5. Call and Put are compared with their own ATM IV. This is a relative-skew reference, not an expensive/cheap signal.")
        st.dataframe(
            display_df.style.apply(highlight_amt_gex_and_relative_iv, axis=1),
            column_config=raw_options_config,
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Raw options chain unavailable.")

with st.expander("Copy dashboard snapshot"):
    snapshot_text = """
=== BTC TRADING COCKPIT SNAPSHOT ===
Time: {time} IST
Price: {price}
Location: {location}
Fast Auction Classification: {state} (Independent Code {code})
Prior VAH / POC / VAL: {vah} / {poc} / {val}
Developing VAH / POC / VAL: {dev_vah} / {dev_poc} / {dev_val}
Options expiry: {expiry}
ATM Straddle: {straddle}
Max Pain: {max_pain}
Call GEX Wall: {call_wall}
Put GEX Wall: {put_wall}
Local OI PCR (Contracts ±10% spot): {pcr}
Delta Futures OI: {oi}
Perp Basis: {basis}
OI Direction: Unavailable without comparable prior observation
""".format(
        time=datetime.datetime.now(IST_TZ).strftime("%Y-%m-%d %H:%M:%S"),
        price="$" + format(latest_price, ",.2f"), location=loc, state=current_amt_name, code=current_amt_state,
        vah=final_vah, poc=final_poc, val=final_val,
        dev_vah=dev_vah_ui, dev_poc=dev_poc_ui, dev_val=dev_val_ui,
        expiry=loaded_expiry,
        straddle="$" + format(straddle_price, ",.2f") if straddle_valid else "N/A",
        max_pain="$" + format(true_max_pain, ",.0f") if not pd.isna(true_max_pain) else "N/A",
        call_wall=call_strike_text if not options_df.empty and atm_row is not None else "N/A",
        put_wall=put_strike_text if not options_df.empty and atm_row is not None else "N/A",
        pcr=format(pcr, ".2f") if not pd.isna(pcr) else "N/A",
        oi="$" + format(oi_usd / 1e6, ".2f") + "M" if oi_usd is not None else "N/A",
        basis=format(sp_prem, ".4f") + "%" if sp_prem is not None else "N/A",
    )
    st.code(snapshot_text, language="text")


st.markdown(
    '<div class="foot">' + "Source status — Binance candles: " + ("available" if not df.empty else "unavailable") + " · Delta futures: " + ("available" if oi_usd is not None else "unavailable") + " · Delta options: " + ("available" if not options_df.empty else "unavailable") + '</div>',
    unsafe_allow_html=True,
)
components.html("<script>setTimeout(function(){ window.parent.location.reload(); }, 180000);</script>", height=0, width=0)
