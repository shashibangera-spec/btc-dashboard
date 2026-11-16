import streamlit as st
import pandas as pd
import numpy as np
import requests
import datetime
import json
from datetime import timezone
from supabase import create_client
from amt_core import fetch_binance_data
from research_options_adapter import load_canonical_research_options, parse_optional_float, validated_prior_values

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
        key = st.secrets["SUPABASE_ANON_KEY"]
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
                "funding": parse_optional_float(row.get("funding_rate")),
                "basis": parse_optional_float(row.get("basis_pct")),
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

def process_options_snapshot(client, snapshot_id, expiry_date, raw=None):
    # Keep the raw chain on one expiry. A collector snapshot contains many expiries;
    # grouping only by strike would incorrectly add OI and blend premiums across expiries.
    if raw is None:
        chain = client.table("options_oi_snapshots").select("strike_price, option_type, oi_contracts, oi_value_usd, mark_price, mark_iv, volume_24h, taker_buy_volume, taker_sell_volume").eq("snapshot_id", snapshot_id).eq("expiry_date", expiry_date).execute()
        if not chain.data: return None
        df = pd.DataFrame(chain.data)
    else:
        df = raw.copy()
        # Premium is a presentation-only field not consumed by the canonical filters.
        # Fetch it separately, but constrain it to the already-pinned snapshot + expiry.
        premium = client.table("options_oi_snapshots").select(
            "product_id, symbol, option_type, strike_price, mark_price"
        ).eq("snapshot_id", snapshot_id).eq("expiry_date", expiry_date).execute()
        if premium.data:
            premium_df = pd.DataFrame(premium.data)
            identity = ["product_id", "symbol", "option_type", "strike_price"]
            df = df.merge(premium_df, on=identity, how="left", validate="one_to_one")
        else:
            df["mark_price"] = np.nan
    if df.empty: return None
    
    pivot_oi = df.pivot_table(index='strike_price', columns='option_type', values='oi_contracts', aggfunc='sum').reset_index()
    pivot_oi_usd = df.pivot_table(index='strike_price', columns='option_type', values='oi_value_usd', aggfunc='sum').reset_index()
    # Display the API mark_price exactly as stored; no currency or contract multiplier.
    pivot_premium = df.pivot_table(index='strike_price', columns='option_type', values='mark_price', aggfunc='mean')
    # Keep both side columns present even if a snapshot has no valid mark/spot for one side.
    pivot_premium = pivot_premium.reindex(columns=['CALL', 'PUT']).reset_index()
    pivot_premium.rename(columns={'CALL': 'Mark_Call', 'PUT': 'Mark_Put'}, inplace=True)
    pivot_oi_usd.rename(columns={'CALL': 'OI_USD_Call', 'PUT': 'OI_USD_Put'}, inplace=True)
    pivot_iv = df.pivot_table(index='strike_price', columns='option_type', values='mark_iv', aggfunc='mean').reset_index()
    pivot_vol = df.pivot_table(index='strike_price', columns='option_type', values='volume_24h', aggfunc='sum').reset_index()
    
    pivot_oi.rename(columns={'CALL': 'Call OI', 'PUT': 'Put OI'}, inplace=True)
    pivot_iv.rename(columns={'CALL': 'Call IV', 'PUT': 'Put IV'}, inplace=True)
    pivot_vol.rename(columns={'CALL': 'Call Vol', 'PUT': 'Put Vol'}, inplace=True)
    
    merged = pd.merge(pivot_oi, pivot_oi_usd, on='strike_price', how='outer')
    merged = pd.merge(merged, pivot_premium, on='strike_price', how='outer')
    merged = pd.merge(merged, pivot_iv, on='strike_price', how='outer')
    merged = pd.merge(merged, pivot_vol, on='strike_price', how='outer')
    
    if 'taker_buy_volume' in df.columns:
        taker_agg = df.groupby('strike_price')[['taker_buy_volume', 'taker_sell_volume']].sum().reset_index()
        merged = pd.merge(merged, taker_agg, on='strike_price', how='outer')
    
    return merged.sort_values('strike_price')

