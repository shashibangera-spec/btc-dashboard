import unittest
from datetime import date, datetime, timedelta, timezone

from session_ohlc_writer import (
    SESSION_SPECS,
    process_date,
    session_bounds,
    validate_and_aggregate,
)

UTC = timezone.utc
DAY = date(2026, 11, 21)
NOW = datetime(2026, 11, 22, 12, 30, tzinfo=UTC)


def make_rows(amt_date, session_name):
    start, _, expected_count = session_bounds(amt_date, session_name)
    rows = []
    for index in range(expected_count):
        opened = start + timedelta(minutes=30 * index)
        base = 60000 + index
        rows.append({
            "candle_open_time": opened.isoformat(),
            # Match the real Binance/Supabase inclusive close timestamp.
            "candle_close_time": (opened + timedelta(minutes=30) - timedelta(milliseconds=1)).isoformat(),
            "open_price": str(base),
            "high_price": str(base + 10),
            "low_price": str(base - 10),
            "close_price": str(base + 2),
            "volume": "1.50000001",
            "data_status": "VALID",
            "exchange": "BINANCE",
            "symbol": "BTCUSDT",
            "timeframe": "30",
        })
    return rows


class FakeResponse:
    def __init__(self, data):
        self.data = data


class FakeQuery:
    def __init__(self, client, table_name):
        self.client = client
        self.table_name = table_name
        self.filters = {}
        self.lower = None
        self.upper = None
        self.offset = 0
        self.limit = None

    def select(self, *_args):
        return self

    def eq(self, key, value):
        self.filters[key] = value
        return self

    def gte(self, key, value):
        self.lower = datetime.fromisoformat(value)
        return self

    def lt(self, key, value):
        self.upper = datetime.fromisoformat(value)
        return self

    def order(self, *_args):
        return self

    def range(self, start, end):
        self.offset, self.limit = start, end - start + 1
        return self

    def upsert(self, record, on_conflict):
        self.client.writes.append((self.table_name, record, on_conflict))
        return self

    def execute(self):
        if self.table_name == "session_ohlc_history":
            return FakeResponse([])
        rows = []
        for row in self.client.source_rows:
            if any(row.get(k) != v for k, v in self.filters.items()):
                continue
            opened = datetime.fromisoformat(row["candle_open_time"])
            if self.lower and opened < self.lower:
                continue
            if self.upper and opened >= self.upper:
                continue
            rows.append(row)
        rows.sort(key=lambda row: row["candle_open_time"])
        if self.limit is not None:
            rows = rows[self.offset:self.offset + self.limit]
        return FakeResponse(rows)


class FakeClient:
    def __init__(self, source_rows):
        self.source_rows = source_rows
        self.writes = []

    def table(self, table_name):
        return FakeQuery(self, table_name)


class SessionBoundsTests(unittest.TestCase):
    def test_expected_session_lengths(self):
        self.assertEqual(
            {name: spec[2] for name, spec in SESSION_SPECS.items()},
            {"ASIA": 16, "EUROPE": 8, "US_CASH": 16, "US_OVERNIGHT": 8},
        )

    def test_overnight_stays_on_amt_date_and_crosses_midnight(self):
        start, end, count = session_bounds(DAY, "US_OVERNIGHT")
        self.assertEqual(start, datetime(2026, 11, 21, 20, 0, tzinfo=UTC))
        self.assertEqual(end, datetime(2026, 11, 22, 0, 0, tzinfo=UTC))
        self.assertEqual(count, 8)


