#!/usr/bin/env python3
"""Read-only empirical Layer 4 analysis for iv-surface-research-v1.0.

Input: the reconciled CSV produced by scripts/run_iv_surface_historical.py.
The script selects only FRESH rows flagged is_latest_fresh_for_candle=True,
recomputes the legacy five-nearest-strikes skew from raw option rows using the
same aggregation/selection/formula as options_filters.calculate_iv_skew(),
and reads forward AMT closes for strictly post-candle returns.

Supabase access is SELECT-only via SUPABASE_URL + SUPABASE_ANON_KEY.
No database writes, production imports, or production-code changes.
"""
from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
import sys
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable
from zoneinfo import ZoneInfo

IST = ZoneInfo("Asia/Kolkata")
UTC = timezone.utc
VERSION = "iv-surface-research-v1.0"
WIDE_SPREAD_PCT = 10.0
SENTINEL_IV = 0.01
PAGE_SIZE = 1000
BATCH_SIZE = 25
EXPECTED_COLUMNS = {
    "snapshot_id", "expiry_date", "snapshot_completed_at", "spot_price",
    "atm_iv", "rr25_call_minus_put_vol_points", "amt_candle_id",
    "candle_close_time", "amt_close_price", "amt_confirmed_state",
    "amt_developing_state", "freshness", "is_latest_fresh_for_candle",
}