@st.cache_data(ttl=120, show_spinner=False)
def fetch_options_forensics():
    """Load one canonical raw options snapshot and its matching derived expiry."""
    try:
        client = create_client(st.secrets["SUPABASE_URL"], st.secrets["SUPABASE_ANON_KEY"])
        canonical = load_canonical_research_options(client)
        if canonical.get("status") != "OK":
            return canonical
        snapshot = canonical["snapshot"]
        current_df = process_options_snapshot(client, snapshot.snapshot_id, snapshot.expiry_date, raw=snapshot.raw)
        previous_df = (
            process_options_snapshot(client, snapshot.previous_snapshot_id, snapshot.expiry_date, raw=snapshot.previous_raw)
            if snapshot.previous_raw is not None else None
        )
        return {
            "status": "OK", "snapshot_id": snapshot.snapshot_id,
            "timestamp": snapshot.completed_at, "previous_snapshot_id": snapshot.previous_snapshot_id,
            "previous_timestamp": snapshot.previous_completed_at, "expiry_date": snapshot.expiry_date,
            "spot_price": snapshot.spot_price, "metrics": canonical["derived"],
            "data": current_df, "previous_data": previous_df, "filters": canonical["filters"],
        }
    except Exception as exc:
        return {"status": "UNAVAILABLE", "reason": type(exc).__name__, "error": str(exc)}

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

