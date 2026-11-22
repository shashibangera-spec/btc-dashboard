import unittest
from datetime import date, datetime, timezone
from unittest.mock import patch

from binance_ohlc_archive import (
    INTERVAL_MS,
    build_range,
    fetch_klines,
    parse_kline,
    persist_records,
    utc_ms,
)


def raw_kline(open_ms, close_ms=None):
    return [
        open_ms, "60000.0", "60100.0", "59900.0", "60050.0", "12.5",
        close_ms if close_ms is not None else open_ms + INTERVAL_MS - 1,
        "750000.0", 1234, "6.25", "375000.0", "0",
    ]


class FakeResponse:
    status_code = 200
    headers = {}

    def __init__(self, payload):
        self.payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self.payload


class FakeSession:
    def __init__(self, pages):
        self.pages = list(pages)
        self.calls = []

    def get(self, url, params, timeout):
        self.calls.append((url, params, timeout))
        return FakeResponse(self.pages.pop(0))


class FakeTable:
    def __init__(self, client):
        self.client = client

    def upsert(self, records, on_conflict):
        self.client.writes.append((records, on_conflict))
        return self

    def execute(self):
        return None


class FakeClient:
    def __init__(self):
        self.writes = []

    def table(self, name):
        self.client_table = name
        return FakeTable(self)


class KlineValidationTests(unittest.TestCase):
    def setUp(self):
        self.open_ms = 1791676800000
        self.now_ms = self.open_ms + INTERVAL_MS * 3

    def test_valid_closed_kline_maps_all_archive_fields(self):
        row = parse_kline(raw_kline(self.open_ms), self.now_ms)
        self.assertEqual(row["symbol"], "BTCUSDT")
        self.assertEqual(row["interval"], "30m")
        self.assertEqual(row["open_price"], "60000.0")
        self.assertEqual(row["base_volume"], "12.5")
        self.assertEqual(row["trade_count"], 1234)
        self.assertEqual(row["source"], "BINANCE_PUBLIC_KLINES")

    def test_open_candle_is_ignored(self):
        close_ms = self.open_ms + INTERVAL_MS - 1
        row = parse_kline(
            raw_kline(self.open_ms, close_ms), close_ms - 1
        )
        self.assertIsNone(row)

    def test_bad_timestamp_grid_rejected(self):
        with self.assertRaises(ValueError):
            parse_kline(raw_kline(self.open_ms + 1), self.now_ms)

    def test_invalid_ohlc_rejected(self):
        row = raw_kline(self.open_ms)
        row[2] = "59999"
        with self.assertRaises(ValueError):
            parse_kline(row, self.now_ms)

    def test_negative_volume_rejected(self):
        row = raw_kline(self.open_ms)
        row[5] = "-1"
        with self.assertRaises(ValueError):
            parse_kline(row, self.now_ms)

    def test_nan_rejected(self):
        row = raw_kline(self.open_ms)
        row[1] = "NaN"
        with self.assertRaises(ValueError):
            parse_kline(row, self.now_ms)


class EndpointFallbackTests(unittest.TestCase):
    def test_http_451_switches_to_public_market_data_endpoint(self):
        start = 1791676800000
        class RestrictedResponse(FakeResponse):
            status_code = 451
            def raise_for_status(self):
                raise AssertionError("451 response should trigger endpoint fallback before raise_for_status")
        class FallbackSession:
            def __init__(self):
                self.calls = []
            def get(self, url, params, timeout):
                self.calls.append(url)
                if len(self.calls) == 1:
                    return RestrictedResponse([])
                return FakeResponse([raw_kline(start)])
        session = FallbackSession()
        with patch("binance_ohlc_archive.time.sleep"):
            rows = fetch_klines(start, start + INTERVAL_MS, start + INTERVAL_MS * 2, session=session)
        self.assertEqual(len(rows), 1)
        self.assertEqual(session.calls[0], "https://api.binance.com/api/v3/klines")
        self.assertEqual(session.calls[1], "https://data-api.binance.vision/api/v3/klines")


class FetchAndPersistenceTests(unittest.TestCase):
    def test_fetch_paginates_and_deduplicates_by_open_time(self):
        start = 1791676800000
        page1 = [raw_kline(start + i * INTERVAL_MS) for i in range(1000)]
        page2 = [
            raw_kline(start + 999 * INTERVAL_MS),
            raw_kline(start + 1000 * INTERVAL_MS),
        ]
        session = FakeSession([page1, page2])
        rows = fetch_klines(
            start, start + 1001 * INTERVAL_MS,
            start + 1002 * INTERVAL_MS, session=session
        )
        self.assertEqual(len(rows), 1001)
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(rows[0]["open_time"], rows[0]["open_time"])
        self.assertEqual(
            rows[-1]["open_time"],
            datetime.fromtimestamp(
                (start + 1000 * INTERVAL_MS) / 1000, timezone.utc
            ).isoformat()
        )

    def test_fetch_empty_range_returns_empty(self):
        session = FakeSession([[]])
        rows = fetch_klines(1000, 2000, 3000, session=session)
        self.assertEqual(rows, [])

    def test_dry_run_never_writes(self):
        client = FakeClient()
        records = [{"open_time": "2026-11-01T00:00:00+00:00"}]
        self.assertEqual(persist_records(client, records, write=False), 0)
        self.assertEqual(client.writes, [])

    def test_write_uses_stable_conflict_key(self):
        client = FakeClient()
        records = [{"open_time": str(i)} for i in range(3)]
        self.assertEqual(persist_records(client, records, write=True), 3)
        self.assertEqual(
            client.writes[0][1],
            "exchange,market_type,symbol,interval,open_time"
        )

    def test_historical_date_range_is_utc_midnight(self):
        now = datetime(2026, 11, 22, 12, 0, tzinfo=timezone.utc)
        args = type("Args", (), {
            "start_date": date(2026, 11, 1),
            "end_date": date(2026, 11, 2),
            "lookback_hours": None,
        })()
        start_ms, end_ms = build_range(args, now)
        self.assertEqual(
            datetime.fromtimestamp(start_ms / 1000, timezone.utc),
            datetime(2026, 11, 1, tzinfo=timezone.utc)
        )
        self.assertEqual(
            datetime.fromtimestamp(end_ms / 1000, timezone.utc),
            datetime(2026, 11, 3, tzinfo=timezone.utc)
        )


if __name__ == "__main__":
    unittest.main()
