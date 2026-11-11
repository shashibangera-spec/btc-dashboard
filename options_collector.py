"""
Delta Exchange India — BTC Options OI & Aggressor Trade Collector
"""

import os
import re
import sys
import time
import math
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import requests
from supabase import create_client

# ---------------------------------------------------------
# CONFIGURATION
# ---------------------------------------------------------
BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
TICKERS_URL = f"{BASE_URL}/v2/tickers"
TRADES_URL = f"{BASE_URL}/v2/trades"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "btc-options-oi-collector/1.2",
}

TIMEOUT = (5, 30)
MAX_RETRIES = 3
RETRY_DELAY = 2

EXCHANGE = "DELTA_INDIA"
UNDERLYING = "BTC"

# ---------------------------------------------------------
# GENERAL HELPERS
# ---------------------------------------------------------
def utc_now():
    return datetime.now(timezone.utc)

def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat()

def to_float(value):
    if value is None or isinstance(value, bool): return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value: return None
    try: result = float(value)
    except (TypeError, ValueError, OverflowError): return None
    if not math.isfinite(result): return None
    return result

def to_int(value):
    result = to_float(value)
    if result is None or not result.is_integer(): return None
    return int(result)

# ---------------------------------------------------------
# EXPIRY IDENTIFICATION & MAPPING
# ---------------------------------------------------------
def expiry_from_ticker(ticker):
    for key in ("expiry_date", "settlement_time", "expiry"):
        value = ticker.get(key)
        if not value or isinstance(value, (int, float)): continue
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try: return datetime.strptime(text[:10], fmt).date()
            except ValueError: pass
    symbol = ticker.get("symbol") or ticker.get("product_symbol") or ""
    match = re.search(r"-(\d{6})$", str(symbol))
    if match:
        try: return datetime.strptime(match.group(1), "%d%m%y").date()
        except ValueError: return None
    return None

def select_nearest_expiry(rows):
    today_utc = utc_now().date()
    expiries = sorted({expiry for ticker in rows if isinstance(ticker, dict) for expiry in [expiry_from_ticker(ticker)] if expiry is not None and expiry >= today_utc})
    if not expiries: raise RuntimeError("No active/future BTC options expiry identified.")
    return expiries[0]

def get_option_type(ticker):
    contract_type = str(ticker.get("contract_type") or ticker.get("option_type") or "").lower()
    if "call" in contract_type: return "CALL"
    if "put" in contract_type: return "PUT"
    symbol = str(ticker.get("symbol") or ticker.get("product_symbol") or "")
    if symbol.startswith("C-"): return "CALL"
    if symbol.startswith("P-"): return "PUT"
    return None

def exchange_timestamp_to_iso(value):
    if value is None: return None
    try:
        timestamp_us = int(str(value).strip())
        return iso_utc(datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc))
    except (ValueError, TypeError, OverflowError, OSError): return None

# ---------------------------------------------------------
# API FETCHING
# ---------------------------------------------------------
def get_api_data(url, params, retries=MAX_RETRIES):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict): raise RuntimeError("Unexpected API response type.")
            if payload.get("success") is False: raise RuntimeError(f"API returned success=false: {payload}")
            return payload.get("result", [])
        except Exception as exc:
            last_error = exc
            if attempt < retries: time.sleep(RETRY_DELAY * attempt)
    print(f"API failed: {url} | {last_error}", file=sys.stderr)
    return []

def get_tickers(params):
    return get_api_data(TICKERS_URL, params)

def fetch_taker_flow(product_id):
    """Fetches recent trades for a product and sums Taker Buys vs Taker Sells"""
    trades = get_api_data(TRADES_URL, {"product_id": product_id, "page_size": 100})
    if not trades: return None, None
    
    taker_buy = 0.0
    taker_sell = 0.0
    
    for t in trades:
        size = to_float(t.get("size"))
        if size is None: continue
        # Check both standard roles and taker_side formats
        if t.get("buyer_role") == "taker" or t.get("taker_side") == "buy": 
            taker_buy += size
        elif t.get("seller_role") == "taker" or t.get("taker_side") == "sell": 
            taker_sell += size
            
    return taker_buy, taker_sell

