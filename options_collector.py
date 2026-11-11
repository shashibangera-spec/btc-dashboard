
"""
Delta Exchange India — BTC Options OI Collector

Pipeline:
Delta public API -> Python collector -> Supabase

Required environment variables:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Dependencies:
    requests
    supabase

Install:
    pip install requests "supabase>=2.0.0"
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

BASE_URL = os.getenv(
    "DELTA_BASE_URL",
    "https://api.india.delta.exchange"
).rstrip("/")

TICKERS_URL = f"{BASE_URL}/v2/tickers"

HEADERS = {
    "Accept": "application/json",
    "User-Agent": "btc-options-oi-collector/1.0",
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
    """
    Safely convert numeric strings or numbers to float.

    Missing, invalid, NaN and infinite values become None.
    They must be stored as SQL NULL, not zero.
    """
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


# ---------------------------------------------------------
# EXPIRY IDENTIFICATION
# Same logic as options_api_test.py
# ---------------------------------------------------------

def expiry_from_ticker(ticker):
    """
    Identify expiry from explicit date fields or Delta symbol.

    Delta option symbols:
        C-BTC-STRIKE-DDMMYY
        P-BTC-STRIKE-DDMMYY
    """

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
            return datetime.strptime(
                match.group(1), "%d%m%y"
            ).date()
        except ValueError:
            return None

    return None


def select_nearest_expiry(rows):
    today_utc = utc_now().date()

    expiries = sorted({
        expiry
        for ticker in rows
        if isinstance(ticker, dict)
        for expiry in [expiry_from_ticker(ticker)]
        if expiry is not None and expiry >= today_utc
    })

    if not expiries:
        raise RuntimeError(
            "No active/future BTC options expiry identified."
        )

    return expiries[0]


# ---------------------------------------------------------
# DELTA API
# ---------------------------------------------------------

def get_tickers(params):
    last_error = None

    for attempt in range(1, MAX_RETRIES + 1):
        try:
            response = requests.get(
                TICKERS_URL,
                params=params,
                headers=HEADERS,
                timeout=TIMEOUT
            )

            response.raise_for_status()
            payload = response.json()

            if not isinstance(payload, dict):
                raise RuntimeError("Unexpected API response type.")

            if payload.get("success") is False:
                raise RuntimeError(
                    f"Delta API returned success=false: {payload}"
                )

            rows = payload.get("result")

            if not isinstance(rows, list):
                raise RuntimeError(
                    "Expected API response['result'] to be a list."
                )

            return [
                row for row in rows
                if isinstance(row, dict)
            ]

        except (
            requests.RequestException,
            ValueError,
            RuntimeError
        ) as exc:

            last_error = exc

            if attempt < MAX_RETRIES:
                time.sleep(RETRY_DELAY * attempt)

    raise RuntimeError(
        f"Delta API failed after {MAX_RETRIES} attempts: {last_error}"
    )


# ---------------------------------------------------------
# TIMESTAMP CONVERSION
# ---------------------------------------------------------

def exchange_timestamp_to_iso(value):
    """
    Delta timestamp observed in Stage 1:
        1794412777233080

    This is Unix microseconds.
    Convert to UTC seconds and then ISO-8601.
    Do not parse the nanosecond 'time' string.
    """

    if value is None:
        return None

    try:
        timestamp_us = int(str(value).strip())

        timestamp_seconds = timestamp_us / 1_000_000

        converted = datetime.fromtimestamp(
            timestamp_seconds,
            tz=timezone.utc
        )

        return iso_utc(converted)

    except (ValueError, TypeError, OverflowError, OSError):
        return None


# ---------------------------------------------------------
# OPTION TYPE
# ---------------------------------------------------------

def get_option_type(ticker):
    contract_type = str(
        ticker.get("contract_type")
        or ticker.get("option_type")
        or ""
    ).lower()

    if "call" in contract_type:
        return "CALL"

    if "put" in contract_type:
        return "PUT"

    symbol = str(
        ticker.get("symbol")
        or ticker.get("product_symbol")
        or ""
    )

    if symbol.startswith("C-"):
        return "CALL"

    if symbol.startswith("P-"):
        return "PUT"

    return None


# ---------------------------------------------------------
# SUPABASE CLIENT
# ---------------------------------------------------------

def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY."
        )

    return create_client(url, key)


# ---------------------------------------------------------
# CONTRACT MAPPING
# ---------------------------------------------------------

def build_snapshot_row(
    ticker,
    snapshot_id,
    collected_at,
    selected_expiry
):
    """
    Convert one raw Delta ticker into options_oi_snapshots format.

    Root-level fields:
        strike_price
        oi_contracts
        oi_value_usd
        volume
        timestamp
        mark_vol

    Nested quotes:
        best_bid
        best_ask
        bid_iv
        ask_iv
        mark_iv
    """

    product_id = to_int(
        ticker.get("product_id") or ticker.get("id")
    )

    symbol = (
        ticker.get("symbol")
        or ticker.get("product_symbol")
    )

    strike_price = to_float(ticker.get("strike_price"))

    option_type = get_option_type(ticker)

    # Required database identity fields.
    identity_errors = []

    if product_id is None:
        identity_errors.append("product_id")

    if not symbol:
        identity_errors.append("symbol")

    if strike_price is None:
        identity_errors.append("strike_price")

    if option_type is None:
        identity_errors.append("option_type")

    if identity_errors:
        return None, (
            "Missing identity fields: "
            + ", ".join(identity_errors)
        )

    quotes = ticker.get("quotes")

    if not isinstance(quotes, dict):
        quotes = {}

    exchange_timestamp = exchange_timestamp_to_iso(
        ticker.get("timestamp")
    )

    # Greeks are optional. Preserve their names and convert
    # numeric string values safely.
    greeks_raw = ticker.get("greeks")

    greeks = None

    if isinstance(greeks_raw, dict):
        greeks = {
            key: to_float(value)
            for key, value in greeks_raw.items()
        }

    # Keep IV values in their original decimal scale.
    # Do not average bid_iv and ask_iv.
    # The observed low bid_iv value (e.g. 0.01) is retained
    # separately for later forensic inspection.
    mark_iv_raw = quotes.get("mark_iv")

    if mark_iv_raw is None:
        mark_iv_raw = ticker.get("mark_vol")

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
        "contract_status": (
            ticker.get("state")
            or ticker.get("status")
        ),

        "oi_contracts": to_float(
            ticker.get("oi_contracts")
        ),

        "oi_value_usd": to_float(
            ticker.get("oi_value_usd")
        ),

        "volume_24h": to_float(
            ticker.get("volume")
        ),

        "turnover_usd": to_float(
            ticker.get("turnover_usd")
        ),

        "mark_price": to_float(
            ticker.get("mark_price")
        ),

        "last_price": to_float(
            ticker.get("close")
            if ticker.get("close") is not None
            else ticker.get("last_price")
        ),

        "spot_price": to_float(
            ticker.get("spot_price")
        ),

        # Nested quotes dictionary.
        "bid_price": to_float(
            quotes.get("best_bid")
        ),

        "ask_price": to_float(
            quotes.get("best_ask")
        ),

        "bid_size": to_float(
            quotes.get("bid_size")
        ),

        "ask_size": to_float(
            quotes.get("ask_size")
        ),

        "bid_iv": to_float(
            quotes.get("bid_iv")
        ),

        "ask_iv": to_float(
            quotes.get("ask_iv")
        ),

        "mark_iv": to_float(mark_iv_raw),

        "greeks": greeks,

        "data_status": "OK",

        # Retain original API record for later auditing.
        "raw_payload": ticker
    }

    # Missing measurements do not invalidate the contract's identity.
    # They are recorded as NULL and the row is marked PARTIAL.
    required_measurements = [
        "oi_contracts",
        "oi_value_usd",
        "volume_24h",
        "mark_price",
        "spot_price",
        "bid_iv",
        "ask_iv",
        "mark_iv"
    ]

    missing = [
        field
        for field in required_measurements
        if row[field] is None
    ]

    if exchange_timestamp is None:
        missing.append("exchange_timestamp")

    if missing:
        row["data_status"] = (
            "PARTIAL:" + ",".join(missing)
        )

    return row, None


# ---------------------------------------------------------
# COLLECTION RUN METADATA
# ---------------------------------------------------------

def write_run_record(
    client,
    snapshot_id,
    started_at,
    completed_at,
    expected_contracts,
    received_contracts,
    inserted_contracts,
    failed_expiries,
    status,
    error_message
):
    payload = {
        "snapshot_id": snapshot_id,
        "started_at": started_at,
        "completed_at": completed_at,
        "underlying": UNDERLYING,
        "expected_contracts": expected_contracts,
        "received_contracts": received_contracts,
        "inserted_contracts": inserted_contracts,
        "failed_expiries": failed_expiries,
        "status": status,
        "error_message": error_message
    }

    client.table(
        "options_collection_runs"
    ).upsert(
        payload,
        on_conflict="snapshot_id"
    ).execute()


# ---------------------------------------------------------
# INSERT CONTRACT ROWS
# ---------------------------------------------------------

def insert_contract_rows(client, rows):
    if not rows:
        return 0, []

    try:
        result = client.table(
            "options_oi_snapshots"
        ).insert(rows).execute()

        return len(result.data or []), []

    except Exception as batch_error:
        print(
            "Batch insert failed. Retrying rows individually:",
            str(batch_error),
            file=sys.stderr
        )

    inserted = 0
    errors = []

    for row in rows:
        try:
            client.table(
                "options_oi_snapshots"
            ).insert(row).execute()

            inserted += 1

        except Exception as exc:
            errors.append(
                f"{row.get('symbol', 'UNKNOWN')}: {exc}"
            )

    return inserted, errors


# ---------------------------------------------------------
# MAIN COLLECTOR
# ---------------------------------------------------------

def main():
    snapshot_id = str(uuid.uuid4())

    started_at = iso_utc(utc_now())

    client = None

    expected_contracts = None
    received_contracts = 0
    inserted_contracts = 0

    failed_expiries = []

    print("=" * 65)
    print("BTC OPTIONS OI COLLECTOR")
    print("=" * 65)

    print("Snapshot ID:", snapshot_id)
    print("Started UTC:", started_at)

    try:
        client = get_supabase_client()

        # Record that the run has started.
        write_run_record(
            client=client,
            snapshot_id=snapshot_id,
            started_at=started_at,
            completed_at=None,
            expected_contracts=None,
            received_contracts=0,
            inserted_contracts=0,
            failed_expiries=[],
            status="RUNNING",
            error_message=None
        )

        # Step 1: Discover all BTC option expiries.
        discovery_params = {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": UNDERLYING
        }

        discovery_rows = get_tickers(discovery_params)

        selected_expiry = select_nearest_expiry(
            discovery_rows
        )

        # Count discovery records matching the chosen expiry.
        expected_contracts = sum(
            1
            for ticker in discovery_rows
            if expiry_from_ticker(ticker) == selected_expiry
        )

        print("Selected nearest expiry:", selected_expiry)

        # Step 2: Fetch only the selected expiry.
        expiry_param = selected_expiry.strftime("%d-%m-%Y")

        chain_params = {
            "contract_types": "call_options,put_options",
            "underlying_asset_symbols": UNDERLYING,
            "expiry_date": expiry_param
        }

        chain_rows = get_tickers(chain_params)

        received_contracts = len(chain_rows)

        print("Expected contracts:", expected_contracts)
        print("Received contracts:", received_contracts)

        if received_contracts == 0:
            raise RuntimeError(
                "Delta returned zero contracts for selected expiry."
            )

        # Step 3: Map raw contracts.
        collected_at = iso_utc(utc_now())

        mapped_rows = []
        mapping_errors = []

        for ticker in chain_rows:
            row, error = build_snapshot_row(
                ticker=ticker,
                snapshot_id=snapshot_id,
                collected_at=collected_at,
                selected_expiry=selected_expiry
            )

            if error:
                symbol = (
                    ticker.get("symbol")
                    or ticker.get("product_symbol")
                    or "UNKNOWN"
                )

                mapping_errors.append(
                    f"{symbol}: {error}"
                )
            else:
                mapped_rows.append(row)

        # Step 4: Insert mapped contracts.
        inserted_contracts, insert_errors = insert_contract_rows(
            client,
            mapped_rows
        )

        all_errors = mapping_errors + insert_errors

        partial_data_rows = sum(
            1
            for row in mapped_rows
            if row["data_status"] != "OK"
        )

        count_mismatch = (
            expected_contracts is not None
            and expected_contracts != received_contracts
        )

        if (
            inserted_contracts == received_contracts
            and not all_errors
            and partial_data_rows == 0
            and not count_mismatch
        ):
            status = "SUCCESS"

        elif inserted_contracts > 0:
            status = "PARTIAL"

        else:
            status = "FAILED"

        if mapping_errors:
            failed_expiries.append({
                "expiry": selected_expiry.isoformat(),
                "error": "Contract mapping errors",
                "count": len(mapping_errors),
                "details": mapping_errors[:20]
            })

        if insert_errors:
            failed_expiries.append({
                "expiry": selected_expiry.isoformat(),
                "error": "Contract insertion errors",
                "count": len(insert_errors),
                "details": insert_errors[:20]
            })

        if count_mismatch:
            failed_expiries.append({
                "expiry": selected_expiry.isoformat(),
                "error": "Discovery and expiry-query contract counts differ",
                "expected": expected_contracts,
                "received": received_contracts
            })

        if partial_data_rows:
            failed_expiries.append({
                "expiry": selected_expiry.isoformat(),
                "error": "Some contracts have missing measurements",
                "partial_contracts": partial_data_rows
            })

        # Step 5: Finalise collection-run metadata.
        completed_at = iso_utc(utc_now())

        error_message = (
            "; ".join(all_errors[:10])
            if all_errors
            else None
        )

        write_run_record(
            client=client,
            snapshot_id=snapshot_id,
            started_at=started_at,
            completed_at=completed_at,
            expected_contracts=expected_contracts,
            received_contracts=received_contracts,
            inserted_contracts=inserted_contracts,
            failed_expiries=failed_expiries,
            status=status,
            error_message=error_message
        )

        print("-" * 65)
        print("COLLECTION SUMMARY")
        print("-" * 65)
        print("Expiry:", selected_expiry)
        print("Expected:", expected_contracts)
        print("Received:", received_contracts)
        print("Mapped:", len(mapped_rows))
        print("Inserted:", inserted_contracts)
        print("Partial data rows:", partial_data_rows)
        print("Run status:", status)
        print("Completed UTC:", completed_at)

        if all_errors:
            print("\nErrors:")
            for error in all_errors[:20]:
                print("-", error, file=sys.stderr)

        print("=" * 65)

        return 0 if status == "SUCCESS" else 1

    except Exception as exc:
        error_message = str(exc)

        print(
            "COLLECTOR FAILED:",
            error_message,
            file=sys.stderr
        )

        if client is not None:
            try:
                write_run_record(
                    client=client,
                    snapshot_id=snapshot_id,
                    started_at=started_at,
                    completed_at=iso_utc(utc_now()),
                    expected_contracts=expected_contracts,
                    received_contracts=received_contracts,
                    inserted_contracts=inserted_contracts,
                    failed_expiries=failed_expiries,
                    status="FAILED",
                    error_message=error_message[:2000]
                )
            except Exception as run_error:
                print(
                    "Could not finalise run record:",
                    run_error,
                    file=sys.stderr
                )

        return 1


if __name__ == "__main__":
    raise SystemExit(main())
