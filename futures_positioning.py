"""
futures_positioning.py
Version: 1.1
Purpose: Resilient multi-exchange BTC futures positioning collector.
"""

import argparse
import json
import socket
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from collections import deque
from datetime import datetime, timezone

VERSION = "1.1"
SYMBOL = "BTCUSDT"
OKX_SWAP_ID = "BTC-USDT-SWAP"
OKX_SPOT_ID = "BTC-USDT"

REQUEST_TIMEOUT_SECONDS = 8
REQUEST_RETRIES = 2
RETRY_DELAY_SECONDS = 0.5
POLL_INTERVAL_SECONDS = 60
MAX_DATA_AGE_SECONDS = 180
DELTA_WINDOW_SECONDS = 30 * 60
DELTA_MIN_AGE_SECONDS = 25 * 60
DELTA_MAX_AGE_SECONDS = 35 * 60
EXCHANGE_NAMES = ("BINANCE", "BYBIT", "OKX")

def utc_now():
    return datetime.now(timezone.utc)

def iso_utc(dt=None):
    if dt is None: dt = utc_now()
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")

def epoch_now():
    return time.time()

def timestamp_from_ms(value):
    try:
        number = float(value)
        if number <= 0: return None
        return number / 1000.0
    except (TypeError, ValueError):
        return None

