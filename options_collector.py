"""
Delta Exchange India — BTC Options OI Collector + Per-Expiry Research Metrics.

GEX convention used here:
  gamma = change in option delta per $1 move in BTC spot (API-provided standard gamma)
  OI = number of option contracts
  contract multiplier = 0.001 BTC per contract (user-confirmed Delta Exchange India BTC options)
  gross GEX per 1% spot move = gamma * OI * multiplier * spot**2 * 0.01

This is an unsigned, model-derived concentration measure. It does not infer dealer
positioning, net gamma, hedging direction, support, resistance, or a price target.
"""
import os
import re
import sys
import time
import math
import uuid
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

import requests
from supabase import create_client

BASE_URL = os.getenv("DELTA_BASE_URL", "https://api.india.delta.exchange").rstrip("/")
TICKERS_URL = f"{BASE_URL}/v2/tickers"
TRADES_URL = f"{BASE_URL}/v2/trades"
HEADERS = {"Accept": "application/json", "User-Agent": "btc-options-oi-collector/1.3"}
TIMEOUT = (5, 30)
MAX_RETRIES = 3
RETRY_DELAY = 2
EXCHANGE = "DELTA_INDIA"
UNDERLYING = "BTC"
GEX_CONTRACT_MULTIPLIER_BTC = 0.001  # Delta Exchange India BTC options: 0.001 BTC per contract.
LOCAL_OI_BAND_PCT = 10.0
METRIC_VERSION = "options-derived-v1.0"


def utc_now():
    return datetime.now(timezone.utc)


def iso_utc(value):
    return value.astimezone(timezone.utc).isoformat()


def to_float(value):
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.strip().replace(",", "")
        if not value:
            return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    return result


def to_int(value):
    result = to_float(value)
    if result is None or not result.is_integer():
        return None
    return int(result)


def expiry_from_ticker(ticker):
    for key in ("expiry_date", "settlement_time", "expiry"):
        value = ticker.get(key)
        if not value or isinstance(value, (int, float)):
            continue
        text = str(value).strip()
        for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                return datetime.strptime(text[:10], fmt).date()
            except ValueError:
                pass
    symbol = ticker.get("symbol") or ticker.get("product_symbol") or ""
    match = re.search(r"-(\d{6})$", str(symbol))
    if match:
        try:
            return datetime.strptime(match.group(1), "%d%m%y").date()
        except ValueError:
            return None
    return None


def active_expiries(rows):
    today_utc = utc_now().date()
    return sorted({
        expiry
        for ticker in rows
        if isinstance(ticker, dict)
        for expiry in [expiry_from_ticker(ticker)]
        if expiry is not None and expiry >= today_utc
    })


def get_option_type(ticker):
    contract_type = str(ticker.get("contract_type") or ticker.get("option_type") or "").lower()
    if "call" in contract_type:
        return "CALL"
    if "put" in contract_type:
        return "PUT"
    symbol = str(ticker.get("symbol") or ticker.get("product_symbol") or "")
    if symbol.startswith("C-"):
        return "CALL"
    if symbol.startswith("P-"):
        return "PUT"
    return None


def exchange_timestamp_to_iso(value):
    if value is None:
        return None
    try:
        timestamp_us = int(str(value).strip())
        return iso_utc(datetime.fromtimestamp(timestamp_us / 1_000_000, tz=timezone.utc))
    except (ValueError, TypeError, OverflowError, OSError):
        return None


def get_api_data(url, params=None, retries=MAX_RETRIES):
    last_error = None
    for attempt in range(1, retries + 1):
        try:
            response = requests.get(url, params=params, headers=HEADERS, timeout=TIMEOUT)
            response.raise_for_status()
            payload = response.json()
            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected API response type.")
            if payload.get("success") is False:
                raise RuntimeError(f"API returned success=false: {payload}")
            result = payload.get("result", [])
            return result if isinstance(result, list) else []
        except Exception as exc:
            last_error = exc
            if attempt < retries:
                time.sleep(RETRY_DELAY * attempt)
    print(f"API failed: {url} | {last_error}", file=sys.stderr)
    return []


def get_tickers(params):
    return get_api_data(TICKERS_URL, params)


