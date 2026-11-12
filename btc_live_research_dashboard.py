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
    if df is None or df.empty or 'strike_price' not in df.columns:
        return pd.DataFrame()
    try:
        price = float(current_price)
    except (TypeError, ValueError, OverflowError):
        return pd.DataFrame()
    if not np.isfinite(price):
        return pd.DataFrame()
    df_copy = df.copy()
    df_copy['strike_price'] = pd.to_numeric(df_copy['strike_price'], errors='coerce')
    df_copy = df_copy[np.isfinite(df_copy['strike_price'])]
    if df_copy.empty:
        return pd.DataFrame()
    df_copy['distance'] = abs(df_copy['strike_price'] - price)
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
        
    total_taker_buy = closest['taker_buy_volume'].sum(min_count=1)
    total_taker_sell = closest['taker_sell_volume'].sum(min_count=1)
    
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

# --- RENDER UI: AMT-FIRST CONFLUENCE COCKPIT ---
st.markdown("### AMT + Price Action Lead; Derivatives Provide Context")
st.caption("Futures and options are research measurements, not standalone directional signals.")

# Top cockpit: three primary readouts.
c1, c2, c3 = st.columns(3)
c1.metric("Current Price", f"${current_price:,.2f}")
c2.metric("AMT State", shadow_state["confirmed_state"] if shadow_state else "N/A")

if layer2_data["status"] == "OK" and layer2_data.get("delta_pct") is not None:
    c3.metric(
        "30m OI Delta",
        f"{layer2_data['delta_pct']:+.2f}%",
        f"${layer2_data['delta_usd']:+,.0f}",
    )
elif "REJECTED_BASELINE" in layer2_data["status"]:
    c3.metric("30m OI Delta", "REJECTED", "Baseline gap outside 25–35m", delta_color="off")
elif layer2_data["status"] == "COVERAGE_MISMATCH":
    c3.metric("30m OI Delta", "UNAVAILABLE", "Contributor coverage changed", delta_color="off")
else:
    c3.metric("30m OI Delta", "N/A", layer2_data["status"], delta_color="off")

st.markdown("---")
st.subheader("Confluence Table")
st.caption("Reference framework: read price acceptance/rejection first; use derivatives only to qualify the context.")
confluence_rows = [
    {
        "AMT / Price Action": "Price accepts above prior value",
        "Derivatives Evidence": "OI rising; funding and basis provide supporting context",
        "Interpretation": "Positioning accompanies the auction, but does not prove direction; require continued price acceptance.",
    },
    {
        "AMT / Price Action": "Price rejects at prior VAH",
        "Derivatives Evidence": "Nearby call OI concentration and/or call IV rising",
        "Interpretation": "Potential overhead risk; wait for price-action confirmation of rejection.",
    },
    {
        "AMT / Price Action": "Price accepts below prior VAL",
        "Derivatives Evidence": "OI rising and basis weakening",
        "Interpretation": "Positioning accompanies the downside auction; derivatives alone do not establish a short.",
    },
    {
        "AMT / Price Action": "Price returns into prior value",
        "Derivatives Evidence": "OI declining",
        "Interpretation": "May reflect position reduction; treat as rotation/balance unless price establishes acceptance elsewhere.",
    },
    {
        "AMT / Price Action": "Balanced auction",
        "Derivatives Evidence": "Mixed, neutral, stale, or incomplete futures/options evidence",
        "Interpretation": "No compelling directional evidence; preserve the AMT read and wait for clearer price behaviour.",
    },
]
st.dataframe(pd.DataFrame(confluence_rows), use_container_width=True, hide_index=True)

futures_tab, options_tab, raw_tab = st.tabs(["Futures", "Options", "Raw Data"])

with futures_tab:
    st.subheader("Futures Positioning")
    st.caption("Aggregate OI is limited to the exchanges contributing to this snapshot; coverage is shown explicitly.")
    f1, f2, f3, f4 = st.columns(4)
    f1.metric(
        "Aggregate OI",
        f"${layer2_data['current_oi_usd']/1e9:.2f}B" if layer2_data["current_oi_usd"] is not None else "N/A",
    )
    f2.metric(
        "Weighted Basis",
        f"{layer2_data['basis_pct']:+.4f}%" if layer2_data["basis_pct"] is not None else "N/A",
    )
    f3.metric(
        "Weighted Funding",
        f"{layer2_data['funding_rate']:+.4f}%" if layer2_data["funding_rate"] is not None else "N/A",
    )
    f4.metric("Exchange Coverage", layer2_data.get("coverage", "UNKNOWN"))
    st.caption(f"OI delta validation status: {layer2_data['status']}")
    if layer2_data["status"] == "OK":
        st.write(
            f"Validated 30m change: ${layer2_data['delta_usd']:+,.0f} "
            f"({layer2_data['delta_pct']:+.2f}%)."
        )
    elif "REJECTED_BASELINE" in layer2_data["status"]:
        st.warning("30m OI change rejected because the comparison interval was outside the accepted 25–35 minute window.")
    elif layer2_data["status"] == "COVERAGE_MISMATCH":
        st.warning("30m OI change unavailable because exchange contributor coverage changed.")
    else:
        st.info("A validated 30m OI comparison is not currently available.")
    st.caption("OI describes outstanding contracts, not which side is directionally dominant. Partial exchange coverage is not full-market OI.")

