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
@st.cache_data(ttl=120, show_spinner=False)
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

@st.cache_data(ttl=120, show_spinner=False)
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
            "snapshot_id": latest_snapshot["snapshot_id"],
            "timestamp": latest_snapshot["completed_at"],
            "previous_timestamp": previous_snapshot["completed_at"] if previous_snapshot else None,
            "data": current_df,
            "previous_data": previous_df
        }
    except Exception as e:
        return {"error": str(e)}

@st.cache_data(ttl=120, show_spinner=False)
def fetch_nearest_valid_options_metrics():
    """Latest collector run, then nearest future expiry with a complete source chain."""
    empty = {"status": "NO_DERIVED_METRICS", "expiry_date": None, "metric_status": {}}
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        runs = client.table("options_collection_runs").select(
            "snapshot_id, completed_at, status"
        ).in_("status", ["SUCCESS", "PARTIAL"]).order("completed_at", desc=True).limit(1).execute()
        if not runs.data:
            return empty
        run = runs.data[0]
        derived = client.table("options_derived_snapshots").select(
            "snapshot_id,expiry_date,calculated_at,source_collected_at,underlying_spot_price,"
            "max_pain_strike,atm_strike,atm_straddle_price,atm_straddle_pct_spot,"
            "call_gex_wall_strike,call_gex_wall_value,put_gex_wall_strike,put_gex_wall_value,"
            "gex_contract_multiplier,local_oi_pcr,local_put_oi_contracts,local_call_oi_contracts,"
            "local_oi_band_pct,metric_version,metric_status"
        ).eq("snapshot_id", run["snapshot_id"]).order("expiry_date", desc=False).execute()
        rows = derived.data or []
        if not rows:
            return {**empty, "status": "NO_DERIVED_ROWS", "source_collected_at": run.get("completed_at")}
        today = datetime.datetime.now(timezone.utc).date()
        future_rows = []
        for row in rows:
            expiry_value = pd.to_datetime(row.get("expiry_date"), errors="coerce")
            if pd.notna(expiry_value) and expiry_value.date() >= today:
                future_rows.append(row)
        valid_rows = [
            row for row in future_rows
            if isinstance(row.get("metric_status"), dict)
            and row["metric_status"].get("chain") == "OK"
        ]
        selected = valid_rows[0] if valid_rows else (future_rows[0] if future_rows else rows[0])
        selected = dict(selected)
        selected["run_completed_at"] = run.get("completed_at")
        selected["run_status"] = run.get("status")
        selected["status"] = "OK" if selected.get("metric_status", {}).get("chain") == "OK" else "INCOMPLETE_CHAIN"
        return selected
    except Exception as exc:
        return {**empty, "status": f"ERROR: {exc}"}


@st.cache_data(ttl=120)
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


# --- AMT ENGINE ---
@st.cache_data(ttl=120, show_spinner=False)
def fetch_amt_shadow_state():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_SERVICE_ROLE_KEY"]
        client = create_client(url, key)
        res = client.table("amt_snapshots").select("candle_open_time, confirmed_state, developing_state, project_echo_code, project_echo_name, project_echo_version, developing_poc, developing_vah, developing_val").order("candle_open_time", desc=True).limit(1).execute()
        if res.data: return res.data[0]
        return None
    except: return None


