"""
collector.py
BTC Futures OI -> Supabase collector

Required files in repository root:
    collector.py
    futures_positioning.py

Environment variables:
    SUPABASE_URL
    SUPABASE_SERVICE_ROLE_KEY

Writes five records per run:
    BINANCE
    BYBIT
    OKX
    DELTA
    AGGREGATE

Unavailable metric values are stored as SQL NULL.
Detailed per-metric statuses and errors are stored in the TEXT status column.
"""

import json
import os
import sys
from datetime import datetime, timezone

from supabase import create_client

import futures_positioning as fp


TABLE_NAME = "futures_oi_snapshots"


def utc_now_iso():
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def metric_value(exchange_data, metric_name):
    """
    Return a metric only when its status is OK.
    Otherwise return None so Supabase receives SQL NULL.
    """
    metric = exchange_data.get(metric_name, {})

    if not isinstance(metric, dict):
        return None

    if metric.get("status") != "OK":
        return None

    value = metric.get("value")

    if value is None:
        return None

    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def metric_status(exchange_data, metric_name):
    metric = exchange_data.get(metric_name, {})

    if not isinstance(metric, dict):
        return {
            "status": "INVALID_RESPONSE",
            "error": "Metric response was not an object",
        }

    result = {
        "status": metric.get("status", "DATA_UNAVAILABLE"),
    }

    # Preserve useful diagnostic information.
    for key in (
        "error",
        "timestamp",
        "source_timestamp",
        "age_seconds",
        "formula",
        "oi_native_btc",
        "conversion_price",
    ):
        if key in metric:
            result[key] = metric[key]

    return result


def make_exchange_row(exchange_name, exchange_data):
    """
    Build one database row for one exchange.

    A metric is only stored as a number when its own status is OK.
    Failed or missing values become None -> SQL NULL.
    """

    status_payload = {
        "record_type": "EXCHANGE",
        "exchange": exchange_name,
        "collected_at": utc_now_iso(),
        "metrics": {
            "oi_usd": metric_status(exchange_data, "oi_usd"),
            "funding_rate": metric_status(exchange_data, "funding_rate"),
            "perp_price": metric_status(exchange_data, "perp_price"),
            "spot_price": metric_status(exchange_data, "spot_price"),
            "basis_pct": metric_status(exchange_data, "basis_pct"),
        },
        "errors": exchange_data.get("errors", {}),
        "endpoint_info": exchange_data.get("endpoint_info", {}),
    }

    return {
        "exchange": exchange_name,
        "oi_usd": metric_value(exchange_data, "oi_usd"),
        "funding_rate": metric_value(exchange_data, "funding_rate"),
        "basis_pct": metric_value(exchange_data, "basis_pct"),
        "status": json.dumps(
            status_payload,
            separators=(",", ":"),
            default=str,
        ),
    }


def make_aggregate_row(snapshot):
    """
    Build the aggregate record.

    This may be a partial aggregate. Its status JSON records the exact
    coverage and contributors, so it is never presented as global OI.
    """

    aggregate = snapshot.get("aggregate", {})
    coverage = snapshot.get("coverage", {})

    status_payload = {
        "record_type": "AGGREGATE",
        "collected_at": snapshot.get("collected_at"),
        "coverage": coverage,
        "interpretation": snapshot.get("interpretation"),
        "oi_contributors": coverage.get("contributors", []),
        "missing_exchanges": coverage.get("missing_exchanges", []),
        "funding_contributors": aggregate.get(
            "funding_contributors", []
        ),
        "basis_contributors": aggregate.get(
            "basis_contributors", []
        ),
        "oi_30m_delta": snapshot.get("oi_30m_delta", {}),
    }

    return {
        "exchange": "AGGREGATE",
        "oi_usd": aggregate.get("oi_usd"),
        "funding_rate": aggregate.get("funding_rate_oi_weighted"),
        "basis_pct": aggregate.get("basis_pct_oi_weighted"),
        "status": json.dumps(
            status_payload,
            separators=(",", ":"),
            default=str,
        ),
    }


def build_database_rows(snapshot):
    rows = []

    exchanges = snapshot.get("exchanges", {})

    # FIXED: Added "DELTA" to this tuple so it gets its own row
    for exchange_name in ("BINANCE", "BYBIT", "OKX", "DELTA"):
        exchange_data = exchanges.get(exchange_name, {})

        rows.append(
            make_exchange_row(exchange_name, exchange_data)
        )

    rows.append(make_aggregate_row(snapshot))

    return rows


def main():
    supabase_url = os.getenv("SUPABASE_URL")
    service_role_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not supabase_url:
        print(
            "ERROR: SUPABASE_URL environment variable is missing.",
            file=sys.stderr,
        )
        return 1

    if not service_role_key:
        print(
            "ERROR: SUPABASE_SERVICE_ROLE_KEY environment variable is missing.",
            file=sys.stderr,
        )
        return 1

    print("BTC Futures Supabase Collector")
    print("Version: 1.0")
    print("Started: {}".format(utc_now_iso()))
    print("Collecting exchange data...")

    try:
        # Reuse the tested v1.2 exchange collectors.
        exchanges = fp.collect_all_exchanges()

        # Build the same partial-coverage aggregate used by the
        # futures-positioning research module.
        snapshot = fp.build_snapshot(exchanges)

        rows = build_database_rows(snapshot)

        print(
            "Coverage: {}".format(
                snapshot.get("coverage", {}).get("label", "UNKNOWN")
            )
        )
        print(
            "Contributors: {}".format(
                snapshot.get("coverage", {}).get("contributors", [])
            )
        )
        print(
            "Missing exchanges: {}".format(
                snapshot.get("coverage", {}).get(
                    "missing_exchanges", []
                )
            )
        )
        print("Preparing {} database rows.".format(len(rows)))

        # Create the Supabase client only after market data collection.
        client = create_client(
            supabase_url,
            service_role_key,
        )

        response = (
            client
            .table(TABLE_NAME)
            .insert(rows)
            .execute()
        )

        inserted_count = (
            len(response.data)
            if getattr(response, "data", None) is not None
            else len(rows)
        )

        print("Supabase insert successful.")
        print("Rows inserted: {}".format(inserted_count))
        print("Finished: {}".format(utc_now_iso()))

        print(
            "Aggregate interpretation: {}".format(
                snapshot.get("interpretation", "Unavailable")
            )
        )

        return 0

    except Exception as exc:
        print(
            "COLLECTOR FAILED: {}: {}".format(
                type(exc).__name__,
                str(exc),
            ),
            file=sys.stderr,
        )
        print(
            "Finished with failure: {}".format(utc_now_iso()),
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    sys.exit(main())
