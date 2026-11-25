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
import html
from decimal import Decimal, InvalidOperation
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from datetime import timezone, timedelta
from supabase import create_client
from options_filters import load_and_calculate_options_filters
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
</style>
""", unsafe_allow_html=True)

# ==========================================
# CONFIGURATION & STATE
# ==========================================
IST_TZ = timezone(timedelta(hours=5, minutes=30))

def _extract_option_oi_values(ticker):
    """Return valid raw BTC OI and unchanged USD OI; never use contract counts as fallback."""
    raw_oi = ticker.get("oi") if isinstance(ticker, dict) and "oi" in ticker else None
    parsed_oi = _decimal_value(raw_oi)
    oi_btc = raw_oi if parsed_oi is not None and parsed_oi >= 0 else None
    return oi_btc, ticker.get("oi_value_usd") if isinstance(ticker, dict) else None


def _decimal_value(value):
    """Parse a finite decimal without routing source values through binary float."""
    if value is None or isinstance(value, (bool, np.bool_)):
        return None
    try:
        number = value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        return None
    return number if number.is_finite() else None


def _build_option_chain_frame(chain):
    """Build the fetched chain while keeping original API OI values in object columns."""
    frame = pd.DataFrame(chain)
    # DataFrame inference can coerce [large_int, None] to float64 and round the
    # integer. Rebuild source-only columns from the original records as object
    # dtype; calculation columns remain the float-normalized values in each row.
    for column in ("OI_Contracts_Source", "OI_USD_Source"):
        if any(column in row for row in chain):
            frame[column] = pd.Series(
                [row.get(column) for row in chain], index=frame.index, dtype=object
            )
    return frame


def _prepare_contract_export(raw_chain_df, selected_expiry, existing_chain_complete):
    """Run the production snapshot export preparation, validation and TSV serialization."""
    columns = [
        "Product_ID", "Symbol", "Expiry", "Type", "Strike",
        "OI_Contracts", "OI_USD",
    ]
    export_df = raw_chain_df.copy()
    missing_columns = [column for column in columns if column not in export_df.columns]
    for column in missing_columns:
        export_df[column] = np.nan
    if "Expiry" in missing_columns and selected_expiry:
        export_df["Expiry"] = selected_expiry

    # Source columns are deliberately object dtype, preserving API int/Decimal/
    # string values; calculation columns remain the existing float-normalized data.
    if "OI_Contracts_Source" in export_df.columns:
        export_df["OI_Contracts"] = pd.Series(
            export_df["OI_Contracts_Source"].tolist(), index=export_df.index, dtype=object
        )
    if "OI_USD_Source" in export_df.columns:
        export_df["OI_USD"] = pd.Series(
            export_df["OI_USD_Source"].tolist(), index=export_df.index, dtype=object
        )

    complete, reasons = _validate_contract_export(
        export_df[columns], selected_expiry, existing_chain_complete
    )
    if missing_columns:
        missing_reason = "missing fields: " + ", ".join(missing_columns)
        if missing_reason not in reasons:
            reasons.append(missing_reason)
    tsv = export_df[columns].to_csv(
        sep="\t", index=False, na_rep="NULL", lineterminator="\n"
    )
    return export_df, ("COMPLETE" if complete else "INCOMPLETE"), reasons, tsv


def _validate_contract_export(frame, selected_expiry, existing_chain_complete=True):
    """Return (complete, reasons) after validating every required exported field."""
    required = ["Product_ID", "Symbol", "Expiry", "Type", "Strike", "OI_Contracts", "OI_USD"]
    reasons = []
    if not existing_chain_complete:
        reasons.append("existing API/product pagination completeness check failed")
    missing = [column for column in required if column not in frame.columns]
    if missing:
        reasons.append("missing fields: " + ", ".join(missing))
        return False, reasons
    if frame.empty:
        return False, reasons + ["contract export contains no rows"]
    if selected_expiry is None or str(selected_expiry).strip() == "":
        reasons.append("selected expiry is unavailable")

    # Product IDs are numeric identifiers: compare canonical integral values,
    # not their textual formatting (e.g. 101 and 101.0 are the same ID).
    canonical_ids = []
    symbols = []
    selected_expiry_text = str(selected_expiry).strip() if selected_expiry is not None else ""
    for row_index, row in frame.iterrows():
        product_id = row["Product_ID"]
        pid = _decimal_value(product_id)
        if pid is None or pid <= 0 or pid != pid.to_integral_value():
            reasons.append(f"row {row_index}: invalid Product_ID")
        else:
            canonical_ids.append(int(pid))

        symbol = row["Symbol"]
        symbol_parts = None
        if not isinstance(symbol, str) or not symbol.strip():
            reasons.append(f"row {row_index}: missing or invalid Symbol")
        else:
            symbol_text = symbol.strip()
            parts = symbol_text.split("-")
            if (
                len(parts) != 4 or parts[0] not in ("C", "P") or parts[1] != "BTC"
                or not parts[2] or not parts[3].isdigit() or len(parts[3]) != 6
            ):
                reasons.append(f"row {row_index}: malformed BTC option Symbol")
            else:
                symbol_parts = parts
                try:
                    datetime.datetime.strptime(parts[3], "%d%m%y")
                except ValueError:
                    reasons.append(f"row {row_index}: symbol expiry is not a real calendar date")
                if selected_expiry_text and parts[3] != selected_expiry_text:
                    reasons.append(f"row {row_index}: symbol expiry does not match selected expiry")
                if row["Type"] in ("Call", "Put") and parts[0] != ("C" if row["Type"] == "Call" else "P"):
                    reasons.append(f"row {row_index}: Symbol side does not match option Type")
                symbols.append(symbol_text)

        expiry = row["Expiry"]
        if expiry is None or pd.isna(expiry) or str(expiry).strip() == "":
            reasons.append(f"row {row_index}: missing Expiry")
        elif selected_expiry_text and str(expiry).strip() != selected_expiry_text:
            reasons.append(f"row {row_index}: Expiry does not match selected expiry")

        if row["Type"] not in ("Call", "Put"):
            reasons.append(f"row {row_index}: invalid option Type")

        strike = _decimal_value(row["Strike"])
        if strike is None or strike <= 0:
            reasons.append(f"row {row_index}: Strike must be numeric, finite and positive")
        if symbol_parts is not None:
            encoded_strike_text = symbol_parts[2].replace(",", "").strip().upper()
            try:
                if encoded_strike_text.endswith("K"):
                    encoded_strike = Decimal(encoded_strike_text[:-1]) * Decimal("1000")
                else:
                    encoded_strike = Decimal(encoded_strike_text)
                if not encoded_strike.is_finite() or encoded_strike <= 0:
                    raise InvalidOperation
                if strike is not None and encoded_strike != strike:
                    reasons.append(f"row {row_index}: symbol strike does not match exported Strike")
            except (InvalidOperation, TypeError, ValueError):
                reasons.append(f"row {row_index}: invalid strike encoded in Symbol")

        for column in ("OI_Contracts", "OI_USD"):
            value = _decimal_value(row[column])
            if value is None or value < 0:
                reasons.append(f"row {row_index}: {column} must be numeric, finite and non-negative")

    if len(canonical_ids) != len(frame):
        reasons.append("one or more Product_ID values are missing or invalid")
    elif len(set(canonical_ids)) != len(canonical_ids):
        reasons.append("duplicate Product_ID values")
    if len(symbols) != len(frame):
        reasons.append("one or more Symbols are missing or invalid")
    elif len(set(symbols)) != len(symbols):
        reasons.append("duplicate Symbols")
    return not reasons, reasons



def _append_contract_export_to_snapshot(snapshot_text, raw_chain_df, loaded_expiry, existing_chain_complete):
    """Append the already-fetched nearest-expiry chain using real snapshot line breaks."""
    snapshot_text = snapshot_text.rstrip("\n") + "\n\n=== DELTA OPTIONS CONTRACT EXPORT · NEAREST EXPIRY ONLY ===\n"
    if raw_chain_df is None or raw_chain_df.empty:
        snapshot_text += "EXPORT STATUS: UNAVAILABLE — raw nearest-expiry contract chain is empty or unavailable.\n"
        return snapshot_text

    export_df, export_status, incomplete_reasons, export_tsv = _prepare_contract_export(
        raw_chain_df, loaded_expiry, existing_chain_complete
    )
    snapshot_text += (
        f"EXPORT STATUS: {export_status} — {len(export_df)} contract rows; "
        f"selected expiry: {loaded_expiry or 'UNKNOWN'}\n"
    )
    if incomplete_reasons:
        snapshot_text += "INCOMPLETE REASONS: " + "; ".join(incomplete_reasons) + "\n"
    return snapshot_text + export_tsv


def _run_contract_export_validation_tests():
    """Focused offline regression tests; run with BTC_EXPORT_SELF_TEST=1."""
    import json as _json

    base = {
        "Product_ID": 101, "Symbol": "C-BTC-100000-301226",
        "Expiry": "301226", "Type": "Call", "Strike": Decimal("100000"),
        "OI_Contracts": Decimal("0"), "OI_USD": Decimal("0"),
    }
    valid, reasons = _validate_contract_export(pd.DataFrame([base]), "301226")
    assert valid, reasons

    contracts, usd = _extract_option_oi_values({"oi_contracts": 0, "oi": 77, "oi_value_usd": 0})
    assert contracts == 0 and usd == 0
    assert _extract_option_oi_values({"oi_contracts": None, "oi": 77})[0] == 77
    assert _extract_option_oi_values({"oi": 0})[0] == 0
    assert _extract_option_oi_values({"oi_contracts": None, "oi": 77.25})[0] == 77.25

    for field in ("Product_ID", "Symbol", "Expiry", "Type", "Strike", "OI_Contracts", "OI_USD"):
        bad = dict(base)
        bad[field] = None
        assert not _validate_contract_export(pd.DataFrame([bad]), "301226")[0], field

    for field, value in (
        ("Strike", Decimal("Infinity")), ("OI_Contracts", Decimal("NaN")),
        ("OI_USD", Decimal("Infinity")), ("OI_Contracts", Decimal("-0.01")),
        ("OI_USD", Decimal("-0.01")),
    ):
        bad = dict(base)
        bad[field] = value
        assert not _validate_contract_export(pd.DataFrame([bad]), "301226")[0], (field, value)

    duplicate = dict(base, Product_ID=Decimal("101.0"), Symbol="P-BTC-100000-301226", Type="Put")
    assert not _validate_contract_export(pd.DataFrame([base, duplicate]), "301226")[0]
    for invalid_id in (0, -1, Decimal("101.5"), "not-an-id"):
        bad = dict(base, Product_ID=invalid_id)
        assert not _validate_contract_export(pd.DataFrame([bad]), "301226")[0], invalid_id

    bad_type = dict(base, Type="CALL")
    assert not _validate_contract_export(pd.DataFrame([bad_type]), "301226")[0]
    bad_expiry_column = dict(base, Expiry="310127")
    assert not _validate_contract_export(pd.DataFrame([bad_expiry_column]), "301226")[0]
    bad_symbol_expiry = dict(base, Symbol="C-BTC-100000-310127")
    assert not _validate_contract_export(pd.DataFrame([bad_symbol_expiry]), "301226")[0]
    invalid_calendar_expiry = dict(base, Symbol="C-BTC-100000-311326", Expiry="311326")
    assert not _validate_contract_export(pd.DataFrame([invalid_calendar_expiry]), "311326")[0]
    mismatched_symbol_strike = dict(base, Symbol="C-BTC-99000-301226")
    assert not _validate_contract_export(pd.DataFrame([mismatched_symbol_strike]), "301226")[0]
    valid_k_strike = dict(base, Symbol="C-BTC-100K-301226")
    assert _validate_contract_export(pd.DataFrame([valid_k_strike]), "301226")[0]
    assert not _validate_contract_export(pd.DataFrame([base]), "301226", existing_chain_complete=False)[0]

    precise = Decimal("123456789.1234567890123456789")
    parsed = _json.loads('{"oi_value_usd":123456789.1234567890123456789}', parse_float=Decimal)
    assert parsed["oi_value_usd"] == precise
    assert str(parsed["oi_value_usd"]) == "123456789.1234567890123456789"
    precision_csv = pd.DataFrame([dict(base, OI_USD=parsed["oi_value_usd"])]).to_csv(index=False)
    assert "123456789.1234567890123456789" in precision_csv
    # Exercise the exact fetch-chain frame builder and production snapshot TSV path.
    huge_integer = 9007199254740993
    long_decimal = Decimal("123456789.12345678901234567890123456789")
    source_chain = [
        {
            "Product_ID": 201, "Symbol": "C-BTC-100000-301226",
            "Expiry": "301226", "Type": "Call", "Strike": 100000.0,
            "OI_Contracts": float(huge_integer), "OI_USD": 1.0,
            "OI_Contracts_Source": huge_integer,
            "OI_USD_Source": long_decimal,
        },
        {
            "Product_ID": 202, "Symbol": "P-BTC-101000-301226",
            "Expiry": "301226", "Type": "Put", "Strike": 101000.0,
            "OI_Contracts": np.nan, "OI_USD": np.nan,
            "OI_Contracts_Source": None, "OI_USD_Source": None,
        },
        {
            "Product_ID": 203, "Symbol": "C-BTC-102000-301226",
            "Expiry": "301226", "Type": "Call", "Strike": 102000.0,
            "OI_Contracts": 123.0, "OI_USD": 456.0,
            "OI_Contracts_Source": Decimal("123.000000000000000000123456789"),
            "OI_USD_Source": "4567890123456789.1234567890123456789",
        },
        {
            "Product_ID": 204, "Symbol": "P-BTC-103000-301226",
            "Expiry": "301226", "Type": "Put", "Strike": 103000.0,
            "OI_Contracts": 789.0, "OI_USD": 987.0,
            "OI_Contracts_Source": "7890000000000000.0000000000000000007",
            "OI_USD_Source": 9876543210987654321,
        },
    ]
    fetched_frame = _build_option_chain_frame(source_chain)
    assert fetched_frame["OI_Contracts_Source"].dtype == object
    assert fetched_frame.loc[0, "OI_Contracts_Source"] == huge_integer
    assert type(fetched_frame.loc[0, "OI_Contracts_Source"]) is int
    assert fetched_frame.loc[1, "OI_Contracts_Source"] is None
    assert fetched_frame.loc[2, "OI_Contracts_Source"] == Decimal("123.000000000000000000123456789")
    assert fetched_frame.loc[3, "OI_Contracts_Source"] == "7890000000000000.0000000000000000007"
    assert fetched_frame.loc[0, "OI_Contracts"] == float(huge_integer)

    exported_frame, export_status, export_reasons, tsv = _prepare_contract_export(
        fetched_frame, "301226", existing_chain_complete=False
    )
    assert export_status == "INCOMPLETE"
    assert any("completeness check failed" in reason for reason in export_reasons)
    tsv_lines = tsv.splitlines()
    assert tsv_lines[0].split("\t")[-2:] == ["OI_Contracts", "OI_USD"]
    assert "9007199254740993" in tsv_lines[1]
    assert "123456789.12345678901234567890123456789" in tsv_lines[1]
    assert tsv_lines[2].endswith("NULL\tNULL")
    assert "123.000000000000000000123456789" in tsv_lines[3]
    assert "4567890123456789.1234567890123456789" in tsv_lines[3]
    assert "7890000000000000.0000000000000000007" in tsv_lines[4]
    assert "9876543210987654321" in tsv_lines[4]
    # Real TSV separators and record terminators are present in the serialized output.
    assert "\t" in tsv_lines[0] and "\\t" not in tsv_lines[0]
    assert tsv.count("\n") == len(tsv_lines)
    assert tsv.endswith("\n")
    assert len(tsv_lines) == len(source_chain) + 1

    # A fully valid subset still reaches COMPLETE; the missing-OI row above is rejected.
    complete_frame, complete_status, complete_reasons, complete_tsv = _prepare_contract_export(
        fetched_frame.iloc[[0, 2, 3]].copy(), "301226", existing_chain_complete=True
    )
    assert complete_status == "COMPLETE", complete_reasons
    assert len(complete_frame) == 3 and len(complete_tsv.splitlines()) == 4

    # Exercise the exact helper used by the dashboard snapshot renderer.
    snapshot = _append_contract_export_to_snapshot(
        "BASE DASHBOARD SNAPSHOT\n", fetched_frame, "301226", existing_chain_complete=False
    )
    snapshot_lines = snapshot.splitlines()
    assert snapshot_lines[0] == "BASE DASHBOARD SNAPSHOT"
    assert snapshot_lines[1] == ""
    assert snapshot_lines[2] == "=== DELTA OPTIONS CONTRACT EXPORT · NEAREST EXPIRY ONLY ==="
    assert snapshot_lines[3].startswith("EXPORT STATUS: INCOMPLETE — 4 contract rows;")
    assert snapshot_lines[4].startswith("INCOMPLETE REASONS:")
    assert snapshot_lines[5].split("\t")[-2:] == ["OI_Contracts", "OI_USD"]
    assert "9007199254740993" in snapshot_lines[6]
    assert snapshot_lines[7].endswith("NULL\tNULL")
    assert "123456789.12345678901234567890123456789" in snapshot_lines[6]

    # Float calculation columns remain distinct from exact source columns.
    assert fetched_frame.loc[0, "OI_Contracts"] == float(huge_integer)
    assert fetched_frame.loc[0, "OI_Contracts_Source"] == huge_integer
    print("PASS: validator safeguards; fetch-chain object source preservation; mixed int/string/Decimal/missing OI; exact production TSV export; float calculation columns unchanged")

def _sum_if_complete(series):
    """Return a sum only when every expected observation is present and valid."""
    values = pd.to_numeric(series, errors="coerce")
    if values.empty or values.isna().any():
        return np.nan
    numeric = values.to_numpy(dtype=float)
    if not np.isfinite(numeric).all() or (numeric < 0).any():
        return np.nan
    return float(numeric.sum())


def _normalize_listed_option_oi(product_universe, received_chain, universe_complete):
    """Build strike-side OI, zeroing only sides absent from a complete product universe."""
    columns = ["Strike", "OI_Contracts_Call", "OI_Contracts_Put"]
    if not product_universe:
        return pd.DataFrame(columns=columns)

    listed = {}
    symbols = set()
    invalid_metadata = False

    for product in product_universe:
        symbol = str(product.get("symbol") or "")
        contract_type = product.get("contract_type")
        side = "Call" if contract_type == "call_options" else "Put" if contract_type == "put_options" else None
        try:
            strike_raw = product.get("strike_price")
            if strike_raw is None:
                parts = symbol.split("-")
                raw_strike = parts[2].replace(",", "").strip().upper()
                strike_raw = float(raw_strike.replace("K", "")) * 1000 if "K" in raw_strike else float(raw_strike)
            strike = float(strike_raw)
        except (IndexError, TypeError, ValueError):
            invalid_metadata = True
            continue

        if (
            not symbol or side is None or not np.isfinite(strike) or strike <= 0
            or symbol in symbols
        ):
            invalid_metadata = True
            continue
        symbols.add(symbol)
        key = (strike, side)
        if key in listed:
            invalid_metadata = True
            continue
        listed[key] = symbol

    if not listed:
        return pd.DataFrame(columns=columns)

    received = {}
    if received_chain is not None and not received_chain.empty and "Symbol" in received_chain.columns:
        for _, row in received_chain.iterrows():
            symbol = str(row.get("Symbol") or "")
            if symbol in received:
                invalid_metadata = True
            received[symbol] = row

    if set(received) - symbols:
        invalid_metadata = True

    strikes = sorted({strike for strike, _ in listed})
    rows = []
    for strike in strikes:
        row_out = {"Strike": strike}
        for side in ("Call", "Put"):
            symbol = listed.get((strike, side))
            column = f"OI_Contracts_{side}"
            if symbol is None:
                row_out[column] = 0.0 if universe_complete and not invalid_metadata else np.nan
                continue
            ticker_row = received.get(symbol)
            if ticker_row is None or invalid_metadata or not universe_complete:
                row_out[column] = np.nan
                continue
            value = pd.to_numeric(pd.Series([ticker_row.get("OI_Contracts")]), errors="coerce").iloc[0]
            row_out[column] = float(value) if pd.notna(value) else np.nan
        rows.append(row_out)

    return pd.DataFrame(rows, columns=columns)


def _listed_ticker_identities_match(products, tickers):
    """Require each listed option symbol to map to its exact ticker Product ID."""
    product_by_symbol = {}
    for product in products:
        symbol = product.get("symbol")
        product_id = product.get("id")
        if not symbol or product_id is None or str(symbol) in product_by_symbol:
            return False
        product_by_symbol[str(symbol)] = str(product_id)

    ticker_by_symbol = {}
    for ticker in tickers:
        symbol = ticker.get("symbol")
        ticker_id = ticker.get("product_id") if ticker.get("product_id") is not None else ticker.get("id")
        if not symbol or ticker_id is None or str(symbol) in ticker_by_symbol:
            return False
        ticker_by_symbol[str(symbol)] = str(ticker_id)

    return product_by_symbol == ticker_by_symbol


def _validated_btc_contract_multiplier(products):
    """Use contract_value only when every selected product declares BTC units."""
    values = []
    for product in products:
        unit = str(product.get("contract_unit_currency") or "").upper()
        value = pd.to_numeric(pd.Series([product.get("contract_value")]), errors="coerce").iloc[0]
        if unit != "BTC" or pd.isna(value) or not np.isfinite(float(value)) or float(value) <= 0:
            return None
        values.append(float(value))
    if not values or not np.allclose(values, values[0], rtol=0.0, atol=1e-12):
        return None
    return values[0]


def _select_positive_wall(frame, column):
    """Select a wall only from observed, finite, strictly positive measurements."""
    if column not in frame.columns:
        return None
    values = pd.to_numeric(frame[column], errors="coerce")
    valid = values.notna() & np.isfinite(values) & (values > 0)
    if not valid.any():
        return None
    return frame.loc[values[valid].idxmax()]


def _validate_gamma_side(expected_products, raw_chain, side):
    """Validate gamma coverage against every listed contract on one option side."""
    contract_type = "call_options" if side == "Call" else "put_options"
    expected = [p for p in expected_products if p.get("contract_type") == contract_type]
    if not expected:
        return "MISSING_" + side.upper() + "_CHAIN"
    expected_symbols = [str(p.get("symbol")) for p in expected]
    if len(set(expected_symbols)) != len(expected_symbols):
        return "DUPLICATE_" + side.upper() + "_SYMBOL"
    if raw_chain is None or raw_chain.empty:
        return "MISSING_" + side.upper() + "_CONTRACTS"
    observed = raw_chain[raw_chain["Type"] == side]
    observed_symbols = observed["Symbol"].dropna().astype(str).tolist()
    if len(observed_symbols) != len(expected_symbols) or set(observed_symbols) != set(expected_symbols):
        return "INCOMPLETE_" + side.upper() + "_CONTRACTS"
    gamma = pd.to_numeric(observed["Gamma"], errors="coerce")
    if gamma.isna().any():
        return "MISSING_GAMMA"
    values = gamma.to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        return "INVALID_GAMMA"
    return "OK"


def _validated_delta_spot_price(values):
    """Require complete, positive, mutually consistent Delta ticker spot prices."""
    numeric = pd.to_numeric(pd.Series(values), errors="coerce")
    if numeric.empty or numeric.isna().any():
        return None
    spots = numeric.to_numpy(dtype=float)
    if not np.isfinite(spots).all() or (spots <= 0).any():
        return None
    median_spot = float(np.median(spots))
    if float(spots.max() - spots.min()) > median_spot * 0.002:
        return None
    return median_spot


def _dashboard_gex_values(frame, gamma_column, oi_column, multiplier, spot, side_complete):
    """Return NaN for unavailable GEX and numeric zero for valid zero BTC OI exposure."""
    if (
        not side_complete or multiplier is None or spot is None
        or not np.isfinite(float(multiplier)) or float(multiplier) <= 0
        or not np.isfinite(float(spot)) or float(spot) <= 0
    ):
        return pd.Series(np.nan, index=frame.index, dtype=float)
    gamma = pd.to_numeric(frame[gamma_column], errors="coerce")
    oi_btc = pd.to_numeric(frame[oi_column], errors="coerce")
    valid = gamma.notna() & oi_btc.notna() & np.isfinite(gamma) & np.isfinite(oi_btc) & (gamma >= 0) & (oi_btc >= 0)
    values = gamma * oi_btc * float(multiplier) * (float(spot) ** 2) * 0.01
    return values.where(valid, np.nan)


def _calculate_max_pain(chain, is_chain_complete):
    """Calculate Max Pain only from a complete, non-null, non-negative option chain."""
    required = {"Strike", "OI_Contracts_Call", "OI_Contracts_Put"}
    if not is_chain_complete or chain.empty or not required.issubset(chain.columns):
        return np.nan

    strikes = pd.to_numeric(chain["Strike"], errors="coerce")
    call_oi = pd.to_numeric(chain["OI_Contracts_Call"], errors="coerce")
    put_oi = pd.to_numeric(chain["OI_Contracts_Put"], errors="coerce")
    if strikes.isna().any() or call_oi.isna().any() or put_oi.isna().any():
        return np.nan

    strike_values = strikes.to_numpy(dtype=float)
    call_values = call_oi.to_numpy(dtype=float)
    put_values = put_oi.to_numpy(dtype=float)
    if (
        not np.isfinite(strike_values).all()
        or not np.isfinite(call_values).all()
        or not np.isfinite(put_values).all()
        or (strike_values <= 0).any()
        or (call_values < 0).any()
        or (put_values < 0).any()
        or len(np.unique(strike_values)) != len(strike_values)
    ):
        return np.nan

    payouts = []
    for settlement in strike_values:
        call_payout = np.maximum(settlement - strike_values, 0) * call_values
        put_payout = np.maximum(strike_values - settlement, 0) * put_values
        payouts.append(float(np.sum(call_payout + put_payout)))
    if not payouts or not np.isfinite(payouts).all():
        return np.nan
    return float(strike_values[int(np.argmin(payouts))])


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
                funding_rate = float(ticker.get("funding_rate", 0)) 
                mark_price = float(ticker.get("mark_price", 0))
                spot_price = float(ticker.get("spot_price", 0))
                oi_usd = float(ticker.get("oi_value_usd", 0))
                sp_prem = ((mark_price - spot_price) / spot_price) * 100 if spot_price > 0 else None
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



@st.cache_data(ttl=60, show_spinner=False)
def fetch_alert_history():
    """Read the canonical signal journal and private Telegram outbox for the Alerts tab."""
    result = {
        "journal": [],
        "outbox": [],
        "journal_status": "UNAVAILABLE",
        "outbox_status": "UNAVAILABLE",
        "journal_reason": None,
        "outbox_reason": None,
    }
    supabase_url = os.environ.get("SUPABASE_URL")
    anon_key = os.environ.get("SUPABASE_ANON_KEY")
    service_key = os.environ.get("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        result["journal_reason"] = "MISSING_SUPABASE_URL"
        result["outbox_reason"] = "MISSING_SUPABASE_URL"
        return result

    journal_key = anon_key or service_key
    if not journal_key:
        result["journal_reason"] = "MISSING_SUPABASE_ANON_KEY"
    else:
        try:
            journal_client = create_client(supabase_url, journal_key)
            response = (
                journal_client.table("system_signal_journal")
                .select(
                    "id,event_timestamp,event_type,event_key,severity,price,amt_state,"
                    "dev_poc,dev_vah,dev_val,call_wall,put_wall,oi_usd,event_details,detected_at"
                )
                .order("detected_at", desc=True)
                .limit(300)
                .execute()
            )
            result["journal"] = response.data or []
            result["journal_status"] = "OK"
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Alerts tab journal read failed: exception_type=%s", type(exc).__name__
            )
            result["journal_reason"] = type(exc).__name__

    # The outbox has no anonymous SELECT policy. Never fall back to the anon key.
    if not service_key:
        result["outbox_reason"] = "SERVER_SIDE_SERVICE_ROLE_KEY_NOT_CONFIGURED"
    else:
        try:
            outbox_client = create_client(supabase_url, service_key)
            response = (
                outbox_client.table("telegram_alert_events")
                .select(
                    "event_key,event_type,event_time,delivery_status,telegram_message_id,"
                    "attempt_count,last_error,created_at,sent_at"
                )
                .order("created_at", desc=True)
                .limit(500)
                .execute()
            )
            result["outbox"] = response.data or []
            result["outbox_status"] = "OK"
        except Exception as exc:
            logging.getLogger(__name__).exception(
                "Alerts tab Telegram outbox read failed: exception_type=%s", type(exc).__name__
            )
            result["outbox_reason"] = type(exc).__name__

    return result


def _match_alert_delivery(journal_row, outbox_rows, outbox_available=True):
    """Match only by canonical identity; never infer delivery from a missing row."""
    if journal_row.get("event_type") not in {
        "AMT_STATE_CHANGE", "GEX_WALL_BREACH", "OI_SHOCK"
    }:
        return None, "NOT TELEGRAM-ELIGIBLE"

    if not outbox_available:
        return None, "DELIVERY LOG UNAVAILABLE"

    event_type = journal_row.get("event_type")
    matches = []
    if event_type == "GEX_WALL_BREACH":
        expected_key = f"SYSTEM_SIGNAL_GEX_WALL_BREACH_{journal_row.get('id')}"
        matches = [row for row in outbox_rows if row.get("event_key") == expected_key]
    else:
        journal_time = pd.to_datetime(
            journal_row.get("event_timestamp"), utc=True, errors="coerce"
        )
        if pd.notna(journal_time):
            for row in outbox_rows:
                outbox_time = pd.to_datetime(
                    row.get("event_time"), utc=True, errors="coerce"
                )
                if row.get("event_type") == event_type and pd.notna(outbox_time) and outbox_time == journal_time:
                    matches.append(row)

    if len(matches) == 1:
        return matches[0], str(matches[0].get("delivery_status") or "STATUS UNKNOWN").upper()
    if len(matches) > 1:
        return None, "AMBIGUOUS MATCH"
    return None, "NO OUTBOX RECORD"


def _render_alerts_tab():
    """Render read-only journal history with private Telegram delivery reconciliation."""
    st.markdown("## ALERT HISTORY")
    st.caption(
        "Canonical detections come from the signal journal. Telegram delivery is reconciled "
        "separately from the private outbox; a missing outbox row is not treated as a failed send."
    )
    if st.button("↻ Refresh alert history", key="refresh_alert_history"):
        fetch_alert_history.clear()

    with st.spinner("Loading recorded events..."):
        history = fetch_alert_history()

    journal_ok = history.get("journal_status") == "OK"
    outbox_ok = history.get("outbox_status") == "OK"
    status_cols = st.columns(3)
    status_cols[0].metric("Journal", "AVAILABLE" if journal_ok else "UNAVAILABLE")
    status_cols[1].metric("Telegram log", "AVAILABLE" if outbox_ok else "UNAVAILABLE")
    status_cols[2].metric("Journal events loaded", len(history.get("journal", [])))

    if not journal_ok:
        st.error("Signal journal could not be read: " + str(history.get("journal_reason") or "UNKNOWN"))
        st.caption("No event history is inferred when the canonical journal is unavailable.")
        return

    if not outbox_ok:
        st.warning(
            "Telegram delivery details are unavailable. The journal remains visible, "
            "but delivery status will not be guessed. "
            + str(history.get("outbox_reason") or "")
        )

    outbox_rows = history.get("outbox", [])
    rows = []
    for event in history.get("journal", []):
        delivery, delivery_status = _match_alert_delivery(
            event, outbox_rows, outbox_available=outbox_ok
        )
        rows.append({
            "Journal ID": event.get("id"),
            "Detected (IST)": event.get("detected_at"),
            "Event time (IST)": event.get("event_timestamp"),
            "Event": event.get("event_type"),
            "Severity": event.get("severity"),
            "Price": event.get("price"),
            "AMT state": event.get("amt_state"),
            "Developing POC": event.get("dev_poc"),
            "Developing VAH": event.get("dev_vah"),
            "Developing VAL": event.get("dev_val"),
            "Call wall": event.get("call_wall"),
            "Put wall": event.get("put_wall"),
            "OI USD": event.get("oi_usd"),
            "Delivery": delivery_status,
            "Attempts": delivery.get("attempt_count") if delivery else None,
            "Telegram message ID": delivery.get("telegram_message_id") if delivery else None,
            "Sent at (IST)": delivery.get("sent_at") if delivery else None,
            "Last error": delivery.get("last_error") if delivery else None,
            "Event details": event.get("event_details"),
        })

    frame = pd.DataFrame(rows)
    if frame.empty:
        st.info("No signal-journal events have been recorded yet.")
        return

    for column in ("Detected (IST)", "Event time (IST)", "Sent at (IST)"):
        frame[column] = pd.to_datetime(frame[column], utc=True, errors="coerce").dt.tz_convert("Asia/Kolkata")

    lookback = st.selectbox(
        "History window",
        ["24 hours", "3 days", "7 days", "30 days", "90 days", "All loaded"],
        index=3,
        key="alerts_history_window",
    )
    event_types = ["All"] + sorted(frame["Event"].dropna().astype(str).unique().tolist())
    selected_type = st.selectbox("Event type", event_types, key="alerts_event_type")
    delivery_types = ["All"] + sorted(frame["Delivery"].dropna().astype(str).unique().tolist())
    selected_delivery = st.selectbox("Delivery status", delivery_types, key="alerts_delivery_status")

    filtered = frame.copy()
    if lookback != "All loaded":
        hours = {
            "24 hours": 24, "3 days": 72, "7 days": 168,
            "30 days": 720, "90 days": 2160,
        }[lookback]
        cutoff = pd.Timestamp.now(tz="UTC") - pd.Timedelta(hours=hours)
        filtered = filtered[filtered["Detected (IST)"] >= cutoff]
    if selected_type != "All":
        filtered = filtered[filtered["Event"] == selected_type]
    if selected_delivery != "All":
        filtered = filtered[filtered["Delivery"] == selected_delivery]

    st.caption(
        f"Showing {len(filtered)} of {len(frame)} loaded journal events. "
        "The query is capped at the latest 300 journal rows and 500 outbox rows."
    )
    display_columns = [
        "Journal ID", "Detected (IST)", "Event time (IST)", "Event", "Severity",
        "Price", "AMT state", "Developing POC", "Developing VAH", "Developing VAL",
        "Call wall", "Put wall", "OI USD", "Delivery", "Attempts", "Telegram message ID",
        "Sent at (IST)", "Last error",
    ]
    st.dataframe(
        filtered[display_columns],
        use_container_width=True,
        hide_index=True,
        column_config={
            "Price": st.column_config.NumberColumn("Price", format="$%,.2f"),
            "Developing POC": st.column_config.NumberColumn("Developing POC", format="$%,.0f"),
            "Developing VAH": st.column_config.NumberColumn("Developing VAH", format="$%,.0f"),
            "Developing VAL": st.column_config.NumberColumn("Developing VAL", format="$%,.0f"),
            "Call wall": st.column_config.NumberColumn("Call wall", format="$%,.0f"),
            "Put wall": st.column_config.NumberColumn("Put wall", format="$%,.0f"),
            "OI USD": st.column_config.NumberColumn("OI USD", format="$%,.0f"),
            "Detected (IST)": st.column_config.DatetimeColumn("Detected (IST)", format="DD MMM YYYY, HH:mm:ss"),
            "Event time (IST)": st.column_config.DatetimeColumn("Event time (IST)", format="DD MMM YYYY, HH:mm:ss"),
            "Sent at (IST)": st.column_config.DatetimeColumn("Sent at (IST)", format="DD MMM YYYY, HH:mm:ss"),
        },
    )

    if outbox_ok:
        with st.expander("Recent Telegram outbox activity (independent delivery log)"):
            outbox_frame = pd.DataFrame(history.get("outbox", []))
            if outbox_frame.empty:
                st.caption("No Telegram outbox records have been returned.")
            else:
                outbox_display = outbox_frame.rename(columns={
                    "event_key": "Event key",
                    "event_type": "Event",
                    "event_time": "Event time (UTC)",
                    "delivery_status": "Delivery",
                    "attempt_count": "Attempts",
                    "telegram_message_id": "Telegram message ID",
                    "created_at": "Outbox created (UTC)",
                    "sent_at": "Sent at (UTC)",
                    "last_error": "Last error",
                })
                outbox_columns = [
                    "Event key", "Event", "Event time (UTC)", "Delivery", "Attempts",
                    "Telegram message ID", "Outbox created (UTC)", "Sent at (UTC)", "Last error",
                ]
                st.dataframe(
                    outbox_display[[column for column in outbox_columns if column in outbox_display.columns]],
                    use_container_width=True,
                    hide_index=True,
                )
                st.caption(
                    "This table also includes Telegram-only operational records such as profile-initialization "
                    "and TEST_ALERT events that do not have a matching system journal row."
                )

    if not filtered.empty:
        selected_id = st.selectbox(
            "Inspect event evidence",
            filtered["Journal ID"].astype(str).tolist(),
            key="alerts_event_detail_id",
        )
        selected = filtered[filtered["Journal ID"].astype(str) == selected_id].iloc[0]
        with st.expander("Selected event details and delivery error"):
            details = selected.get("Event details")
            st.json(details if isinstance(details, (dict, list)) else {})
            if selected.get("Last error"):
                st.error(str(selected.get("Last error")))
            elif selected.get("Delivery") == "NO OUTBOX RECORD":
                st.caption(
                    "No matching outbox record was found for this Telegram-eligible event. "
                    "This is not proof that Telegram did not receive a message."
                )



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
            f'Call {float(result.get("call_ratio")):.3f}× / '
            f'Put {float(result.get("put_ratio")):.3f}× · '
            f'{result.get("relative_volume", "N/A")} · '
            f'{result.get("ratio_comparison", "N/A")}',
            "OK · descriptive only; 0.5 and 1.5 are research heuristics, not established market thresholds",
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
    """Render the five validated, snapshot-pinned research filters in a full-width row."""
    selection = payload.get("selection", {}) if isinstance(payload, dict) else {}
    if isinstance(payload, dict) and payload.get("status") == "OK":
        expiry = html.escape(str(selection.get("expiry_date", "N/A")))
        try:
            spot = f'${float(selection.get("spot_price")):,.2f}'
        except (TypeError, ValueError):
            spot = "N/A"
        snapshot_id = html.escape(str(selection.get("snapshot_id", "N/A"))[:12])
        metadata = (
            f"Expiry {expiry} · Snapshot spot {html.escape(spot)} · "
            f"Snapshot ID {snapshot_id}"
        )
        filters = payload.get("filters", {})
    else:
        reason = selection.get("reason", "OPTIONS_FILTERS_UNAVAILABLE")
        metadata = f"Snapshot unavailable · {html.escape(str(reason))}"
        filters = {}

    st.markdown(
        '<div class="sec"><span class="bar"></span>'
        '<span class="st">OPTIONS RESEARCH FILTERS · VALIDATED SNAPSHOT</span>'
        f'<span class="hint">{metadata}</span></div>',
        unsafe_allow_html=True,
    )

    labels = (
        ("nearby_oi", "Nearby OI"),
        ("iv_skew", "IV Skew"),
        ("volume_oi", "24h Volume / OI"),
        ("oi_migration", "OI Migration"),
        ("taker_activity", "Sampled Taker Trade Activity"),
    )
    columns = st.columns(5)
    for column, (key, label) in zip(columns, labels):
        result = filters.get(key, {"status": "UNAVAILABLE", "reason": "MISSING_FILTER_RESULT"})
        _, status_text = _format_options_filter_result(key, result)
        if result.get("status") == "OK":
            if key == "nearby_oi":
                walls = result.get("walls", [])
                primary = " · ".join(
                    f'{wall.get("type", "N/A")} ${float(wall.get("strike")):,.0f}'
                    for wall in walls
                ) if walls else "No local concentration detected"
                secondary = ""
            elif key == "iv_skew":
                primary = f'{float(result.get("call_iv_premium_pct")):+.2f}%'
                secondary = "Call IV Premium"
            elif key == "volume_oi":
                primary = (
                    f'Call {float(result.get("call_ratio")):.3f}× / '
                    f'Put {float(result.get("put_ratio")):.3f}×'
                )
                secondary = (
                    f'{result.get("relative_volume", "N/A")} · '
                    f'{result.get("ratio_comparison", "N/A")} · '
                    '0.5 and 1.5 are research heuristics, not established market thresholds'
                )
            elif key == "oi_migration":
                primary = str(result.get("classification", "N/A"))
                elapsed = result.get("elapsed_seconds")
                interval = f' · {float(elapsed) / 60:.0f}m interval' if elapsed is not None else ""
                secondary = (
                    f'Call {float(result.get("call_shift")):+,.0f} / '
                    f'Put {float(result.get("put_shift")):+,.0f}{interval}'
                )
            elif key == "taker_activity":
                primary = f'Balance {float(result.get("sampled_volume_balance")):+,.2f}'
                secondary = (
                    f'Buy {float(result.get("sampled_buy_volume")):,.2f} / '
                    f'Sell {float(result.get("sampled_sell_volume")):,.2f}'
                )
            else:
                primary, secondary = "N/A", ""
        else:
            primary = "N/A"
            secondary = str(result.get("reason") or result.get("status") or "Unavailable")

        tone = "t-up" if str(status_text).startswith("OK") else "t-flat"
        secondary_html = (
            f'<div style="margin-top:5px; color:var(--muted); font-size:0.68rem; '
            f'line-height:1.3; overflow-wrap:anywhere;">{html.escape(str(secondary))}</div>'
            if secondary else ""
        )
        card_html = (
            f'<div class="card {tone}" style="height:150px; min-height:150px; '
            f'box-sizing:border-box; display:flex; flex-direction:column; overflow:hidden;">'
            f'<div class="k">{html.escape(label)}</div>'
            f'<div class="v" style="font-size:1.02rem; line-height:1.25; overflow-wrap:anywhere;">'
            f'{html.escape(str(primary))}</div>'
            f'{secondary_html}'
            f'<div class="s" style="margin-top:auto; padding-top:7px;">'
            f'{html.escape(str(status_text))}</div>'
            '</div>'
        )
        with column:
            st.markdown(card_html, unsafe_allow_html=True)


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

    # The overnight segment immediately before this AMT cycle belongs to the
    # prior cycle: [01:30, 05:30) IST. Its Macro Bias reference is the complete
    # AMT cycle immediately preceding that overnight segment.
    overnight_start = cycle_start - pd.Timedelta(hours=4)
    prior_overnight_data = df[
        (df['Date'] >= overnight_start) & (df['Date'] < cycle_start)
    ].sort_values('Date')

    if not prior_overnight_data.empty:
        reference_start = cycle_start - pd.Timedelta(hours=48)
        reference_end = cycle_start - pd.Timedelta(hours=24)
        reference_data = df[
            (df['Date'] >= reference_start) & (df['Date'] < reference_end)
        ].sort_values('Date')
        expected_reference_dates = pd.date_range(
            start=reference_start, periods=48, freq="30min"
        )
        reference_dates = pd.DatetimeIndex(reference_data['Date'])
        reference_complete = (
            len(reference_data) == 48
            and reference_dates.is_unique
            and reference_dates.equals(expected_reference_dates)
        )

        prior_vah = prior_val = np.nan
        if reference_complete:
            prior_vah, prior_val, _ = calculate_tpo(reference_data)

        vah, val, poc = calculate_tpo(prior_overnight_data)
        close = prior_overnight_data['close'].iloc[-1]
        high = prior_overnight_data['high'].max()
        low = prior_overnight_data['low'].min()

        macro_bias = "N/A"
        if reference_complete and not pd.isna(prior_vah) and not pd.isna(prior_val):
            macro_bias = "⚪ Inside Prior Value"
            if close > prior_vah: macro_bias = "🟢 Bullish (Above Prior VAH)"
            elif close < prior_val: macro_bias = "🔴 Bearish (Below Prior VAL)"
            elif high > prior_vah and close < prior_vah: macro_bias = "⚠️ Bull Trap"
            elif low < prior_val and close > prior_val: macro_bias = "⚠️ Bear Trap"

        micro_bias = "⚪ Balanced (Inside Value)"
        if not pd.isna(vah) and not pd.isna(val):
            if close > vah: micro_bias = "🟢 Strong Close"
            elif close < val: micro_bias = "🔴 Weak Close"

        sessions.append({
            "Session": "Prior US Overnight (01:30–05:30)",
            "Open": prior_overnight_data['open'].iloc[0],
            "High": high,
            "Low": low,
            "Close": close,
            "Macro Bias": macro_bias,
            "Micro Bias": micro_bias,
            "POC": poc,
            "VAH": vah,
            "VAL": val,
        })

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
                data = response.json(parse_float=Decimal)
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
        # Delta 'oi' is BTC OI, so GEX uses a unit scale of 1.0 (no contract multiplier).
        contract_size = 1.0

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
                oi_contracts_source, oi_usd_source = _extract_option_oi_values(ticker)
                oi_usd = float(oi_usd_source) if oi_usd_source is not None else None
                oi_contracts = float(oi_contracts_source) if oi_contracts_source is not None else None
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
                
                chain.append({"Product_ID": opt.get("id"), "Symbol": sym, "Expiry": nearest_expiry, "Strike": strike, "Type": opt_type, "Bid": bid, "Ask": ask, "Mark": mark, "OI_USD": oi_usd, "OI_Contracts": oi_contracts, "OI_USD_Source": oi_usd_source, "OI_Contracts_Source": oi_contracts_source, "Gamma": gamma, "Spot_Price": ticker.get("spot_price"), "IV": iv, "Volume": volume, "Timestamp": ts_val})
            except (ValueError, TypeError): continue

        df = _build_option_chain_frame(chain)
        if df.empty: return pd.DataFrame(), nearest_expiry, expiry_dt, expiry_source, contract_size, None, False

        expected_symbol_set = set(str(sym) for sym in expected_symbols)
        received_symbol_set = set(df["Symbol"].dropna().astype(str)) if not df.empty else set()
        oi_values = pd.to_numeric(df["OI_Contracts"], errors="coerce") if not df.empty else pd.Series()
        product_ids = [opt.get("id") for opt in filtered_options]
        ticker_subset = [t for t in tickers if t.get("symbol") in expected_symbol_set]
        ticker_symbols = [t.get("symbol") for t in ticker_subset]
        ticker_ids = [t.get("product_id") if t.get("product_id") is not None else t.get("id") for t in ticker_subset]
        identities_unique = (
            all(value is not None for value in product_ids)
            and len(set(str(value) for value in product_ids)) == len(product_ids)
            and len(set(expected_symbols)) == len(expected_symbols)
            and all(value is not None for value in ticker_ids)
            and len(set(str(value) for value in ticker_ids)) == len(ticker_ids)
            and len(ticker_symbols) == len(set(ticker_symbols))
            and _listed_ticker_identities_match(filtered_options, ticker_subset)
        )
        is_chain_complete = (prod_complete and tick_complete and identities_unique and expected_symbol_set == received_symbol_set and len(df) == len(expected_symbol_set) and oi_values.notna().all() and np.isfinite(oi_values.to_numpy(dtype=float)).all() and (oi_values >= 0).all())
        call_gamma_status = _validate_gamma_side(filtered_options, df, "Call")
        put_gamma_status = _validate_gamma_side(filtered_options, df, "Put")

        df_pivot = df.pivot_table(index="Strike", columns="Type", values=["Bid", "Ask", "Mark", "OI_USD", "OI_Contracts", "Gamma", "IV", "Volume"], aggfunc="first").reset_index()
        new_cols = []
        for col in df_pivot.columns:
            if isinstance(col, tuple): new_cols.append("_".join([str(c) for c in col if c != ""]).strip("_"))
            else: new_cols.append(str(col))
        df_pivot.columns = new_cols

        # Rebuild only contract OI from the selected-expiry product universe. A side
        # is zero only when that contract is structurally unlisted; listed-but-missing
        # tickers and invalid OI remain NULL. Incomplete pagination cannot prove absence.
        oi_by_strike = _normalize_listed_option_oi(filtered_options, df, prod_complete and tick_complete)
        df_pivot = df_pivot.drop(
            columns=["OI_Contracts_Call", "OI_Contracts_Put"], errors="ignore"
        ).merge(oi_by_strike, on="Strike", how="outer")

        expected_columns = ['Bid_Call', 'Ask_Call', 'Mark_Call', 'OI_USD_Call', 'OI_Contracts_Call', 'Gamma_Call', 'IV_Call', 'Volume_Call', 'Bid_Put', 'Ask_Put', 'Mark_Put', 'OI_USD_Put', 'OI_Contracts_Put', 'Gamma_Put', 'IV_Put', 'Volume_Put']
        for col in expected_columns:
            if col not in df_pivot.columns: df_pivot[col] = np.nan
        df_pivot["GEX_Call_Status"] = call_gamma_status
        df_pivot["GEX_Put_Status"] = put_gamma_status

        return df_pivot, nearest_expiry, expiry_dt, expiry_source, contract_size, df, is_chain_complete
    except Exception:
        return pd.DataFrame(), None, None, None, None, None, False

# ==========================================
# CALCULATION & PROCESSING
# ==========================================
def _set_dashboard_view(view):
    """Update the active dashboard view from a native button callback."""
    st.session_state["dashboard_view"] = view


def _render_dashboard():
    """Diagnostic button navigation outside the auto-refreshing market fragment."""
    dashboard_view = st.session_state.get("dashboard_view", "MARKET")
    market_col, alerts_col = st.columns(2)
    with market_col:
        st.button(
            "MARKET",
            key="view_market",
            type="primary" if dashboard_view == "MARKET" else "secondary",
            on_click=_set_dashboard_view,
            args=("MARKET",),
            use_container_width=True,
        )
    with alerts_col:
        st.button(
            "ALERTS",
            key="view_alerts",
            type="primary" if dashboard_view == "ALERTS" else "secondary",
            on_click=_set_dashboard_view,
            args=("ALERTS",),
            use_container_width=True,
        )

    view_slot = st.empty()
    with view_slot.container():
        if dashboard_view == "ALERTS":
            _render_alerts_tab()
            return

        loading = st.empty()
        loading.caption("Loading market data…")
        _render_market_dashboard()
        loading.empty()


@st.fragment(run_every="3m")
def _render_market_dashboard():
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
    delta_options_spot = (
        _validated_delta_spot_price(raw_chain_df["Spot_Price"])
        if raw_chain_df is not None and not raw_chain_df.empty and "Spot_Price" in raw_chain_df.columns
        else None
    )
    options_filter_results = fetch_options_filter_results()
    
    true_max_pain, straddle_price, atm_strike, true_max_pain = np.nan, np.nan, 0.0, np.nan
    call_oi_wall, put_oi_wall, call_gamma_wall, put_gamma_wall = None, None, None, None
    pcr, pcr_volume, atm_row = 0.0, 0.0, None
    straddle_valid = False
    call_strike_text = "N/A"
    put_strike_text = "N/A"
    filtered_options = pd.DataFrame()
    
    if not options_df.empty and latest_price > 0:
        valid_contract_size = 1.0  # BTC-denominated OI; retained parameter is a neutral compatibility scale.
        call_gamma_complete = (
            is_chain_complete
            and "GEX_Call_Status" in options_df.columns
            and options_df["GEX_Call_Status"].eq("OK").all()
        )
        put_gamma_complete = (
            is_chain_complete
            and "GEX_Put_Status" in options_df.columns
            and options_df["GEX_Put_Status"].eq("OK").all()
        )
        options_df['GEX_Call'] = _dashboard_gex_values(
            options_df, "Gamma_Call", "OI_Contracts_Call",
            valid_contract_size, delta_options_spot, call_gamma_complete,
        )
        options_df['GEX_Put'] = _dashboard_gex_values(
            options_df, "Gamma_Put", "OI_Contracts_Put",
            valid_contract_size, delta_options_spot, put_gamma_complete,
        )
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
            call_oi_wall = _select_positive_wall(temp_filt, "OI_USD_Call")
            put_oi_wall = _select_positive_wall(temp_filt, "OI_USD_Put")
            call_gamma_wall = _select_positive_wall(temp_filt, "GEX_Call")
            put_gamma_wall = _select_positive_wall(temp_filt, "GEX_Put")

            total_calls_btc_oi = _sum_if_complete(temp_filt["OI_Contracts_Call"])
            total_puts_btc_oi = _sum_if_complete(temp_filt["OI_Contracts_Put"])
            pcr = (
                total_puts_btc_oi / total_calls_btc_oi
                if pd.notna(total_calls_btc_oi) and pd.notna(total_puts_btc_oi) and total_calls_btc_oi > 0
                else np.nan
            )
            total_calls_vol = _sum_if_complete(temp_filt["Volume_Call"])
            total_puts_vol = _sum_if_complete(temp_filt["Volume_Put"])
            pcr_volume = (
                total_puts_vol / total_calls_vol
                if pd.notna(total_calls_vol) and pd.notna(total_puts_vol) and total_calls_vol > 0
                else np.nan
            )

            true_max_pain = _calculate_max_pain(options_df, is_chain_complete)

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
    
    previous_ui_state = st.session_state.get("fast_prev_amt_state")
    previous_ui_name = state_names.get(previous_ui_state) if previous_ui_state is not None else None
    current_story = current_amt_name
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
    h2.markdown(_ui_hero("AUCTION (LAST CLOSE)", current_amt_name, "Independent code " + str(current_amt_state), state_tone, "state"), unsafe_allow_html=True)
    h3.markdown(_ui_hero("LIVE PRICE LOCATION", loc, loc_sub, loc_tone, "state"), unsafe_allow_html=True)
    
    # ---------- AUCTION STORY CALLOUT ----------
    _has_auction_transition = (
        previous_ui_state is not None and previous_ui_state != current_amt_state
    )
    story_html = ""
    if _has_auction_transition:
        _story_parts = auction_story.split(" → ")
        story_html = ('<div class="t"><span class="prev">' + _story_parts[0] + '</span><span class="arr">→</span>'
                      '<span class="cur">' + _story_parts[1] + '</span></div>')
    
    def _ui_chip(label, value):
        cls = "up" if value.startswith("+") else "down" if value.startswith("-") else ""
        return ('<span class="chip"><span class="ck">' + label + '</span><span class="cv ' + cls + '">'
                + value + '</span></span>')
    
    st.markdown(
        '<div class="story t-' + state_tone + '"><div class="k">AUCTION STORY</div>'
        + story_html
        + '<div class="chips">'
        + _ui_chip("Price vs Prior VAL", prior_val_distance)
        + _ui_chip("Price vs Developing VAH", dev_vah_distance)
        + _ui_chip("Nearest GEX Wall", nearest_wall)
        + '</div></div>',
        unsafe_allow_html=True,
    )
    
    # ---------- WHAT CHANGED — UI ONLY ----------
    current_ui_snapshot = {
        "dev_vah": float(dev_vah_ui) if not pd.isna(dev_vah_ui) else None,
        "dev_poc": float(dev_poc_ui) if not pd.isna(dev_poc_ui) else None,
        "dev_val": float(dev_val_ui) if not pd.isna(dev_val_ui) else None,
    }
    previous_ui_snapshot = st.session_state.get("fast_previous_snapshot")
    changed_levels = []
    
    if previous_ui_snapshot is not None:
        for label, key in (("DEV VAH", "dev_vah"), ("DEV POC", "dev_poc"), ("DEV VAL", "dev_val")):
            current_value = current_ui_snapshot.get(key)
            previous_value = previous_ui_snapshot.get(key)
            try:
                current_value = float(current_value) if current_value is not None and not pd.isna(current_value) else None
                previous_value = float(previous_value) if previous_value is not None and not pd.isna(previous_value) else None
            except (ValueError, TypeError):
                current_value = None
                previous_value = None

            if current_value is not None and previous_value is not None and current_value != previous_value:
                changed_levels.append((label, current_value, current_value - previous_value))

    if changed_levels:
        _ui_section("WHAT CHANGED", "Developing value levels changed vs previous rendered snapshot")
        change_columns = st.columns(len(changed_levels))
        for column, (label, current_value, delta_value) in zip(change_columns, changed_levels):
            column.metric(
                label,
                "$" + format(current_value, ",.0f"),
                "$" + format(delta_value, "+,.0f"),
            )
    
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
            _ui_card("Developing VAL", _ui_num(dev_val_ui), tone="down"),
            _ui_card("Developing POC", _ui_num(dev_poc_ui), tone="warn"),
            _ui_card("Developing VAH", _ui_num(dev_vah_ui), tone="up"),
            _ui_card("VA Expansion", va_expansion_value, _va_sub, _va_tone),
        ], "g4"),
        unsafe_allow_html=True,
    )
    st.markdown('<div class="foot">Independent dashboard classification; not a prediction or a replacement for the locked Pine AMT reference.</div>', unsafe_allow_html=True)

    # ---------- FUTURES OI CONTEXT — descriptive only ----------
    oi_context = fetch_futures_oi_context()
    st.markdown("**Futures OI Context**")
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
                    _ui_card("Local BTC OI PCR · ±10%", format(pcr, ".2f") if not pd.isna(pcr) else "N/A", tone="info"),
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
    
    render_options_filter_panel(options_filter_results)
    
    _ui_section("DIAGNOSTICS AND RAW DATA", "expand for full tables")
    with st.expander("Session OHLC history"):
        if not session_df.empty:
            session_display_df = session_df.copy()
            value_columns = ["VAL", "POC", "VAH"]
            session_display_df = session_display_df[
                [column for column in session_display_df.columns if column not in value_columns]
                + value_columns
            ]
            st.dataframe(session_display_df, use_container_width=True, hide_index=True)
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
        st.caption("Largest individual Call and Put OI concentrations within ±15% of spot. USD OI is shown in USD; BTC OI quantities are shown separately.")
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
                                    "OI (BTC)", format="%.6f"
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
    Local BTC OI PCR (±10% spot): {pcr}
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
        # Append the already-fetched nearest-expiry contract chain. Do not issue another API request.
        snapshot_text = _append_contract_export_to_snapshot(
            snapshot_text, raw_chain_df, loaded_expiry, is_chain_complete
        )

        st.code(snapshot_text, language="text")
    
    
    st.markdown(
        '<div class="foot">' + "Source status — Binance candles: " + ("available" if not df.empty else "unavailable") + " · Delta futures: " + ("available" if oi_usd is not None else "unavailable") + " · Delta options: " + ("available" if not options_df.empty else "unavailable") + '</div>',
        unsafe_allow_html=True,
    )

if os.environ.get("BTC_EXPORT_SELF_TEST") == "1":
    _run_contract_export_validation_tests()
else:
    _render_dashboard()

