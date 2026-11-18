
#!/usr/bin/env python3
"""
Safely retain only the newest N days of raw options snapshots.

Deletion is restricted to public.options_oi_snapshots.

A UTC date partition is eligible for deletion only after its archive passes:
  1. Exact manifest match (source table, partition date, filename)
  2. Exactly one matching manifest row
  3. File-size verification
  4. SHA-256 verification
  5. Gzip and CSV readability verification
  6. CSV row-count verification
  7. CSV collected_at min/max verification against the manifest
  8. Exact snapshot-ID coverage for every current database deletion candidate

All eligible UTC partitions are preflighted before the first deletion.

Default mode is DRY RUN. Use --execute to enable deletion.

Required environment variables:
  SUPABASE_URL
  SUPABASE_SERVICE_ROLE_KEY

Optional environment variables:
  ARCHIVE_DIR       Default: btc_data_archive
  RETENTION_DAYS    Default: 3
  PAGE_SIZE         Default: 1000
"""

from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import logging
import os
import sys

from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any

from supabase import create_client


# ---------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------

SOURCE_TABLE = "public.options_oi_snapshots"
TABLE_NAME = "options_oi_snapshots"

MANIFEST_NAME = "archive_manifest.csv"
ARCHIVE_PREFIX = "options_oi_"
ARCHIVE_SUFFIX = ".csv.gz"

MANIFEST_COLUMNS = [
    "source_table",
    "archive_mode",
    "partition_date_utc",
    "row_count",
    "min_collected_at_utc",
    "max_collected_at_utc",
    "file_name",
    "file_size_bytes",
    "sha256",
    "exported_at_utc",
    "cutoff_utc",
]

PAGE_SIZE = int(os.getenv("PAGE_SIZE", "1000"))
DEFAULT_RETENTION_DAYS = int(os.getenv("RETENTION_DAYS", "3"))
DEFAULT_ARCHIVE_DIR = os.getenv("ARCHIVE_DIR", "btc_data_archive")

LOG_FORMAT = "%(asctime)s UTC | %(levelname)s | %(message)s"


# ---------------------------------------------------------------------
# Logging and timestamp helpers
# ---------------------------------------------------------------------

logging.Formatter.converter = __import__("time").gmtime

logging.basicConfig(
    level=logging.INFO,
    format=LOG_FORMAT,
    datefmt="%Y-%m-%d %H:%M:%S",
)

log = logging.getLogger("options_retention")


def parse_utc(value: str) -> datetime:
    """Parse an ISO-8601 timestamp and normalize it to UTC."""
    if not value:
        raise ValueError("Empty timestamp")

    normalized = value.strip().replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)

    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp has no timezone: {value}")

    return parsed.astimezone(timezone.utc)


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def utc_day_bounds(day: date) -> tuple[datetime, datetime]:
    start = datetime.combine(day, time.min, tzinfo=timezone.utc)
    end = start + timedelta(days=1)
    return start, end


def date_from_timestamp(value: str) -> date:
    return parse_utc(value).date()


# ---------------------------------------------------------------------
# Supabase helpers
# ---------------------------------------------------------------------

def get_supabase_client():
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")

    if not url or not key:
        raise RuntimeError(
            "Missing SUPABASE_URL or SUPABASE_SERVICE_ROLE_KEY environment variable."
        )

    return create_client(url, key)


def count_old_rows_for_day(
    client: Any,
    day: date,
    cutoff: datetime,
) -> int:
    """
    Count rows in one UTC date partition that are strictly older
    than the exact retention cutoff.
    """
    start, end = utc_day_bounds(day)

    response = (
        client.table(TABLE_NAME)
        .select("collected_at", count="exact", head=True)
        .gte("collected_at", iso_utc(start))
        .lt("collected_at", iso_utc(end))
        .lt("collected_at", iso_utc(cutoff))
        .execute()
    )

    if response.count is None:
        raise RuntimeError(f"Supabase did not return an exact count for {day}.")

    return int(response.count)


def discover_eligible_dates(
    client: Any,
    cutoff: datetime,
) -> set[date]:
    """
    Paginate through all rows older than the exact cutoff and collect
    their UTC partition dates.

    This does not assume that the database's default response limit
    contains all eligible rows.
    """
    discovered: set[date] = set()
    offset = 0
    scanned = 0

    while True:
        response = (
            client.table(TABLE_NAME)
            .select("collected_at")
            .lt("collected_at", iso_utc(cutoff))
            .order("collected_at")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )

        rows = response.data or []

        if not rows:
            break

        for row in rows:
            timestamp = row.get("collected_at")

            if not timestamp:
                raise RuntimeError(
                    "Encountered an eligible database row with empty collected_at. "
                    "Stopping without deletion."
                )

            discovered.add(date_from_timestamp(timestamp))

        scanned += len(rows)
        offset += len(rows)

        if len(rows) < PAGE_SIZE:
            break

    log.info("Scanned %s old timestamp records", scanned)
    return discovered