# ---------------------------------------------------------
# MAPPING & SUPABASE
# ---------------------------------------------------------
def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key: raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")
    return create_client(url, key)

def build_snapshot_row(ticker, snapshot_id, collected_at, selected_expiry, taker_buy=None, taker_sell=None):
    product_id = to_int(ticker.get("product_id") or ticker.get("id"))
    symbol = ticker.get("symbol") or ticker.get("product_symbol")
    strike_price = to_float(ticker.get("strike_price"))
    option_type = get_option_type(ticker)

    if None in (product_id, symbol, strike_price, option_type): return None, "Missing identity fields"

    quotes = ticker.get("quotes")
    if not isinstance(quotes, dict): quotes = {}

    exchange_timestamp = exchange_timestamp_to_iso(ticker.get("timestamp"))
    greeks_raw = ticker.get("greeks")
    greeks = {k: to_float(v) for k, v in greeks_raw.items()} if isinstance(greeks_raw, dict) else None
    mark_iv_raw = quotes.get("mark_iv") or ticker.get("mark_vol")

    row = {
        "snapshot_id": snapshot_id,
        "collected_at": collected_at,
        "exchange_timestamp": exchange_timestamp,
        "exchange": EXCHANGE,
        "underlying": UNDERLYING,
        "expiry_date": selected_expiry.isoformat(),
        "option_type": option_type,
        "strike_price": strike_price,
        "product_id": product_id,
        "symbol": str(symbol),
        "contract_status": ticker.get("state") or ticker.get("status"),
        "oi_contracts": to_float(ticker.get("oi_contracts")),
        "oi_value_usd": to_float(ticker.get("oi_value_usd")),
        "volume_24h": to_float(ticker.get("volume")),
        "turnover_usd": to_float(ticker.get("turnover_usd")),
        "mark_price": to_float(ticker.get("mark_price")),
        "last_price": to_float(ticker.get("close") or ticker.get("last_price")),
        "spot_price": to_float(ticker.get("spot_price")),
        "bid_price": to_float(quotes.get("best_bid")),
        "ask_price": to_float(quotes.get("best_ask")),
        "bid_size": to_float(quotes.get("bid_size")),
        "ask_size": to_float(quotes.get("ask_size")),
        "bid_iv": to_float(quotes.get("bid_iv")),
        "ask_iv": to_float(quotes.get("ask_iv")),
        "mark_iv": to_float(mark_iv_raw),
        "taker_buy_volume": taker_buy,
        "taker_sell_volume": taker_sell,
        "greeks": greeks,
        "data_status": "OK",
        "raw_payload": ticker
    }

    required_measurements = ["oi_contracts", "oi_value_usd", "volume_24h", "mark_price", "spot_price", "bid_iv", "ask_iv", "mark_iv"]
    missing = [f for f in required_measurements if row[f] is None]
    if exchange_timestamp is None: missing.append("exchange_timestamp")
    if missing: row["data_status"] = "PARTIAL:" + ",".join(missing)

    return row, None

def write_run_record(client, snapshot_id, started_at, completed_at, expected, received, inserted, failed, status, error):
    payload = {
        "snapshot_id": snapshot_id, "started_at": started_at, "completed_at": completed_at,
        "underlying": UNDERLYING, "expected_contracts": expected, "received_contracts": received,
        "inserted_contracts": inserted, "failed_expiries": failed, "status": status, "error_message": error
    }
    client.table("options_collection_runs").upsert(payload, on_conflict="snapshot_id").execute()

