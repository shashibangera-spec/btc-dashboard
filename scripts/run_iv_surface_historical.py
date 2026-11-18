#!/usr/bin/env python3
"""Run versioned historical IV-surface research from Supabase (read-only).

Requires SUPABASE_URL and SUPABASE_ANON_KEY with SELECT policies on the three
source tables. Uses bounded snapshot batches (default 25); never writes to DB.
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import sys
from datetime import datetime, time, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from supabase import create_client

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from iv_surface_research import VERSION, calculate_iv_surface_research

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
BATCH_SIZE_DEFAULT = 25
PAGE_SIZE = 1000


def parse_dt(value):
    if not value:
        return None
    dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)


def fetch_all(query, page_size=PAGE_SIZE):
    rows, start = [], 0
    while True:
        page = query.range(start, start + page_size - 1).execute().data or []
        rows.extend(page)
        if len(page) < page_size:
            break
        start += page_size
    return rows


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE_DEFAULT)
    parser.add_argument("--output-dir", default="research_outputs")
    parser.add_argument("--format", choices=("csv", "parquet"), default="csv")
    args = parser.parse_args()
    if not 20 <= args.batch_size <= 25:
        parser.error("--batch-size must be between 20 and 25")
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        parser.error("Set SUPABASE_URL and SUPABASE_ANON_KEY (anon key only).")

    client = create_client(url, key)
    # Discover snapshot IDs from raw rows, then de-duplicate in stable order.
    raw_ids = fetch_all(client.table("options_oi_snapshots")
                        .select("snapshot_id").order("id"))
    snapshot_ids = list(dict.fromkeys(r["snapshot_id"] for r in raw_ids
                                      if r.get("snapshot_id")))
    runs_by_id, amt = {}, []
    for offset in range(0, len(snapshot_ids), args.batch_size):
        batch = snapshot_ids[offset:offset + args.batch_size]
        raw = fetch_all(client.table("options_oi_snapshots").select(
            "id,snapshot_id,collected_at,expiry_date,option_type,strike_price,"
            "spot_price,bid_price,ask_price,bid_iv,ask_iv,mark_iv,greeks"
        ).in_("snapshot_id", batch).order("id"))
        runs = fetch_all(client.table("options_collection_runs").select(
            "snapshot_id,started_at,completed_at,status"
        ).in_("snapshot_id", batch))
        for run in runs:
            runs_by_id[run["snapshot_id"]] = run
        grouped = {}
        for row in raw:
            grouped.setdefault((row["snapshot_id"], row["expiry_date"]), []).append(row)
        # Keep observations until after per-snapshot run metadata is joined.
        if offset == 0:
            observations = {}
        observations.update(grouped)

    # AMT candle history is small; select only the known BTC 30m source.
    amt = fetch_all(client.table("amt_snapshots").select(
        "id,symbol,timeframe,candle_open_time,candle_close_time,open_price,"
        "high_price,low_price,close_price,volume,atr_21,atr_buffer,prior_vah,"
        "prior_poc,prior_val,developing_vah,developing_poc,developing_val,"
        "confirmed_state,developing_state,state_output,profile_id"
    ).eq("symbol", "BTCUSDT").eq("timeframe", "30m").order("candle_close_time"))
    session_candles = []
    for candle in amt:
        op, cl = parse_dt(candle["candle_open_time"]), parse_dt(candle["candle_close_time"])
        if not op or not cl:
            continue
        local_open, local_close = op.astimezone(IST), cl.astimezone(IST)
        if local_open.time() >= time(5, 30) and local_close.time() <= time(17, 30):
            session_candles.append((candle, cl))

    rows_out = []
    for (snapshot_id, expiry), contracts in sorted(observations.items()):
        run = runs_by_id.get(snapshot_id, {})
        completed = parse_dt(run.get("completed_at"))
        started = parse_dt(run.get("started_at"))
        spot_values = [float(r["spot_price"]) for r in contracts if r.get("spot_price") is not None]
        spot = spot_values[0] if spot_values else None
        calc = calculate_iv_surface_research({"spot_price": spot, "contracts": contracts})
        session_date = started.astimezone(IST).date() if started else None
        eligible = [(c, close) for c, close in session_candles
                    if session_date and close.astimezone(IST).date() == session_date
                    and completed and close >= completed]
        eligible.sort(key=lambda item: item[1])
        candle, close = eligible[0] if eligible else (None, None)
        delay = (close - completed).total_seconds() / 60 if close and completed else None
        freshness = "UNMATCHED" if delay is None else ("FRESH" if delay <= 30 else "STALE")
        settle = datetime.combine(datetime.fromisoformat(expiry).date(), time(17, 30), IST).astimezone(UTC)
        hours_to_settlement = (settle - completed).total_seconds() / 3600 if completed else None
        legacy_calls = [r for r in contracts if str(r.get("option_type")).upper() == "CALL"
                        and r.get("mark_iv") is not None and r.get("bid_price") and r.get("ask_price")]
        legacy_puts = [r for r in contracts if str(r.get("option_type")).upper() == "PUT"
                       and r.get("mark_iv") is not None and r.get("bid_price") and r.get("ask_price")]
        def nearest_five(side):
            side_rows = [r for r in contracts if str(r.get("option_type")).upper() == side
                         and r.get("strike_price") is not None and spot is not None
                         and r.get("mark_iv") is not None and r.get("bid_price") and r.get("ask_price")]
            side_rows.sort(key=lambda r: abs(float(r["strike_price"]) - spot))
            vals = [float(r["mark_iv"]) for r in side_rows[:5]]
            return sum(vals) / len(vals) if vals else None
        legacy_call, legacy_put = nearest_five("CALL"), nearest_five("PUT")
        out = {
            "snapshot_id": snapshot_id, "expiry_date": expiry,
            "run_started_at": run.get("started_at"), "snapshot_completed_at": run.get("completed_at"),
            "spot_price": spot, "contract_count": len(contracts), **calc,
            "legacy_call_iv_nearest5": legacy_call, "legacy_put_iv_nearest5": legacy_put,
            "legacy_call_iv_premium_pct": ((legacy_call / legacy_put - 1) * 100
                if legacy_call is not None and legacy_put not in (None, 0) else None),
            "legacy_call_valid_count": len(legacy_calls), "legacy_put_valid_count": len(legacy_puts),
            "amt_candle_id": candle.get("id") if candle else None,
            "candle_open_time": candle.get("candle_open_time") if candle else None,
            "candle_close_time": candle.get("candle_close_time") if candle else None,
            "alignment_delay_minutes": delay, "freshness": freshness,
            "is_latest_fresh_for_candle": False,
            "hours_to_1730_settlement": hours_to_settlement,
            "days_to_expiry": hours_to_settlement / 24 if hours_to_settlement is not None else None,
        }
        if candle:
            for field in ("open_price","high_price","low_price","close_price","volume","atr_21",
                          "atr_buffer","prior_vah","prior_poc","prior_val","developing_vah",
                          "developing_poc","developing_val","confirmed_state","developing_state",
                          "state_output","profile_id"):
                out["amt_" + field] = candle.get(field)
        rows_out.append(out)

    # Flag all expiry rows sharing the latest fresh snapshot_id for each AMT candle.
    latest = {}
    for row in rows_out:
        if row["freshness"] == "FRESH" and row["amt_candle_id"] is not None:
            key = row["amt_candle_id"]
            stamp = row["snapshot_completed_at"] or ""
            if key not in latest or stamp > latest[key][0]:
                latest[key] = (stamp, row["snapshot_id"])
    for row in rows_out:
        if row["amt_candle_id"] in latest and row["snapshot_id"] == latest[row["amt_candle_id"]][1]:
            row["is_latest_fresh_for_candle"] = True

    if len(rows_out) != 1995:
        raise SystemExit(f"Reconciliation failed: expected 1995 observations, got {len(rows_out)}")
    outdir = Path(args.output_dir)
    outdir.mkdir(parents=True, exist_ok=True)
    output = outdir / f"iv-surface-research-v1.0.{args.format}"
    if args.format == "csv":
        fields = sorted({k for row in rows_out for k in row})
        with output.open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            for row in rows_out:
                writer.writerow({k: json.dumps(v) if isinstance(v, (dict, list)) else v
                                 for k, v in row.items()})
    else:
        try:
            import pandas as pd
        except ImportError as exc:
            raise SystemExit("Parquet output requires pandas and pyarrow.") from exc
        pd.DataFrame(rows_out).to_parquet(output, index=False)
    print(f"Rows reconciled: {len(rows_out)}")
    print(f"Snapshots: {len(snapshot_ids)} | expiry observations: {len(observations)}")
    print(f"Freshness: { {k: sum(r['freshness']==k for r in rows_out) for k in ('FRESH','STALE','UNMATCHED')} }")
    print(f"RR25 available: {sum(r['rr25_call_minus_put_vol_points'] is not None for r in rows_out)}")
    print(f"Output: {output.resolve()}")


if __name__ == "__main__":
    main()
