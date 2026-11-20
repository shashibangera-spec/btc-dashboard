"""Focused synthetic regression tests for shadow_monitor.py (shadow-v1.1).

These tests call only calculate_shadow_rows() with in-memory candles.
They do not call main(), Supabase, collectors, alerts, or production services.
"""
import unittest
from datetime import datetime, timedelta, timezone

from shadow_monitor import calculate_shadow_rows


BASE = datetime(2026, 11, 20, 0, 0, tzinfo=timezone.utc)


def candle(
    index,
    *,
    close,
    low,
    high=None,
    val=100.0,
    atr=10.0,
    state="DISCOVERY_CONFIRMED_BELOW",
    profile="profile-1",
):
    """Build one valid completed 30-minute synthetic AMT source row."""
    opened = BASE + timedelta(minutes=30 * index)
    closed = opened + timedelta(minutes=30)
    high = max(close, high if high is not None else close)
    low = min(close, low)
    return {
        "exchange": "BINANCE",
        "symbol": "BTCUSDT",
        "timeframe": "30",
        "candle_open_time": opened.isoformat(),
        "candle_close_time": closed.isoformat(),
        "profile_id": profile,
        "confirmed_state": state,
        "close_price": float(close),
        "high_price": float(high),
        "low_price": float(low),
        "developing_val": float(val),
        "developing_poc": 105.0,
        "atr_21": float(atr),
        "data_status": "VALID",
    }


class ShadowMonitorSyntheticTests(unittest.TestCase):
    def test_1_counters_reset_on_state_and_profile_changes(self):
        rows = [
            candle(0, close=110, low=108),
            candle(1, close=105, low=99),  # first rejection: counters become 1/1
            candle(2, close=110, low=108, state="IN_VALUE"),
            candle(3, close=105, low=99, state="IN_VALUE"),
            candle(4, close=110, low=108, state="IN_VALUE", profile="profile-2"),
        ]

        result = calculate_shadow_rows(rows)

        self.assertEqual(result[1]["val_test_status"], "REJECTED")
        self.assertEqual(result[1]["val_test_count"], 1)
        self.assertEqual(result[1]["val_rejection_count"], 1)

        self.assertEqual(result[2]["val_test_status"], "RESET_CONTEXT")
        self.assertEqual(result[2]["val_test_count"], 0)
        self.assertEqual(result[2]["val_rejection_count"], 0)

        self.assertEqual(result[3]["val_test_status"], "REJECTED")
        self.assertEqual(result[3]["val_test_count"], 1)
        self.assertEqual(result[3]["val_rejection_count"], 1)

        self.assertEqual(result[4]["val_test_status"], "RESET_CONTEXT")
        self.assertEqual(result[4]["val_test_count"], 0)
        self.assertEqual(result[4]["val_rejection_count"], 0)

    def test_2_reset_row_clears_stale_episode_metadata(self):
        rows = [
            candle(0, close=110, low=108),
            candle(1, close=100, low=99),  # exactly on frozen VAL: STARTED
            candle(2, close=105, low=103, state="IN_VALUE"),
        ]

        result = calculate_shadow_rows(rows)
        started = result[1]
        reset = result[2]

        self.assertEqual(started["val_test_status"], "STARTED")
        self.assertTrue(started["val_test_active"])
        self.assertIsNotNone(started["val_test_started_at"])
        self.assertEqual(started["val_test_level"], 100.0)

        self.assertEqual(reset["val_test_status"], "RESET_CONTEXT")
        self.assertFalse(reset["val_test_active"])
        self.assertIsNone(reset["val_test_started_at"])
        self.assertIsNone(reset["val_test_level"])

    def test_3_intrabar_wick_touch_and_close_above_is_rejected(self):
        rows = [
            candle(0, close=110, low=108),
            candle(1, close=105, low=99),  # wick through 100, close back above
        ]

        result = calculate_shadow_rows(rows)
        rejected = result[1]

        self.assertEqual(rejected["val_test_status"], "REJECTED")
        self.assertEqual(rejected["val_test_count"], 1)
        self.assertEqual(rejected["val_rejection_count"], 1)
        self.assertFalse(rejected["val_test_active"])

    def test_4_episode_ending_without_retest_is_neutral_none(self):
        rows = [
            candle(0, close=110, low=108),
            candle(1, close=100, low=99),  # episode starts on VAL
            candle(2, close=105, low=101),  # moves away; no wick rejection
        ]

        result = calculate_shadow_rows(rows)
        ended = result[2]

        self.assertEqual(ended["val_test_status"], "NONE")
        self.assertEqual(ended["val_test_count"], 1)
        self.assertEqual(ended["val_rejection_count"], 0)
        self.assertFalse(ended["val_test_active"])
        self.assertIsNone(ended["val_test_started_at"])
        self.assertIsNone(ended["val_test_level"])

    def test_5_close_below_frozen_val_is_broken(self):
        rows = [
            candle(0, close=110, low=108),
            candle(1, close=100, low=99),  # episode starts on VAL
            candle(2, close=98, low=97, high=104),  # closes below frozen VAL
        ]

        result = calculate_shadow_rows(rows)
        broken = result[2]

        self.assertEqual(broken["val_test_status"], "BROKEN")
        self.assertEqual(broken["val_test_count"], 1)
        self.assertEqual(broken["val_rejection_count"], 0)
        self.assertFalse(broken["val_test_active"])
        self.assertIsNone(broken["val_test_started_at"])
        self.assertIsNone(broken["val_test_level"])

    def test_6_excursion_atr_uses_frozen_anchor_atr(self):
        rows = [
            candle(0, close=110, low=108, atr=10),
            candle(1, close=90, low=89, high=95, atr=20),
        ]

        result = calculate_shadow_rows(rows)
        later = result[1]

        self.assertEqual(later["transition_anchor_price"], 110.0)
        self.assertEqual(later["downside_close_excursion_usd"], 20.0)
        # 20 USD excursion / frozen 10 ATR = 2.0, not 1.0 using current ATR 20.
        self.assertAlmostEqual(later["downside_close_excursion_atr"], 2.0)
        self.assertEqual(later["calculation_version"], "shadow-v1.1")


if __name__ == "__main__":
    unittest.main(verbosity=2)
