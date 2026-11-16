"""Offline regression tests for journal-driven GEX Telegram notifications."""
import importlib
import json
import os
import sys
import types

# Import the production module without requiring live credentials or network access.
os.environ.setdefault("SUPABASE_URL", "https://unit.test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "unit-test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unit-test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "unit-test-chat")

class _FakeSupabaseClient:
    def table(self, *_args, **_kwargs):
        raise AssertionError("A pure unit test unexpectedly accessed Supabase")

fake_supabase = types.ModuleType("supabase")
fake_supabase.create_client = lambda *_args, **_kwargs: _FakeSupabaseClient()
sys.modules.setdefault("supabase", fake_supabase)

import telegram_alerts as notifier

REAL_ROW_6_DETAILS = {
    "wall_side": "PUT",
    "rearm_rule": "A completed candle must close inside both walls before another breach can be recorded.",
    "expiry_date": "2026-11-16",
    "wall_identity": "PUT:2026-11-16:64600.00000000",
    "breached_level": 64600,
    "first_close_price": 64474.7,
    "first_close_timestamp": "2026-11-16T05:59:59.999Z",
    "wall_source_snapshot_id": "6970a026-7496-4dbd-8b06-d3e5a1dad69c",
    "confirmation_close_price": 64473.05,
    "consecutive_confirmation": True,
    "wall_source_collected_at": "2026-11-16T06:03:30.845Z",
    "confirmation_close_timestamp": "2026-11-16T06:29:59.999Z",
}

CUTOVER = "2026-11-16T06:30:00Z"

def journal_row(**overrides):
    row = {
        "id": 6,
        "event_timestamp": "2026-11-16T06:29:59.999Z",
        "event_type": "GEX_WALL_BREACH",
        "event_key": "PUT:2026-11-16:64600.00000000",
        "event_details": dict(REAL_ROW_6_DETAILS),
        "created_at": "2026-11-16T06:30:01Z",
    }
    row.update(overrides)
    return row


def test_real_detector_event_details_validate_and_build_message():
    details = notifier.validate_gex_event_details(json.dumps(REAL_ROW_6_DETAILS))
    assert details["confirmation_close_price"] == 64473.05
    alert = notifier.build_gex_alert(journal_row())
    assert alert["key"] == "SYSTEM_SIGNAL_GEX_WALL_BREACH_6"
    assert "Confirmation close: $64,473.05" in alert["msg"]
    assert "Breached wall: $64,600.00" in alert["msg"]
    assert alert["time"] == "2026-11-16T06:29:59.999Z"


def test_missing_or_malformed_required_wall_details_fail_closed():
    missing = dict(REAL_ROW_6_DETAILS)
    del missing["wall_source_snapshot_id"]
    try:
        notifier.validate_gex_event_details(missing)
        assert False, "missing key should fail"
    except ValueError as exc:
        assert "wall_source_snapshot_id" in str(exc)

    malformed = dict(REAL_ROW_6_DETAILS, confirmation_close_price=float("nan"))
    try:
        notifier.validate_gex_event_details(malformed)
        assert False, "NaN should fail"
    except ValueError as exc:
        assert "finite number" in str(exc)

    assert not notifier.gex_row_is_eligible(
        journal_row(event_details=missing), CUTOVER
    )


def test_cutover_uses_created_at_not_event_timestamp():
    # Event timestamp is just before cutover, but the journal insertion is after it.
    assert notifier.gex_row_is_eligible(journal_row(), CUTOVER)
    pre_cutover = journal_row(created_at="2026-11-16T06:29:59.999Z")
    assert not notifier.gex_row_is_eligible(pre_cutover, CUTOVER)


def test_wrong_event_type_and_invalid_timestamp_are_ineligible():
    assert not notifier.gex_row_is_eligible(
        journal_row(event_type="AMT_STATE_CHANGE"), CUTOVER
    )
    assert not notifier.gex_row_is_eligible(
        journal_row(created_at="not-a-timestamp"), CUTOVER
    )


def test_duplicate_identity_is_stable_and_independent_of_event_time():
    first = notifier.build_gex_alert(journal_row())
    replay = notifier.build_gex_alert(journal_row(created_at="2026-11-16T07:00:00Z"))
    assert first["key"] == replay["key"] == "SYSTEM_SIGNAL_GEX_WALL_BREACH_6"


def test_same_candle_amt_and_gex_are_both_preserved():
    amt = {"key": "AMT_STATE_CHANGE_2026-11-16T06:29:59Z", "type": "AMT_STATE_CHANGE"}
    gex = notifier.build_gex_alert(journal_row())
    oi = {"key": "OI_SHOCK_2026-11-16T06:29:59Z", "type": "OI_SHOCK"}
    alerts = notifier.collect_alerts(amt, [gex], oi)
    assert [item["type"] for item in alerts] == [
        "AMT_STATE_CHANGE", "GEX_WALL_BREACH", "OI_SHOCK"
    ]
    assert len({item["key"] for item in alerts}) == 3


def test_confirmation_price_and_breached_level_are_from_event_details():
    row = journal_row()
    row["price"] = 99999
    row["put_wall"] = 88888
    alert = notifier.build_gex_alert(row)
    assert "$64,473.05" in alert["msg"]
    assert "$64,600.00" in alert["msg"]
    assert "$99,999.00" not in alert["msg"]
    assert "$88,888.00" not in alert["msg"]


def test_failed_delivery_is_retryable_on_next_run_and_sent_is_suppressed(monkeypatch):
    class Query:
        def __init__(self, rows):
            self.rows = rows
        def select(self, *_args, **_kwargs): return self
        def eq(self, *_args, **_kwargs): return self
        def gte(self, *_args, **_kwargs): return self
        def order(self, *_args, **_kwargs): return self
        def limit(self, *_args, **_kwargs): return self
        def execute(self):
            return types.SimpleNamespace(data=[journal_row()])

    class FakeClient:
        def table(self, name):
            assert name == "system_signal_journal"
            return Query([journal_row()])

    monkeypatch.setattr(notifier, "supabase", FakeClient())
    monkeypatch.setattr(notifier, "GEX_ALERT_CUTOVER_UTC", CUTOVER)
    delivery_state = {"status": "FAILED"}
    monkeypatch.setattr(
        notifier, "check_event_exists",
        lambda key: delivery_state["status"] == "SENT"
    )

    # FAILED does not suppress the same journal event: next workflow run retries it.
    retry = notifier.evaluate_gex_breach()
    assert len(retry) == 1
    assert retry[0]["key"] == "SYSTEM_SIGNAL_GEX_WALL_BREACH_6"

    # Once SENT, a valid successful delivery is suppressed on later workflow runs.
    delivery_state["status"] = "SENT"
    assert notifier.evaluate_gex_breach() == []
