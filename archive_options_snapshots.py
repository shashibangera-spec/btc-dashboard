#!/usr/bin/env python3
import argparse
import csv
import gzip
import hashlib
import json
import os
import sys
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from pathlib import Path
import psycopg

TABLE_NAME = "public.options_oi_snapshots"
COLUMNS = [
    "id", "snapshot_id", "collected_at", "exchange_timestamp", "exchange",
    "underlying", "expiry_date", "option_type", "strike_price", "product_id",
    "symbol", "contract_status", "oi_contracts", "oi_value_usd", "volume_24h",
    "turnover_usd", "mark_price", "last_price", "spot_price", "bid_price",
    "ask_price", "bid_size", "ask_size", "bid_iv", "ask_iv", "mark_iv",
    "greeks", "data_status", "raw_payload", "taker_buy_volume", "taker_sell_volume",
]
BATCH_SIZE = 2000
MANIFEST_COLUMNS = [
    "source_table", "archive_mode", "partition_date_utc", "row_count",
    "min_collected_at_utc", "max_collected_at_utc", "file_name", "file_size_bytes",
    "sha256", "exported_at_utc", "cutoff_utc",
]

def parse_arguments():
    parser = argparse.ArgumentParser(description="Read-only BTC options archive exporter")
    parser.add_argument("--output", default="./btc_data_archive", help="Local archive output directory")
    parser.add_argument("--older-than-days", type=int, default=3, help="Export records older than this many days (default: 3)")
    parser.add_argument("--test-date", type=str, default=None, help="Export one specific UTC date, YYYY-MM-DD")
    parser.add_argument("--batch-size", type=int, default=BATCH_SIZE, help="Number of database records fetched per batch")
    args = parser.parse_args()
    if args.test_date:
        try: args.test_date = date.fromisoformat(args.test_date)
        except ValueError: parser.error("--test-date must use YYYY-MM-DD")
    return args

def csv_value(value):
    if value is None: return ""
    if isinstance(value, (dict, list)): return json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    if isinstance(value, (datetime, date)): return value.isoformat()
    if isinstance(value, Decimal): return str(value)
    return value

def utc_midnight(day):
    return datetime.combine(day, time.min, tzinfo=timezone.utc)

def date_range(start_day, end_day):
    current = start_day
    while current <= end_day:
        yield current
        current += timedelta(days=1)

