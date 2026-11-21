"""Regression tests for retired Value Migration event generation."""
from datetime import datetime, timedelta, timezone

import signal_detector


def _adjacent_amt_rows(previous_state="IN_VALUE", current_state="DISCOVERY_BELOW"):
    now = datetime.now(timezone.utc)
    current_open = now - timedelta(minutes=35)
    previous_open = current_open - timedelta(minutes=30)
    previous_close = previous_open + timedelta(minutes=30) - timedelta(milliseconds=1)
    current_close = current_open + timedelta(minutes=30) - timedelta(milliseconds=1)

    previous = {
        "candle_open_time": previous_open.isoformat(),
        "candle_close_time": previous_close.isoformat(),
        "close_price": 60000.0,
        "confirmed_state": previous_state,
        "developing_poc": 60000.0,
    }
    current = {
        "candle_open_time": current_open.isoformat(),
        "candle_close_time": current_close.isoformat(),
        "close_price": 60500.0,
        "confirmed_state": current_state,
        "developing_poc": 62500.0,
    }
    return [previous, current]


def test_large_developing_poc_change_does_not_create_value_migration_event(monkeypatch):
    monkeypatch.setattr(
        signal_detector,
        "build_event_row",
        lambda event_type, *_args, **_kwargs: {"event_type": event_type},
    )
    rows = _adjacent_amt_rows(previous_state="IN_VALUE", current_state="IN_VALUE")

    events = signal_detector.detect_latest_amt_events(rows, [], [])

    assert events == []


def test_amt_state_change_still_generates_event_despite_large_poc_change(monkeypatch):
    monkeypatch.setattr(
        signal_detector,
        "build_event_row",
        lambda event_type, *_args, **_kwargs: {"event_type": event_type},
    )
    rows = _adjacent_amt_rows(previous_state="IN_VALUE", current_state="DISCOVERY_BELOW")

    events = signal_detector.detect_latest_amt_events(rows, [], [])

    assert [event["event_type"] for event in events] == ["AMT_STATE_CHANGE"]
