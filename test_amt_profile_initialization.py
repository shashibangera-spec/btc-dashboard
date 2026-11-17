"""Regression tests for AMT profile initialization notifications."""
import os
import sys
import types

os.environ.setdefault("SUPABASE_URL", "https://unit.test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "unit-test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unit-test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "unit-test-chat")

class FakeClient:
    def table(self, *_args, **_kwargs):
        raise AssertionError("Unexpected database access")

fake_supabase = types.ModuleType("supabase")
fake_supabase.create_client = lambda *_args, **_kwargs: FakeClient()
sys.modules.setdefault("supabase", fake_supabase)

import telegram_alerts as notifier


class Query:
    def __init__(self, rows):
        self.rows = rows
    def select(self, *_args, **_kwargs): return self
    def eq(self, *_args, **_kwargs): return self
    def order(self, *_args, **_kwargs): return self
    def limit(self, *_args, **_kwargs): return self
    def execute(self): return types.SimpleNamespace(data=self.rows)


def candle(profile, state, close_time, close=65000):
    return {
        "profile_id": profile,
        "confirmed_state": state,
        "candle_open_time": close_time.replace(":59:59.999Z", ":30:00.000Z"),
        "candle_close_time": close_time,
        "close_price": close,
        "data_status": "VALID",
    }


def run_with_rows(monkeypatch, rows):
    class Client:
        def table(self, name):
            assert name == "amt_snapshots"
            return Query(rows)
    monkeypatch.setattr(notifier, "supabase", Client())
    monkeypatch.setattr(notifier, "check_event_exists", lambda _key: False)
    return notifier.evaluate_amt_transition()


def test_profile_boundary_emits_distinct_initialization_event(monkeypatch):
    current = candle("P_2026-11-16", "IN_VALUE", "2026-11-16T00:29:59.999Z")
    previous = candle("P_2026-11-15", "REENTRY_CONFIRMED", "2026-11-15T23:59:59.999Z")
    alert = run_with_rows(monkeypatch, [current, previous])
    assert alert["type"] == "AMT_PROFILE_INITIALIZATION"
    assert alert["key"] == "AMT_PROFILE_INITIALIZATION_P_2026-11-16_2026-11-16T00:29:59.999Z"
    assert "NEW PROFILE / STATE INITIALIZATION" in alert["msg"]
    assert "Initial confirmed state: IN_VALUE" in alert["msg"]
    assert alert["payload"]["previous_profile_id"] == "P_2026-11-15"
    assert alert["payload"]["profile_id"] == "P_2026-11-16"


def test_same_profile_state_change_remains_intraday_alert(monkeypatch):
    current = candle("P_2026-11-16", "DISCOVERY_CONFIRMED_BELOW", "2026-11-16T01:29:59.999Z")
    previous = candle("P_2026-11-16", "IN_VALUE", "2026-11-16T00:59:59.999Z")
    alert = run_with_rows(monkeypatch, [current, previous])
    assert alert["type"] == "AMT_STATE_CHANGE"
    assert alert["key"] == "AMT_STATE_CHANGE_2026-11-16T01:29:59.999Z"
    assert "IN_VALUE → DISCOVERY_CONFIRMED_BELOW" in alert["msg"]


def test_same_profile_same_state_emits_nothing(monkeypatch):
    current = candle("P_2026-11-16", "IN_VALUE", "2026-11-16T01:29:59.999Z")
    previous = candle("P_2026-11-16", "IN_VALUE", "2026-11-16T00:59:59.999Z")
    assert run_with_rows(monkeypatch, [current, previous]) is None


def test_missing_profile_identifier_fails_closed(monkeypatch, capsys):
    current = candle(None, "IN_VALUE", "2026-11-16T00:29:59.999Z")
    previous = candle("P_2026-11-15", "REENTRY_CONFIRMED", "2026-11-15T23:59:59.999Z")
    assert run_with_rows(monkeypatch, [current, previous]) is None
    assert "profile_id is missing" in capsys.readouterr().out


def test_existing_initialization_delivery_suppresses_duplicate(monkeypatch):
    current = candle("P_2026-11-16", "IN_VALUE", "2026-11-16T00:29:59.999Z")
    previous = candle("P_2026-11-15", "REENTRY_CONFIRMED", "2026-11-15T23:59:59.999Z")
    class Client:
        def table(self, name):
            assert name == "amt_snapshots"
            return Query([current, previous])
    monkeypatch.setattr(notifier, "supabase", Client())
    monkeypatch.setattr(notifier, "check_event_exists", lambda key: key.startswith("AMT_PROFILE_INITIALIZATION_"))
    assert notifier.evaluate_amt_transition() is None
