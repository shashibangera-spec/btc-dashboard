"""Regression tests for Futures regional-block retry and coverage integrity."""
import json
import urllib.error

import sys
import types

fake_supabase = types.ModuleType("supabase")
fake_supabase.create_client = lambda *_args, **_kwargs: object()
sys.modules.setdefault("supabase", fake_supabase)

import collector
import futures_positioning as fp


def http_error(code):
    return urllib.error.HTTPError("https://example.test", code, "blocked", {}, None)


def test_request_json_does_not_retry_403_or_451(monkeypatch):
    calls = []
    sleeps = []
    def fail(*_args, **_kwargs):
        calls.append(1)
        raise http_error(451)
    monkeypatch.setattr(fp.urllib.request, "urlopen", fail)
    monkeypatch.setattr(fp.time, "sleep", lambda delay: sleeps.append(delay))
    try:
        fp.request_json("https://example.test")
        assert False, "regional block must raise"
    except fp.DataFetchError as exc:
        assert exc.category == "REGIONAL_ACCESS_BLOCK"
        assert exc.status_code == 451
    assert len(calls) == 1
    assert sleeps == []


def test_host_fallback_stops_after_deterministic_403(monkeypatch):
    calls = []
    def fail(url, params=None):
        calls.append(url)
        raise fp.DataFetchError("REGIONAL_ACCESS_BLOCK", "blocked", url, status_code=403)
    monkeypatch.setattr(fp, "request_json", fail)
    try:
        fp.request_with_host_fallback(("https://one.test", "https://two.test"), "/oi")
        assert False, "regional block must raise"
    except fp.DataFetchError as exc:
        assert exc.status_code == 403
        assert exc.category == "REGIONAL_ACCESS_BLOCK"
    assert calls == ["https://one.test/oi"]


def test_request_json_keeps_retry_for_transient_http_5xx(monkeypatch):
    calls = []
    def fail(*_args, **_kwargs):
        calls.append(1)
        if len(calls) < 3:
            raise http_error(503)
        class Response:
            status = 200
            def __enter__(self): return self
            def __exit__(self, *_args): return False
            def read(self): return b'{"ok": true}'
        return Response()
    monkeypatch.setattr(fp.urllib.request, "urlopen", fail)
    monkeypatch.setattr(fp.time, "sleep", lambda _delay: None)
    result = fp.request_json("https://example.test")
    assert result["payload"] == {"ok": True}
    assert len(calls) == 3


def test_aggregate_database_row_explicitly_labels_partial_coverage():
    snapshot = {
        "collected_at": "2026-11-17T00:00:00Z",
        "coverage": {
            "available": 2, "expected": 4, "label": "2/4",
            "status": "PARTIAL", "contributors": ["OKX", "DELTA"],
            "missing_exchanges": ["BINANCE", "BYBIT"],
        },
        "aggregate": {"oi_usd": 2_000_000_000, "funding_rate_oi_weighted": 0.0001,
                      "basis_pct_oi_weighted": 0.02, "funding_contributors": ["OKX", "DELTA"],
                      "basis_contributors": ["OKX", "DELTA"]},
        "interpretation": "PARTIAL: OKX, DELTA OI.",
    }
    row = collector.make_aggregate_row(snapshot, "2026-11-17T00:00:00Z")
    status = json.loads(row["status"])
    assert status["coverage_status"] == "PARTIAL_COVERAGE (2/4)"
    assert status["coverage"]["contributors"] == ["OKX", "DELTA"]
    assert status["missing_exchanges"] == ["BINANCE", "BYBIT"]
    assert row["oi_usd"] == 2_000_000_000
