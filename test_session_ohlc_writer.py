import unittest
from datetime import date, datetime, timedelta, timezone

from session_ohlc_writer import (
    SESSION_SPECS,
    session_bounds,
    validate_and_aggregate,
)

UTC = timezone.utc
NOW = datetime(2026, 11, 22, 12, 30, tzinfo=UTC)


def make_rows(amt_date, session_name):
    start, _, expected_count = session_bounds(amt_date, session_name)
    rows = []
    for index in range(expected_count):
        opened = start + timedelta(minutes=30 * index)
        base = 60000 + index
        rows.append({
            "candle_open_time": opened.isoformat(),
            "candle_close_time": (opened + timedelta(minutes=30)).isoformat(),
            "open_price": base,
            "high_price": base + 10,
            "low_price": base - 10,
            "close_price": base + 2,
            "volume": 1.5,
            "data_status": "VALID",
        })
    return rows


class SessionBoundsTests(unittest.TestCase):
    def test_expected_session_lengths(self):
        self.assertEqual(
            {name: spec[2] for name, spec in SESSION_SPECS.items()},
            {"ASIA": 16, "EUROPE": 8, "US_CASH": 16, "US_OVERNIGHT": 8},
        )

    def test_overnight_stays_on_amt_date_and_crosses_midnight(self):
        start, end, count = session_bounds(date(2026, 11, 21), "US_OVERNIGHT")
        self.assertEqual(start, datetime(2026, 11, 21, 20, 0, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 11, 22, 0, 0, tzinfo=UTC))
        self.assertEqual(count, 8)


class SessionAggregationTests(unittest.TestCase):
    def test_complete_asia_session_aggregates_correctly(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "COMPLETE")
        self.assertEqual(result["candle_count"], 16)
        self.assertEqual(result["open_price"], 60000)
        self.assertEqual(result["high_price"], 60025)
        self.assertEqual(result["low_price"], 59990)
        self.assertEqual(result["close_price"], 60017)
        self.assertEqual(result["volume"], 24.0)

    def test_complete_europe_session_aggregates_correctly(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "EUROPE")
        result = validate_and_aggregate(day, "EUROPE", rows, NOW)
        self.assertEqual(result["data_status"], "COMPLETE")
        self.assertEqual(result["candle_count"], 8)
        self.assertEqual(result["volume"], 12.0)

    def test_missing_candle_is_incomplete_and_has_no_partial_ohlc(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        rows.pop(5)
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INCOMPLETE")
        self.assertEqual(result["candle_count"], 15)
        self.assertIsNone(result["open_price"])
        self.assertIsNone(result["volume"])

    def test_duplicate_timestamp_is_invalid(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        rows.append(dict(rows[0]))
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_bad_ohlc_relationship_is_invalid(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        rows[0]["high_price"] = rows[0]["open_price"] - 1
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_non_valid_source_status_is_invalid(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        rows[0]["data_status"] = "DATA_UNAVAILABLE"
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_unclosed_candle_is_invalid(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        rows[-1]["candle_close_time"] = (NOW + timedelta(minutes=30)).isoformat()
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_negative_volume_is_invalid(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "ASIA")
        rows[2]["volume"] = -1
        result = validate_and_aggregate(day, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_partial_overnight_window_is_not_complete(self):
        day = date(2026, 11, 21)
        rows = make_rows(day, "US_OVERNIGHT")
        result = validate_and_aggregate(day, "US_OVERNIGHT", rows, NOW)
        self.assertEqual(result["data_status"], "COMPLETE")
        self.assertEqual(result["candle_count"], 8)


if __name__ == "__main__":
    unittest.main()
