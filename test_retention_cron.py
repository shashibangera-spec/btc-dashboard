from datetime import date, datetime, timezone
from types import SimpleNamespace

import pytest

import retention_cron


class FakeDeleteQuery:
    def __init__(self, client):
        self.client = client
        self.ids = None
        self.filters = []

    def delete(self, count=None):
        self.count_mode = count
        return self

    def in_(self, column, values):
        assert column == "id"
        self.ids = list(values)
        return self

    def gte(self, column, value):
        self.filters.append(("gte", column, value))
        return self

    def lt(self, column, value):
        self.filters.append(("lt", column, value))
        return self

    def execute(self):
        self.client.calls.append(self)
        if self.client.mismatch_call == len(self.client.calls):
            return SimpleNamespace(count=len(self.ids) - 1)
        return SimpleNamespace(count=len(self.ids))


class FakeClient:
    def __init__(self, mismatch_call=None):
        self.calls = []
        self.mismatch_call = mismatch_call

    def table(self, name):
        assert name == retention_cron.TABLE_NAME
        return FakeDeleteQuery(self)


def test_delete_uses_bounded_id_batches_and_checks_each_count(monkeypatch):
    monkeypatch.setattr(retention_cron, "DELETE_BATCH_SIZE", 2)
    client = FakeClient()
    ids = {f"id-{i:03d}" for i in range(5)}
    cutoff = datetime(2026, 11, 20, tzinfo=timezone.utc)

    deleted = retention_cron.delete_old_rows_for_day(
        client=client,
        day=date(2026, 11, 15),
        cutoff=cutoff,
        expected_count=5,
        expected_ids=ids,
    )

    assert deleted == 5
    assert [len(call.ids) for call in client.calls] == [2, 2, 1]
    assert [call.ids for call in client.calls] == [
        ["id-000", "id-001"],
        ["id-002", "id-003"],
        ["id-004"],
    ]
    for call in client.calls:
        assert ("gte", "collected_at", "2026-11-15T00:00:00+00:00") in call.filters
        assert ("lt", "collected_at", "2026-11-16T00:00:00+00:00") in call.filters
        assert ("lt", "collected_at", cutoff.isoformat()) in call.filters


def test_delete_stops_immediately_when_a_batch_count_does_not_match(monkeypatch):
    monkeypatch.setattr(retention_cron, "DELETE_BATCH_SIZE", 2)
    client = FakeClient(mismatch_call=2)
    ids = {f"id-{i:03d}" for i in range(5)}

    with pytest.raises(RuntimeError, match="batch 2"):
        retention_cron.delete_old_rows_for_day(
            client=client,
            day=date(2026, 11, 15),
            cutoff=datetime(2026, 11, 20, tzinfo=timezone.utc),
            expected_count=5,
            expected_ids=ids,
        )

    assert len(client.calls) == 2


def test_delete_rejects_preflight_id_count_mismatch():
    client = FakeClient()

    with pytest.raises(RuntimeError, match="Verified ID/count mismatch"):
        retention_cron.delete_old_rows_for_day(
            client=client,
            day=date(2026, 11, 15),
            cutoff=datetime(2026, 11, 20, tzinfo=timezone.utc),
            expected_count=2,
            expected_ids={"only-one"},
        )

    assert client.calls == []


def test_delete_zero_rows_does_not_issue_a_request():
    client = FakeClient()

    deleted = retention_cron.delete_old_rows_for_day(
        client=client,
        day=date(2026, 11, 15),
        cutoff=datetime(2026, 11, 20, tzinfo=timezone.utc),
        expected_count=0,
        expected_ids=set(),
    )

    assert deleted == 0
    assert client.calls == []