with options_tab:
    st.subheader("Options Positioning — Five Research Filters")
    if opt_forensics and "data" in opt_forensics:
        st.caption(f"Latest successful snapshot: {opt_forensics['timestamp']}")
        
        # Each filter is deliberately summarized in one sentence; raw measurements remain in Raw Data.
        if walls_res and walls_res.get("status") == "OK":
            walls = walls_res.get("walls", [])
            if walls:
                wall_text = "; ".join(
                    f"{wall['type']} local concentration at ${wall['strike']:,.0f} (OI {wall['oi']:,.0f})"
                    for wall in walls
                )
                st.write(f"**1. Nearby OI concentration:** {wall_text}; this is a local five-strike comparison, not proof of a price barrier.")
            else:
                st.write("**1. Nearby OI concentration:** No distinct local OI concentration was detected among the five nearest strikes.")
        else:
            st.write("**1. Nearby OI concentration:** Unavailable or insufficient data to assess the five nearest strikes.")

        if iv_analysis and iv_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
            st.write(
                f"**2. Implied-volatility skew:** Nearby average call IV is {iv_analysis['avg_call_iv']*100:.2f}% "
                f"versus put IV {iv_analysis['avg_put_iv']*100:.2f}% (relative skew {iv_analysis['skew_pct']:+.2f}%, "
                f"{iv_analysis['status']}); this five-strike average is a rough context measure, not delta-matched skew."
            )
        else:
            st.write("**2. Implied-volatility skew:** Unavailable or insufficient IV data.")

        if vol_analysis and vol_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
            st.write(
                f"**3. Options Turnover Relative to Open Interest:** Call volume/OI is {vol_analysis['call_ratio']:.3f}x "
                f"and put volume/OI is {vol_analysis['put_ratio']:.3f}x ({vol_analysis['status']}); volume does not distinguish opening from closing trades."
            )
        else:
            st.write("**3. Options Turnover Relative to Open Interest:** Unavailable or insufficient volume/OI data.")

        if migration_analysis and migration_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
            st.write(
                f"**4. OI peak-strike migration:** Call peak shifted {migration_analysis['call_shift']:+,.0f} "
                f"and put peak shifted {migration_analysis['put_shift']:+,.0f} ({migration_analysis['status']}); "
                f"this compares only the maximum-OI strikes, not the full strike distribution."
            )
        elif migration_analysis and migration_analysis.get("status") == "INSUFFICIENT_DATA":
            st.write("**4. OI peak-strike migration:** Waiting for a second successful snapshot with usable OI on both sides.")
        else:
            st.write("**4. OI peak-strike migration:** Unavailable because the current or previous snapshot is missing required data.")

        if aggressor_analysis and aggressor_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
            st.write(
                f"**5. Taker activity (raw):** Combined nearby taker buys are {aggressor_analysis['buy_vol']:,.0f} "
                f"versus sells {aggressor_analysis['sell_vol']:,.0f} ({aggressor_analysis['status']}); "
                f"calls and puts are combined, so this is not a validated BTC-direction signal."
            )
        else:
            st.write("**5. Taker activity (raw):** Unavailable or insufficient taker-volume data.")
    elif opt_forensics and "error" in opt_forensics:
        st.error(f"Error loading options data: {opt_forensics['error']}")
    else:
        st.info("PENDING: Waiting for options_collector.py to populate Supabase.")

with raw_tab:
    st.subheader("Raw Options Chain — 10 Nearest Strikes")
    if opt_forensics and "data" in opt_forensics:
        closest_df = get_nearest_strikes(opt_df, current_price, count=10)
        st.dataframe(
            closest_df.drop(columns=["distance"], errors="ignore"),
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
            },
        )
    elif opt_forensics and "error" in opt_forensics:
        st.error(f"Error loading options data: {opt_forensics['error']}")
    else:
        st.info("PENDING: Waiting for options_collector.py to populate Supabase.")

    st.markdown("---")
    with st.expander("📋 Copy/Paste Text Report for AI Support"):
        st.write("Click the copy icon in the top right of the box below to copy this for support.")

        report_lines = []
        report_lines.append("--- BTC TERMINAL REPORT ---")
        report_lines.append(f"Time (UTC): {datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}")
        report_lines.append(f"Price: ${current_price:,.2f}")
        report_lines.append(f"AMT State: {shadow_state['confirmed_state'] if shadow_state else 'N/A'}")
        report_lines.append(f"Coverage: {layer2_data['coverage']}")

        report_lines.append("\n--- LAYER 2 (FUTURES) ---")
        report_lines.append(f"OI: ${layer2_data['current_oi_usd']/1e9:.2f}B" if layer2_data["current_oi_usd"] else "OI: N/A")
        report_lines.append(f"30m Delta: {layer2_data['status']} {layer2_data.get('delta_pct', '')}")
        report_lines.append(f"Basis: {layer2_data['basis_pct']}")
        report_lines.append(f"Funding: {layer2_data['funding_rate']}")

        report_lines.append("\n--- LAYER 3 (OPTIONS) ---")
        report_lines.append(f"Walls: {walls_res}")
        report_lines.append(f"IV Skew: {iv_analysis}")
        report_lines.append(f"Options Turnover Relative to Open Interest: {vol_analysis}")
        report_lines.append(f"Migration: {migration_analysis}")
        report_lines.append(f"Aggressor Flow: {aggressor_analysis}")

        final_report = "\n".join(report_lines)
        st.code(final_report, language="text")