@st.fragment(run_every="3m")
def render_live_dashboard():
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


    shadow_state = fetch_amt_shadow_state()
    layer2_data = fetch_layer2_supabase_data()
    opt_forensics = fetch_options_forensics()
    options_metrics = fetch_nearest_valid_options_metrics()

    opt_df = opt_forensics["data"] if opt_forensics and "data" in opt_forensics else None
    prev_opt_df = opt_forensics.get("previous_data") if opt_forensics else None

    # Execute Analyzers
    walls_res = analyze_neighboring_strikes(opt_df, current_price)
    iv_analysis = analyze_iv_skew(opt_df, current_price)
    vol_analysis = analyze_volume_vs_oi(opt_df, current_price)
    migration_analysis = analyze_oi_migration(opt_df, prev_opt_df)
    aggressor_analysis = analyze_aggressor_trades(opt_df, current_price)

    # --- COPY/PASTE LIVE REPORT FOR AI ANALYSIS ---
    report_options = options_metrics or {}
    report_shadow_state = shadow_state or {}
    report_walls = (
        "; ".join(
            f"{wall.get('type', 'OI')} local OI concentration: {float(wall['strike']):,.0f} (OI {float(wall['oi']):,.0f})"
            for wall in (walls_res.get("walls", []) if walls_res and walls_res.get("status") == "OK" else [])
        ) or "N/A"
    )
    report_text = f"""--- BTC TERMINAL LIVE REPORT ---
Time (UTC): {datetime.datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')}

[AMT COCKPIT]
Current Price: USD {float(current_price):,.2f}
AMT State: {report_shadow_state.get('confirmed_state') or 'N/A'}
Developing State: {report_shadow_state.get('developing_state') or 'N/A'}
Project Echo: {report_shadow_state.get('project_echo_code') or 'N/A'} / {report_shadow_state.get('project_echo_name') or 'N/A'} (v{report_shadow_state.get('project_echo_version') or 'N/A'})
Prior VAH: {f'USD {float(vah):,.2f}' if pd.notna(vah) else 'N/A'}
Prior POC: {f'USD {float(poc):,.2f}' if pd.notna(poc) else 'N/A'}
Prior VAL: {f'USD {float(val):,.2f}' if pd.notna(val) else 'N/A'}
Developing VAH: {f'USD {float(report_shadow_state["developing_vah"]):,.2f}' if report_shadow_state.get('developing_vah') is not None else 'N/A'}
Developing POC: {f'USD {float(report_shadow_state["developing_poc"]):,.2f}' if report_shadow_state.get('developing_poc') is not None else 'N/A'}
Developing VAL: {f'USD {float(report_shadow_state["developing_val"]):,.2f}' if report_shadow_state.get('developing_val') is not None else 'N/A'}

[FUTURES RESEARCH]
Aggregate OI: {f'USD {float(layer2_data["current_oi_usd"])/1e9:.2f}B' if layer2_data.get('current_oi_usd') is not None else 'N/A'}
30m OI Delta Status: {layer2_data.get('status', 'N/A')}
30m OI Delta: {f'{float(layer2_data["delta_pct"]):+.2f}% ({float(layer2_data["delta_usd"]):+,.0f} USD)' if layer2_data.get('status') == 'OK' and layer2_data.get('delta_pct') is not None and layer2_data.get('delta_usd') is not None else 'N/A'}
Weighted Basis: {f'{float(layer2_data["basis_pct"]):+.4f}%' if layer2_data.get('basis_pct') is not None else 'N/A'}
Weighted Funding: {f'{float(layer2_data["funding_rate"]):+.4f}%' if layer2_data.get('funding_rate') is not None else 'N/A'}
Exchange Coverage: {layer2_data.get('coverage', 'UNKNOWN')}

[OPTIONS RESEARCH]
Expiry: {report_options.get('expiry_date') or 'N/A'}
Options Metric Status: {report_options.get('status', 'N/A')}
Max Pain (theoretical): {f'USD {float(report_options["max_pain_strike"]):,.0f}' if report_options.get('max_pain_strike') is not None else 'N/A'}
ATM Straddle: {f'USD {float(report_options["atm_straddle_price"]):,.2f}' if report_options.get('atm_straddle_price') is not None else 'N/A'}
ATM Straddle (% Spot): {f'{float(report_options["atm_straddle_pct_spot"]):.2f}%' if report_options.get('atm_straddle_pct_spot') is not None else 'N/A'}
Local OI PCR (±10%): {f'{float(report_options["local_oi_pcr"]):.3f}' if report_options.get('local_oi_pcr') is not None else 'N/A'}
Gross Call GEX Concentration Wall: {f'USD {float(report_options["call_gex_wall_strike"]):,.0f}' if report_options.get('call_gex_wall_strike') is not None else 'N/A'}
Call GEX (model-derived, unsigned): {f'{float(report_options["call_gex_wall_value"]):,.2f}' if report_options.get('call_gex_wall_value') is not None else 'N/A'}
Gross Put GEX Concentration Wall: {f'USD {float(report_options["put_gex_wall_strike"]):,.0f}' if report_options.get('put_gex_wall_strike') is not None else 'N/A'}
Put GEX (model-derived, unsigned): {f'{float(report_options["put_gex_wall_value"]):,.2f}' if report_options.get('put_gex_wall_value') is not None else 'N/A'}
Nearby OI Concentrations: {report_walls}
Metric Quality: {report_options.get('metric_status', 'N/A')}

Note: OI/GEX concentrations are descriptive positioning measurements. Gross GEX is unsigned and does not identify dealer positioning or net gamma. AMT/price action remains primary; derivatives are context only."""

    with st.expander("📋 Copy/Paste Text Report for AI", expanded=False):
        st.caption("Copy the text block below and paste it into your AI chat for analysis.")
        st.code(report_text, language="text")

    # --- RENDER UI: AMT-FIRST CONFLUENCE COCKPIT ---
    st.markdown("### AMT + Price Action Lead; Derivatives Provide Context")
    st.caption("Futures and options are research measurements, not standalone directional signals.")

    # Top cockpit: three primary readouts.
    c1, c2, c3 = st.columns(3)
    c1.metric("Current Price", f"${current_price:,.2f}")
    c2.metric("AMT State", shadow_state["confirmed_state"] if shadow_state else "N/A")

    if shadow_state:
        echo_label = shadow_state.get("project_echo_name") or shadow_state.get("project_echo_code") or "N/A"
        st.caption(
            f"Project Echo: {echo_label} "
            f"({shadow_state.get('project_echo_code') or 'NO_CODE'}; "
            f"version {shadow_state.get('project_echo_version') or 'N/A'}) | "
            f"Developing state: {shadow_state.get('developing_state') or 'N/A'}"
        )
    else:
        st.caption("Project Echo: N/A — no AMT snapshot available.")

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

    # Prior-session AMT reference levels, calculated from the completed prior session above.
    level1, level2, level3 = st.columns(3)
    level1.metric("Prior VAH", f"${vah:,.2f}" if pd.notna(vah) else "N/A")
    level2.metric("Prior POC", f"${poc:,.2f}" if pd.notna(poc) else "N/A")
    level3.metric("Prior VAL", f"${val:,.2f}" if pd.notna(val) else "N/A")

    # Developing intraday profile levels, calculated by amt_collector.py.
    developing_levels = st.columns(3)
    developing_poc = shadow_state.get("developing_poc") if shadow_state else None
    developing_vah = shadow_state.get("developing_vah") if shadow_state else None
    developing_val = shadow_state.get("developing_val") if shadow_state else None
    developing_levels[0].metric(
        "Developing VAH",
        f"${float(developing_vah):,.2f}" if developing_vah is not None else "N/A",
    )
    developing_levels[1].metric(
        "Developing POC",
        f"${float(developing_poc):,.2f}" if developing_poc is not None else "N/A",
    )
    developing_levels[2].metric(
        "Developing VAL",
        f"${float(developing_val):,.2f}" if developing_val is not None else "N/A",
    )

    st.markdown("---")
    st.subheader("Confluence Table")
    st.caption("AMT determines the auction read; derivatives qualify context and do not act as standalone signals.")
    current_amt_state = str((shadow_state or {}).get("confirmed_state") or "N/A").upper()
    state_message = (
        f"Current AMT state: {current_amt_state}. Downside discovery is a condition to monitor, not a short signal."
        if "BELOW" in current_amt_state or "DOWNSIDE" in current_amt_state
        else f"Current AMT state: {current_amt_state}. Upside acceptance still requires price confirmation."
        if "ABOVE" in current_amt_state or "UPSIDE" in current_amt_state
        else f"Current AMT state: {current_amt_state}. Use the reference conditions below only when price confirms them."
    )
    with st.container(border=True):
        if "BELOW" in current_amt_state or "DOWNSIDE" in current_amt_state:
            st.warning(state_message)
        elif "ABOVE" in current_amt_state or "UPSIDE" in current_amt_state:
            st.success(state_message)
        else:
            st.info(state_message)
        confluence_rows = [
            {
                "AMT / Price Action": "Accepts above prior value",
                "Derivatives context": "OI rising; basis and funding",
                "Read-through": "Exposure is expanding alongside the auction; require continued acceptance.",
            },
            {
                "AMT / Price Action": "Rejects at prior VAH",
                "Derivatives context": "Nearby call OI; call IV",
                "Read-through": "Potential overhead context; confirm rejection in price.",
            },
            {
                "AMT / Price Action": "Accepts below prior VAL",
                "Derivatives context": "OI rising; basis weakening",
                "Read-through": "Exposure accompanies downside discovery; not a standalone short signal.",
            },
            {
                "AMT / Price Action": "Re-enters prior value",
                "Derivatives context": "OI declining",
                "Read-through": "May indicate position reduction; assess whether value is reclaimed.",
            },
            {
                "AMT / Price Action": "Balanced auction",
                "Derivatives context": "Mixed, neutral, stale or incomplete data",
                "Read-through": "Keep the AMT read; wait for clearer price behaviour.",
            },
        ]
        confluence_df = pd.DataFrame(confluence_rows)
        st.dataframe(
            confluence_df,
            use_container_width=True,
            hide_index=True,
            height=245,
            column_config={
                "AMT / Price Action": st.column_config.TextColumn("AMT / Price Action", width="medium"),
                "Derivatives context": st.column_config.TextColumn("Derivatives context", width="medium"),
                "Read-through": st.column_config.TextColumn("Read-through", width="large"),
            },
        )

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
        st.subheader("Options Research Panel — Nearest Valid Expiry")
        if options_metrics and options_metrics.get("expiry_date"):
            metric_status = options_metrics.get("metric_status") or {}
            st.caption(
                f"Expiry: {options_metrics.get('expiry_date')} | "
                f"Source collected: {options_metrics.get('source_collected_at') or 'N/A'} | "
                f"Calculated: {options_metrics.get('calculated_at') or 'N/A'} | "
                f"Chain: {metric_status.get('chain', 'UNKNOWN')}"
            )
            om1, om2, om3 = st.columns(3)
            om1.metric(
                "Max Pain (theoretical)",
                f"${float(options_metrics['max_pain_strike']):,.0f}"
                if options_metrics.get("max_pain_strike") is not None else "N/A",
            )
            om2.metric(
                "ATM Straddle",
                f"${float(options_metrics['atm_straddle_price']):,.2f}"
                if options_metrics.get("atm_straddle_price") is not None else "N/A",
                f"{float(options_metrics['atm_straddle_pct_spot']):.2f}% of spot"
                if options_metrics.get("atm_straddle_pct_spot") is not None else None,
            )
            om3.metric(
                "Local OI PCR (±10%)",
                f"{float(options_metrics['local_oi_pcr']):.3f}"
                if options_metrics.get("local_oi_pcr") is not None else "N/A",
            )
            om4, om5, om6, om7 = st.columns(4)
            om4.metric(
                "Gross Call GEX Concentration",
                f"${float(options_metrics['call_gex_wall_strike']):,.0f}"
                if options_metrics.get("call_gex_wall_strike") is not None else "N/A",
            )
            om5.metric(
                "Call GEX (model-derived)",
                f"${float(options_metrics['call_gex_wall_value']):,.2f}"
                if options_metrics.get("call_gex_wall_value") is not None else "N/A",
            )
            om6.metric(
                "Gross Put GEX Concentration",
                f"${float(options_metrics['put_gex_wall_strike']):,.0f}"
                if options_metrics.get("put_gex_wall_strike") is not None else "N/A",
            )
            om7.metric(
                "Put GEX (model-derived)",
                f"${float(options_metrics['put_gex_wall_value']):,.2f}"
                if options_metrics.get("put_gex_wall_value") is not None else "N/A",
            )
            st.caption(
                "GEX is model-derived using a 0.001 BTC-per-contract multiplier and standard API gamma. "
                "Gross unsigned OI concentration only; this does not identify dealer positioning or net gamma."
            )
            st.caption(
                f"ATM strike: ${float(options_metrics['atm_strike']):,.0f}"
                if options_metrics.get("atm_strike") is not None else "ATM strike: N/A"
            )
            st.caption(
                "Metric quality: " + " | ".join(
                    f"{key}: {value}" for key, value in metric_status.items()
                )
            )
        else:
            st.info(
                "No complete-chain derived options snapshot is available for an active expiry. "
                f"Status: {options_metrics.get('status', 'N/A') if options_metrics else 'N/A'}"
            )

        st.markdown("---")
        st.subheader("Options Positioning — Five Existing Research Filters")
        if opt_forensics and "data" in opt_forensics:
            st.caption(f"Latest successful snapshot: {opt_forensics['timestamp']}")
            st.caption("Five-strike research snapshot; these measurements describe options positioning, not BTC direction.")
            filter_cols = st.columns(3)

            # 1. Nearby OI concentration
            with filter_cols[0]:
                with st.container(border=True):
                    st.markdown("**1. Nearby OI**")
                    if walls_res and walls_res.get("status") == "OK":
                        nearby_walls = walls_res.get("walls", [])
                        if nearby_walls:
                            wall_strikes = list(dict.fromkeys(
                                f"${float(w['strike']):,.0f}" for w in nearby_walls
                            ))
                            st.metric("Detected strike", f"🟢 {' / '.join(wall_strikes)}")
                            st.caption("Local concentration detected; not a guaranteed barrier.")
                        else:
                            st.metric("Detected strike", "🔴 None")
                            st.caption("No local OI peak detected.")
                    else:
                        st.metric("Detected strike", "N/A")
                        st.caption("Insufficient OI data.")

            # 2. IV skew: positive means call IV exceeds put IV.
            with filter_cols[1]:
                with st.container(border=True):
                    st.markdown("**2. IV Skew**")
                    if iv_analysis and iv_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
                        skew_value = float(iv_analysis["skew_pct"])
                        skew_color = "green" if skew_value > 0 else "red" if skew_value < 0 else "gray"
                        skew_label = "Call > Put" if skew_value > 0 else "Put > Call" if skew_value < 0 else "Equal"
                        st.markdown(f"<div style='font-size:1.65rem;font-weight:700;color:{skew_color}'>{skew_value:+.2f}%</div>", unsafe_allow_html=True)
                        st.caption(f"{skew_label} IV (relative to put IV); five-strike average.")
                    else:
                        st.metric("Relative skew", "N/A")
                        st.caption("Insufficient IV data.")

            # 3. Options turnover relative to OI.
            with filter_cols[2]:
                with st.container(border=True):
                    st.markdown("**3. Turnover / OI**")
                    if vol_analysis and vol_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
                        whale_flag = max(float(vol_analysis["call_ratio"]), float(vol_analysis["put_ratio"])) > 0.5
                        st.metric("Call / Put", f"{vol_analysis['call_ratio']:.2f}x / {vol_analysis['put_ratio']:.2f}x")
                        st.markdown("🐋 **Elevated turnover**" if whale_flag else "**No high-turnover flag**")
                        st.caption("Volume/OI; does not identify opening versus closing.")
                    else:
                        st.metric("Call / Put", "N/A")
                        st.caption("Insufficient volume/OI data.")

            # Second row: two wider cards for migration and taker activity.
            filter_cols_row2 = st.columns(2)

            # 4. Peak-strike migration. Mixed peak movement is shown as Stable (not a directional signal).
            with filter_cols_row2[0]:
                with st.container(border=True):
                    st.markdown("**4. OI Migration**")
                    if migration_analysis and migration_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
                        migration_status = migration_analysis.get("status")
                        if migration_status == "BULLISH_MIGRATION":
                            migration_label, migration_arrow = "Up", "⬆️"
                        elif migration_status == "BEARISH_MIGRATION":
                            migration_label, migration_arrow = "Down", "⬇️"
                        else:
                            migration_label, migration_arrow = "Stable", "➡️"
                        st.metric("Peak-strike shift", f"{migration_arrow} {migration_label}")
                        st.caption(f"Call {migration_analysis['call_shift']:+,.0f} | Put {migration_analysis['put_shift']:+,.0f}")
                    else:
                        st.metric("Peak-strike shift", "N/A")
                        st.caption("Requires two usable snapshots.")

            # 5. Combined nearby taker activity.
            with filter_cols_row2[1]:
                with st.container(border=True):
                    st.markdown("**5. Taker Activity**")
                    if aggressor_analysis and aggressor_analysis.get("status") not in ["UNAVAILABLE", "INSUFFICIENT_DATA"]:
                        buy_volume = float(aggressor_analysis["buy_vol"])
                        sell_volume = float(aggressor_analysis["sell_vol"])
                        if aggressor_analysis["status"] == "AGGRESSIVE_BUYING":
                            taker_color, taker_label = "green", "Buy dominant"
                        elif aggressor_analysis["status"] == "AGGRESSIVE_SELLING":
                            taker_color, taker_label = "red", "Sell dominant"
                        else:
                            taker_color, taker_label = "gray", "No clear dominance"
                        st.markdown(f"<div style='font-size:1.05rem;font-weight:700;color:{taker_color}'>Buy {buy_volume:,.0f}<br>Sell {sell_volume:,.0f}</div>", unsafe_allow_html=True)
                        st.caption(taker_label + "; calls and puts combined.")
                    else:
                        st.metric("Buy / Sell", "N/A")
                        st.caption("Insufficient taker-volume data.")
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



render_live_dashboard()
