"""Offline regression tests for context-only OI_SHOCK enrichment."""
import os
import sys
import types
from datetime import datetime, timedelta, timezone
from pathlib import Path

os.environ.setdefault("SUPABASE_URL", "https://unit.test")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "unit-test-key")
os.environ.setdefault("TELEGRAM_BOT_TOKEN", "unit-test-token")
os.environ.setdefault("TELEGRAM_CHAT_ID", "unit-test-chat")

fake_supabase = types.ModuleType("supabase")
fake_supabase.create_client = lambda *_args, **_kwargs: object()
sys.modules.setdefault("supabase", fake_supabase)

import signal_detector as detector
import telegram_alerts as notifier


def test_detector_amt_price_location_classification():
    assert detector.classify_amt_price_location({"close_price": 111, "prior_vah": 110, "prior_val": 100}) == "ABOVE_VAH"
    assert detector.classify_amt_price_location({"close_price": 99, "prior_vah": 110, "prior_val": 100}) == "BELOW_VAL"
    assert detector.classify_amt_price_location({"close_price": 105, "prior_vah": 110, "prior_val": 100}) == "INSIDE_VALUE"
    assert detector.classify_amt_price_location({"close_price": 105, "prior_vah": None, "prior_val": 100}) == "LOCATION_UNAVAILABLE"


def detector_fixture(interval=30, change=4.0, amt_rows=None):
    event_time = datetime(2026, 11, 17, 12, 0, tzinfo=timezone.utc)
    contributors = ["DELTA", "OKX"]
    futures = [
        {"created_at": event_time-timedelta(minutes=interval), "observed_at": event_time-timedelta(minutes=interval), "oi_usd": 100_000_000.0, "contributors": contributors},
        {"created_at": event_time, "observed_at": event_time, "oi_usd": 100_000_000.0*(1+change/100), "contributors": contributors},
    ]
    amt = amt_rows if amt_rows is not None else [{
        "candle_open_time": (event_time-timedelta(minutes=30)).isoformat(),
        "candle_close_time": (event_time-timedelta(seconds=1)).isoformat(),
        "close_price": 99_000, "prior_vah": 100_000, "prior_val": 99_500,
        "confirmed_state": "DISCOVERY_BELOW", "developing_poc": 99_700,
        "developing_vah": 100_000, "developing_val": 99_000,
    }]
    return event_time, futures, amt


def test_detector_adds_asof_amt_context(monkeypatch):
    event_time, futures, amt = detector_fixture()
    monkeypatch.setattr(detector, "utc_now", lambda: event_time+timedelta(minutes=1))
    event = detector.detect_oi_shock(futures, amt, [])
    assert event["event_details"]["price_location"] == "BELOW_VAL"
    assert event["event_details"]["amt_state"] == "DISCOVERY_BELOW"
    assert event["event_details"]["prior_val"] == 99_500


def test_detector_keeps_event_when_amt_lookup_unavailable(monkeypatch):
    event_time, futures, _ = detector_fixture(amt_rows=[])
    monkeypatch.setattr(detector, "utc_now", lambda: event_time+timedelta(minutes=1))
    event = detector.detect_oi_shock(futures, [], [])
    assert event is not None
    assert event["event_details"]["price_location"] == "LOCATION_UNAVAILABLE"
    assert event["event_details"]["amt_state"] == "AMT_STATE_UNAVAILABLE"


def test_detector_preserves_strict_threshold_interval_and_contributor_matching(monkeypatch):
    event_time, futures, amt = detector_fixture(change=2.99)
    monkeypatch.setattr(detector, "utc_now", lambda: event_time+timedelta(minutes=1))
    assert detector.detect_oi_shock(futures, amt, []) is None
    for interval in (25, 35):
        _, f, a = detector_fixture(interval=interval)
        assert detector.detect_oi_shock(f, a, []) is not None
    for interval in (24, 36):
        _, f, a = detector_fixture(interval=interval)
        assert detector.detect_oi_shock(f, a, []) is None
    _, f, a = detector_fixture()
    f[0]["contributors"] = ["DELTA"]
    assert detector.detect_oi_shock(f, a, []) is None