def fetch_taker_flow(product_id):
    url = f"{TRADES_URL}/{product_id}"
    trades = get_api_data(url, {"page_size": 100})
    if not trades:
        return None, None
    if os.getenv("DEBUG_TRADES") == "1":
        print(f"DEBUG: Sample trade for product {product_id}: {trades[0]}")
    taker_buy = 0.0
    taker_sell = 0.0
    for trade in trades:
        size = to_float(trade.get("size"))
        if size is None:
            continue
        side = str(trade.get("side", "")).lower()
        buyer_role = str(trade.get("buyer_role", "")).lower()
        seller_role = str(trade.get("seller_role", "")).lower()
        taker_side = str(trade.get("taker_side", "")).lower()
        if side == "buy" or buyer_role == "taker" or taker_side == "buy":
            taker_buy += size
        elif side == "sell" or seller_role == "taker" or taker_side == "sell":
            taker_sell += size
    return taker_buy, taker_sell


def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    if not url or not key:
        raise RuntimeError("Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY.")
    return create_client(url, key)


def build_snapshot_row(ticker, snapshot_id, collected_at, expiry, taker_buy=None, taker_sell=None):
    product_id = to_int(ticker.get("product_id") or ticker.get("id"))
    symbol = ticker.get("symbol") or ticker.get("product_symbol")
    strike_price = to_float(ticker.get("strike_price"))
    option_type = get_option_type(ticker)
    if None in (product_id, symbol, strike_price, option_type):
        return None, "Missing identity fields"

    quotes = ticker.get("quotes")
    if not isinstance(quotes, dict):
        quotes = {}
    exchange_timestamp = exchange_timestamp_to_iso(ticker.get("timestamp"))
    greeks_raw = ticker.get("greeks")
    greeks = (
        {key: to_float(value) for key, value in greeks_raw.items()}
        if isinstance(greeks_raw, dict) else None
    )
    mark_iv_raw = quotes.get("mark_iv")
    if mark_iv_raw is None:
        mark_iv_raw = ticker.get("mark_vol")

    row = {
        "snapshot_id": snapshot_id,
        "collected_at": collected_at,
        "exchange_timestamp": exchange_timestamp,
        "exchange": EXCHANGE,
        "underlying": UNDERLYING,
        "expiry_date": expiry.isoformat(),
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
        "raw_payload": ticker,
    }
    required = [
        "oi_contracts", "oi_value_usd", "volume_24h", "mark_price", "spot_price",
        "bid_iv", "ask_iv", "mark_iv",
    ]
    missing = [field for field in required if row[field] is None]
    if exchange_timestamp is None:
        missing.append("exchange_timestamp")
    if missing:
        row["data_status"] = "PARTIAL:" + ",".join(missing)
    return row, None


def write_run_record(client, snapshot_id, started_at, completed_at, expected, received,
                     inserted, failed, status, error):
    payload = {
        "snapshot_id": snapshot_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "underlying": UNDERLYING,
        "expected_contracts": expected,
        "received_contracts": received,
        "inserted_contracts": inserted,
        "failed_expiries": failed,
        "status": status,
        "error_message": error,
    }
    client.table("options_collection_runs").upsert(payload, on_conflict="snapshot_id").execute()


def _valid_oi_rows(rows):
    return all(
        to_float(row.get("oi_contracts")) is not None
        and to_float(row.get("oi_contracts")) >= 0
        for row in rows
    )