def timestamp_from_iso(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None: parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.timestamp()
    except (TypeError, ValueError):
        return None

def age_seconds(timestamp):
    if timestamp is None: return None
    return max(0.0, epoch_now() - float(timestamp))

def safe_float(value):
    try:
        result = float(value)
        if result != result or result in (float("inf"), float("-inf")): return None
        return result
    except (TypeError, ValueError):
        return None

class DataFetchError(Exception):
    def __init__(self, category, message, url=None):
        super().__init__(message)
        self.category = category
        self.message = str(message)
        self.url = url

    def as_dict(self):
        result = {"category": self.category, "message": self.message}
        if self.url: result["url"] = self.url
        return result

def classify_exception(exc):
    if isinstance(exc, socket.gaierror): return "DNS_FAILURE"
    if isinstance(exc, (socket.timeout, TimeoutError)): return "CONNECTION_TIMEOUT"
    if isinstance(exc, urllib.error.HTTPError): return "HTTP_ERROR"
    if isinstance(exc, urllib.error.URLError):
        reason = getattr(exc, "reason", None)
        if isinstance(reason, socket.gaierror): return "DNS_FAILURE"
        if isinstance(reason, (socket.timeout, TimeoutError)): return "CONNECTION_TIMEOUT"
        return "CONNECTION_ERROR"
    if isinstance(exc, json.JSONDecodeError): return "INVALID_RESPONSE"
    if isinstance(exc, (ValueError, TypeError, KeyError)): return "INVALID_RESPONSE"
    return "CONNECTION_ERROR"

def request_json(url, params=None):
    if params:
        query = urllib.parse.urlencode(params)
        separator = "&" if "?" in url else "?"
        url = url + separator + query

    headers = {"User-Agent": "BTC-Futures-Positioning-Research/1.1", "Accept": "application/json"}
    last_error = None

    for attempt in range(REQUEST_RETRIES + 1):
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
                status = response.status
                body = response.read().decode("utf-8")
            if status < 200 or status >= 300:
                raise DataFetchError("HTTP_ERROR", "HTTP status {}".format(status), url)
            try:
                payload = json.loads(body)
            except json.JSONDecodeError as exc:
                raise DataFetchError("INVALID_RESPONSE", "Invalid JSON: {}".format(exc), url)
            return {"payload": payload, "fetched_at": epoch_now(), "fetched_at_utc": iso_utc(), "url": url}
        except DataFetchError as exc:
            last_error = exc
        except Exception as exc:
            last_error = DataFetchError(classify_exception(exc), str(exc), url)
        if attempt < REQUEST_RETRIES: time.sleep(RETRY_DELAY_SECONDS)
    raise last_error

def request_with_host_fallback(hosts, path, params=None):
    failures = []
    for host in hosts:
        url = host.rstrip("/") + path
        try:
            result = request_json(url, params=params)
            result["host"] = host
            result["fallback_used"] = host != hosts[0]
            return result
        except DataFetchError as exc:
            failures.append({"host": host, "error": exc.as_dict()})

    categories = [item["error"]["category"] for item in failures]
    priority = ("DNS_FAILURE", "CONNECTION_TIMEOUT", "CONNECTION_ERROR", "HTTP_ERROR", "API_ERROR", "INVALID_RESPONSE")
    final_category = "CONNECTION_ERROR"
    for category in priority:
        if category in categories:
            final_category = category
            break
    raise DataFetchError(final_category, "All hosts failed: {}".format(json.dumps(failures, separators=(",", ":"))))

def metric_ok(value, timestamp, source_timestamp=None):
    if value is None:
        return {"value": None, "status": "INVALID_RESPONSE", "timestamp": None, "source_timestamp": source_timestamp}
    age = age_seconds(timestamp)
    if age is not None and age > MAX_DATA_AGE_SECONDS:
        return {"value": None, "status": "STALE_DATA", "timestamp": iso_utc(datetime.fromtimestamp(timestamp, timezone.utc)), "source_timestamp": source_timestamp, "age_seconds": round(age, 1)}
    return {"value": value, "status": "OK", "timestamp": iso_utc(datetime.fromtimestamp(timestamp, timezone.utc)) if timestamp else None, "source_timestamp": source_timestamp, "age_seconds": round(age, 1) if age is not None else None}

def metric_failure(error):
    return {"value": None, "status": error.category, "error": error.as_dict(), "timestamp": None}

def api_require(condition, message):
    if not condition: raise DataFetchError("API_ERROR", message)

def exchange_template():
    return {
        "oi_usd": {"value": None, "status": "NOT_COLLECTED"},
        "funding_rate": {"value": None, "status": "NOT_COLLECTED"},
        "perp_price": {"value": None, "status": "NOT_COLLECTED"},
        "spot_price": {"value": None, "status": "NOT_COLLECTED"},
        "basis_pct": {"value": None, "status": "NOT_CALCULATED"},
        "errors": {},
        "endpoint_info": {},
    }

BINANCE_FUTURES_HOSTS = ("https://fapi.binance.com", "https://fapi1.binance.com", "https://fapi2.binance.com")
BINANCE_SPOT_HOSTS = ("https://api.binance.com",)

def collect_binance():
    result = exchange_template()
    try:
        oi_response = request_with_host_fallback(BINANCE_FUTURES_HOSTS, "/fapi/v1/openInterest", {"symbol": SYMBOL})
        oi_data = oi_response["payload"]
        api_require(isinstance(oi_data, dict) and "openInterest" in oi_data, "Missing openInterest")
        oi_btc = safe_float(oi_data.get("openInterest"))
        api_require(oi_btc is not None and oi_btc > 0, "Invalid open interest")
        result["endpoint_info"]["open_interest_host"] = oi_response["host"]
        result["_oi_btc"] = oi_btc
        result["_oi_timestamp"] = timestamp_from_ms(oi_data.get("time")) or oi_response["fetched_at"]
    except DataFetchError as exc:
        result["oi_usd"] = metric_failure(exc)
        result["errors"]["open_interest"] = exc.as_dict()

    try:
        premium_response = request_with_host_fallback(BINANCE_FUTURES_HOSTS, "/fapi/v1/premiumIndex", {"symbol": SYMBOL})
        premium = premium_response["payload"]
        api_require(isinstance(premium, dict), "Premium index not object")
        mark_price = safe_float(premium.get("markPrice"))
        funding_rate = safe_float(premium.get("lastFundingRate"))
        if mark_price is None or mark_price <= 0: raise DataFetchError("INVALID_RESPONSE", "Invalid mark price")
        premium_timestamp = timestamp_from_ms(premium.get("time")) or premium_response["fetched_at"]
        result["_mark_price"] = mark_price
        result["_mark_timestamp"] = premium_timestamp
        result["funding_rate"] = metric_ok(funding_rate, premium_timestamp, premium.get("time"))
        result["endpoint_info"]["premium_index_host"] = premium_response["host"]
    except DataFetchError as exc:
        result["funding_rate"] = metric_failure(exc)
        result["errors"]["funding"] = exc.as_dict()

    try:
        perp_response = request_with_host_fallback(BINANCE_FUTURES_HOSTS, "/fapi/v1/ticker/price", {"symbol": SYMBOL})
        perp_data = perp_response["payload"]
        perp_price = safe_float(perp_data.get("price"))
        api_require(perp_price is not None and perp_price > 0, "Invalid perp price")
        perp_timestamp = timestamp_from_ms(perp_data.get("time")) or perp_response["fetched_at"]
        result["perp_price"] = metric_ok(perp_price, perp_timestamp, perp_data.get("time"))
    except DataFetchError as exc:
        result["perp_price"] = metric_failure(exc)
        result["errors"]["perp_price"] = exc.as_dict()

    try:
        spot_response = request_with_host_fallback(BINANCE_SPOT_HOSTS, "/api/v3/ticker/price", {"symbol": SYMBOL})
        spot_data = spot_response["payload"]
        spot_price = safe_float(spot_data.get("price"))
        api_require(spot_price is not None and spot_price > 0, "Invalid spot price")
        result["spot_price"] = metric_ok(spot_price, spot_response["fetched_at"], None)
    except DataFetchError as exc:
        result["spot_price"] = metric_failure(exc)
        result["errors"]["spot_price"] = exc.as_dict()

    oi_btc = result.pop("_oi_btc", None)
    oi_timestamp = result.pop("_oi_timestamp", None)
    conversion_price = result.pop("_mark_price", None)
    conversion_timestamp = result.pop("_mark_timestamp", None)

    if conversion_price is None:
        conversion_price = result["perp_price"].get("value")
        conversion_timestamp = timestamp_from_iso(result["perp_price"].get("timestamp"))

    if oi_btc is not None and conversion_price is not None:
        oi_usd = oi_btc * conversion_price
        effective_timestamp = min(value for value in (oi_timestamp, conversion_timestamp) if value is not None)
        result["oi_usd"] = metric_ok(oi_usd, effective_timestamp, None)
        result["oi_usd"]["oi_native_btc"] = oi_btc
        result["oi_usd"]["conversion_price"] = conversion_price
    elif oi_btc is not None:
        error = DataFetchError("DATA_UNAVAILABLE", "No valid price for USD conversion")
        result["oi_usd"] = metric_failure(error)
        result["errors"]["oi_usd_conversion"] = error.as_dict()
    elif result["oi_usd"].get("status") == "NOT_COLLECTED":
        result["oi_usd"] = metric_failure(DataFetchError("DATA_UNAVAILABLE", "Binance OI unavailable"))

    perp = result["perp_price"].get("value")
    spot = result["spot_price"].get("value")
    if perp is not None and spot is not None and spot > 0:
        result["basis_pct"] = {"value": ((perp / spot) - 1.0) * 100.0, "status": "OK", "formula": "((perp_last / spot_last) - 1) * 100"}
    else:
        result["basis_pct"] = {"value": None, "status": "DATA_UNAVAILABLE", "formula": "((perp_last / spot_last) - 1) * 100"}

    return result

BYBIT_HOST = "https://api.bybit.com"

def collect_bybit():
    result = exchange_template()
    try:
        response = request_json(BYBIT_HOST + "/v5/market/tickers", {"category": "linear", "symbol": SYMBOL})
        payload = response["payload"]
        api_require(isinstance(payload, dict) and str(payload.get("retCode")) == "0", "Bybit error")
        rows = payload.get("result", {}).get("list", [])
        api_require(isinstance(rows, list) and len(rows) > 0, "No Bybit rows")
        row = rows[0]
        oi_usd = safe_float(row.get("openInterestValue"))
        funding = safe_float(row.get("fundingRate"))
        perp_price = safe_float(row.get("lastPrice"))
        source_time = timestamp_from_ms(payload.get("time"))
        metric_time = source_time or response["fetched_at"]
        
        if oi_usd is not None and oi_usd > 0:
            result["oi_usd"] = metric_ok(oi_usd, metric_time, payload.get("time"))
        else:
            error = DataFetchError("INVALID_RESPONSE", "Invalid Bybit OI")
            result["oi_usd"] = metric_failure(error)
            result["errors"]["open_interest"] = error.as_dict()
            
        result["funding_rate"] = metric_ok(funding, metric_time, payload.get("time"))
        result["perp_price"] = metric_ok(perp_price, metric_time, payload.get("time"))
    except DataFetchError as exc:
        result["oi_usd"] = metric_failure(exc)
        result["funding_rate"] = metric_failure(exc)
        result["perp_price"] = metric_failure(exc)
        result["errors"]["linear_ticker"] = exc.as_dict()

    try:
        response = request_json(BYBIT_HOST + "/v5/market/tickers", {"category": "spot", "symbol": SYMBOL})
        payload = response["payload"]
        api_require(isinstance(payload, dict) and str(payload.get("retCode")) == "0", "Bybit spot error")
        rows = payload.get("result", {}).get("list", [])
        api_require(isinstance(rows, list) and len(rows) > 0, "No Bybit spot rows")
        spot_price = safe_float(rows[0].get("lastPrice"))
        result["spot_price"] = metric_ok(spot_price, timestamp_from_ms(payload.get("time")) or response["fetched_at"], payload.get("time"))
    except DataFetchError as exc:
        result["spot_price"] = metric_failure(exc)
        result["errors"]["spot_price"] = exc.as_dict()

    perp = result["perp_price"].get("value")
    spot = result["spot_price"].get("value")
    if perp is not None and spot is not None and spot > 0:
        result["basis_pct"] = {"value": ((perp / spot) - 1.0) * 100.0, "status": "OK", "formula": "((perp_last / spot_last) - 1) * 100"}
    else:
        result["basis_pct"] = {"value": None, "status": "DATA_UNAVAILABLE", "formula": "((perp_last / spot_last) - 1) * 100"}
    return result

OKX_HOSTS = ("https://openapi.okx.com", "https://www.okx.com")

def okx_request(path, params=None):
    response = request_with_host_fallback(OKX_HOSTS, path, params)
    payload = response["payload"]
    api_require(isinstance(payload, dict) and str(payload.get("code")) == "0", "OKX error")
    rows = payload.get("data")
    api_require(isinstance(rows, list) and len(rows) > 0, "No OKX rows")
    response["rows"] = rows
    return response

def collect_okx():
    result = exchange_template()
    try:
        response = okx_request("/api/v5/public/open-interest", {"instType": "SWAP", "instId": OKX_SWAP_ID})
        row = response["rows"][0]
        oi_usd = safe_float(row.get("oiUsd"))
        api_require(oi_usd is not None and oi_usd > 0, "Invalid OKX OI")
        result["oi_usd"] = metric_ok(oi_usd, timestamp_from_ms(row.get("ts")) or response["fetched_at"], row.get("ts"))
        result["endpoint_info"]["open_interest_host"] = response["host"]
    except DataFetchError as exc:
        result["oi_usd"] = metric_failure(exc)
        result["errors"]["open_interest"] = exc.as_dict()

    try:
        response = okx_request("/api/v5/public/funding-rate", {"instId": OKX_SWAP_ID})
        row = response["rows"][0]
        funding = safe_float(row.get("fundingRate"))
        result["funding_rate"] = metric_ok(funding, response["fetched_at"], row.get("fundingTime"))
        result["endpoint_info"]["funding_host"] = response["host"]
    except DataFetchError as exc:
        result["funding_rate"] = metric_failure(exc)
        result["errors"]["funding"] = exc.as_dict()

    try:
        response = okx_request("/api/v5/market/ticker", {"instId": OKX_SWAP_ID})
        row = response["rows"][0]
        perp_price = safe_float(row.get("last"))
        result["perp_price"] = metric_ok(perp_price, timestamp_from_ms(row.get("ts")) or response["fetched_at"], row.get("ts"))
    except DataFetchError as exc:
        result["perp_price"] = metric_failure(exc)
        result["errors"]["perp_price"] = exc.as_dict()

    try:
        response = okx_request("/api/v5/market/ticker", {"instId": OKX_SPOT_ID})
        row = response["rows"][0]
        spot_price = safe_float(row.get("last"))
        result["spot_price"] = metric_ok(spot_price, timestamp_from_ms(row.get("ts")) or response["fetched_at"], row.get("ts"))
    except DataFetchError as exc:
        result["spot_price"] = metric_failure(exc)
        result["errors"]["spot_price"] = exc.as_dict()

    perp = result["perp_price"].get("value")
    spot = result["spot_price"].get("value")
    if perp is not None and spot is not None and spot > 0:
        result["basis_pct"] = {"value": ((perp / spot) - 1.0) * 100.0, "status": "OK", "formula": "((perp_last / spot_last) - 1) * 100"}
    else:
        result["basis_pct"] = {"value": None, "status": "DATA_UNAVAILABLE", "formula": "((perp_last / spot_last) - 1) * 100"}
    return result

COLLECTORS = {"BINANCE": collect_binance, "BYBIT": collect_bybit, "OKX": collect_okx}

def collect_all_exchanges():
    exchanges = {}
    for name, collector in COLLECTORS.items():
        try:
            exchanges[name] = collector()
        except Exception as exc:
            error = DataFetchError(classify_exception(exc), "Collector failure: {}".format(exc))
            failed = exchange_template()
            for metric in ("oi_usd", "funding_rate", "perp_price", "spot_price"):
                failed[metric] = metric_failure(error)
            failed["basis_pct"] = {"value": None, "status": "DATA_UNAVAILABLE"}
            failed["errors"]["collector"] = error.as_dict()
            exchanges[name] = failed
    return exchanges

def valid_metric_value(exchange_data, metric):
    item = exchange_data.get(metric, {})
    if not isinstance(item, dict): return None
    if item.get("status") != "OK": return None
    return safe_float(item.get("value"))

def build_snapshot(exchanges):
    oi_contributors = []
    missing_exchanges = []
    total_oi_usd = 0.0
    funding_weighted_sum = 0.0
    funding_weight = 0.0
    basis_weighted_sum = 0.0
    basis_weight = 0.0
    funding_contributors = []
    basis_contributors = []

    for name in EXCHANGE_NAMES:
        data = exchanges.get(name, {})
        oi = valid_metric_value(data, "oi_usd")
        if oi is not None and oi > 0:
            total_oi_usd += oi
            oi_contributors.append(name)
        else:
            missing_exchanges.append(name)
            
        funding = valid_metric_value(data, "funding_rate")
        if funding is not None and oi is not None and oi > 0:
            funding_weighted_sum += funding * oi
            funding_weight += oi
            funding_contributors.append(name)
            
        basis = valid_metric_value(data, "basis_pct")
        if basis is not None and oi is not None and oi > 0:
            basis_weighted_sum += basis * oi
            basis_weight += oi
            basis_contributors.append(name)

    coverage_count = len(oi_contributors)
    total_exchanges = len(EXCHANGE_NAMES)
    aggregate_oi = total_oi_usd if coverage_count else None

    if coverage_count == total_exchanges: coverage_status = "COMPLETE"
    elif coverage_count > 0: coverage_status = "PARTIAL"
    else: coverage_status = "UNAVAILABLE"

    if coverage_count == 0: interpretation = "No valid OI."
    elif coverage_count == 1: interpretation = f"PARTIAL: {oi_contributors[0].capitalize()}-only OI."
    elif coverage_count == 2: interpretation = f"PARTIAL: {', '.join(oi_contributors)} OI."
    else: interpretation = "THREE-EXCHANGE OBSERVATION."

    return {
        "collected_at": iso_utc(),
        "collected_epoch": epoch_now(),
        "coverage": {
            "available": coverage_count, "expected": total_exchanges, "label": f"{coverage_count}/{total_exchanges}",
            "status": coverage_status, "contributors": oi_contributors, "missing_exchanges": missing_exchanges,
        },
        "aggregate": {
            "oi_usd": aggregate_oi,
            "funding_rate_oi_weighted": funding_weighted_sum / funding_weight if funding_weight > 0 else None,
            "basis_pct_oi_weighted": basis_weighted_sum / basis_weight if basis_weight > 0 else None,
            "funding_contributors": funding_contributors, "basis_contributors": basis_contributors,
        },
        "interpretation": interpretation,
        "exchanges": exchanges,
        "oi_30m_delta": {"status": "PENDING_BASELINE", "change_pct": None, "change_usd": None, "matched_contributors": [], "message": "Waiting for history."},
    }

class DeltaTracker:
    def __init__(self):
        self.history = deque(maxlen=120)

    def add_and_calculate(self, snapshot):
        current_epoch = snapshot["collected_epoch"]
        current_values = {}
        for name in EXCHANGE_NAMES:
            value = valid_metric_value(snapshot["exchanges"].get(name, {}), "oi_usd")
            if value is not None and value > 0: current_values[name] = value

        eligible = []
        for old in self.history:
            age = current_epoch - old["collected_epoch"]
            if DELTA_MIN_AGE_SECONDS <= age <= DELTA_MAX_AGE_SECONDS:
                eligible.append((abs(age - DELTA_WINDOW_SECONDS), old))

        if not eligible:
            snapshot["oi_30m_delta"] = {"status": "PENDING_BASELINE", "change_pct": None, "change_usd": None, "matched_contributors": [], "message": "Waiting for history."}
            self.history.append({"collected_epoch": current_epoch, "oi_values": current_values})
            return snapshot

        eligible.sort(key=lambda item: item[0])
        old_snapshot = eligible[0][1]
        old_values = old_snapshot["oi_values"]
        matched = sorted(set(current_values.keys()) & set(old_values.keys()))

        if not matched:
            delta_result = {"status": "NO_MATCHED_CONTRIBUTORS", "change_pct": None, "change_usd": None, "matched_contributors": [], "message": "No matched exchanges."}
        else:
            old_total = sum(old_values[name] for name in matched)
            current_total = sum(current_values[name] for name in matched)
            change_usd = current_total - old_total
            change_pct = ((current_total / old_total) - 1.0) * 100.0 if old_total > 0 else None
            delta_result = {
                "status": "OK" if change_pct is not None else "INVALID_BASELINE",
                "change_pct": change_pct, "change_usd": change_usd, "matched_contributors": matched,
                "baseline_timestamp": iso_utc(datetime.fromtimestamp(old_snapshot["collected_epoch"], timezone.utc)),
                "baseline_age_minutes": round((current_epoch - old_snapshot["collected_epoch"]) / 60.0, 2),
                "old_matched_oi_usd": old_total, "current_matched_oi_usd": current_total,
                "message": "Calculated using matched contributors."
            }

        snapshot["oi_30m_delta"] = delta_result
        self.history.append({"collected_epoch": current_epoch, "oi_values": current_values})
        cutoff = current_epoch - (90 * 60)
        while self.history and self.history[0]["collected_epoch"] < cutoff: self.history.popleft()
        return snapshot

class FuturesPositioning:
    def __init__(self):
        self.delta_tracker = DeltaTracker()

    def collect(self):
        exchanges = collect_all_exchanges()
        snapshot = build_snapshot(exchanges)
        return self.delta_tracker.add_and_calculate(snapshot)