# --- AMT ENGINE ---
@st.cache_data(ttl=120, show_spinner=False)
def fetch_amt_shadow_state():
    try:
        url = st.secrets["SUPABASE_URL"]
        key = st.secrets["SUPABASE_ANON_KEY"]
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

    current_price = completed_df.iloc[-1]['close']

    shadow_state = fetch_amt_shadow_state()
    prior_values = validated_prior_values(shadow_state)
    if prior_values is not None:
        vah, poc, val = prior_values
    else:
        vah = poc = val = np.nan

    layer2_data = fetch_layer2_supabase_data()
    opt_forensics = fetch_options_forensics()
    options_metrics = opt_forensics.get("metrics") if opt_forensics and opt_forensics.get("status") == "OK" else None
    options_reference_price = opt_forensics.get("spot_price") if opt_forensics and opt_forensics.get("status") == "OK" else None

    opt_df = opt_forensics.get("data") if opt_forensics and opt_forensics.get("status") == "OK" else None
    prev_opt_df = opt_forensics.get("previous_data") if opt_forensics else None

    # All five filter results are calculated by options_filters.py against the
    # exact pinned snapshot selected by Stage A. No dashboard-side recalculation.
    filter_results = opt_forensics.get("filters", {}) if opt_forensics and opt_forensics.get("status") == "OK" else {}
    unavailable_filter = {"status": "UNAVAILABLE", "reason": "CANONICAL_OPTIONS_SNAPSHOT_UNAVAILABLE"}
    walls_res = filter_results.get("nearby_oi", unavailable_filter)
    iv_analysis = filter_results.get("iv_skew", unavailable_filter)
    vol_analysis = filter_results.get("volume_oi", unavailable_filter)
    migration_analysis = filter_results.get("oi_migration", unavailable_filter)
    aggressor_analysis = filter_results.get("taker_activity", unavailable_filter)

    # --- COPY/PASTE LIVE REPORT FOR AI ANALYSIS ---
    report_options = options_metrics or {}
    report_shadow_state = shadow_state or {}
    report_walls = (
        "; ".join(
            f"{wall.get('type', 'OI')} local OI concentration: {float(wall['strike']):,.0f} (OI {float(wall['oi']):,.0f})"
            for wall in (walls_res.get("walls", []) if walls_res and walls_res.get("status") == "OK" else [])
        ) or "N/A"
    )
    def filter_report_status(result):
        if not result:
            return "UNAVAILABLE"
        status = result.get("status", "UNAVAILABLE")
        reason = result.get("reason")
        return f"{status} ({reason})" if reason else status
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
Nearby OI Filter: {filter_report_status(walls_res)}
IV Skew Filter: {filter_report_status(iv_analysis)} | Classification: {iv_analysis.get('classification', 'N/A')} | Skew: {f"{float(iv_analysis['skew_pct']):+.2f}%" if iv_analysis.get('status') == 'OK' and iv_analysis.get('skew_pct') is not None else 'N/A'}
24h Volume / OI Filter: {filter_report_status(vol_analysis)} | Classification: {vol_analysis.get('classification', 'N/A')} | Direction: {vol_analysis.get('direction', 'N/A')} | Call/Put: {f"{float(vol_analysis['call_ratio']):.3f}x / {float(vol_analysis['put_ratio']):.3f}x" if vol_analysis.get('status') == 'OK' else 'N/A'}
OI Migration Filter: {filter_report_status(migration_analysis)} | Classification: {migration_analysis.get('classification', 'N/A')} | Call/Put shift: {f"{float(migration_analysis['call_shift']):+,.0f} / {float(migration_analysis['put_shift']):+,.0f}" if migration_analysis.get('status') == 'OK' else 'N/A'}
Sampled Taker Activity Filter: {filter_report_status(aggressor_analysis)} | Classification: {aggressor_analysis.get('classification', 'N/A')} | Buy/Sell: {f"{float(aggressor_analysis['taker_buy']):,.0f} / {float(aggressor_analysis['taker_sell']):,.0f}" if aggressor_analysis.get('status') == 'OK' else 'N/A'} (sampled contracts only)
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
                        st.caption(f"{walls_res.get('status', 'UNAVAILABLE')}: {walls_res.get('reason', 'No valid OI result').replace('_', ' ').lower()}.")

            # 2. IV skew: positive means call IV exceeds put IV.
            with filter_cols[1]:
                with st.container(border=True):
                    st.markdown("**2. IV Skew**")
                    if iv_analysis and iv_analysis.get("status") == "OK":
                        skew_value = float(iv_analysis["skew_pct"])
                        skew_color = "green" if skew_value > 0 else "red" if skew_value < 0 else "gray"
                        skew_label = "Call > Put" if skew_value > 0 else "Put > Call" if skew_value < 0 else "Equal"
                        st.markdown(f"<div style='font-size:1.65rem;font-weight:700;color:{skew_color}'>{skew_value:+.2f}%</div>", unsafe_allow_html=True)
                        st.caption(f"{skew_label} IV (relative to put IV); five-strike average.")
                    else:
                        st.metric("Relative skew", "N/A")
                        st.caption(f"{iv_analysis.get('status', 'UNAVAILABLE')}: {iv_analysis.get('reason', 'Insufficient IV data').replace('_', ' ').lower()}.")

            # 3. Options turnover relative to OI.
            with filter_cols[2]:
                with st.container(border=True):
                    st.markdown("**3. Turnover / OI**")
                    if vol_analysis and vol_analysis.get("status") == "OK":
                        whale_flag = max(float(vol_analysis["call_ratio"]), float(vol_analysis["put_ratio"])) > 0.5
                        st.metric("Call / Put", f"{vol_analysis['call_ratio']:.2f}x / {vol_analysis['put_ratio']:.2f}x")
                        st.markdown("🐋 **Elevated turnover**" if whale_flag else "**No high-turnover flag**")
                        st.caption("Volume/OI; does not identify opening versus closing.")
                    else:
                        st.metric("Call / Put", "N/A")
                        st.caption(f"{vol_analysis.get('status', 'UNAVAILABLE')}: {vol_analysis.get('reason', 'Insufficient volume/OI data').replace('_', ' ').lower()}.")

            # Second row: two wider cards for migration and taker activity.
            filter_cols_row2 = st.columns(2)

            # 4. Peak-strike migration. Mixed peak movement is shown as Stable (not a directional signal).
            with filter_cols_row2[0]:
                with st.container(border=True):
                    st.markdown("**4. OI Migration**")
                    if migration_analysis and migration_analysis.get("status") == "OK":
                        migration_status = migration_analysis.get("classification")
                        if migration_status == "BULLISH_MIGRATION":
                            migration_label, migration_arrow = "Up", "⬆️"
                        elif migration_status == "BEARISH_MIGRATION":
                            migration_label, migration_arrow = "Down", "⬇️"
                        elif migration_status == "STABLE":
                            migration_label, migration_arrow = "Stable", "➡️"
                        elif migration_status == "MILD_BULLISH":
                            migration_label, migration_arrow = "Mixed", "↗️"
                        else:
                            migration_label, migration_arrow = "N/A", "—"
                        st.metric("Peak-strike shift", f"{migration_arrow} {migration_label}")
                        st.caption(f"Call {migration_analysis['call_shift']:+,.0f} | Put {migration_analysis['put_shift']:+,.0f}")
                    else:
                        st.metric("Peak-strike shift", "N/A")
                        st.caption(f"{migration_analysis.get('status', 'UNAVAILABLE')}: {migration_analysis.get('reason', 'Requires two usable snapshots').replace('_', ' ').lower()}.")

            # 5. Combined nearby taker activity.
            with filter_cols_row2[1]:
                with st.container(border=True):
                    st.markdown("**5. Taker Activity**")
                    if aggressor_analysis and aggressor_analysis.get("status") == "OK":
                        buy_volume = float(aggressor_analysis["taker_buy"])
                        sell_volume = float(aggressor_analysis["taker_sell"])
                        if aggressor_analysis["classification"] == "AGGRESSIVE_BUYING":
                            taker_color, taker_label = "green", "Buy dominant"
                        elif aggressor_analysis["classification"] == "AGGRESSIVE_SELLING":
                            taker_color, taker_label = "red", "Sell dominant"
                        else:
                            taker_color, taker_label = "gray", "No clear dominance"
                        st.markdown(f"<div style='font-size:1.05rem;font-weight:700;color:{taker_color}'>Buy {buy_volume:,.0f}<br>Sell {sell_volume:,.0f}</div>", unsafe_allow_html=True)
                        st.caption(taker_label + "; calls and puts combined.")
                    else:
                        st.metric("Buy / Sell", "N/A")
                        st.caption(f"{aggressor_analysis.get('status', 'UNAVAILABLE')}: {aggressor_analysis.get('reason', 'Insufficient taker-volume data').replace('_', ' ').lower()}.")
        # --- LAYER 2: OI CONCENTRATION MAP ---
        st.markdown("---")
        st.subheader("📊 OI Concentration Map")
        st.caption("Visual concentration across the same 10 nearest strikes shown in the evidence table. Call OI extends left; Put OI extends right.")
        if opt_forensics and "data" in opt_forensics:
            chart_df = get_nearest_strikes(opt_df, options_reference_price, count=10).copy()
            if not chart_df.empty and "Call OI" in chart_df.columns and "Put OI" in chart_df.columns:
                chart_df["Call OI"] = pd.to_numeric(chart_df["Call OI"], errors="coerce").fillna(0)
                chart_df["Put OI"] = pd.to_numeric(chart_df["Put OI"], errors="coerce").fillna(0)
                chart_df["Call OI Display"] = -chart_df["Call OI"]
                chart_df["Strike"] = chart_df["strike_price"].map(lambda x: "$" + format(float(x), ",.0f"))
                oi_chart_data = chart_df.melt(
                    id_vars=["Strike"],
                    value_vars=["Call OI Display", "Put OI"],
                    var_name="Side",
                    value_name="OI",
                )
                oi_chart_data["Side"] = oi_chart_data["Side"].replace({
                    "Call OI Display": "Call OI",
                    "Put OI": "Put OI",
                })
                st.vega_lite_chart(
                    oi_chart_data,
                    {
                        "mark": "bar",
                        "encoding": {
                            "y": {"field": "Strike", "type": "ordinal", "sort": None, "title": "Strike"},
                            "x": {"field": "OI", "type": "quantitative", "title": "OI (Call left / Put right)"},
                            "color": {"field": "Side", "type": "nominal", "legend": {"title": None}},
                            "tooltip": [
                                {"field": "Strike", "type": "nominal"},
                                {"field": "Side", "type": "nominal"},
                                {"field": "OI", "type": "quantitative", "format": ",.0f"},
                            ],
                        },
                        "height": 420,
                    },
                    use_container_width=True,
                )
            else:
                st.info("No usable Call/Put OI columns are available for the concentration map.")
        elif opt_forensics and "error" in opt_forensics:
            st.error(f"Error loading options data: {opt_forensics['error']}")
        else:
            st.info("PENDING: Waiting for options_collector.py to populate Supabase.")

    with raw_tab:
        st.subheader("Raw Options Chain — 10 Nearest Strikes")
        if opt_forensics and "data" in opt_forensics:
            closest_df = get_nearest_strikes(opt_df, options_reference_price, count=10).copy()
            evidence_df = closest_df.drop(columns=["distance"], errors="ignore").copy()

            # Presentation-only evidence markers: reuse existing analyzer/derived outputs.
            nearby_strikes = set()
            if walls_res and walls_res.get("status") == "OK":
                nearby_strikes = {
                    float(w["strike"])
                    for w in walls_res.get("walls", [])
                    if w.get("strike") is not None
                }

            max_pain_strike = None
            if options_metrics and options_metrics.get("max_pain_strike") is not None:
                try:
                    max_pain_strike = float(options_metrics["max_pain_strike"])
                except (TypeError, ValueError):
                    max_pain_strike = None

            max_call_oi = evidence_df["Call OI"].max() if "Call OI" in evidence_df.columns else None
            max_put_oi = evidence_df["Put OI"].max() if "Put OI" in evidence_df.columns else None

            def build_evidence(row):
                labels = []
                try:
                    strike_value = float(row.get("strike_price"))
                except (TypeError, ValueError):
                    strike_value = None
                if strike_value is not None and strike_value in nearby_strikes:
                    labels.append("📍 Nearby OI")
                if max_pain_strike is not None and strike_value == max_pain_strike:
                    labels.append("🎯 Max Pain")
                if max_call_oi is not None and pd.notna(row.get("Call OI")) and row.get("Call OI") == max_call_oi:
                    labels.append("🔵 Max Call OI")
                if max_put_oi is not None and pd.notna(row.get("Put OI")) and row.get("Put OI") == max_put_oi:
                    labels.append("🟠 Max Put OI")
                # CMP anchor uses the already-selected nearest-expiry ATM strike
                # and the latest completed-candle market price.
                if atm_strike is not None and strike_value is not None and np.isclose(strike_value, atm_strike):
                    labels.append(f"⚡ CMP: ${latest_price:,.2f}")
                return " · ".join(labels)

            # Presentation-only Delta-style order-book layout.
            # Keep the existing evidence labels and source values; this block only
            # renames, formats, orders, and styles the dataframe for display.
            # Reuse the already-calculated options ATM strike and latest market price.
            try:
                atm_value = options_metrics.get("atm_strike") if options_metrics else None
                atm_strike = float(atm_value) if atm_value is not None and np.isfinite(float(atm_value)) else None
            except (TypeError, ValueError, OverflowError):
                atm_strike = None
            latest_price = float(current_price)

            evidence_df.insert(0, "Signal", evidence_df.apply(build_evidence, axis=1))
            evidence_df = evidence_df.rename(columns={
                "strike_price": "Strike",
                "OI_USD_Call": "Call OI (USD)",
                "OI_USD_Put": "Put OI (USD)",
                "Mark_Call": "Call Prem",
                "Mark_Put": "Put Prem",
            })

            # Ensure optional display fields exist even when one side is absent.
            display_columns = [
                "Signal",
                "Call Prem",
                "Call OI (USD)",
                "Call Vol",
                "Call IV",
                "Strike",
                "Put IV",
                "Put Vol",
                "Put OI (USD)",
                "Put Prem",
            ]
            for column in display_columns:
                if column not in evidence_df.columns:
                    evidence_df[column] = pd.NA

            # Contract-count columns and taker-volume diagnostics remain available
            # to the research calculations, but are intentionally omitted here.
            display_df = evidence_df.loc[:, display_columns].copy()

            def highlight_order_book(row):
                styles = pd.Series("", index=row.index)

                # CMP row takes precedence over every other evidence highlight.
                try:
                    strike_value = float(row.get("Strike"))
                except (TypeError, ValueError, OverflowError):
                    strike_value = None
                if (
                    atm_strike is not None
                    and strike_value is not None
                    and np.isclose(strike_value, atm_strike)
                ):
                    styles[:] = "background-color: #6A1B9A; color: white; font-weight: bold"
                    return styles

                # Keep the existing max-OI evidence emphasis, now on USD notional.
                if max_call_oi is not None and pd.notna(row.get("Call OI (USD)")):
                    call_contracts = closest_df.loc[
                        closest_df["strike_price"] == row.get("Strike"), "Call OI"
                    ]
                    if not call_contracts.empty and pd.notna(call_contracts.iloc[0]) and call_contracts.iloc[0] == max_call_oi:
                        styles["Call OI (USD)"] = "background-color: #dbeeff; color: black; font-weight: 700"
                if max_put_oi is not None and pd.notna(row.get("Put OI (USD)")):
                    put_contracts = closest_df.loc[
                        closest_df["strike_price"] == row.get("Strike"), "Put OI"
                    ]
                    if not put_contracts.empty and pd.notna(put_contracts.iloc[0]) and put_contracts.iloc[0] == max_put_oi:
                        styles["Put OI (USD)"] = "background-color: #ffe6cc; color: black; font-weight: 700"

                # A neutral grey strike column visually separates Calls and Puts.
                strike_style = "background-color: #eef1f4; color: #20252b; font-weight: 700; border-left: 1px solid #c7cdd4; border-right: 1px solid #c7cdd4"
                try:
                    strike_value = float(row.get("Strike"))
                except (TypeError, ValueError):
                    strike_value = None
                if max_pain_strike is not None and strike_value == max_pain_strike:
                    strike_style += "; box-shadow: inset 0 0 0 1px #b7a45a"
                styles["Strike"] = strike_style
                return styles

            styled_evidence = display_df.style.apply(highlight_order_book, axis=1).format({
                "Call OI (USD)": lambda value: "" if pd.isna(value) else f"${value / 1_000_000:,.2f}M",
                "Put OI (USD)": lambda value: "" if pd.isna(value) else f"${value / 1_000_000:,.2f}M",
                "Call Prem": lambda value: "" if pd.isna(value) else f"${value:,.2f}",
                "Put Prem": lambda value: "" if pd.isna(value) else f"${value:,.2f}",
                "Call Vol": lambda value: "" if pd.isna(value) else f"{value:,.0f}",
                "Put Vol": lambda value: "" if pd.isna(value) else f"{value:,.0f}",
                "Call IV": lambda value: "" if pd.isna(value) else f"{value:.4f}",
                "Put IV": lambda value: "" if pd.isna(value) else f"{value:.4f}",
                "Strike": lambda value: "" if pd.isna(value) else f"${value:,.0f}",
            })
            st.caption("📍 Nearby OI · 🎯 Max Pain · 🔵 Max Call OI · 🟠 Max Put OI")
            st.dataframe(
                styled_evidence,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Signal": st.column_config.TextColumn("Signal", width="large"),
                    "Call Prem": st.column_config.TextColumn("Call Prem"),
                    "Call OI (USD)": st.column_config.TextColumn("Call OI (USD)"),
                    "Call Vol": st.column_config.TextColumn("Call Vol"),
                    "Call IV": st.column_config.TextColumn("Call IV"),
                    "Strike": st.column_config.TextColumn("Strike"),
                    "Put IV": st.column_config.TextColumn("Put IV"),
                    "Put Vol": st.column_config.TextColumn("Put Vol"),
                    "Put OI (USD)": st.column_config.TextColumn("Put OI (USD)"),
                    "Put Prem": st.column_config.TextColumn("Put Prem"),
                },
            )

            # Separate research view for aggressor-side volume; the compact
            # Calls–Strike–Puts order-book table above remains unchanged.
            st.markdown("---")
            st.subheader("Aggressor Taker Volume (10 Nearest Strikes)")
            taker_columns = ["strike_price", "taker_buy_volume", "taker_sell_volume"]
            if all(column in closest_df.columns for column in taker_columns):
                taker_df = closest_df.loc[:, taker_columns].copy()
                taker_df = taker_df.rename(columns={"strike_price": "Strike"})
                taker_df["taker_buy_volume"] = pd.to_numeric(
                    taker_df["taker_buy_volume"], errors="coerce"
                )
                taker_df["taker_sell_volume"] = pd.to_numeric(
                    taker_df["taker_sell_volume"], errors="coerce"
                )

                def highlight_taker_strike(row):
                    styles = pd.Series("", index=row.index)
                    styles["Strike"] = (
                        "background-color: #eef1f4; color: #20252b; "
                        "font-weight: 700; border-left: 1px solid #c7cdd4; "
                        "border-right: 1px solid #c7cdd4"
                    )
                    return styles

                styled_taker = taker_df.style.apply(
                    highlight_taker_strike, axis=1
                ).format({
                    "Strike": lambda value: "" if pd.isna(value) else f"${value:,.0f}",
                    "taker_buy_volume": lambda value: "" if pd.isna(value) else f"{value:,.0f}",
                    "taker_sell_volume": lambda value: "" if pd.isna(value) else f"{value:,.0f}",
                })
                st.dataframe(
                    styled_taker,
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "Strike": st.column_config.TextColumn("Strike"),
                        "taker_buy_volume": st.column_config.TextColumn("taker_buy_volume"),
                        "taker_sell_volume": st.column_config.TextColumn("taker_sell_volume"),
                    },
                )
            else:
                st.info("Aggressor taker-volume fields are unavailable in this snapshot.")
        elif opt_forensics and "error" in opt_forensics:
            st.error(f"Error loading options data: {opt_forensics['error']}")
        else:
            st.info("PENDING: Waiting for options_collector.py to populate Supabase.")



render_live_dashboard()