# ---------------------------------------------------------------------
# Manifest and archive verification
# ---------------------------------------------------------------------

def load_manifest(manifest_path: Path) -> list[dict[str, str]]:
    if not manifest_path.is_file():
        raise FileNotFoundError(f"Manifest not found: {manifest_path}")

    with manifest_path.open(
        "r",
        newline="",
        encoding="utf-8-sig",
    ) as handle:
        reader = csv.DictReader(handle)

        if reader.fieldnames != MANIFEST_COLUMNS:
            raise ValueError(
                "Manifest header does not match the expected schema.\n"
                f"Expected: {MANIFEST_COLUMNS}\n"
                f"Found:    {reader.fieldnames}"
            )

        return list(reader)


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()

    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)

    return digest.hexdigest()


def verify_archive(
    archive_dir: Path,
    manifest_rows: list[dict[str, str]],
    day: date,
) -> tuple[bool, str]:
    """
    Verify one archive against exactly one matching manifest entry.

    Returns (True, explanation) only if every check passes.
    Any ambiguity or mismatch returns (False, reason).
    """
    expected_file = f"{ARCHIVE_PREFIX}{day.isoformat()}{ARCHIVE_SUFFIX}"

    matching_rows = [
        row
        for row in manifest_rows
        if row.get("source_table") == SOURCE_TABLE
        and row.get("partition_date_utc") == day.isoformat()
        and row.get("file_name") == expected_file
    ]

    if len(matching_rows) != 1:
        return (
            False,
            f"Expected exactly one matching manifest row; found {len(matching_rows)}.",
        )

    manifest = matching_rows[0]

    # Prevent a manifest filename from escaping the archive directory.
    if Path(manifest["file_name"]).name != manifest["file_name"]:
        return False, "Manifest filename contains an unsafe path."

    archive_path = archive_dir / expected_file

    if not archive_path.is_file():
        return False, f"Archive file is missing: {archive_path}"

    actual_size = archive_path.stat().st_size

    try:
        expected_size = int(manifest["file_size_bytes"])
        expected_rows = int(manifest["row_count"])
    except (TypeError, ValueError):
        return False, "Manifest row_count or file_size_bytes is invalid."

    if expected_size <= 0 or expected_rows < 0:
        return False, "Manifest contains an invalid size or row count."

    if actual_size != expected_size:
        return (
            False,
            f"File-size mismatch: manifest={expected_size}, actual={actual_size}.",
        )

    expected_hash = manifest["sha256"].strip().lower()

    if len(expected_hash) != 64 or any(
        char not in "0123456789abcdef" for char in expected_hash
    ):
        return False, "Manifest SHA-256 value is not a valid 64-character hex digest."

    actual_hash = sha256_file(archive_path)

    if actual_hash != expected_hash:
        return (
            False,
            f"SHA-256 mismatch: manifest={expected_hash}, actual={actual_hash}.",
        )

    # Verify the compressed CSV and its contents.
    actual_count = 0
    actual_min: datetime | None = None
    actual_max: datetime | None = None

    try:
        with gzip.open(
            archive_path,
            mode="rt",
            encoding="utf-8-sig",
            newline="",
        ) as handle:
            reader = csv.DictReader(handle)

            if not reader.fieldnames or "collected_at" not in reader.fieldnames:
                return False, "Archive CSV is missing the collected_at column."

            for row in reader:
                timestamp_text = row.get("collected_at")

                if not timestamp_text:
                    return False, "Archive contains a row with empty collected_at."

                timestamp = parse_utc(timestamp_text)

                if timestamp.date() != day:
                    return (
                        False,
                        f"Archive contains a timestamp outside partition {day}: "
                        f"{timestamp_text}",
                    )

                actual_count += 1

                if actual_min is None or timestamp < actual_min:
                    actual_min = timestamp

                if actual_max is None or timestamp > actual_max:
                    actual_max = timestamp

    except Exception as exc:
        return False, f"Archive gzip/CSV validation failed: {exc}"

    if actual_count != expected_rows:
        return (
            False,
            f"Row-count mismatch: manifest={expected_rows}, actual={actual_count}.",
        )

    if actual_count == 0:
        return False, "Archive contains zero rows; refusing deletion."

    try:
        manifest_min = parse_utc(manifest["min_collected_at_utc"])
        manifest_max = parse_utc(manifest["max_collected_at_utc"])
    except Exception as exc:
        return False, f"Manifest min/max timestamps are invalid: {exc}"

    if actual_min != manifest_min:
        return (
            False,
            f"Minimum timestamp mismatch: manifest={iso_utc(manifest_min)}, "
            f"archive={iso_utc(actual_min)}.",
        )

    if actual_max != manifest_max:
        return (
            False,
            f"Maximum timestamp mismatch: manifest={iso_utc(manifest_max)}, "
            f"archive={iso_utc(actual_max)}.",
        )

    return (
        True,
        f"Verified {expected_file}: {actual_count} rows, "
        f"{actual_size} bytes, SHA-256 matched.",
    )


