import streamlit as st
import plotly.graph_objects as go
import pandas as pd
import numpy as np
import requests
import datetime
import math
import logging
import os
import json
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone, timedelta
from supabase import create_client
import html as _htmllib
from plotly.subplots import make_subplots
from options_filters import load_and_calculate_options_filters
from fast_dashboard_data_helpers import optional_float, strict_sum, safe_ratio, select_observed_max_row, calculate_max_pain
from pine_state_machine import Candle, PineConfig, PineStateMachine, STATE_NAMES

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

/* =====================================================================
   COCKPIT v2 — visual-first layer (appended; overrides above where needed)
   ===================================================================== */
.block-container { padding-top: 0.6rem !important; max-width: 1760px; }

/* tiles (hero strip) */
.hero4 { display: grid; grid-template-columns: 1.05fr 1fr 1.25fr 1.35fr; gap: 10px; }
@media (max-width: 1100px) { .hero4 { grid-template-columns: 1fr 1fr; } }
.tile { background: linear-gradient(145deg, #1D222A 0%, #151921 100%); border: 1px solid var(--border);
  border-top: 3px solid var(--accent, var(--border)); border-radius: 12px; padding: 10px 16px 12px 16px; min-height: 112px;
  display: flex; flex-direction: column; justify-content: center; gap: 3px; box-shadow: 0 0 30px -18px var(--glow, transparent); }
.tile .k { color: var(--muted); font-size: 0.62rem; font-weight: 700; letter-spacing: 0.16em; text-transform: uppercase; }
.tile .big { font-family: var(--sans); font-weight: 800; font-size: 1.55rem; letter-spacing: 0.01em; text-transform: uppercase; color: var(--accent); text-shadow: 0 0 22px var(--glow); line-height: 1.1; }
.tile .big.px { font-family: var(--mono); font-size: 2.3rem; color: #fff; text-shadow: none; letter-spacing: -0.03em; text-transform: none; font-variant-numeric: tabular-nums; }
.tile .sub { font-family: var(--mono); font-size: 0.7rem; color: var(--muted); }
.nr { display: grid; grid-template-columns: 18px 1fr auto auto; gap: 8px; align-items: baseline; font-size: 0.76rem; padding: 2px 0; }
.nr .ar { font-weight: 800; } .nr .nn { font-weight: 700; letter-spacing: 0.03em; }
.nr .np { font-family: var(--mono); font-weight: 700; color: var(--text); }
.nr .nd { font-family: var(--mono); font-size: 0.7rem; min-width: 74px; text-align: right; font-weight: 700; }
.up { color: #00E676; } .down { color: #FF5252; } .flat { color: #8B96A5; }

/* what-changed strip */
.dl { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; padding: 6px 2px 0 2px; }
.dl .lab { color: var(--muted); font-size: 0.6rem; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; margin-right: 4px; }
.dchip { display: inline-flex; align-items: center; gap: 7px; background: rgba(20,24,30,.9); border: 1px solid var(--border); border-radius: 999px; padding: 3px 12px; font-size: 0.72rem; }
.dchip .dk { color: var(--muted); font-size: 0.6rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; }
.dchip .dv { font-family: var(--mono); font-weight: 700; color: var(--text); }
.dchip .dd { font-family: var(--mono); font-weight: 700; font-size: 0.7rem; }
.dchip .pv { color: var(--muted); font-weight: 600; } .dchip .ar { color: var(--accent, #FFC107); margin: 0 4px; }

/* chart legend */
.lg { display: flex; flex-wrap: wrap; gap: 14px; align-items: center; padding: 2px 4px 0 4px; font-size: 0.66rem; color: var(--muted); }
.lg span { display: inline-flex; align-items: center; gap: 6px; letter-spacing: 0.04em; }
.lg i { display: inline-block; width: 18px; height: 0; border-top: 2px solid var(--c); }
.lg i.dash { border-top-style: dashed; } .lg i.dot { border-top-style: dotted; border-top-width: 3px; }
.lg i.blk { height: 9px; width: 14px; border: 0; background: var(--c); opacity: .5; border-radius: 2px; }

/* level stack */
.ls { display: flex; flex-direction: column; gap: 2px; }
.lv { position: relative; display: grid; grid-template-columns: 8px 1fr auto auto; gap: 9px; align-items: center; padding: 5px 9px; border-radius: 6px; overflow: hidden; background: rgba(255,255,255,.015); }
.lv > * { position: relative; }
.lv .bar { position: absolute; left: 0; top: 0; bottom: 0; opacity: .16; }
.lv .sw { width: 8px; height: 8px; border-radius: 2px; background: var(--muted); }
.lv .nm { color: #C9D1D9; font-weight: 600; font-size: 0.72rem; letter-spacing: 0.03em; white-space: nowrap; }
.lv .nm .off { color: #6B7683; font-size: 0.58rem; border: 1px solid #36404B; border-radius: 4px; padding: 0 4px; margin-left: 6px; letter-spacing: 0.08em; text-transform: uppercase; }
.lv .pr { font-family: var(--mono); font-weight: 700; font-size: 0.78rem; color: var(--text); font-variant-numeric: tabular-nums; }
.lv .ds { font-family: var(--mono); font-size: 0.7rem; font-weight: 700; min-width: 92px; text-align: right; }
.lv .ds i { font-style: normal; color: var(--muted); font-weight: 500; margin-left: 4px; }
.lv.cmp { background: #FFD600; box-shadow: 0 0 18px -4px rgba(255,214,0,.7); margin: 3px 0; padding: 6px 9px; }
.lv.cmp .nm, .lv.cmp .pr, .lv.cmp .ds { color: #0E1117; font-weight: 800; }
.lv.cmp .sw { background: #0E1117; }

/* module panels */
.mod { background: var(--panel); border: 1px solid var(--border); border-radius: 12px; padding: 10px 16px 12px 16px; }
.mh { display: flex; align-items: center; justify-content: space-between; gap: 10px; margin-bottom: 6px; }
.mh .mt { color: var(--text); font-size: 0.68rem; font-weight: 800; letter-spacing: 0.16em; text-transform: uppercase; display: flex; align-items: center; gap: 8px; }
.mh .mt::before { content: ""; width: 3px; height: 12px; background: var(--amber); border-radius: 2px; }
.mh .mx { color: var(--muted); font-family: var(--mono); font-size: 0.66rem; }
.mr { display: grid; grid-template-columns: 92px 1fr auto; gap: 12px; align-items: center; padding: 7px 0; border-top: 1px solid #1F252D; }
.mr:first-of-type { border-top: 0; }
.mr .rl { color: var(--muted); font-size: 0.62rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; }
.mr .rv { font-family: var(--mono); font-weight: 700; font-size: 0.86rem; color: var(--text); text-align: right; white-space: nowrap; }
.mr .rv small { display: block; font-size: 0.62rem; font-weight: 500; color: var(--muted); }
.mr .nums-s { font-family: var(--mono); font-size: 0.6rem; color: #6B7683; margin-top: 2px; letter-spacing: 0.02em; }
.pill { display: inline-block; font-family: var(--mono); font-size: 0.66rem; font-weight: 700; padding: 2px 9px; border-radius: 999px; border: 1px solid var(--border); color: var(--muted); }
.pill.up { color: #00E676; border-color: rgba(0,230,118,.4); background: rgba(0,230,118,.07); }
.pill.down { color: #FF5252; border-color: rgba(255,82,82,.4); background: rgba(255,82,82,.07); }
.chipr { display: flex; flex-wrap: wrap; gap: 8px; padding-top: 8px; border-top: 1px solid #1F252D; }
.wchip { display: inline-flex; gap: 8px; align-items: baseline; background: rgba(10,13,18,.65); border: 1px solid var(--border); border-left: 3px solid var(--c); border-radius: 8px; padding: 3px 10px; }
.wchip .wk { color: var(--muted); font-size: 0.58rem; font-weight: 700; letter-spacing: 0.1em; text-transform: uppercase; }
.wchip .wv { font-family: var(--mono); font-weight: 700; font-size: 0.8rem; }

/* range bar (value areas) */
.rb { position: relative; height: 22px; margin: 2px 0; }
.rb .trk { position: absolute; left: 0; right: 0; top: 9px; height: 4px; border-radius: 2px; background: #232932; }
.rb .bnd { position: absolute; top: 5px; height: 12px; border-radius: 3px; background: var(--c); opacity: .6; }
.rb .poc { position: absolute; top: 2px; width: 3px; height: 18px; background: #FFC107; border-radius: 1px; box-shadow: 0 0 6px rgba(255,193,7,.65); }
.rb .px { position: absolute; top: -3px; width: 2px; height: 28px; background: #FFD600; box-shadow: 0 0 8px rgba(255,214,0,.85); }
.rb .px::before { content: ""; position: absolute; top: -2px; left: -3px; width: 8px; height: 8px; border-radius: 50%; background: #FFD600; }

/* diverging bar */
.db { position: relative; height: 10px; background: #232932; border-radius: 5px; }
.db::before { content: ""; position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1px; background: #6B7683; z-index: 2; }
.db .f { position: absolute; top: 0; bottom: 0; border-radius: 5px; }
/* split bar (put / call) */
.sp { position: relative; display: flex; height: 10px; border-radius: 5px; overflow: hidden; background: #232932; }
.sp i { display: block; height: 100%; opacity: .85; }
.sp::after { content: ""; position: absolute; left: 50%; top: -3px; bottom: -3px; width: 1px; background: #E6EDF3; opacity: .6; }

/* options-filter cards (drawer) */
.g3 { grid-template-columns: repeat(3, 1fr); }
@media (max-width: 900px) { .g3 { grid-template-columns: 1fr; } }
.card .v.sm2 { font-family: var(--mono); font-size: 0.82rem; font-weight: 600; line-height: 1.35; word-break: break-word; }

/* source status */
.stat { display: flex; flex-wrap: wrap; gap: 16px; align-items: center; color: #6B7683; font-size: 0.66rem; letter-spacing: 0.06em; text-transform: uppercase; margin-top: 4px; }
.stat span { display: inline-flex; align-items: center; gap: 6px; }
.stat b { width: 7px; height: 7px; border-radius: 50%; display: inline-block; }
</style>
""", unsafe_allow_html=True)

# ==========================================
# CONFIGURATION & STATE
# ==========================================
IST_TZ = timezone(timedelta(hours=5, minutes=30))

# Canonical TradingView v7.2.2 auction phase labels.
state_names = STATE_NAMES

@st.cache_resource
def get_persistent_state():
    return {
        "inverted_gamma_history": [], 
        "active_inverted_event": None, 
        "last_inverted_status": False
    }
p_state = get_persistent_state()
st.session_state.setdefault("trade_journal", "")

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
journal_input = st.sidebar.text_area("Log Trade Details", value=st.session_state["trade_journal"], height=200)
if st.sidebar.button("Save Journal"):
    st.session_state["trade_journal"] = journal_input
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
                funding_rate = optional_float(ticker.get("funding_rate"))
                mark_price = optional_float(ticker.get("mark_price"))
                spot_price = optional_float(ticker.get("spot_price"))
                oi_usd = optional_float(ticker.get("oi_value_usd"))
                sp_prem = ((mark_price - spot_price) / spot_price) * 100 if mark_price is not None and spot_price is not None and spot_price > 0 else None
                return funding_rate, mark_price, oi_usd, sp_prem
        return None, None, None, None
    except Exception:
        return None, None, None, None


@st.cache_data(ttl=60, show_spinner=False)
def fetch_futures_oi_context():
    """Fetch aggregate OI and its as-of confirmed AMT context for display only."""
    unavailable = {"status": "UNAVAILABLE"}
    try:
        url = os.environ.get("SUPABASE_URL")
        key = os.environ.get("SUPABASE_ANON_KEY") or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        if not url or not key:
            return unavailable
        client = create_client(url, key)
        response = (client.table("futures_oi_snapshots")
            .select("created_at,oi_usd,status").eq("exchange", "AGGREGATE")
            .order("created_at", desc=True).limit(20).execute())
        observations = []
        for row in response.data or []:
            try:
                status = row.get("status")
                if isinstance(status, str):
                    status = json.loads(status)
                if not isinstance(status, dict):
                    status = {}
                contributors = (status.get("coverage") or {}).get("contributors")
                if not isinstance(contributors, list):
                    contributors = status.get("oi_contributors", [])
                contributors = sorted({str(v).upper() for v in contributors if v})
                observed_at = parse_timestamp(status.get("collected_at"))
                oi_value = float(row.get("oi_usd"))
                if observed_at is None or not np.isfinite(oi_value) or oi_value <= 0 or not contributors:
                    continue
                observations.append({"observed_at": observed_at, "oi_usd": oi_value, "contributors": contributors})
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
        observations.sort(key=lambda item: item["observed_at"])
        if not observations:
            return unavailable
        current = observations[-1]
        baseline = None
        interval_minutes = None
        for previous in observations[:-1]:
            age = (current["observed_at"] - previous["observed_at"]).total_seconds() / 60.0
            if 25.0 <= age <= 35.0 and previous["contributors"] == current["contributors"]:
                if baseline is None or abs(age - 30.0) < abs(interval_minutes - 30.0):
                    baseline, interval_minutes = previous, age
        change_pct = None
        direction = "N/A"
        if baseline is not None:
            change_pct = ((current["oi_usd"] / baseline["oi_usd"]) - 1.0) * 100.0
            direction = "EXPANDING" if change_pct > 0 else "CONTRACTING" if change_pct < 0 else "UNCHANGED"
        amt_response = (client.table("amt_snapshots")
            .select("candle_close_time,close_price,prior_vah,prior_val,confirmed_state")
            .eq("exchange", "BINANCE").eq("symbol", "BTCUSDT").eq("timeframe", "30")
            .eq("data_status", "VALID").lte("candle_close_time", current["observed_at"].isoformat())
            .order("candle_close_time", desc=True).limit(1).execute())
        amt_rows = amt_response.data or []
        amt = amt_rows[0] if amt_rows else {}
        try:
            price, vah, val = float(amt.get("close_price")), float(amt.get("prior_vah")), float(amt.get("prior_val"))
            valid = all(np.isfinite(v) for v in (price, vah, val)) and vah >= val
        except (TypeError, ValueError):
            valid = False
        location = ("ABOVE_VAH" if price > vah else "BELOW_VAL" if price < val else "INSIDE_VALUE") if valid else "LOCATION_UNAVAILABLE"
        return {"status": "OK", "oi_usd": current["oi_usd"], "change_pct": change_pct,
            "direction": direction, "interval_minutes": interval_minutes,
            "coverage": current["contributors"], "observed_at": current["observed_at"].isoformat(),
            "price_location": location, "amt_state": amt.get("confirmed_state") or "AMT_STATE_UNAVAILABLE"}
    except Exception as exc:
        logging.getLogger(__name__).warning("Futures OI context unavailable: %s", exc)
        return unavailable


@st.cache_data(ttl=180, show_spinner=False)
def fetch_options_filter_results():
    """Load validated, snapshot-pinned Options Filters results (cached for 3 minutes)."""
    try:
        supabase_url = os.environ.get("SUPABASE_URL")
        supabase_key = (
            os.environ.get("SUPABASE_ANON_KEY")
            or os.environ.get("SUPABASE_SERVICE_ROLE_KEY")
        )
        if not supabase_url or not supabase_key:
            missing = []
            if not supabase_url:
                missing.append("SUPABASE_URL")
            if not supabase_key:
                missing.append("SUPABASE_ANON_KEY or SUPABASE_SERVICE_ROLE_KEY")
            reason = "MISSING_SUPABASE_ENV:" + ", ".join(missing)
            logging.getLogger(__name__).error(
                "Options Research Filters unavailable: required environment configuration missing: %s",
                ", ".join(missing),
            )
            return {
                "status": "UNAVAILABLE",
                "selection": {"status": "UNAVAILABLE", "reason": reason},
                "filters": {},
            }
        client = create_client(supabase_url, supabase_key)
        return load_and_calculate_options_filters(client)
    except Exception as exc:
        logging.getLogger(__name__).exception(
            "Options Research Filters fetch failed: exception_type=%s exception=%r",
            type(exc).__name__, exc,
        )
        return {
            "status": "UNAVAILABLE",
            "selection": {"status": "UNAVAILABLE", "reason": type(exc).__name__},
            "filters": {},
        }


def _format_options_filter_result(key, result):
    """Format only validated backend output; never recalculate filter logic in the UI."""
    status = result.get("status", "UNAVAILABLE")
    if status != "OK":
        reason = result.get("reason") or status
        return "N/A", f"{status} · {reason}"

    if key == "nearby_oi":
        walls = result.get("walls", [])
        if not walls:
            return "No local concentration detected", "OK"
        summary = " · ".join(
            f'{wall.get("type", "N/A")} ${float(wall.get("strike")):,.0f}'
            for wall in walls
        )
        return summary, "OK"

    if key == "iv_skew":
        return (
            f'Call IV Premium {float(result.get("call_iv_premium_pct")):+.2f}%',
            "OK · descriptive only",
        )

    if key == "volume_oi":
        return (
            f'{result.get("classification", "N/A")} · '
            f'Call {float(result.get("call_ratio")):.3f} / '
            f'Put {float(result.get("put_ratio")):.3f} · '
            f'{result.get("direction", "N/A")}',
            "OK",
        )

    if key == "oi_migration":
        elapsed = result.get("elapsed_seconds")
        elapsed_text = f' · {float(elapsed) / 60:.0f}m interval' if elapsed is not None else ""
        return (
            f'{result.get("classification", "N/A")} · '
            f'Call {float(result.get("call_shift")):+,.0f} / '
            f'Put {float(result.get("put_shift")):+,.0f}{elapsed_text}',
            "OK",
        )

    if key == "taker_activity":
        return (
            f'Sampled Buy {float(result.get("sampled_buy_volume")):,.2f} / '
            f'Sell {float(result.get("sampled_sell_volume")):,.2f} · '
            f'Balance {float(result.get("sampled_volume_balance")):+,.2f}',
            "OK · sampled, descriptive only",
        )

    return "N/A", "UNAVAILABLE · UNKNOWN_FILTER"


def render_options_filter_panel(payload):
    """Render the backend's validated results without accessing raw database data."""
    with st.expander("Options research filters · validated snapshot", expanded=False):
        if not isinstance(payload, dict) or payload.get("status") != "OK":
            selection = payload.get("selection", {}) if isinstance(payload, dict) else {}
            reason = selection.get("reason", "OPTIONS_FILTERS_UNAVAILABLE")
            st.caption(f"Research filters unavailable · {reason}")
            return

        selection = payload.get("selection", {})
        st.caption(
            f'Expiry {selection.get("expiry_date", "N/A")} · '
            f'Snapshot spot ${float(selection.get("spot_price")):,.2f} · '
            f'Snapshot {str(selection.get("snapshot_id", "N/A"))[:12]}'
        )
        filters = payload.get("filters", {})
        labels = (
            ("nearby_oi", "Nearby OI"),
            ("iv_skew", "IV Skew"),
            ("volume_oi", "24h Volume / OI"),
            ("oi_migration", "OI Migration"),
            ("taker_activity", "Sampled Taker Trade Activity"),
        )
        cards = []
        for key, label in labels:
            result = filters.get(key, {"status": "UNAVAILABLE", "reason": "MISSING_FILTER_RESULT"})
            summary, status_text = _format_options_filter_result(key, result)
            tone = "up" if str(status_text).startswith("OK") else "flat"
            cards.append(
                '<div class="card t-' + tone + '"><div class="k">' + _htmllib.escape(label) + '</div>'
                '<div class="v sm2">' + _htmllib.escape(str(summary)) + '</div>'
                '<div class="s">' + _htmllib.escape(str(status_text)) + '</div></div>'
            )
        st.markdown('<div class="grid g3">' + "".join(cards) + '</div>', unsafe_allow_html=True)
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
    """
    Replay completed 30m candles through the standalone Pine v7.2.2 engine.

    The state machine derives prior-cycle value from its own TPO profile, as
    TradingView does. The legacy manual-value arguments remain in the signature
    for call-site compatibility, but do not override Pine-derived state inputs.
    """
    if df.empty:
        return pd.DataFrame(), manual_valid, {}

    required = {"Date", "ShiftedDate", "open", "high", "low", "close"}
    missing = required.difference(df.columns)
    if missing:
        raise ValueError(f"AMT state replay missing required columns: {sorted(missing)}")

    replay_df = df.sort_values("Date").reset_index(drop=True).copy()
    candles = [
        Candle(
            open_time=row["Date"].to_pydatetime(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
        )
        for _, row in replay_df.iterrows()
    ]

    engine = PineStateMachine(PineConfig())
    results = engine.replay(candles)
    if len(results) != len(replay_df):
        raise RuntimeError("Pine state replay did not return one result per completed candle.")

    # Dashboard journal is scoped to the current 00:00 UTC / 05:30 IST AMT cycle.
    current_cycle = replay_df["ShiftedDate"].iloc[-1].strftime("%Y-%m-%d")
    current_indices = [
        i for i, value in enumerate(replay_df["ShiftedDate"])
        if value.strftime("%Y-%m-%d") == current_cycle
    ]
    if not current_indices:
        return pd.DataFrame(), manual_valid, {}

    journal = []
    candle_state_map = {}
    previous_state = None
    last_index = current_indices[-1]

    for i in current_indices:
        bar = replay_df.iloc[i]
        result = results[i]
        state_code = int(result.state_code)
        time_label = bar["Date"].strftime("%H:%M")
        candle_state_map[time_label] = state_code

        if previous_state is None or state_code != previous_state or i == last_index:
            journal.append({
                "Time": time_label,
                "Close": float(bar["close"]),
                "Code": state_code,
                "State Name": result.state_name,
                "Dev VAH": result.developing_vah if result.developing_vah is not None else np.nan,
                "Dev POC": result.developing_poc if result.developing_poc is not None else np.nan,
                "Dev VAL": result.developing_val if result.developing_val is not None else np.nan,
            })
        previous_state = state_code

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
                oi_usd = optional_float(ticker.get("oi_value_usd"))
                oi_contracts_raw = ticker.get("oi_contracts")
                if oi_contracts_raw is None:
                    oi_contracts_raw = ticker.get("oi")
                oi_contracts = optional_float(oi_contracts_raw)
                volume = optional_float(ticker.get("volume"))
                quotes = ticker.get("quotes") or {}
                bid_raw = quotes.get("best_bid")
                if bid_raw is None: bid_raw = ticker.get("best_bid")
                ask_raw = quotes.get("best_ask")
                if ask_raw is None: ask_raw = ticker.get("best_ask")
                bid = optional_float(bid_raw)
                ask = optional_float(ask_raw)
                mark = optional_float(ticker.get("mark_price"))
                greeks = ticker.get("greeks") or {}
                gamma = optional_float(greeks.get("gamma"))
                iv_raw = quotes.get("mark_iv")
                if iv_raw is None or iv_raw == "": iv_raw = quotes.get("ask_iv")
                iv_value = optional_float(iv_raw)
                iv = iv_value * 100 if iv_value is not None else None
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
@st.fragment(run_every="3m")
def _render_dashboard():
    df = fetch_amt_data()
    
    completed_df = df[df['IsClosed']].copy() if not df.empty else pd.DataFrame()
    live_df = df[~df['IsClosed']].copy() if not df.empty else pd.DataFrame()
    
    if not live_df.empty:
        latest_price = float(live_df['close'].iloc[-1])
    elif not completed_df.empty:
        latest_price = float(completed_df['close'].iloc[-1])
    else:
        latest_price = np.nan
    
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
    options_filter_results = fetch_options_filter_results()
    
    true_max_pain, straddle_price, atm_strike = np.nan, np.nan, np.nan
    call_oi_wall, put_oi_wall, call_gamma_wall, put_gamma_wall = None, None, None, None
    pcr, pcr_volume, atm_row = np.nan, np.nan, None
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
            temp_filt = filtered_options.copy()
            call_oi_wall = select_observed_max_row(temp_filt, "OI_USD_Call")
            put_oi_wall = select_observed_max_row(temp_filt, "OI_USD_Put")
            call_gamma_wall = select_observed_max_row(temp_filt, "GEX_Call")
            put_gamma_wall = select_observed_max_row(temp_filt, "GEX_Put")
            total_calls_contracts = strict_sum(temp_filt["OI_Contracts_Call"])
            total_puts_contracts = strict_sum(temp_filt["OI_Contracts_Put"])
            pcr = safe_ratio(total_puts_contracts, total_calls_contracts)
            total_calls_vol = strict_sum(temp_filt["Volume_Call"])
            total_puts_vol = strict_sum(temp_filt["Volume_Put"])
            pcr_volume = safe_ratio(total_puts_vol, total_calls_vol)
            true_max_pain = calculate_max_pain(raw_chain_df, is_chain_complete)
    
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
    # RENDER UI — VISUAL-FIRST COCKPIT (presentation only)
    # Every variable used below is a read-only feed from the calculation block above.
    # ==========================================
    _render_stamp = datetime.datetime.now(IST_TZ).strftime("%d %b %Y · %H:%M:%S IST")
    st.markdown(
        '<div class="topbar"><div>'
        '<div class="brand"><em>₿</em> BTC AMT &amp; DERIVATIVES OBSERVATION COCKPIT</div>'
        '<div class="tag">AMT first · current auction behaviour · derivatives as context</div>'
        '</div>'
        '<div class="live"><span class="dot"></span>RENDERED ' + _render_stamp + '</div></div>',
        unsafe_allow_html=True,
    )

    previous_ui_state = st.session_state.get("fast_prev_amt_state")
    previous_ui_name = state_names.get(previous_ui_state) if previous_ui_state is not None else None

    dev_vah_ui = journal_df_check.iloc[-1]["Dev VAH"] if not journal_df_check.empty else np.nan
    dev_poc_ui = journal_df_check.iloc[-1]["Dev POC"] if not journal_df_check.empty else np.nan
    dev_val_ui = journal_df_check.iloc[-1]["Dev VAL"] if not journal_df_check.empty else np.nan

    # ---------- UI helpers (formatting / HTML only) ----------
    def _isnum(value):
        try:
            return value is not None and not pd.isna(value) and bool(np.isfinite(float(value)))
        except (TypeError, ValueError):
            return False

    def _esc(value):
        return _htmllib.escape(str(value))

    def _ui_num(value, fmt=",.0f", prefix="$", suffix=""):
        if not _isnum(value):
            return "N/A"
        return prefix + format(value, fmt) + suffix

    def _ui_section(title, hint=""):
        st.markdown(
            '<div class="sec"><span class="bar"></span><span class="st">' + title + '</span>'
            '<span class="hint">' + hint + '</span></div>',
            unsafe_allow_html=True,
        )

    def _ui_card(label, value, sub="", tone="flat", tint=False, small=False):
        sub_html = '<div class="s">' + sub + '</div>' if sub else ""
        return ('<div class="card t-' + tone + (' tint' if tint else '') + '"><div class="k">' + label + '</div>'
                '<div class="v' + (' sm' if small else '') + '">' + value + '</div>' + sub_html + '</div>')

    def _ui_grid(cards, cls):
        return '<div class="grid ' + cls + '">' + "".join(cards) + '</div>'

    def signed_distance_text(value, reference):
        if not _isnum(value) or not _isnum(reference):
            return "N/A"
        delta_value = value - reference
        sign = "+" if delta_value >= 0 else "-"
        return sign + "$" + format(abs(delta_value), ",.0f")

    _UI_STATE_TONE = {2: "up", 4: "up", 6: "up", 8: "up", 3: "down", 5: "down", 7: "down", 9: "down"}
    def _ui_state_tone(code):
        if code in (0, 1):
            return "flat"
        return _UI_STATE_TONE.get(code, "warn")

    state_tone = _ui_state_tone(current_amt_state)
    loc_tone = {"Above VAH": "up", "Below VAL": "down", "Inside VA": "warn"}.get(loc, "flat")
    _TONE_HEX = {"up": "#00E676", "down": "#FF5252", "warn": "#FFC107", "flat": "#5A6573"}

    # Colour semantics: BLUE = prior value · PURPLE = developing value · AMBER = POC
    #                   GREEN/RED = call/put options · WHITE = max pain · YELLOW = price
    C_PRICE, C_PRIOR, C_POC, C_DEV = "#FFD600", "#4FC3F7", "#FFC107", "#B388FF"
    C_CALL, C_PUT, C_PAIN, C_MUTE = "#00E676", "#FF5252", "#E6EDF3", "#8B96A5"
    _MONO = "JetBrains Mono, Menlo, Consolas, monospace"
    _has_px = latest_price > 0

    # ---------- unified level book (every reference price in one list) ----------
    levels = []
    def _add_level(label, short, value, color, dash="solid", width=1.5, group="value"):
        if _isnum(value):
            levels.append({"label": label, "short": short, "price": float(value), "color": color,
                           "dash": dash, "width": width, "group": group, "off": False})

    _add_level("Prior VAH", "pVAH", final_vah, C_PRIOR, "solid", 1.6)
    _add_level("Prior POC", "pPOC", final_poc, C_POC, "solid", 2.2)
    _add_level("Prior VAL", "pVAL", final_val, C_PRIOR, "solid", 1.6)
    _add_level("Developing VAH", "dVAH", dev_vah_ui, C_DEV, "dash", 1.3)
    _add_level("Developing POC", "dPOC", dev_poc_ui, C_DEV, "dot", 1.9)
    _add_level("Developing VAL", "dVAL", dev_val_ui, C_DEV, "dash", 1.3)
    if call_gamma_wall is not None:
        _add_level("Call GEX wall", "CALL γ", call_gamma_wall["Strike"], C_CALL, "dash", 1.4, "opt")
    if put_gamma_wall is not None:
        _add_level("Put GEX wall", "PUT γ", put_gamma_wall["Strike"], C_PUT, "dash", 1.4, "opt")
    _add_level("Max pain", "PAIN", true_max_pain, C_PAIN, "dot", 1.4, "opt")
    straddle_hi = straddle_lo = np.nan
    if straddle_valid and _has_px:
        straddle_hi, straddle_lo = latest_price + straddle_price, latest_price - straddle_price
        _add_level("Straddle ▲ implied", "STRD ▲", straddle_hi, "#9AA5B1", "dot", 1.0, "opt")
        _add_level("Straddle ▼ implied", "STRD ▼", straddle_lo, "#9AA5B1", "dot", 1.0, "opt")

    # ---------- chart window + price range (options levels only widen the view if they are near) ----------
    win_df = df.tail(96).copy() if not df.empty else pd.DataFrame()
    _base = [latest_price] if _has_px else []
    _base += [lv["price"] for lv in levels if lv["group"] == "value"]
    if not win_df.empty:
        _recent = win_df.tail(48)
        _base += [float(_recent["low"].min()), float(_recent["high"].max())]
    chart_ok = _has_px and len(_base) > 1 and not win_df.empty
    y_lo = y_hi = 0.0
    if chart_ok:
        _b_lo, _b_hi = min(_base), max(_base)
        _b_span = max(_b_hi - _b_lo, latest_price * 0.004)
        for lv in levels:
            if lv["group"] == "opt":
                lv["off"] = not (_b_lo - _b_span * 0.6 <= lv["price"] <= _b_hi + _b_span * 0.6)
        _view = _base + [lv["price"] for lv in levels if lv["group"] == "opt" and not lv["off"]]
        y_lo, y_hi = min(_view), max(_view)
        _pad = max((y_hi - y_lo) * 0.07, latest_price * 0.002)
        y_lo -= _pad
        y_hi += _pad

    # ---------- nearest levels above / below price ----------
    _above = [lv for lv in levels if _has_px and lv["price"] > latest_price]
    _below = [lv for lv in levels if _has_px and lv["price"] < latest_price]
    nearest_up = min(_above, key=lambda lv: lv["price"]) if _above else None
    nearest_dn = max(_below, key=lambda lv: lv["price"]) if _below else None

    def _near_row(arrow, lv, tone):
        if lv is None:
            return '<div class="nr"><span class="ar flat">' + arrow + '</span><span class="nn flat">none in book</span><span></span><span></span></div>'
        dist = lv["price"] - latest_price
        return ('<div class="nr"><span class="ar ' + tone + '">' + arrow + '</span>'
                '<span class="nn" style="color:' + lv["color"] + '">' + _esc(lv["label"]) + '</span>'
                '<span class="np">' + _ui_num(lv["price"]) + '</span>'
                '<span class="nd ' + tone + '">' + format(dist, "+,.0f") + ' · ' + format(dist / latest_price * 100, "+.2f") + '%</span></div>')

    # ---------- range-bar / diverging-bar builders ----------
    def _rb(lo, hi, poc, price, s_lo, s_hi, color):
        span = (s_hi - s_lo) or 1.0
        def pos(x):
            return max(0.0, min(100.0, (x - s_lo) / span * 100.0))
        parts = ['<div class="rb"><div class="trk"></div>']
        if _isnum(lo) and _isnum(hi):
            parts.append('<div class="bnd" style="left:' + format(pos(lo), ".2f") + '%;width:' + format(max(pos(hi) - pos(lo), 0.8), ".2f") + '%;background:' + color + '"></div>')
        if _isnum(poc):
            parts.append('<div class="poc" style="left:calc(' + format(pos(poc), ".2f") + '% - 1px)"></div>')
        if _has_px and price is not None:
            parts.append('<div class="px" style="left:calc(' + format(pos(price), ".2f") + '% - 1px)"></div>')
        parts.append('</div>')
        return "".join(parts)

    def _db(value, scale, pos_color, neg_color):
        if not _isnum(value) or not scale:
            return '<div class="db"></div>'
        frac = max(-1.0, min(1.0, float(value) / scale)) * 50.0
        if frac >= 0:
            return '<div class="db"><div class="f" style="left:50%;width:' + format(frac, ".2f") + '%;background:' + pos_color + '"></div></div>'
        return '<div class="db"><div class="f" style="right:50%;width:' + format(-frac, ".2f") + '%;background:' + neg_color + '"></div></div>'

    # ---------- 1 · HERO STRIP (3-second orientation) ----------
    if loc == "Above VAH":
        loc_sub = signed_distance_text(latest_price, final_vah) + " vs prior VAH"
    elif loc == "Below VAL":
        loc_sub = signed_distance_text(latest_price, final_val) + " vs prior VAL"
    elif loc == "Inside VA":
        loc_sub = "Between prior VAL and VAH"
    else:
        loc_sub = ""

    _loc_scale = [x for x in (final_val, final_vah, latest_price if _has_px else None) if _isnum(x)]
    _loc_bar = ""
    if len(_loc_scale) >= 2 and _isnum(final_val) and _isnum(final_vah):
        _ls_lo, _ls_hi = min(_loc_scale), max(_loc_scale)
        _ls_pad = max((_ls_hi - _ls_lo) * 0.15, 1.0)
        _loc_bar = '<div style="margin-top:6px;--c:' + C_PRIOR + '">' + _rb(final_val, final_vah, final_poc, latest_price if _has_px else None, _ls_lo - _ls_pad, _ls_hi + _ls_pad, C_PRIOR) + '</div>'

    hero_html = (
        '<div class="hero4">'
        '<div class="tile t-info"><div class="k">BTC / USDT</div><div class="big px">' + _ui_num(latest_price, ",.2f") + '</div>'
        '<div class="sub">BINANCE · SPOT · 30M</div></div>'
        '<div class="tile t-' + state_tone + '"><div class="k">AUCTION (LAST CLOSE)</div><div class="big">' + _esc(current_amt_name) + '</div>'
        '<div class="sub">Independent code ' + str(current_amt_state) + '</div></div>'
        '<div class="tile t-' + loc_tone + '"><div class="k">LIVE PRICE LOCATION</div><div class="big">' + _esc(loc) + '</div>'
        '<div class="sub">' + _esc(loc_sub) + '</div>' + _loc_bar + '</div>'
        '<div class="tile t-flat"><div class="k">Nearest levels</div>'
        + _near_row("▲", nearest_up, "up") + _near_row("▼", nearest_dn, "down") + '</div>'
        '</div>'
    )
    st.markdown(hero_html, unsafe_allow_html=True)

    # ---------- 2 · SINCE LAST REFRESH (UI-only snapshot comparison; same keys as before) ----------
    current_ui_snapshot = {
        "state": current_amt_state,
        "dev_vah": float(dev_vah_ui) if not pd.isna(dev_vah_ui) else None,
        "dev_poc": float(dev_poc_ui) if not pd.isna(dev_poc_ui) else None,
        "dev_val": float(dev_val_ui) if not pd.isna(dev_val_ui) else None,
    }
    previous_ui_snapshot = st.session_state.get("fast_previous_snapshot")

    def _level_change_chip(label, key, color):
        try:
            cur = current_ui_snapshot.get(key)
            prev = previous_ui_snapshot.get(key)
            cur = float(cur) if cur is not None and not pd.isna(cur) else None
            prev = float(prev) if prev is not None and not pd.isna(prev) else None
        except (ValueError, TypeError):
            cur = prev = None
        if cur is None or prev is None:
            return '<span class="dchip"><span class="dk">' + label + '</span><span class="dv">N/A</span></span>'
        diff = cur - prev
        if diff == 0:
            dd = '<span class="dd flat">▬ unchanged</span>'
        else:
            dd = '<span class="dd ' + ("up" if diff > 0 else "down") + '">' + ("▲ " if diff > 0 else "▼ ") + "$" + format(abs(diff), ",.0f") + '</span>'
        return ('<span class="dchip" style="border-left:3px solid ' + color + '"><span class="dk">' + label + '</span>'
                '<span class="dv">$' + format(cur, ",.0f") + '</span>' + dd + '</span>')

    if previous_ui_snapshot is None:
        delta_html = '<div class="dl"><span class="lab">Since last refresh</span><span class="dchip"><span class="dk">Baseline captured · changes appear after the next refresh</span></span></div>'
    else:
        if previous_ui_snapshot.get("state") != current_amt_state:
            state_chip = ('<span class="dchip t-' + state_tone + '" style="border-left:3px solid var(--accent)"><span class="dk">State</span>'
                          '<span class="dv"><span class="pv">' + _esc(state_names.get(previous_ui_snapshot.get("state"), "UNKNOWN")) + '</span>'
                          '<span class="ar">→</span>' + _esc(current_amt_name) + '</span></span>')
        else:
            state_chip = '<span class="dchip"><span class="dk">State</span><span class="dv">' + _esc(current_amt_name) + '</span><span class="dd flat">▬ unchanged</span></span>'
        delta_html = ('<div class="dl"><span class="lab">Since last refresh</span>' + state_chip
                      + _level_change_chip("Dev VAH", "dev_vah", C_DEV)
                      + _level_change_chip("Dev POC", "dev_poc", C_POC)
                      + _level_change_chip("Dev VAL", "dev_val", C_DEV) + '</div>')
    st.markdown(delta_html, unsafe_allow_html=True)

    st.session_state["fast_previous_snapshot"] = current_ui_snapshot
    st.session_state["fast_prev_amt_state"] = current_amt_state

    # ---------- 3 · MAIN COCKPIT: one price axis for candles + value + options ----------
    def _build_cockpit_figure():
        plot_x = win_df["Date"].dt.tz_localize(None)
        win = win_df.assign(_x=plot_x)
        today_key = df["ShiftedDate"].iloc[-1].strftime("%Y-%m-%d")
        today_mask = win["ShiftedDate"].dt.strftime("%Y-%m-%d") == today_key
        state_map = journal_res[2] if (journal_res and len(journal_res) > 2 and journal_res[2]) else {}

        fig = make_subplots(
            rows=2, cols=2, shared_xaxes=True, column_widths=[0.77, 0.23], row_heights=[0.93, 0.07],
            horizontal_spacing=0.01, vertical_spacing=0.012,
        )

        # --- layer 0: value / options bands (behind everything) ---
        if _isnum(final_val) and _isnum(final_vah):
            fig.add_shape(type="rect", xref="paper", yref="y", x0=0, x1=1, y0=final_val, y1=final_vah,
                          fillcolor="rgba(79,195,247,0.075)", line_width=0, layer="below")
        if _isnum(straddle_hi) and y_lo <= straddle_lo and straddle_hi <= y_hi:
            fig.add_shape(type="rect", xref="x2 domain", yref="y", x0=0, x1=1, y0=straddle_lo, y1=straddle_hi,
                          fillcolor="rgba(154,165,177,0.07)", line_width=0, layer="below")

        # --- candles (closed solid, forming hollow) ---
        closed = win[win["IsClosed"]]
        forming = win[~win["IsClosed"]]
        if not closed.empty:
            fig.add_trace(go.Candlestick(
                x=closed["_x"], open=closed["open"], high=closed["high"], low=closed["low"], close=closed["close"],
                increasing=dict(line=dict(color=C_CALL, width=1), fillcolor="rgba(0,230,118,0.55)"),
                decreasing=dict(line=dict(color=C_PUT, width=1), fillcolor="rgba(255,82,82,0.55)"),
                name="BTC 30m", showlegend=False,
            ), row=1, col=1)
        if not forming.empty:
            fig.add_trace(go.Candlestick(
                x=forming["_x"], open=forming["open"], high=forming["high"], low=forming["low"], close=forming["close"],
                increasing=dict(line=dict(color="#FFFFFF", width=1.4), fillcolor="rgba(0,0,0,0)"),
                decreasing=dict(line=dict(color="#FFFFFF", width=1.4), fillcolor="rgba(0,0,0,0)"),
                name="Forming", showlegend=False,
            ), row=1, col=1)

        # --- auction-state ribbon (current AMT cycle only) ---
        rib = win[today_mask & win["Date"].dt.strftime("%H:%M").isin(list(state_map.keys()))]
        if not rib.empty:
            codes = [int(state_map[t]) for t in rib["Date"].dt.strftime("%H:%M")]
            fig.add_trace(go.Bar(
                x=rib["_x"], y=[1] * len(codes), width=1800000 * 0.94,
                marker=dict(color=[_TONE_HEX[_ui_state_tone(c)] for c in codes], line=dict(width=0)),
                customdata=[state_names.get(c, "UNKNOWN") for c in codes],
                hovertemplate="%{x|%H:%M} · %{customdata}<extra></extra>", showlegend=False,
            ), row=2, col=1)

        # --- options OI profile on the same price axis (puts ◂ | ▸ calls) ---
        oi_view = pd.DataFrame()
        if filtered_options is not None and not filtered_options.empty:
            oi_view = filtered_options[["Strike", "OI_USD_Call", "OI_USD_Put"]].copy()
            for col_ in oi_view.columns:
                oi_view[col_] = pd.to_numeric(oi_view[col_], errors="coerce")
            oi_view = oi_view.dropna(subset=["Strike"])
            oi_view = oi_view[(oi_view["Strike"] >= y_lo) & (oi_view["Strike"] <= y_hi)].sort_values("Strike")
        x_max = 1.0
        if not oi_view.empty:
            strikes = oi_view["Strike"].tolist()
            steps = np.diff(sorted(set(strikes)))
            bar_w = float(np.median(steps)) * 0.72 if len(steps) else latest_price * 0.002
            call_m = (oi_view["OI_USD_Call"] / 1e6).tolist()
            put_m = (oi_view["OI_USD_Put"] / 1e6).tolist()
            call_max = pd.to_numeric(pd.Series(call_m), errors="coerce").max(skipna=True)
            put_max = pd.to_numeric(pd.Series(put_m), errors="coerce").max(skipna=True)
            valid_maxima = [float(value) for value in (call_max, put_max) if pd.notna(value)]
            x_max = max(valid_maxima + [0.001]) * 1.38
            top_call = float(call_max) if pd.notna(call_max) else np.nan
            top_put = float(put_max) if pd.notna(put_max) else np.nan
            cw = float(call_gamma_wall["Strike"]) if call_gamma_wall is not None else None
            pw = float(put_gamma_wall["Strike"]) if put_gamma_wall is not None else None
            call_cols = ["rgba(0,230,118,%s)" % ("0.95" if pd.notna(v) and v == top_call and v > 0 else "0.5") for v in call_m]
            put_cols = ["rgba(255,82,82,%s)" % ("0.95" if pd.notna(v) and v == top_put and v > 0 else "0.5") for v in put_m]
            call_line = ["#FFFFFF" if cw is not None and s == cw else "rgba(0,0,0,0)" for s in strikes]
            put_line = ["#FFFFFF" if pw is not None and s == pw else "rgba(0,0,0,0)" for s in strikes]
            fig.add_trace(go.Bar(
                y=strikes, x=call_m, orientation="h", width=bar_w, showlegend=False,
                marker=dict(color=call_cols, line=dict(color=call_line, width=1.6)),
                text=[("$%.1fM" % v) if (pd.notna(v) and v == top_call and v > 0) else "" for v in call_m],
                textposition="outside", cliponaxis=False, textfont=dict(size=10, color=C_CALL, family=_MONO),
                hovertemplate="Strike $%{y:,.0f}<br>Call OI $%{x:.2f}M<extra></extra>",
            ), row=1, col=2)
            fig.add_trace(go.Bar(
                y=strikes, x=[-v for v in put_m], orientation="h", width=bar_w, showlegend=False,
                marker=dict(color=put_cols, line=dict(color=put_line, width=1.6)),
                text=[("$%.1fM" % v) if (pd.notna(v) and v == top_put and v > 0) else "" for v in put_m],
                textposition="outside", cliponaxis=False, textfont=dict(size=10, color=C_PUT, family=_MONO),
                customdata=put_m, hovertemplate="Strike $%{y:,.0f}<br>Put OI $%{customdata:.2f}M<extra></extra>",
            ), row=1, col=2)

        # --- level lines across both panels + right-edge labels (de-overlapped) ---
        labels = []
        for lv in levels:
            if lv["off"] or not (y_lo <= lv["price"] <= y_hi):
                continue
            fig.add_shape(type="line", xref="paper", yref="y", x0=0, x1=1, y0=lv["price"], y1=lv["price"],
                          line=dict(color=lv["color"], width=lv["width"], dash=lv["dash"]), layer="below")
            labels.append({"y": lv["price"], "text": lv["short"] + " " + format(lv["price"], ",.0f"), "color": lv["color"], "price": False})
        fig.add_shape(type="line", xref="paper", yref="y", x0=0, x1=1, y0=latest_price, y1=latest_price,
                      line=dict(color=C_PRICE, width=1.8, dash="dash"), layer="above")
        labels.append({"y": latest_price, "text": "CMP " + format(latest_price, ",.0f"), "color": "#0E1117", "price": True})

        labels.sort(key=lambda item: item["y"])
        min_gap = (y_hi - y_lo) * 0.036
        for i in range(1, len(labels)):
            if labels[i]["y"] - labels[i - 1]["y"] < min_gap:
                labels[i]["y"] = labels[i - 1]["y"] + min_gap
        for item in labels:
            fig.add_annotation(
                xref="paper", yref="y", x=1.006, y=item["y"], xanchor="left", yanchor="middle", showarrow=False,
                text=("<b>" + item["text"] + "</b>") if item["price"] else item["text"],
                font=dict(size=10.5, color=item["color"], family=_MONO),
                bgcolor=C_PRICE if item["price"] else "rgba(0,0,0,0)", borderpad=2,
            )

        # --- session-open divider ---
        if today_mask.any() and not today_mask.all():
            open_x = win.loc[today_mask, "_x"].iloc[0]
            fig.add_shape(type="line", xref="x", yref="y domain", x0=open_x, x1=open_x, y0=0, y1=1,
                          line=dict(color="rgba(255,255,255,0.28)", width=1, dash="dot"))
            fig.add_annotation(xref="x", yref="y domain", x=open_x, y=0.015, text="SESSION OPEN 05:30", showarrow=False,
                               xanchor="left", yanchor="bottom", font=dict(size=9, color="#8B96A5", family=_MONO))

        # --- panel captions ---
        fig.add_annotation(xref="x domain", yref="y domain", x=0, y=1.0, yanchor="bottom", xanchor="left", showarrow=False,
                           text="30M CANDLES · IST · HOLLOW = FORMING", font=dict(size=9.5, color="#8B96A5", family=_MONO))
        fig.add_annotation(xref="x2 domain", yref="y2 domain", x=0.5, y=1.0, yanchor="bottom", xanchor="center", showarrow=False,
                           text="◂ PUT OI  ·  CALL OI ▸", font=dict(size=9.5, color="#8B96A5", family=_MONO))
        fig.add_annotation(xref="x3 domain", yref="y3 domain", x=0, y=0.5, xanchor="right", yanchor="middle", showarrow=False,
                           text="AMT", font=dict(size=8.5, color="#6B7683", family=_MONO))

        # --- axes + theme ---
        last_x = win["_x"].iloc[-1]
        fig.update_xaxes(range=[win["_x"].iloc[0], last_x + pd.Timedelta(minutes=30 * 3)], rangeslider_visible=False,
                         showgrid=False, linecolor="rgba(255,255,255,0.10)", tickformat="%H:%M\n%d %b",
                         tickfont=dict(size=10, family=_MONO, color="#8B96A5"), row=2, col=1)
        fig.update_xaxes(rangeslider_visible=False, showgrid=False, showticklabels=False, row=1, col=1)
        fig.update_yaxes(range=[y_lo, y_hi], tickformat="$,.0f", tickfont=dict(size=10.5, family=_MONO, color="#8B96A5"),
                         gridcolor="rgba(255,255,255,0.05)", zeroline=False, linecolor="rgba(255,255,255,0.10)", row=1, col=1)
        fig.update_yaxes(range=[y_lo, y_hi], showticklabels=False, gridcolor="rgba(255,255,255,0.05)", zeroline=False, row=1, col=2)
        fig.update_xaxes(range=[-x_max, x_max], showticklabels=False, showgrid=False, zeroline=True,
                         zerolinecolor="rgba(255,255,255,0.28)", zerolinewidth=1, row=1, col=2)
        fig.update_yaxes(visible=False, range=[0, 1], row=2, col=1)
        fig.update_xaxes(visible=False, row=2, col=2)
        fig.update_yaxes(visible=False, row=2, col=2)
        fig.update_layout(
            template="plotly_dark", height=650, barmode="relative", bargap=0.05,
            margin=dict(l=64, r=122, t=26, b=34),
            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
            font=dict(color="#C9D1D9", family="Inter, -apple-system, Segoe UI, sans-serif", size=12),
            hoverlabel=dict(bgcolor="#1A1E24", bordercolor="#2A313A", font=dict(color="#E6EDF3", family=_MONO)),
            hovermode="closest", dragmode=False, showlegend=False,
        )
        return fig

    def _legend_html():
        def item(label, color, kind=""):
            return '<span style="--c:' + color + '"><i class="' + kind + '"></i>' + label + '</span>'
        return ('<div class="lg">'
                + item("Prior value (p)", C_PRIOR) + item("Prior POC", C_POC) + item("Developing value (d)", C_DEV, "dash")
                + item("Call γ-wall / OI", C_CALL, "dash") + item("Put γ-wall / OI", C_PUT, "dash")
                + item("Max pain", C_PAIN, "dot") + item("Straddle range", "#9AA5B1", "blk")
                + '<span style="--c:#FFFFFF"><i class="blk" style="background:transparent;border:1.5px solid #fff;opacity:1"></i>Outlined bar = GEX wall</span>'
                + '</div>')

    def _level_stack_html():
        if not _has_px or not levels:
            return '<div class="mod"><div class="mh"><span class="mt">Level stack</span></div><div class="foot">Reference levels unavailable.</div></div>'
        rows = [{"cmp": True, "price": latest_price}] + levels
        rows.sort(key=lambda r: -r["price"])
        max_d = max(abs(lv["price"] - latest_price) for lv in levels) or 1.0
        out = []
        for r in rows:
            if r.get("cmp"):
                out.append('<div class="lv cmp"><span class="sw"></span><span class="nm">CMP · PRICE NOW</span>'
                           '<span class="pr">' + _ui_num(latest_price, ",.0f") + '</span><span class="ds"></span></div>')
                continue
            dist = r["price"] - latest_price
            tone = "up" if dist > 0 else "down"
            out.append(
                '<div class="lv"><span class="bar" style="width:' + format(abs(dist) / max_d * 100.0, ".1f") + '%;background:' + r["color"] + '"></span>'
                '<span class="sw" style="background:' + r["color"] + '"></span>'
                '<span class="nm">' + _esc(r["label"]) + ('<span class="off">off-chart</span>' if r["off"] else "") + '</span>'
                '<span class="pr">' + _ui_num(r["price"]) + '</span>'
                '<span class="ds ' + tone + '">' + ("+" if dist > 0 else "−") + "$" + format(abs(dist), ",.0f")
                + '<i>' + format(dist / latest_price * 100.0, "+.1f") + '%</i></span></div>'
            )
        return ('<div class="mod"><div class="mh"><span class="mt">Level stack</span><span class="mx">sorted by price · bar = distance</span></div>'
                '<div class="ls">' + "".join(out) + '</div></div>')

    # value-area migration module (prior vs developing vs forming, one shared scale)
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
        _va_pill, _va_cls = "▼ " + va_expansion_value + " downward", "down"
    elif va_expansion_label == "Upward":
        _va_pill, _va_cls = "▲ " + va_expansion_value + " upward", "up"
    else:
        _va_pill, _va_cls = "VA " + va_expansion_value, ""

    def _migration_html():
        sets = [
            ("Prior", final_val, final_poc, final_vah, C_PRIOR),
            ("Developing", dev_val_ui, dev_poc_ui, dev_vah_ui, C_DEV),
            ("Forming", provisional_val, provisional_poc, provisional_vah, "#9575CD"),
        ]
        pts = [x for (_, a, b, c, _) in sets for x in (a, b, c) if _isnum(x)]
        if _has_px:
            pts.append(latest_price)
        if len(pts) < 2:
            return '<div class="mod"><div class="mh"><span class="mt">Value migration</span></div><div class="foot">Value areas unavailable.</div></div>'
        s_lo, s_hi = min(pts), max(pts)
        s_pad = max((s_hi - s_lo) * 0.08, 1.0)
        s_lo -= s_pad
        s_hi += s_pad
        rows = []
        for name, v_lo, v_poc, v_hi, color in sets:
            nums = " · ".join(_ui_num(x, ",.0f", "") for x in (v_lo, v_poc, v_hi))
            width_txt = _ui_num(v_hi - v_lo) if _isnum(v_hi) and _isnum(v_lo) else "N/A"
            rows.append('<div class="mr" style="grid-template-columns:78px 1fr auto"><div class="rl">' + name + '</div>'
                        '<div>' + _rb(v_lo, v_hi, v_poc, latest_price if _has_px else None, s_lo, s_hi, color)
                        + '<div class="nums-s">VAL · POC · VAH &nbsp;' + nums + '</div></div>'
                        '<div class="rv"><small>width</small>' + width_txt + '</div></div>')
        return ('<div class="mod"><div class="mh"><span class="mt">Value migration</span>'
                '<span class="pill ' + _va_cls + '">' + _esc(_va_pill) + '</span></div>' + "".join(rows)
                + '<div class="foot" style="margin-top:6px">Forming includes the live 30m candle and is informational only; classification uses completed candles.</div></div>')

    if chart_ok:
        col_chart, col_side = st.columns([3.15, 1.05])
        with col_chart:
            st.markdown(_legend_html(), unsafe_allow_html=True)
            st.plotly_chart(_build_cockpit_figure(), use_container_width=True, theme=None, config={"displayModeBar": False})
        with col_side:
            st.markdown(_level_stack_html(), unsafe_allow_html=True)
            st.markdown(_migration_html(), unsafe_allow_html=True)
    else:
        st.info("Price data unavailable; the cockpit chart cannot be drawn.")
        col_a, col_b = st.columns(2)
        col_a.markdown(_level_stack_html(), unsafe_allow_html=True)
        col_b.markdown(_migration_html(), unsafe_allow_html=True)

    # ---------- 4 · DERIVATIVES CONTEXT ----------
    oi_context = fetch_futures_oi_context()
    opt_col, fut_col = st.columns(2)

    with opt_col:
        _exp_txt = ("EXPIRY " + str(loaded_expiry)) if loaded_expiry else ""
        if not options_df.empty and atm_row is not None:
            rows = []
            if _isnum(pcr):
                put_share = pcr / (1.0 + pcr) * 100.0
                rows.append('<div class="mr"><div class="rl">Put / Call OI<br>±10% of spot</div>'
                            '<div><div class="sp"><i style="width:' + format(100.0 - put_share, ".1f") + '%;background:' + C_CALL + '"></i>'
                            '<i style="width:' + format(put_share, ".1f") + '%;background:' + C_PUT + '"></i></div>'
                            '<div class="nums-s">CALLS ◂ ▸ PUTS</div></div>'
                            '<div class="rv">' + format(pcr, ".2f") + '<small>PCR</small></div></div>')
            else:
                rows.append('<div class="mr"><div class="rl">Put / Call OI</div><div></div><div class="rv">N/A</div></div>')
            if _isnum(true_max_pain) and _has_px:
                mp_dist = true_max_pain - latest_price
                mp_sub = ("Δ vs dev POC " + format(mp_poc_distance, "+,.0f")) if _isnum(mp_poc_distance) else "vs price"
                rows.append('<div class="mr"><div class="rl">Max pain<br>vs price</div>'
                            '<div>' + _db(mp_dist, latest_price * 0.05, C_PAIN, C_PAIN) + '<div class="nums-s">◂ below · ±5% scale · above ▸</div></div>'
                            '<div class="rv">' + _ui_num(true_max_pain) + '<small>' + format(mp_dist, "+,.0f") + ' · ' + _esc(mp_sub) + '</small></div></div>')
            else:
                rows.append('<div class="mr"><div class="rl">Max pain</div><div></div><div class="rv">N/A</div></div>')
            if straddle_valid:
                ratio_txt = (format(straddle_ratio, ".2f") + "× prior VA width") if _isnum(straddle_ratio) else "VA width n/a"
                _sv = [x for x in (prior_va_width, straddle_price * 2.0) if _isnum(x)]
                _s_max = max(_sv) if _sv else 1.0
                _va_bar = ('<div style="height:6px;border-radius:3px;background:' + C_PRIOR + ';opacity:.55;width:' + format((prior_va_width / _s_max) * 100.0, ".1f") + '%"></div>') if _isnum(prior_va_width) else ""
                _st_bar = '<div style="height:6px;border-radius:3px;background:#9AA5B1;margin-top:4px;width:' + format((straddle_price * 2.0 / _s_max) * 100.0, ".1f") + '%"></div>'
                rows.append('<div class="mr"><div class="rl">ATM straddle<br>vs prior VA</div>'
                            '<div>' + _va_bar + _st_bar + '<div class="nums-s">BLUE = prior VA width · GREY = straddle × 2</div></div>'
                            '<div class="rv">±' + _ui_num(straddle_price) + '<small>' + _esc(ratio_txt) + '</small></div></div>')
            wall_chips = (
                '<div class="chipr">'
                '<span class="wchip" style="--c:' + C_CALL + '"><span class="wk">Call GEX wall</span><span class="wv" style="color:' + C_CALL + '">' + _esc(call_strike_text) + '</span></span>'
                '<span class="wchip" style="--c:' + C_PUT + '"><span class="wk">Put GEX wall</span><span class="wv" style="color:' + C_PUT + '">' + _esc(put_strike_text) + '</span></span>'
                '</div>'
            )
            st.markdown('<div class="mod"><div class="mh"><span class="mt">Options positioning</span><span class="mx">' + _esc(_exp_txt) + '</span></div>'
                        + "".join(rows) + wall_chips + '</div>', unsafe_allow_html=True)
        else:
            st.markdown('<div class="mod"><div class="mh"><span class="mt">Options positioning</span></div>'
                        '<div class="foot">Options context unavailable or ATM quote validation did not pass. Missing values are not treated as zero.</div></div>',
                        unsafe_allow_html=True)

    with fut_col:
        rows = []
        _oi_ok = oi_context.get("status") == "OK"
        if _oi_ok:
            oi_change = oi_context.get("change_pct")
            if oi_change is not None:
                rows.append('<div class="mr"><div class="rl">Aggregate OI<br>change</div>'
                            '<div>' + _db(oi_change, 2.0, C_PRIOR, C_PRIOR) + '<div class="nums-s">◂ falling · ±2% scale · rising ▸ (context only)</div></div>'
                            '<div class="rv">' + format(oi_change, "+.2f") + '%<small>' + _esc(oi_context.get("direction", "N/A")) + '</small></div></div>')
            else:
                rows.append('<div class="mr"><div class="rl">Aggregate OI<br>change</div><div class="nums-s">No comparable baseline</div><div class="rv">N/A</div></div>')
            rows.append('<div class="mr"><div class="rl">Aggregate OI</div><div></div>'
                        '<div class="rv">$' + format(oi_context["oi_usd"] / 1e6, ".2f") + 'M</div></div>')
        else:
            rows.append('<div class="mr"><div class="rl">Aggregate OI</div><div class="nums-s">Futures OI context unavailable; nothing is inferred or zero-filled.</div><div class="rv">N/A</div></div>')
        rows.append('<div class="mr"><div class="rl">Delta OI</div><div></div><div class="rv">'
                    + (("$" + format(oi_usd / 1e6, ".2f") + "M") if oi_usd is not None else "N/A") + '</div></div>')
        basis_status = "Discount" if sp_prem is not None and sp_prem < 0 else "Premium" if sp_prem is not None and sp_prem > 0 else "Unavailable"
        _basis_cls = {"Discount": "down", "Premium": "up"}.get(basis_status, "flat")
        rows.append('<div class="mr"><div class="rl">Perp basis</div>'
                    '<div>' + _db(sp_prem, 0.10, C_CALL, C_PUT) + '<div class="nums-s">◂ discount · ±0.10% scale · premium ▸</div></div>'
                    '<div class="rv ' + _basis_cls + '">' + (format(sp_prem, ".4f") + "%" if sp_prem is not None else "N/A") + '<small>' + basis_status + '</small></div></div>')
        _fut_hint = ""
        if _oi_ok:
            _iv = oi_context.get("interval_minutes")
            _fut_hint = (format(_iv, ".0f") + "m window") if _iv is not None else "no matched baseline"
        st.markdown('<div class="mod"><div class="mh"><span class="mt">Futures context</span><span class="mx">' + _esc(_fut_hint) + '</span></div>'
                    + "".join(rows) + '</div>', unsafe_allow_html=True)

    render_options_filter_panel(options_filter_results)

    st.markdown('<div class="foot">GEX walls are options-derived reference levels, not guaranteed support or resistance. Futures OI and basis are context, not directional signals. Independent dashboard classification; not a prediction or a replacement for the locked Pine AMT reference.</div>', unsafe_allow_html=True)
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
    
    with st.expander("Futures OI context · detail"):
        oi_context_cols = st.columns(4)
        if oi_context.get("status") == "OK":
            oi_context_cols[0].metric("Aggregate OI", "$" + format(oi_context["oi_usd"] / 1e6, ".2f") + "M")
            oi_change = oi_context.get("change_pct")
            oi_context_cols[1].metric("OI Change", f"{oi_change:+.2f}%" if oi_change is not None else "N/A",
                oi_context.get("direction", "N/A") if oi_change is not None else "No comparable baseline", delta_color="off")
            oi_context_cols[2].metric("Price Location", oi_context.get("price_location", "LOCATION_UNAVAILABLE"))
            oi_context_cols[3].metric("Confirmed AMT State", oi_context.get("amt_state", "AMT_STATE_UNAVAILABLE"))
            interval = oi_context.get("interval_minutes")
            interval_text = f"{interval:.1f}m comparison" if interval is not None else "No matched 25–35m baseline"
            st.caption("As-of aggregate OI observation · " + interval_text
                + " · Coverage: " + ", ".join(oi_context.get("coverage", []))
                + " · " + str(oi_context.get("observed_at", "timestamp unavailable"))
                + " · Context only; no directional trading signal has been validated.")
        else:
            for column, label in zip(oi_context_cols, ("Aggregate OI", "OI Change", "Price Location", "Confirmed AMT State")):
                column.metric(label, "N/A")
            st.caption("Futures OI context unavailable; no values are inferred or zero-filled.")

    with st.expander("Copy dashboard snapshot"):
        snapshot_text = """
    === BTC AMT &amp; DERIVATIVES OBSERVATION COCKPIT SNAPSHOT ===
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
        '<div class="stat">'
        '<span><b style="background:' + ("#00E676" if not df.empty else "#FF5252") + '"></b>Binance candles</span>'
        '<span><b style="background:' + ("#00E676" if oi_usd is not None else "#FF5252") + '"></b>Delta futures</span>'
        '<span><b style="background:' + ("#00E676" if not options_df.empty else "#FF5252") + '"></b>Delta options</span>'
        '</div>',
        unsafe_allow_html=True,
    )

_render_dashboard()