def finite(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


def parse_bool(value: Any) -> bool:
    return str(value).strip().lower() in {"true", "1", "yes"}


def parse_dt(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return result if result.tzinfo else result.replace(tzinfo=UTC)


def load_fresh_observations(path: str | Path) -> list[dict[str, Any]]:
    with Path(path).open("r", newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        missing = EXPECTED_COLUMNS - set(reader.fieldnames or [])
        if missing:
            raise ValueError(f"Input CSV missing required columns: {sorted(missing)}")
        rows = [
            row for row in reader
            if row.get("freshness") == "FRESH"
            and parse_bool(row.get("is_latest_fresh_for_candle"))
        ]
    if not rows:
        raise ValueError("No rows satisfy freshness=FRESH and is_latest_fresh_for_candle=True.")
    return rows


def dte_days(expiry_value: str, completed_value: str) -> int | None:
    completed = parse_dt(completed_value)
    if completed is None:
        return None
    try:
        expiry = date.fromisoformat(str(expiry_value)[:10])
    except (TypeError, ValueError):
        return None
    return (expiry - completed.astimezone(IST).date()).days


def dte_bucket(days: int | None) -> str:
    if days is None:
        return "UNKNOWN"
    if days <= 0:
        return "0DTE"
    if days <= 2:
        return "1-2 DTE"
    if days <= 7:
        return "Weekly (3-7 DTE)"
    return "Monthly (>7 DTE)"


def legacy_nearest_five_skew(contracts: Iterable[dict[str, Any]],
                             spot_price: float | None) -> dict[str, Any]:
    """Faithful reproduction of options_filters.calculate_iv_skew.

    _aggregate_chain groups by strike and side, taking mean mark_iv per side.
    _nearest selects the five closest unique strikes, then averages non-null
    Call IV and Put IV values independently. The reported metric is
    ((mean_call - mean_put) / mean_put) * 100.
    """
    if spot_price is None:
        return {"legacy_skew_pct": None, "legacy_call_iv": None,
                "legacy_put_iv": None, "legacy_reason": "MISSING_SPOT"}
    grouped: dict[float, dict[str, list[float]]] = defaultdict(
        lambda: {"CALL": [], "PUT": []}
    )
    for contract in contracts:
        strike = finite(contract.get("strike_price"))
        iv = finite(contract.get("mark_iv"))
        side = str(contract.get("option_type") or "").upper()
        if strike is None or side not in {"CALL", "PUT"} or iv is None:
            continue
        grouped[strike][side].append(iv)
    if not grouped:
        return {"legacy_skew_pct": None, "legacy_call_iv": None,
                "legacy_put_iv": None, "legacy_reason": "NO_DATA"}
    chain = []
    for strike, sides in grouped.items():
        call_iv = statistics.mean(sides["CALL"]) if sides["CALL"] else None
        put_iv = statistics.mean(sides["PUT"]) if sides["PUT"] else None
        chain.append((strike, abs(strike - spot_price), call_iv, put_iv))
    # Stable tie-break mirrors the strike-sorted chain passed to pandas nsmallest.
    chain.sort(key=lambda item: item[0])
    nearest = sorted(chain, key=lambda item: item[1])[:5]
    calls = [item[2] for item in nearest if item[2] is not None]
    puts = [item[3] for item in nearest if item[3] is not None]
    if not calls or not puts:
        return {"legacy_skew_pct": None, "legacy_call_iv": None,
                "legacy_put_iv": None, "legacy_reason": "NULL_IV_SIDE"}
    avg_call, avg_put = statistics.mean(calls), statistics.mean(puts)
    if avg_put == 0:
        return {"legacy_skew_pct": None, "legacy_call_iv": avg_call,
                "legacy_put_iv": avg_put, "legacy_reason": "ZERO_PUT_IV"}
    return {
        "legacy_skew_pct": ((avg_call - avg_put) / avg_put) * 100.0,
        "legacy_call_iv": avg_call,
        "legacy_put_iv": avg_put,
        "legacy_reason": None,
    }


def quote_exposure(contracts: Iterable[dict[str, Any]]) -> dict[str, Any]:
    wide_count = sentinel_count = valid_spreads = 0
    max_spread = None
    for contract in contracts:
        bid, ask = finite(contract.get("bid_price")), finite(contract.get("ask_price"))
        bid_iv, ask_iv = finite(contract.get("bid_iv")), finite(contract.get("ask_iv"))
        if ((bid_iv is not None and math.isclose(bid_iv, SENTINEL_IV, abs_tol=1e-12))
                or (ask_iv is not None and math.isclose(ask_iv, SENTINEL_IV, abs_tol=1e-12))):
            sentinel_count += 1
        if bid is not None and ask is not None and bid > 0 and ask > 0 and bid <= ask:
            mid = (bid + ask) / 2.0
            if mid > 0:
                spread = (ask - bid) / mid * 100.0
                valid_spreads += 1
                max_spread = spread if max_spread is None else max(max_spread, spread)
                if spread > WIDE_SPREAD_PCT:
                    wide_count += 1
    return {
        "wide_spread_contract_count": wide_count,
        "valid_spread_contract_count": valid_spreads,
        "wide_spread_exposed": wide_count > 0,
        "sentinel_contract_count": sentinel_count,
        "sentinel_exposed": sentinel_count > 0,
        "max_spread_pct_mid": max_spread,
    }


def pearson(pairs: Iterable[tuple[float, float]]) -> tuple[float | None, int]:
    clean = [(x, y) for x, y in pairs if math.isfinite(x) and math.isfinite(y)]
    n = len(clean)
    if n < 2:
        return None, n
    xs, ys = [p[0] for p in clean], [p[1] for p in clean]
    mx, my = statistics.mean(xs), statistics.mean(ys)
    dx, dy = [x - mx for x in xs], [y - my for y in ys]
    denom = math.sqrt(sum(x*x for x in dx) * sum(y*y for y in dy))
    if denom == 0:
        return None, n
    return sum(x*y for x, y in zip(dx, dy)) / denom, n


def metric_summary(rows: list[dict[str, Any]], key: str) -> tuple[int, float | None, float | None]:
    values = [finite(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    if not values:
        return 0, None, None
    return len(values), statistics.mean(values), statistics.median(values)


def fmt(value: float | None, digits: int = 4) -> str:
    return "NA" if value is None else f"{value:.{digits}f}"


def pct(numerator: int, denominator: int) -> str:
    return "NA" if denominator == 0 else f"{numerator / denominator * 100:.2f}%"


def fetch_all(query: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    start = 0
    while True:
        page = query.range(start, start + PAGE_SIZE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE_SIZE:
            return rows
        start += PAGE_SIZE


def fetch_contracts_for_fresh_rows(client: Any,
                                   rows: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    snapshot_ids = sorted({row["snapshot_id"] for row in rows if row.get("snapshot_id")})
    wanted = {(row["snapshot_id"], str(row["expiry_date"])[:10]) for row in rows}
    result: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    columns = ("snapshot_id,expiry_date,option_type,strike_price,mark_iv,"
               "bid_price,ask_price,bid_iv,ask_iv")
    for offset in range(0, len(snapshot_ids), BATCH_SIZE):
        batch = snapshot_ids[offset:offset + BATCH_SIZE]
        raw = fetch_all(
            client.table("options_oi_snapshots").select(columns)
            .in_("snapshot_id", batch).order("id")
        )
        for contract in raw:
            key = (contract.get("snapshot_id"), str(contract.get("expiry_date"))[:10])
            if key in wanted:
                result[key].append(contract)
    missing = wanted - set(result)
    if missing:
        print(f"WARNING: {len(missing)} fresh snapshot/expiry groups had no raw option rows.",
              file=sys.stderr)
    return result


def fetch_amt_closes(client: Any, rows: list[dict[str, Any]]) -> dict[datetime, float]:
    anchors = [parse_dt(row.get("candle_close_time")) for row in rows]
    anchors = [value for value in anchors if value is not None]
    if not anchors:
        return {}
    start = min(anchors)
    end = max(anchors) + timedelta(minutes=60)
    raw = fetch_all(
        client.table("amt_snapshots").select(
            "id,symbol,timeframe,candle_close_time,close_price"
        ).eq("symbol", "BTCUSDT").eq("timeframe", "30")
        .gte("candle_close_time", start.isoformat())
        .lte("candle_close_time", end.isoformat())
        .order("candle_close_time")
    )
    closes: dict[datetime, float] = {}
    for candle in raw:
        stamp, close = parse_dt(candle.get("candle_close_time")), finite(candle.get("close_price"))
        if stamp is not None and close is not None:
            closes[stamp] = close
    return closes


def build_candle_records(rows: list[dict[str, Any]],
                         closes: dict[datetime, float]) -> list[dict[str, Any]]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        if row.get("amt_candle_id"):
            grouped[str(row["amt_candle_id"])].append(row)
    records = []
    for candle_id, expiry_rows in grouped.items():
        anchor_time = parse_dt(expiry_rows[0].get("candle_close_time"))
        anchor_close = finite(expiry_rows[0].get("amt_close_price"))
        if anchor_time is None or anchor_close is None:
            continue
        record: dict[str, Any] = {
            "amt_candle_id": candle_id,
            "candle_close_time": anchor_time,
            "confirmed_state": unique_state(expiry_rows, "amt_confirmed_state"),
            "developing_state": unique_state(expiry_rows, "amt_developing_state"),
            "expiry_observation_count": len(expiry_rows),
            "atm_iv": median_metric(expiry_rows, "atm_iv"),
            "rr25": median_metric(expiry_rows, "rr25_call_minus_put_vol_points"),
            "legacy_skew": median_metric(expiry_rows, "legacy_skew_pct"),
            "btc_close": anchor_close,
        }
        # Strict post-candle returns: use only closes at t+30m and t+60m.
        for minutes in (30, 60):
            future_time = anchor_time + timedelta(minutes=minutes)
            future_close = closes.get(future_time)
            record[f"btc_return_{minutes}m_pct"] = (
                (future_close / anchor_close - 1.0) * 100.0
                if future_close is not None and future_time > anchor_time else None
            )
        records.append(record)
    return sorted(records, key=lambda row: row["candle_close_time"])


def unique_state(rows: list[dict[str, Any]], key: str) -> str:
    values = {str(row.get(key) or "MISSING") for row in rows}
    return next(iter(values)) if len(values) == 1 else "STATE_CONFLICT"


def median_metric(rows: list[dict[str, Any]], key: str) -> float | None:
    values = [finite(row.get(key)) for row in rows]
    values = [value for value in values if value is not None]
    return statistics.median(values) if values else None


def print_stats_table(title: str, rows: list[dict[str, Any]],
                      group_key: str, metrics: list[tuple[str, str]]) -> None:
    print(f"\n{title}")
    headers = [group_key, "Candles"] + [
        part for label, _ in metrics for part in (f"{label} mean", f"{label} median")
    ]
    print(" | ".join(headers))
    print("-+-".join("-" * min(22, max(8, len(item))) for item in headers))
    groups = defaultdict(list)
    for row in rows:
        groups[str(row.get(group_key) or "MISSING")].append(row)
    for group in sorted(groups):
        subset = groups[group]
        cells = [group, str(len(subset))]
        for _, key in metrics:
            n, mean, med = metric_summary(subset, key)
            cells.extend([fmt(mean), fmt(med)])
        print(" | ".join(cells))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", default="research_outputs/iv-surface-research-v1.0.csv")
    args = parser.parse_args()
    url, key = os.getenv("SUPABASE_URL"), os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        parser.error("Set SUPABASE_URL and SUPABASE_ANON_KEY (anon key only).")

    rows = load_fresh_observations(args.input)
    # Add numeric metrics and DTE labels from the reconciled, versioned output.
    for row in rows:
        row["atm_iv"] = finite(row.get("atm_iv"))
        row["rr25_call_minus_put_vol_points"] = finite(row.get("rr25_call_minus_put_vol_points"))
        row["spot_price"] = finite(row.get("spot_price"))
        row["dte"] = dte_days(row.get("expiry_date", ""), row.get("snapshot_completed_at", ""))
        row["dte_bucket"] = dte_bucket(row["dte"])

    from supabase import create_client
    client = create_client(url, key)
    contracts_by_observation = fetch_contracts_for_fresh_rows(client, rows)
    exposure_counts = defaultdict(int)
    for row in rows:
        group_key = (row["snapshot_id"], str(row["expiry_date"])[:10])
        contracts = contracts_by_observation.get(group_key, [])
        row.update(legacy_nearest_five_skew(contracts, row["spot_price"]))
        row.update(quote_exposure(contracts))
        if row["wide_spread_exposed"] and row["sentinel_exposed"]:
            exposure_counts["WIDE_AND_SENTINEL"] += 1
        elif row["wide_spread_exposed"]:
            exposure_counts["WIDE_ONLY"] += 1
        elif row["sentinel_exposed"]:
            exposure_counts["SENTINEL_ONLY"] += 1
        else:
            exposure_counts["NEITHER"] += 1

    # Pairwise comparison: RR25 is in volatility points; legacy skew is percent.
    comparable = [
        row for row in rows
        if finite(row.get("rr25_call_minus_put_vol_points")) is not None
        and finite(row.get("legacy_skew_pct")) is not None
    ]
    rr_legacy_pairs = [
        (float(row["rr25_call_minus_put_vol_points"]), float(row["legacy_skew_pct"]))
        for row in comparable
    ]
    corr, corr_n = pearson(rr_legacy_pairs)
    flips = sum(
        1 for row in comparable
        if float(row["rr25_call_minus_put_vol_points"]) * float(row["legacy_skew_pct"]) < 0
    )
    print("\n========== EMPIRICAL LAYER 4 — READ-ONLY ANALYSIS ==========")
    print(f"Input: {args.input} | methodology: {VERSION}")
    print(f"Selected observations: {len(rows)} (FRESH and latest-fresh flag true)")
    print(f"Distinct AMT candles represented: {len({r.get('amt_candle_id') for r in rows})}")
    print("\n1) RR25 vs legacy nearest-five skew")
    print("Legacy = exact options_filters method: aggregate mean mark IV by strike/side,")
    print("select five nearest strikes, then ((mean Call IV - mean Put IV)/mean Put IV)*100.")
    print(f"Comparable observations: {len(comparable)} | Pearson r: {fmt(corr)} (n={corr_n})")
    print(f"Sign-flip disagreements: {flips}/{len(comparable)} ({pct(flips, len(comparable))})")
    print("Exposure mix (per expiry observation):")
    for label in ("NEITHER", "WIDE_ONLY", "SENTINEL_ONLY", "WIDE_AND_SENTINEL"):
        print(f"  {label}: {exposure_counts[label]} ({pct(exposure_counts[label], len(rows))})")
    print("Sensitivity cohorts (correlation and sign disagreement):")
    cohorts = [
        ("ALL", comparable),
        ("NO_WIDE_SPREAD_EXPOSURE", [r for r in comparable if not r["wide_spread_exposed"]]),
        ("WIDE_SPREAD_EXPOSED", [r for r in comparable if r["wide_spread_exposed"]]),
        ("NO_SENTINEL_EXPOSURE", [r for r in comparable if not r["sentinel_exposed"]]),
        ("SENTINEL_EXPOSED", [r for r in comparable if r["sentinel_exposed"]]),
        ("CLEAN_NEITHER", [r for r in comparable if not r["wide_spread_exposed"] and not r["sentinel_exposed"]]),
    ]
    for label, cohort in cohorts:
        c, n = pearson([
            (float(r["rr25_call_minus_put_vol_points"]), float(r["legacy_skew_pct"]))
            for r in cohort
        ])
        disagreement = sum(
            1 for r in cohort
            if float(r["rr25_call_minus_put_vol_points"]) * float(r["legacy_skew_pct"]) < 0
        )
        print(f"  {label}: n={n}, r={fmt(c)}, sign flips={disagreement}/{n} ({pct(disagreement, n)})")
    print("Note: spread/sentinel exposure is flagged across all contracts in that expiry chain;")
    print("RR25 itself continues to use its v1.0 valid-price/mark-IV rules.")

    print("\n2) Term structure — per expiry (expiry observations, not candle-deduplicated)")
    expiries = defaultdict(list)
    for row in rows:
        expiries[str(row["expiry_date"])[:10]].append(row)
    print("Expiry | DTE bucket | N | ATM IV mean | ATM IV median | RR25 mean | RR25 median")
    for expiry in sorted(expiries):
        subset = expiries[expiry]
        bucket = dte_bucket(subset[0]["dte"])
        _, atm_mean, atm_med = metric_summary(subset, "atm_iv")
        _, rr_mean, rr_med = metric_summary(subset, "rr25_call_minus_put_vol_points")
        print(f"{expiry} | {bucket} | {len(subset)} | {fmt(atm_mean)} | {fmt(atm_med)} | {fmt(rr_mean)} | {fmt(rr_med)}")
    print("\nTerm structure — DTE buckets")
    print("Bucket | N | ATM IV mean | ATM IV median | RR25 mean | RR25 median")
    bucket_order = ["0DTE", "1-2 DTE", "Weekly (3-7 DTE)", "Monthly (>7 DTE)", "UNKNOWN"]
    for bucket in bucket_order:
        subset = [row for row in rows if row["dte_bucket"] == bucket]
        if not subset:
            continue
        _, atm_mean, atm_med = metric_summary(subset, "atm_iv")
        _, rr_mean, rr_med = metric_summary(subset, "rr25_call_minus_put_vol_points")
        print(f"{bucket} | {len(subset)} | {fmt(atm_mean)} | {fmt(atm_med)} | {fmt(rr_mean)} | {fmt(rr_med)}")
    print("DTE bins are explicit research buckets: 0, 1–2, 3–7 (weekly), and >7 (monthly) calendar days.")

    # Forward returns require exact timestamps at t+30m/t+60m. Missing intervals
    # remain NA; no prior candle, same-candle close, or later-than-target close is substituted.
    closes = fetch_amt_closes(client, rows)
    candles = build_candle_records(rows, closes)
    print(f"\n3) AMT auction states — deduplicated candle records: {len(candles)}")
    state_metrics = [
        ("ATM IV", "atm_iv"),
        ("RR25", "rr25"),
        ("Legacy skew %", "legacy_skew"),
    ]
    print_stats_table("By confirmed_state (one median metric per candle)", candles,
                      "confirmed_state", state_metrics)
    print_stats_table("By developing_state (one median metric per candle)", candles,
                      "developing_state", state_metrics)

    print("\nForward BTC close-to-close returns (strictly post-anchor candle)")
    for horizon in (30, 60):
        key_name = f"btc_return_{horizon}m_pct"
        available = sum(finite(c.get(key_name)) is not None for c in candles)
        print(f"  +{horizon}m return available: {available}/{len(candles)}")
    print("Pearson correlations: candle-level median IV metric vs subsequent BTC return")
    print("Metric | +30m r (n) | +60m r (n)")
    for label, metric in (("ATM IV", "atm_iv"), ("RR25", "rr25"), ("Legacy skew %", "legacy_skew")):
        values = []
        for horizon in (30, 60):
            ret_key = f"btc_return_{horizon}m_pct"
            pairs = [
                (float(c[metric]), float(c[ret_key]))
                for c in candles
                if finite(c.get(metric)) is not None and finite(c.get(ret_key)) is not None
            ]
            value, n = pearson(pairs)
            values.append(f"{fmt(value)} (n={n})")
        print(f"{label} | {values[0]} | {values[1]}")

    print("\nForward return coverage note: exact t+30m/t+60m candle_close_time required;")
    print("missing/gapped future candles are excluded, and returns never use pre-anchor prices.")
    print("============================================================")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