def _spot_for_rows(rows):
    spots = [to_float(row.get("spot_price")) for row in rows]
    spots = [spot for spot in spots if spot is not None and spot > 0]
    if not spots:
        return None, "MISSING_SPOT"
    median_spot = sorted(spots)[len(spots) // 2]
    if max(spots) - min(spots) > median_spot * 0.002:
        return None, "INCONSISTENT_SPOT"
    return median_spot, "OK"


def _calculate_max_pain(rows, spot):
    if not _valid_oi_rows(rows):
        return None, "MISSING_OR_INVALID_OI"
    calls = [row for row in rows if row.get("option_type") == "CALL"]
    puts = [row for row in rows if row.get("option_type") == "PUT"]
    if not calls or not puts:
        return None, "MISSING_CALL_OR_PUT_CHAIN"
    if sum(float(row["oi_contracts"]) for row in calls) <= 0:
        return None, "ZERO_CALL_OI"
    if sum(float(row["oi_contracts"]) for row in puts) <= 0:
        return None, "ZERO_PUT_OI"

    strikes = sorted({float(row["strike_price"]) for row in rows})
    payouts = []
    for settlement in strikes:
        payout = 0.0
        for row in calls:
            payout += float(row["oi_contracts"]) * max(settlement - float(row["strike_price"]), 0.0)
        for row in puts:
            payout += float(row["oi_contracts"]) * max(float(row["strike_price"]) - settlement, 0.0)
        payouts.append((payout, abs(settlement - spot), settlement))
    # Deterministic tie-break: lowest payout, then closest to spot, then lower strike.
    return min(payouts, key=lambda item: (item[0], item[1], item[2]))[2], "OK"


def _calculate_atm_straddle(rows, spot):
    by_strike = {}
    for row in rows:
        strike = to_float(row.get("strike_price"))
        mark = to_float(row.get("mark_price"))
        side = row.get("option_type")
        if strike is None or mark is None or mark < 0 or side not in ("CALL", "PUT"):
            continue
        by_strike.setdefault(strike, {})[side] = mark
    candidates = [
        (abs(strike - spot), strike, values["CALL"] + values["PUT"])
        for strike, values in by_strike.items()
        if "CALL" in values and "PUT" in values
    ]
    if not candidates:
        return None, None, "NO_COMMON_CALL_PUT_MARKS"
    _, strike, premium = min(candidates, key=lambda item: (item[0], item[1]))
    return strike, premium, "OK"


def _calculate_local_pcr(rows, spot):
    if not _valid_oi_rows(rows):
        return None, None, None, "MISSING_OR_INVALID_OI"
    lower = spot * (1.0 - LOCAL_OI_BAND_PCT / 100.0)
    upper = spot * (1.0 + LOCAL_OI_BAND_PCT / 100.0)
    local = [
        row for row in rows
        if lower <= float(row["strike_price"]) <= upper
    ]
    call_oi = sum(float(row["oi_contracts"]) for row in local if row["option_type"] == "CALL")
    put_oi = sum(float(row["oi_contracts"]) for row in local if row["option_type"] == "PUT")
    if call_oi <= 0:
        return None, put_oi, call_oi, "ZERO_LOCAL_CALL_OI"
    if not any(row["option_type"] == "PUT" for row in local):
        return None, put_oi, call_oi, "NO_LOCAL_PUT_CONTRACTS"
    return put_oi / call_oi, put_oi, call_oi, "OK"


def _calculate_gex_wall(rows, option_type, spot):
    selected = [row for row in rows if row.get("option_type") == option_type]
    if not selected:
        return None, None, "MISSING_" + option_type + "_CHAIN"
    exposures = {}
    for row in selected:
        oi = to_float(row.get("oi_contracts"))
        greeks = row.get("greeks")
        gamma = to_float(greeks.get("gamma")) if isinstance(greeks, dict) else None
        if oi is None or oi < 0:
            return None, None, "MISSING_OR_INVALID_OI"
        if gamma is None:
            return None, None, "MISSING_GAMMA"
        if gamma < 0:
            return None, None, "INVALID_NEGATIVE_GAMMA"
        strike = to_float(row.get("strike_price"))
        if strike is None:
            return None, None, "MISSING_STRIKE"
        # API gamma is delta change per $1 BTC move; multiplier is 0.001 BTC/contract.
        # Unsigned OI-weighted gross GEX per 1% spot move; not dealer/net gamma.
        gex = gamma * oi * GEX_CONTRACT_MULTIPLIER_BTC * (spot ** 2) * 0.01
        exposures[strike] = exposures.get(strike, 0.0) + gex
    if not any(value > 0 for value in exposures.values()):
        return None, None, "ZERO_GAMMA_ONLY"
    wall_strike, wall_value = max(exposures.items(), key=lambda item: (item[1], -item[0]))
    return wall_strike, wall_value, "OK"


def build_derived_row(snapshot_id, expiry, rows, expected_count, inserted_count):
    source_times = [row.get("collected_at") for row in rows if row.get("collected_at")]
    source_collected_at = max(source_times) if source_times else None
    status = {}
    chain_complete = expected_count > 0 and inserted_count == expected_count and len(rows) == expected_count
    status["chain"] = "OK" if chain_complete else "INCOMPLETE_OI_CHAIN"
    spot, spot_status = _spot_for_rows(rows)
    status["spot"] = spot_status

    result = {
        "snapshot_id": snapshot_id,
        "expiry_date": expiry.isoformat(),
        "source_collected_at": source_collected_at,
        "underlying_spot_price": spot,
        "gex_contract_multiplier": GEX_CONTRACT_MULTIPLIER_BTC,
        "local_oi_band_pct": LOCAL_OI_BAND_PCT,
        "metric_version": METRIC_VERSION,
        "metric_status": status,
    }

    if not chain_complete:
        for key in ("max_pain", "atm_straddle", "call_gex", "put_gex", "local_oi_pcr"):
            status[key] = "INCOMPLETE_OI_CHAIN"
        return result

    if spot is None:
        for key in ("max_pain", "atm_straddle", "call_gex", "put_gex", "local_oi_pcr"):
            status[key] = "INVALID_SPOT"
        return result

    max_pain, max_pain_status = _calculate_max_pain(rows, spot)
    result["max_pain_strike"] = max_pain
    status["max_pain"] = max_pain_status

    atm_strike, straddle, straddle_status = _calculate_atm_straddle(rows, spot)
    result["atm_strike"] = atm_strike
    result["atm_straddle_price"] = straddle
    result["atm_straddle_pct_spot"] = (straddle / spot * 100.0) if straddle is not None and spot > 0 else None
    status["atm_straddle"] = straddle_status

    call_strike, call_gex, call_status = _calculate_gex_wall(rows, "CALL", spot)
    put_strike, put_gex, put_status = _calculate_gex_wall(rows, "PUT", spot)
    result["call_gex_wall_strike"] = call_strike
    result["call_gex_wall_value"] = call_gex
    result["put_gex_wall_strike"] = put_strike
    result["put_gex_wall_value"] = put_gex
    status["call_gex"] = call_status
    status["put_gex"] = put_status

    pcr, put_oi, call_oi, pcr_status = _calculate_local_pcr(rows, spot)
    result["local_oi_pcr"] = pcr
    result["local_put_oi_contracts"] = put_oi
    result["local_call_oi_contracts"] = call_oi
    status["local_oi_pcr"] = pcr_status
    return result


def insert_option_rows(client, rows):
    if not rows:
        return 0, []
    errors = []
    try:
        response = client.table("options_oi_snapshots").insert(rows).execute()
        return len(response.data or []), errors
    except Exception as batch_error:
        print(f"Batch insert failed; retrying contracts individually: {batch_error}", file=sys.stderr)
    inserted = 0
    for row in rows:
        try:
            client.table("options_oi_snapshots").insert(row).execute()
            inserted += 1
        except Exception as exc:
            errors.append(f"{row.get('symbol')}: {exc}")
    return inserted, errors


def main():
    snapshot_id = str(uuid.uuid4())
    started_at = iso_utc(utc_now())
    client = None
    total_expected = 0
    total_received = 0
    total_inserted = 0
    failed_expiries = []
    all_errors = []

    print("=" * 65)
    print("BTC OPTIONS OI + PER-EXPIRY RESEARCH COLLECTOR")
    print("=" * 65)
    print("Snapshot ID:", snapshot_id)

    try:
        client = get_supabase_client()
        write_run_record(client, snapshot_id, started_at, None, None, 0, 0, [], "RUNNING", None)

        discovery_rows = get_tickers({
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": UNDERLYING,
        })
        expiries = active_expiries(discovery_rows)
        if not expiries:
            raise RuntimeError("No active/future BTC options expiries identified.")

        expected_by_expiry = {
            expiry: sum(1 for ticker in discovery_rows if expiry_from_ticker(ticker) == expiry)
            for expiry in expiries
        }
        total_expected = sum(expected_by_expiry.values())
        print("Active expiries:", ", ".join(expiry.isoformat() for expiry in expiries))

        # One collection run may contain many expiries; every derived record is keyed
        # by (snapshot_id, expiry_date), preserving expiry-specific provenance.
        for expiry in expiries:
            expiry_label = expiry.isoformat()
            try:
                chain_rows = get_tickers({
                    "contract_types": "call_options,put_options",
                    "underlying_asset_symbols": UNDERLYING,
                    "expiry_date": expiry.strftime("%d-%m-%Y"),
                })
                total_received += len(chain_rows)
                expected_count = expected_by_expiry[expiry]
                print(f"{expiry_label}: expected {expected_count}; received {len(chain_rows)}")
                if not chain_rows:
                    failed_expiries.append({"expiry": expiry_label, "error": "ZERO_CONTRACTS_RETURNED"})
                    continue

                spot = next(
                    (to_float(ticker.get("spot_price")) for ticker in chain_rows
                     if to_float(ticker.get("spot_price")) and to_float(ticker.get("spot_price")) > 0),
                    None,
                )
                if spot is None:
                    perp_tickers = get_tickers({
                        "contract_types": "perpetual_futures",
                        "underlying_asset_symbols": UNDERLYING,
                    })
                    if perp_tickers:
                        spot = next(
                            (to_float(ticker.get("spot_price")) for ticker in perp_tickers
                             if to_float(ticker.get("spot_price")) and to_float(ticker.get("spot_price")) > 0),
                            None,
                        )

                if spot is not None:
                    ranked = sorted(
                        chain_rows,
                        key=lambda ticker: abs((to_float(ticker.get("strike_price")) or spot) - spot),
                    )
                    closest_ids = {
                        to_int(ticker.get("product_id") or ticker.get("id"))
                        for ticker in ranked[:10]
                    }
                else:
                    closest_ids = set()
                    print(f"{expiry_label}: no spot price; trade tape skipped.", file=sys.stderr)

                collected_at = iso_utc(utc_now())
                mapped_rows = []
                mapping_errors = []
                for ticker in chain_rows:
                    product_id = to_int(ticker.get("product_id") or ticker.get("id"))
                    taker_buy, taker_sell = None, None
                    if product_id in closest_ids:
                        taker_buy, taker_sell = fetch_taker_flow(product_id)
                        time.sleep(0.1)
                    row, error = build_snapshot_row(
                        ticker, snapshot_id, collected_at, expiry, taker_buy, taker_sell
                    )
                    if error:
                        mapping_errors.append(f"{ticker.get('symbol', 'UNKNOWN')}: {error}")
                    else:
                        mapped_rows.append(row)

                inserted, insert_errors = insert_option_rows(client, mapped_rows)
                total_inserted += inserted
                mapping_errors.extend(insert_errors)
                all_errors.extend(mapping_errors[:20])

                # Only successfully inserted contracts contribute to derived metrics.
                # For a fully complete chain, inserted_count must equal the discovery count.
                successful_rows = mapped_rows if inserted == len(mapped_rows) and not insert_errors else []
                if not successful_rows and inserted > 0:
                    # Recover exact persisted rows for partial insertions, keyed to this run+expiry.
                    persisted = client.table("options_oi_snapshots").select(
                        "expiry_date,option_type,strike_price,oi_contracts,mark_price,spot_price,greeks,collected_at"
                    ).eq("snapshot_id", snapshot_id).eq("expiry_date", expiry_label).execute()
                    successful_rows = persisted.data or []

                derived = build_derived_row(
                    snapshot_id, expiry, successful_rows, expected_count, inserted
                )
                try:
                    client.table("options_derived_snapshots").upsert(
                        derived, on_conflict="snapshot_id,expiry_date"
                    ).execute()
                except Exception as exc:
                    mapping_errors.append(f"Derived metrics insert failed: {exc}")
                    all_errors.append(f"{expiry_label}: derived metrics insert failed: {exc}")
                if mapping_errors:
                    failed_expiries.append({
                        "expiry": expiry_label,
                        "error": "PARTIAL_COLLECTION_OR_DERIVATION",
                        "details": mapping_errors[:10],
                    })
            except Exception as expiry_error:
                failed_expiries.append({"expiry": expiry_label, "error": str(expiry_error)[:1000]})
                all_errors.append(f"{expiry_label}: {expiry_error}")
                print(f"Expiry failed: {expiry_label}: {expiry_error}", file=sys.stderr)

        status = (
            "SUCCESS" if total_inserted == total_expected and not failed_expiries
            else "PARTIAL" if total_inserted > 0
            else "FAILED"
        )
        write_run_record(
            client, snapshot_id, started_at, iso_utc(utc_now()), total_expected,
            total_received, total_inserted, failed_expiries, status,
            "; ".join(all_errors[:8]) if all_errors else None,
        )
        print(f"Expected: {total_expected}; received: {total_received}; inserted: {total_inserted}")
        print(f"Run status: {status}")
        return 0 if status == "SUCCESS" else 1

    except Exception as exc:
        print("COLLECTOR FAILED:", str(exc), file=sys.stderr)
        if client:
            write_run_record(
                client, snapshot_id, started_at, iso_utc(utc_now()), total_expected,
                total_received, total_inserted, failed_expiries, "FAILED", str(exc)[:2000],
            )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