def test_telegram_amt_context_and_missing_lookup(monkeypatch):
    assert notifier.classify_amt_price_location({"close_price": 90, "prior_vah": 110, "prior_val": 100}) == "BELOW_VAL"
    class Query:
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def lte(self, *_a, **_k): return self
        def order(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def execute(self):
            return types.SimpleNamespace(data=[{"candle_close_time":"2026-11-17T11:59:59Z","close_price":90,"prior_vah":110,"prior_val":100,"confirmed_state":"DISCOVERY_BELOW"}])
    class Client:
        def table(self, name):
            assert name == "amt_snapshots"
            return Query()
    monkeypatch.setattr(notifier, "supabase", Client())
    context = notifier.load_amt_context_asof("2026-11-17T12:00:00Z")
    assert context["price_location"] == "BELOW_VAL"
    assert context["amt_state"] == "DISCOVERY_BELOW"
    class BrokenClient:
        def table(self, _name): raise RuntimeError("database unavailable")
    monkeypatch.setattr(notifier, "supabase", BrokenClient())
    missing = notifier.load_amt_context_asof("2026-11-17T12:00:00Z")
    assert missing["price_location"] == "LOCATION_UNAVAILABLE"
    assert missing["amt_state"] == "AMT_STATE_UNAVAILABLE"


def test_telegram_message_includes_context_and_retains_inclusive_threshold(monkeypatch):
    current = {"created_at":"2026-11-17T12:00:00Z","oi_usd":103_000_000,"status":{"coverage":{"contributors":["DELTA","OKX"]}}}
    previous = {"created_at":"2026-11-17T11:30:00Z","oi_usd":100_000_000,"status":{"coverage":{"contributors":["DELTA","OKX"]}}}
    class Query:
        def __init__(self, table): self.name = table
        def select(self, *_a, **_k): return self
        def eq(self, *_a, **_k): return self
        def order(self, *_a, **_k): return self
        def limit(self, *_a, **_k): return self
        def lte(self, *_a, **_k): return self
        def execute(self):
            if self.name == "futures_oi_snapshots": return types.SimpleNamespace(data=[current, previous])
            return types.SimpleNamespace(data=[{"candle_close_time":"2026-11-17T11:59:59Z","close_price":90,"prior_vah":110,"prior_val":100,"confirmed_state":"DISCOVERY_BELOW"}])
    class Client:
        def table(self, name): return Query(name)
    monkeypatch.setattr(notifier, "supabase", Client())
    monkeypatch.setattr(notifier, "check_event_exists", lambda _key: False)
    alert = notifier.evaluate_oi_shock()
    assert alert is not None
    assert "Price Location: BELOW_VAL" in alert["msg"]
    assert "AMT State: DISCOVERY_BELOW" in alert["msg"]
    assert alert["payload"]["price_location"] == "BELOW_VAL"


def test_oi_source_guards_remain_unchanged():
    root = Path(__file__).resolve().parent
    detector_source = (root/"signal_detector.py").read_text()
    notifier_source = (root/"telegram_alerts.py").read_text()
    assert "if OI_MIN_INTERVAL_MINUTES <= age_minutes <= OI_MAX_INTERVAL_MINUTES:" in detector_source
    assert 'if previous["contributors"] == current["contributors"]:' in detector_source
    assert "if abs(change_pct) <= OI_SHOCK_THRESHOLD_PCT:" in detector_source
    assert "if not (25 <= delta_mins <= 35): return None" in notifier_source
    assert "if not contrib_c or contrib_c != contrib_p or len(contrib_c) < 2:" in notifier_source
    assert "if abs(pct_change) >= 3.0:" in notifier_source