# ---------------------------------------------------------------------
# Deletion
# ---------------------------------------------------------------------


def fetch_eligible_row_ids(client: Any, day: date, cutoff: datetime) -> set[str]:
    """Fetch every exact deletion-candidate ID for one UTC partition."""
    start, end = utc_day_bounds(day)
    ids: set[str] = set()
    offset = 0
    while True:
        response = (
            client.table(TABLE_NAME)
            .select("id,collected_at")
            .gte("collected_at", iso_utc(start))
            .lt("collected_at", iso_utc(end))
            .lt("collected_at", iso_utc(cutoff))
            .order("collected_at")
            .order("id")
            .range(offset, offset + PAGE_SIZE - 1)
            .execute()
        )
        rows = response.data or []
        for row in rows:
            row_id = row.get("id")
            if row_id is None:
                raise RuntimeError(f"Deletion candidate in {day} has no id.")
            ids.add(str(row_id))
        offset += len(rows)
        if len(rows) < PAGE_SIZE:
            break
    return ids


def read_archive_ids(archive_dir: Path, day: date) -> set[str]:
    """Read unique snapshot IDs from the checksum-verified daily archive."""
    archive_path = archive_dir / f"{ARCHIVE_PREFIX}{day.isoformat()}{ARCHIVE_SUFFIX}"
    ids: set[str] = set()
    with gzip.open(archive_path, mode="rt", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or "id" not in reader.fieldnames:
            raise RuntimeError(f"Archive {archive_path.name} is missing id column.")
        for row in reader:
            row_id = row.get("id")
            if not row_id:
                raise RuntimeError(f"Archive {archive_path.name} contains a row without id.")
            if row_id in ids:
                raise RuntimeError(f"Archive {archive_path.name} contains duplicate id {row_id}.")
            ids.add(row_id)
    return ids


def verify_archive_covers_deletion_candidates(
    archive_dir: Path, day: date, eligible_ids: set[str]
) -> tuple[bool, str]:
    """Require exact ID coverage of all rows currently eligible for deletion."""
    archived_ids = read_archive_ids(archive_dir, day)
    missing_ids = eligible_ids - archived_ids
    if missing_ids:
        sample = ", ".join(sorted(missing_ids)[:5])
        return False, (
            f"Archive incomplete for {day}: {len(missing_ids)} eligible database "
            f"row(s) absent from archive (sample IDs: {sample})."
        )
    return True, (
        f"Archive covers all {len(eligible_ids)} eligible database rows; "
        f"{len(archived_ids)} total archived rows retained."
    )

def delete_old_rows_for_day(
    client: Any,
    day: date,
    cutoff: datetime,
    expected_count: int,
    expected_ids: set[str],
) -> int:
    """
    Delete only rows in the verified UTC partition whose collected_at
    is strictly older than the exact cutoff.

    The returned PostgREST count must match the pre-delete count.
    """
    if expected_count <= 0:
        return 0
    if len(expected_ids) != expected_count:
        raise RuntimeError(
            f"Verified ID/count mismatch for {day}: "
            f"IDs={len(expected_ids)}, expected count={expected_count}."
        )

    start, end = utc_day_bounds(day)

    response = (
        client.table(TABLE_NAME)
        .delete(count="exact")
        .gte("collected_at", iso_utc(start))
        .lt("collected_at", iso_utc(end))
        .lt("collected_at", iso_utc(cutoff))
        .execute()
    )

    deleted_count = response.count

    if deleted_count is None:
        raise RuntimeError(
            f"Supabase did not return an exact deletion count for {day}. "
            "Stop and inspect the database before rerunning."
        )

    deleted_count = int(deleted_count)

    if deleted_count != expected_count:
        raise RuntimeError(
            f"Deletion count mismatch for {day}: "
            f"pre-delete count={expected_count}, deleted={deleted_count}. "
            "Stop and inspect the database."
        )

    return deleted_count


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify raw-options archives and apply three-day retention."
    )

    parser.add_argument(
        "--execute",
        action="store_true",
        help="Actually delete eligible rows. Without this flag, run dry-run only.",
    )

    parser.add_argument(
        "--retention-days",
        type=int,
        default=DEFAULT_RETENTION_DAYS,
        help="Retention window in days. Default comes from RETENTION_DAYS or 3.",
    )

    parser.add_argument(
        "--archive-dir",
        default=DEFAULT_ARCHIVE_DIR,
        help="Directory containing archive_manifest.csv and archive files.",
    )

    args = parser.parse_args()

    if args.retention_days < 0:
        parser.error("--retention-days cannot be negative.")

    if PAGE_SIZE < 1:
        parser.error("PAGE_SIZE must be at least 1.")

    archive_dir = Path(args.archive_dir).resolve()
    manifest_path = archive_dir / MANIFEST_NAME

    mode = "EXECUTE" if args.execute else "DRY RUN"

    # Freeze one cutoff for the entire run. Do not recalculate it per date.
    run_started = datetime.now(timezone.utc)
    cutoff = run_started - timedelta(days=args.retention_days)

    log.info("Mode: %s", mode)
    log.info("Target table: %s", SOURCE_TABLE)
    log.info("Retention: %s days", args.retention_days)
    log.info("Retention cutoff: %s", iso_utc(cutoff))
    log.info("Archive directory: %s", archive_dir)

    if not archive_dir.is_dir():
        log.error("Archive directory does not exist: %s", archive_dir)
        return 1

    try:
        manifest_rows = load_manifest(manifest_path)
    except Exception as exc:
        log.error("Cannot load or validate archive manifest: %s", exc)
        return 1

    try:
        client = get_supabase_client()
        eligible_dates = discover_eligible_dates(client, cutoff)
    except Exception as exc:
        log.error("Database connection or eligible-date scan failed: %s", exc)
        return 1

    log.info("Eligible UTC dates discovered: %s", len(eligible_dates))

    total_eligible = 0
    total_deleted = 0
    verified_dates = 0
    skipped_dates = 0
    deletion_plan: list[tuple[date, int, set[str]]] = []

    # Phase 1: preflight EVERY eligible date before deleting anything.
    # This prevents an incomplete later partition from causing partial retention.
    for day in sorted(eligible_dates):
        log.info("--------------------------------------------------")
        log.info("Preflight UTC partition: %s", day)

        verified, message = verify_archive(
            archive_dir=archive_dir,
            manifest_rows=manifest_rows,
            day=day,
        )
        if not verified:
            log.error("ARCHIVE VERIFICATION FAILED for %s: %s", day, message)
            log.error("Preflight failed. No database rows will be deleted in this run.")
            return 1

        verified_dates += 1
        log.info("ARCHIVE VERIFIED: %s", message)

        try:
            eligible_ids = fetch_eligible_row_ids(client, day, cutoff)
            eligible_count = count_old_rows_for_day(client, day, cutoff)
            if eligible_count != len(eligible_ids):
                raise RuntimeError(
                    f"Exact count/ID mismatch for {day}: count={eligible_count}, "
                    f"unique IDs={len(eligible_ids)}."
                )
            covered, coverage_message = verify_archive_covers_deletion_candidates(
                archive_dir, day, eligible_ids
            )
            if not covered:
                raise RuntimeError(coverage_message)
            log.info("ARCHIVE COVERAGE VERIFIED: %s", coverage_message)
        except Exception as exc:
            log.error(
                "ARCHIVE/DB RECONCILIATION FAILED for %s: %s. "
                "Preflight failed; no database rows will be deleted in this run.",
                day, exc
            )
            return 1

        if eligible_count == 0:
            log.info("No rows older than the exact cutoff in %s.", day)
            continue

        total_eligible += eligible_count
        deletion_plan.append((day, eligible_count, eligible_ids))
        log.info("Rows eligible for %s: %s", day, eligible_count)

    if not args.execute:
        log.info("DRY RUN: all %s eligible partition(s) passed archive preflight.",
                 len(deletion_plan))
    else:
        # Phase 2: only execute after every eligible date has passed preflight.
        for day, eligible_count, eligible_ids in deletion_plan:
            try:
                deleted = delete_old_rows_for_day(
                    client=client,
                    day=day,
                    cutoff=cutoff,
                    expected_count=eligible_count,
                    expected_ids=eligible_ids,
                )
            except Exception as exc:
                log.error(
                    "Deletion failed or count could not be confirmed for %s: %s",
                    day, exc
                )
                log.error("Stop and inspect the database before rerunning.")
                return 1

            total_deleted += deleted
            log.info("Rows deleted for %s: %s", day, deleted)

    log.info("==================================================")
    log.info("RETENTION RUN SUMMARY")
    log.info("Mode: %s", mode)
    log.info("Retention cutoff: %s", iso_utc(cutoff))
    log.info("Eligible dates: %s", len(eligible_dates))
    log.info("Archive-verified dates: %s", verified_dates)
    log.info("Skipped dates: %s", skipped_dates)
    log.info("Rows eligible: %s", total_eligible)
    log.info("Rows deleted: %s", total_deleted)

    if args.execute:
        log.info("EXECUTION COMPLETE.")
    else:
        log.info("DRY RUN COMPLETE: database was not modified.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
