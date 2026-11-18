"""Regression tests for rolling options archive completeness and retention guards."""
import csv
import gzip
from datetime import date
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

from archive_options_snapshots import COLUMNS, merge_rows_by_id, read_existing_archive
from retention_cron import (
    DEFAULT_RETENTION_DAYS,
    verify_archive_covers_deletion_candidates,
)


def make_row(row_id, timestamp="2026-11-10T12:00:00+00:00"):
    row = {column: "" for column in COLUMNS}
    row.update({"id": str(row_id), "collected_at": timestamp, "exchange": "DELTA"})
    return row


def write_archive(path: Path, rows):
    with gzip.open(path, "wt", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)


def test_partial_archive_is_merged_with_newly_eligible_rows():
    old = make_row("old")
    new = make_row("new", "2026-11-10T13:00:00+00:00")

    merged = merge_rows_by_id([old], [new])

    assert {row["id"] for row in merged} == {"old", "new"}
    assert len(merged) == 2


def test_repeated_snapshot_id_is_deduplicated_when_contents_match():
    row = make_row("same")

    merged = merge_rows_by_id([row], [dict(row)])

    assert len(merged) == 1
    assert merged[0]["id"] == "same"


def test_conflicting_snapshot_id_fails_closed():
    archived = make_row("same")
    changed = make_row("same", "2026-11-10T13:00:00+00:00")

    with pytest.raises(RuntimeError, match="Conflicting contents"):
        merge_rows_by_id([archived], [changed])


def test_existing_partial_archive_can_be_read_for_reconciliation(tmp_path):
    archive = tmp_path / "options_oi_2026-11-10.csv.gz"
    write_archive(archive, [make_row("one")])

    assert [row["id"] for row in read_existing_archive(archive)] == ["one"]


def test_retention_requires_all_current_database_candidates_in_archive(tmp_path):
    archive = tmp_path / "options_oi_2026-11-10.csv.gz"
    write_archive(archive, [make_row("old"), make_row("candidate-a"), make_row("candidate-b")])

    ok, message = verify_archive_covers_deletion_candidates(
        tmp_path, date(2026, 11, 10), {"candidate-a", "candidate-b"}
    )

    assert ok is True
    assert "2 eligible database rows" in message


def test_retention_rejects_archive_missing_any_database_candidate(tmp_path):
    archive = tmp_path / "options_oi_2026-11-10.csv.gz"
    write_archive(archive, [make_row("candidate-a")])

    ok, message = verify_archive_covers_deletion_candidates(
        tmp_path, date(2026, 11, 10), {"candidate-a", "candidate-b"}
    )

    assert ok is False
    assert "1 eligible database row(s) absent" in message


def test_retention_rejects_duplicate_ids_in_archive(tmp_path):
    archive = tmp_path / "options_oi_2026-11-10.csv.gz"
    write_archive(archive, [make_row("duplicate"), make_row("duplicate")])

    with pytest.raises(RuntimeError, match="duplicate id"):
        verify_archive_covers_deletion_candidates(
            tmp_path, date(2026, 11, 10), {"duplicate"}
        )


def test_default_retention_is_three_days():
    assert DEFAULT_RETENTION_DAYS == 3