def calculate_sha256(file_path):
    digest = hashlib.sha256()
    with open(file_path, "rb") as file:
        for chunk in iter(lambda: file.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()

def discover_dates(connection, test_date, cutoff):
    if test_date:
        start = utc_midnight(test_date); end = start + timedelta(days=1)
        query = f"SELECT COUNT(*), MIN(collected_at), MAX(collected_at) FROM {TABLE_NAME} WHERE collected_at >= %s AND collected_at < %s"
        params = (start, end); mode = "TEST_DATE"
    else:
        query = f"SELECT COUNT(*), MIN(collected_at), MAX(collected_at) FROM {TABLE_NAME} WHERE collected_at < %s"
        params = (cutoff,); mode = "OLDER_THAN_DAYS"
        
    with connection.cursor() as cursor:
        cursor.execute(query, params)
        total, minimum, maximum = cursor.fetchone()
    if not total: return [], mode
    first_day = minimum.astimezone(timezone.utc).date(); last_day = maximum.astimezone(timezone.utc).date()
    return list(date_range(first_day, last_day)), mode


def _row_signature(row):
    return tuple("" if row.get(column) is None else str(row.get(column)) for column in COLUMNS)


def merge_rows_by_id(existing_rows, database_rows):
    """Merge archived and newly eligible rows without losing prior partial exports."""
    id_column = COLUMNS.index("id")
    merged = {}
    for source, rows in (("archive", existing_rows), ("database", database_rows)):
        for row in rows:
            normalized = {
                column: ("" if row.get(column) is None else str(row.get(column)))
                for column in COLUMNS
            }
            row_id = normalized["id"]
            if not row_id:
                raise RuntimeError(f"{source} contains a row without id; refusing archive.")
            previous = merged.get(row_id)
            if previous is not None and _row_signature(previous) != _row_signature(normalized):
                raise RuntimeError(f"Conflicting contents for snapshot id {row_id}; refusing archive.")
            merged[row_id] = normalized
    return list(merged.values())


def read_existing_archive(path):
    """Read and validate an existing daily archive before merging into it."""
    if not path.exists():
        return []
    rows = []
    with gzip.open(path, mode="rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if reader.fieldnames != COLUMNS:
            raise RuntimeError(
                f"Existing archive schema mismatch for {path.name}: {reader.fieldnames}"
            )
        for row in reader:
            if not row.get("id") or not row.get("collected_at"):
                raise RuntimeError(f"Existing archive contains an incomplete row: {path.name}")
            rows.append(row)
    if not rows:
        raise RuntimeError(f"Existing archive is empty; refusing to replace {path.name}")
    return rows

def export_one_day(connection, day, output_directory, mode, cutoff, batch_size):
    start = utc_midnight(day); end = start + timedelta(days=1)
    output_name = f"options_oi_{day.isoformat()}.csv.gz"
    final_path = output_directory / output_name
    temp_path = output_directory / (output_name + ".tmp")

    if temp_path.exists():
        temp_path.unlink()

    if mode == "TEST_DATE":
        filter_sql = "collected_at >= %s AND collected_at < %s"
        filter_params = (start, end)
    else:
        filter_sql = "collected_at >= %s AND collected_at < %s AND collected_at < %s"
        filter_params = (start, end, cutoff)

    existing_rows = read_existing_archive(final_path)
    columns_sql = ", ".join(COLUMNS)
    export_query = f"SELECT {columns_sql} FROM {TABLE_NAME} WHERE {filter_sql} ORDER BY collected_at ASC, id ASC"
    database_rows = []

    try:
        with connection.cursor(name=f"archive_cursor_{day.strftime('%Y%m%d')}") as cursor:
            cursor.execute(export_query, filter_params)
            while True:
                rows = cursor.fetchmany(batch_size)
                if not rows:
                    break
                for values in rows:
                    database_rows.append({
                        column: ("" if value is None else str(csv_value(value)))
                        for column, value in zip(COLUMNS, values)
                    })

        if not database_rows and not existing_rows:
            return None
        if not database_rows:
            print(f"UNCHANGED: {day} has no newly eligible rows; existing archive preserved.")
            return None

        merged_rows = merge_rows_by_id(existing_rows, database_rows)
        merged_rows.sort(key=lambda row: (
            datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00")),
            row["id"],
        ))

        with gzip.open(temp_path, mode="wt", encoding="utf-8", newline="") as compressed_file:
            writer = csv.writer(compressed_file, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(COLUMNS)
            for row in merged_rows:
                writer.writerow([row[column] for column in COLUMNS])

        # Re-open the completed temporary archive before publishing it.
        verified_count = 0
        with gzip.open(temp_path, mode="rt", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != COLUMNS:
                raise RuntimeError(f"Generated archive schema mismatch for {day}")
            for row in reader:
                if not row.get("id") or datetime.fromisoformat(
                    row["collected_at"].replace("Z", "+00:00")
                ).astimezone(timezone.utc).date() != day:
                    raise RuntimeError(f"Generated archive contains invalid partition data for {day}")
                verified_count += 1
        if verified_count != len(merged_rows):
            raise RuntimeError(
                f"Archive row-count mismatch for {day}: expected={len(merged_rows)}, actual={verified_count}"
            )
        os.replace(temp_path, final_path)
    except Exception:
        if temp_path.exists():
            temp_path.unlink()
        raise

    timestamps = [
        datetime.fromisoformat(row["collected_at"].replace("Z", "+00:00"))
        for row in merged_rows
    ]
    min_time = min(timestamps)
    max_time = max(timestamps)
    file_size = final_path.stat().st_size
    checksum = calculate_sha256(final_path)
    print(
        f"ARCHIVED {day}: {len(database_rows):,} newly eligible + "
        f"{len(existing_rows):,} previously archived -> {len(merged_rows):,} total rows | "
        f"{file_size:,} bytes"
    )

    return {
        "source_table": TABLE_NAME,
        "archive_mode": mode,
        "partition_date_utc": day.isoformat(),
        "row_count": len(merged_rows),
        "min_collected_at_utc": min_time.isoformat(),
        "max_collected_at_utc": max_time.isoformat(),
        "file_name": output_name,
        "file_size_bytes": file_size,
        "sha256": checksum,
        "exported_at_utc": datetime.now(timezone.utc).isoformat(),
        "cutoff_utc": cutoff.isoformat() if cutoff else "",
    }

def write_manifest(output_directory, manifest_rows):
    if not manifest_rows:
        return

    manifest_path = output_directory / "archive_manifest.csv"
    existing = []
    if manifest_path.exists():
        with open(manifest_path, mode="r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            if reader.fieldnames != MANIFEST_COLUMNS:
                raise RuntimeError("Existing archive manifest schema mismatch.")
            existing = list(reader)

    for update in manifest_rows:
        key = (update["source_table"], update["partition_date_utc"], update["file_name"])
        matches = [
            row for row in existing
            if (row.get("source_table"), row.get("partition_date_utc"), row.get("file_name")) == key
        ]
        if len(matches) > 1:
            raise RuntimeError(f"Duplicate manifest entries for {key}; refusing update.")
        if matches:
            existing[existing.index(matches[0])] = update
        else:
            existing.append(update)

    temp_path = manifest_path.with_suffix(".csv.tmp")
    with open(temp_path, mode="w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=MANIFEST_COLUMNS)
        writer.writeheader()
        writer.writerows(existing)
    os.replace(temp_path, manifest_path)
    print(f"Manifest atomically updated: {manifest_path}")

def main():
    args = parse_arguments()
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        print("ERROR: DATABASE_URL missing."); sys.exit(1)

    output_directory = Path(args.output).resolve()
    output_directory.mkdir(parents=True, exist_ok=True)
    now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(days=args.older_than_days)) if not args.test_date else None

    print("BTC OPTIONS ARCHIVE PIPELINE")
    print(f"Output: {output_directory}")
    print(f"Started UTC: {now_utc.isoformat()}")

    manifest_rows = []
    try:
        with psycopg.connect(database_url, connect_timeout=20, application_name="btc_options_archive_readonly") as connection:
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            print("Database connection successful. (READ ONLY)")

            days, mode = discover_dates(connection, args.test_date, cutoff)
            if not days:
                print("No eligible records found."); return

            print(f"UTC date partitions found: {len(days)}")

            for day in days:
                result = export_one_day(connection, day, output_directory, mode, cutoff, args.batch_size)
                if result: manifest_rows.append(result)

            write_manifest(output_directory, manifest_rows)
            print("\nARCHIVE RUN COMPLETE")

    except Exception as error:
        print(f"\nARCHIVE RUN FAILED\n{type(error).__name__}: {error}")
        sys.exit(1)

if __name__ == "__main__":
    main()