class SessionAggregationTests(unittest.TestCase):
    def test_all_four_real_session_shapes_validate(self):
        for session_name in SESSION_SPECS:
            with self.subTest(session=session_name):
                result = validate_and_aggregate(
                    DAY, session_name, make_rows(DAY, session_name), NOW
                )
                self.assertEqual(result["data_status"], "COMPLETE")
                self.assertEqual(result["candle_count"], SESSION_SPECS[session_name][2])

    def test_binance_inclusive_59_999_close_timestamp_is_accepted(self):
        rows = make_rows(DAY, "ASIA")
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "COMPLETE")

    def test_ohlc_and_volume_aggregate_exactly(self):
        rows = make_rows(DAY, "ASIA")
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["open_price"], "60000")
        self.assertEqual(result["high_price"], "60025")
        self.assertEqual(result["low_price"], "59990")
        self.assertEqual(result["close_price"], "60017")
        self.assertEqual(result["volume"], "24.00000016")

    def test_missing_candle_is_incomplete_and_has_no_partial_ohlc(self):
        rows = make_rows(DAY, "ASIA")
        rows.pop(5)
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INCOMPLETE")
        self.assertEqual(result["candle_count"], 15)
        self.assertIsNone(result["open_price"])
        self.assertIsNone(result["volume"])

    def test_missing_interval_with_same_row_count_is_incomplete(self):
        rows = make_rows(DAY, "ASIA")
        rows.pop(5)
        replacement = dict(rows[4])
        replacement_open = datetime.fromisoformat(replacement["candle_open_time"]) + timedelta(minutes=15)
        replacement["candle_open_time"] = replacement_open.isoformat()
        replacement["candle_close_time"] = (
            replacement_open + timedelta(minutes=30) - timedelta(milliseconds=1)
        ).isoformat()
        rows.append(replacement)
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INCOMPLETE")

    def test_duplicate_timestamp_is_invalid(self):
        rows = make_rows(DAY, "ASIA")
        rows.append(dict(rows[0]))
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_bad_ohlc_relationship_is_invalid(self):
        rows = make_rows(DAY, "ASIA")
        rows[0]["high_price"] = str(int(rows[0]["open_price"]) - 1)
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_non_valid_source_status_is_invalid(self):
        rows = make_rows(DAY, "ASIA")
        rows[0]["data_status"] = "DATA_UNAVAILABLE"
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_candle_that_has_not_closed_is_invalid(self):
        rows = make_rows(DAY, "ASIA")
        rows[-1]["candle_close_time"] = (NOW + timedelta(milliseconds=1)).isoformat()
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_negative_volume_is_invalid(self):
        rows = make_rows(DAY, "ASIA")
        rows[2]["volume"] = "-1"
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_non_finite_numeric_is_invalid(self):
        rows = make_rows(DAY, "ASIA")
        rows[2]["volume"] = "NaN"
        result = validate_and_aggregate(DAY, "ASIA", rows, NOW)
        self.assertEqual(result["data_status"], "INVALID")

    def test_future_session_is_incomplete(self):
        before_asia_ends = datetime(2026, 11, 21, 7, 59, tzinfo=UTC)
        result = validate_and_aggregate(
            DAY, "ASIA", make_rows(DAY, "ASIA"), before_asia_ends
        )
        self.assertEqual(result["data_status"], "INCOMPLETE")
        self.assertIsNone(result["close_price"])


class WriterSafetyTests(unittest.TestCase):
    def test_default_process_is_read_only(self):
        source = []
        for session_name in SESSION_SPECS:
            source.extend(make_rows(DAY, session_name))
        client = FakeClient(source)
        records = process_date(client, DAY, NOW, write=False)
        self.assertEqual(len(records), 4)
        self.assertEqual(len(client.writes), 0)

    def test_write_mode_upserts_only_complete_records_with_stable_key(self):
        source = []
        for session_name in SESSION_SPECS:
            source.extend(make_rows(DAY, session_name))
        # Remove one Asia candle: it must not be written as COMPLETE.
        source = [
            row for row in source
            if not (
                row["candle_open_time"] == datetime(2026, 11, 21, 2, 30, tzinfo=UTC).isoformat()
            )
        ]
        client = FakeClient(source)
        records = process_date(client, DAY, NOW, write=True)
        self.assertEqual(len(records), 4)
        self.assertEqual(len(client.writes), 3)
        self.assertTrue(all(item[1]["data_status"] == "COMPLETE" for item in client.writes))
        self.assertTrue(all(item[2] == "amt_date,session_name" for item in client.writes))


if __name__ == "__main__":
    unittest.main()