# ---------------------------------------------------------
# MAIN COLLECTOR
# ---------------------------------------------------------
def main():
    snapshot_id = str(uuid.uuid4())
    started_at = iso_utc(utc_now())
    client = None
    expected, received, inserted = None, 0, 0
    failed_expiries = []

    print("=" * 65)
    print("BTC OPTIONS OI & AGGRESSOR COLLECTOR")
    print("=" * 65)
    print("Snapshot ID:", snapshot_id)

    try:
        client = get_supabase_client()
        write_run_record(client, snapshot_id, started_at, None, None, 0, 0, [], "RUNNING", None)

        # Step 1: Discover expiries
        discovery_rows = get_tickers({"contract_types": "call_options,put_options", "underlying_asset_symbols": UNDERLYING})
        selected_expiry = select_nearest_expiry(discovery_rows)
        expected = sum(1 for t in discovery_rows if expiry_from_ticker(t) == selected_expiry)
        print("Selected nearest expiry:", selected_expiry)

        # Step 2: Fetch chain
        chain_rows = get_tickers({"contract_types": "call_options,put_options", "underlying_asset_symbols": UNDERLYING, "expiry_date": selected_expiry.strftime("%d-%m-%Y")})
        received = len(chain_rows)
        print("Expected contracts:", expected)
        print("Received contracts:", received)

        if received == 0: raise RuntimeError("Zero contracts returned for selected expiry.")

        # Step 3: Find closest strikes for Trade Tape (with Spot Fallback)
        spot_price = to_float(chain_rows[0].get("spot_price"))
        
        # FALLBACK: If options ticker doesn't have spot price, fetch it from perpetuals
        if not spot_price:
            print("Options ticker missing spot_price. Fetching from perpetuals...")
            perp_tickers = get_tickers({"contract_types": "perpetual_futures", "underlying_asset_symbols": UNDERLYING})
            if perp_tickers:
                spot_price = to_float(perp_tickers[0].get("spot_price"))
                
        if spot_price:
            print(f"Using Spot Price: {spot_price}")
            for t in chain_rows:
                t["_distance"] = abs(to_float(t.get("strike_price") or 0) - spot_price)
            closest_strikes = sorted(chain_rows, key=lambda x: x["_distance"])[:10]
            closest_ids = {t.get("product_id") or t.get("id") for t in closest_strikes}
        else:
            closest_ids = set()
            print("Warning: No spot price found anywhere, skipping trade tape.")

        # Step 4: Map and fetch trades
        collected_at = iso_utc(utc_now())
        mapped_rows = []
        mapping_errors = []

        for ticker in chain_rows:
            product_id = to_int(ticker.get("product_id") or ticker.get("id"))
            
            # Fetch trades ONLY if it's one of the closest strikes
            taker_buy, taker_sell = None, None
            if product_id in closest_ids:
                print(f"Fetching trades for product {product_id}...")
                taker_buy, taker_sell = fetch_taker_flow(product_id)
                time.sleep(0.1) # Small delay to prevent rate limits

            row, error = build_snapshot_row(ticker, snapshot_id, collected_at, selected_expiry, taker_buy, taker_sell)
            if error: mapping_errors.append(f"{ticker.get('symbol', 'UNKNOWN')}: {error}")
            else: mapped_rows.append(row)

        # Step 5: Insert
        if mapped_rows:
            try:
                res = client.table("options_oi_snapshots").insert(mapped_rows).execute()
                inserted = len(res.data or [])
            except Exception as e:
                print("Batch insert failed, retrying individually...", file=sys.stderr)
                for row in mapped_rows:
                    try:
                        client.table("options_oi_snapshots").insert(row).execute()
                        inserted += 1
                    except Exception as ex:
                        mapping_errors.append(f"{row.get('symbol')}: {ex}")

        status = "SUCCESS" if (inserted == received and not mapping_errors) else "PARTIAL" if inserted > 0 else "FAILED"

        if mapping_errors:
            failed_expiries.append({"expiry": selected_expiry.isoformat(), "error": "Mapping/Insertion errors", "details": mapping_errors[:10]})

        write_run_record(client, snapshot_id, started_at, iso_utc(utc_now()), expected, received, inserted, failed_expiries, status, "; ".join(mapping_errors[:5]) if mapping_errors else None)

        print(f"Inserted: {inserted}")
        print(f"Run status: {status}")
        return 0 if status == "SUCCESS" else 1

    except Exception as exc:
        print("COLLECTOR FAILED:", str(exc), file=sys.stderr)
        if client:
            write_run_record(client, snapshot_id, started_at, iso_utc(utc_now()), expected, received, inserted, failed_expiries, "FAILED", str(exc)[:2000])
        return 1

if __name__ == "__main__":
    raise SystemExit(main())
