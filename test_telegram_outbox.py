"""Regression tests for durable Telegram outbox ordering and state transitions."""
import os
import sys
import types

os.environ.setdefault("SUPABASE_URL", "https://unit.test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "unit-test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unit-test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "unit-test-chat")

fake_supabase = types.ModuleType("supabase")
fake_supabase.create_client = lambda *_args, **_kwargs: object()
sys.modules.setdefault("supabase", fake_supabase)

import telegram_alerts as notifier


class Result:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, table, store):
        self.table_name = table
        self.store = store
        self.operation = None
        self.record = None
        self.key = None

    def select(self, *_args, **_kwargs):
        self.operation = "select"
        return self

    def upsert(self, record, **_kwargs):
        self.operation = "upsert"
        self.record = record
        return self

    def update(self, record):
        self.operation = "update"
        self.record = record
        return self

    def eq(self, column, value):
        assert column == "event_key"
        self.key = value
        return self

    def limit(self, *_args, **_kwargs):
        return self

    def execute(self):
        if self.operation == "select":
            row = self.store.get(self.key)
            return Result([dict(row)] if row else [])
        if self.operation == "upsert":
            self.store[self.record["event_key"]] = dict(self.record)
            return Result([dict(self.record)])
        if self.operation == "update":
            assert self.key in self.store
            self.store[self.key].update(self.record)
            return Result([dict(self.store[self.key])])
        raise AssertionError("unexpected query operation")


class FakeSupabase:
    def __init__(self):
        self.rows = {}
        self.calls = []

    def table(self, name):
        assert name == "telegram_alert_events"
        return FakeQuery(name, self.rows)


def alert():
    return {
        "key": "AMT_STATE_CHANGE_test",
        "type": "AMT_STATE_CHANGE",
        "time": "2026-11-17T00:00:00Z",
        "payload": {"state": "DISCOVERY BELOW"},
        "msg": "test alert",
    }


def test_pending_row_is_written_before_send_and_finalized_sent(monkeypatch):
    db = FakeSupabase()
    events = []
    monkeypatch.setattr(notifier, "supabase", db)
    monkeypatch.setattr(notifier, "send_telegram", lambda _msg: events.append("send") or (True, 987))

    item = alert()
    assert notifier.begin_event_delivery(item) is True
    assert db.rows[item["key"]]["delivery_status"] == "PENDING"
    events.append("pending_written")

    success, message_id = notifier.send_telegram(item["msg"])
    assert success is True
    notifier.finish_event_delivery(item, "SENT", message_id=message_id)
    events.append("sent_finalized")

    assert events == ["pending_written", "send", "sent_finalized"]
    assert db.rows[item["key"]]["delivery_status"] == "SENT"
    assert db.rows[item["key"]]["telegram_message_id"] == 987
    assert db.rows[item["key"]]["attempt_count"] == 1


def test_failed_delivery_is_finalized_and_retry_increments_attempt(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(notifier, "supabase", db)
    item = alert()

    assert notifier.begin_event_delivery(item) is True
    notifier.finish_event_delivery(item, "FAILED", error="Telegram unavailable")
    assert db.rows[item["key"]]["delivery_status"] == "FAILED"
    assert db.rows[item["key"]]["last_error"] == "Telegram unavailable"

    assert notifier.begin_event_delivery(item) is True
    assert db.rows[item["key"]]["delivery_status"] == "PENDING"
    assert db.rows[item["key"]]["attempt_count"] == 2


def test_confirmed_sent_event_is_not_enqueued_again(monkeypatch):
    db = FakeSupabase()
    monkeypatch.setattr(notifier, "supabase", db)
    item = alert()

    assert notifier.begin_event_delivery(item) is True
    notifier.finish_event_delivery(item, "SENT", message_id=987)
    assert notifier.begin_event_delivery(item) is False
    assert db.rows[item["key"]]["attempt_count"] == 1


def test_initial_outbox_write_failure_prevents_telegram_send(monkeypatch):
    class BrokenSupabase:
        def table(self, _name):
            raise RuntimeError("database unavailable")

    sent = []
    monkeypatch.setattr(notifier, "supabase", BrokenSupabase())
    monkeypatch.setattr(notifier, "evaluate_amt_transition", lambda: alert())
    monkeypatch.setattr(notifier, "evaluate_gex_breach", lambda: [])
    monkeypatch.setattr(notifier, "evaluate_oi_shock", lambda: None)
    monkeypatch.setattr(notifier, "send_telegram", lambda msg: sent.append(msg) or (True, 987))
    monkeypatch.setenv("TEST_MODE", "false")

    try:
        notifier.main()
        assert False, "main should abort if PENDING cannot be persisted"
    except SystemExit as exc:
        assert exc.code == 1
    assert sent == []
