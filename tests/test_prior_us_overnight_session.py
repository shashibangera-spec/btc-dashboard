"""Focused synthetic tests for the prior US overnight session row."""

import ast
import math
from pathlib import Path
import types
import unittest

import numpy as np
import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DASHBOARD = ROOT / "btc_live_dashboard.py"


def _load_session_helpers():
    source = DASHBOARD.read_text(encoding="utf-8")
    tree = ast.parse(source)
    names = {"calculate_tpo", "calculate_session_journey"}
    nodes = [
        node for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in nodes} != names:
        raise AssertionError("Could not locate the pure session calculation helpers.")
    module = types.ModuleType("session_helpers")
    module.__dict__.update({"np": np, "pd": pd, "math": math})
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(DASHBOARD), "exec"), module.__dict__)
    return module


def _fixture():
    helpers = _load_session_helpers()
    cycle_start = pd.Timestamp("2026-11-20 05:30:00", tz="Asia/Kolkata")
    reference_dates = pd.date_range(cycle_start - pd.Timedelta(hours=48), periods=48, freq="30min")
    intervening_dates = pd.date_range(cycle_start - pd.Timedelta(hours=24), periods=40, freq="30min")
    overnight_dates = pd.date_range(cycle_start - pd.Timedelta(hours=4), periods=8, freq="30min")
    asia_dates = pd.DatetimeIndex([cycle_start])

    rows = []
    for date in reference_dates:
        rows.append((date, 1040.0, 1100.0, 1000.0, 1050.0))
    for date in intervening_dates:
        # Deliberately very different from the preceding AMT cycle. These
        # candles must not become the overnight row's Macro Bias reference.
        rows.append((date, 5040.0, 5100.0, 5000.0, 5050.0))
    for i, date in enumerate(overnight_dates):
        rows.append((date, 2010.0 + i, 2100.0 if i == 3 else 2080.0, 2000.0, 2050.0 + i))
    for date in asia_dates:
        rows.append((date, 3010.0, 3100.0, 3000.0, 3050.0))

    frame = pd.DataFrame(rows, columns=["Date", "open", "high", "low", "close"])
    frame = frame.sort_values("Date").reset_index(drop=True)
    frame["ShiftedDate"] = frame["Date"] - pd.Timedelta(hours=5, minutes=30)
    return helpers, frame, cycle_start


class PriorUSOvernightSessionTests(unittest.TestCase):
    def test_prior_overnight_is_first_row_and_uses_exact_half_open_window(self):
        helpers, frame, cycle_start = _fixture()
        sessions = helpers.calculate_session_journey(frame, np.nan, np.nan)

        self.assertEqual(sessions.iloc[0]["Session"], "Prior US Overnight (01:30–05:30)")
        overnight = sessions.iloc[0]
        self.assertEqual(overnight["Open"], 2010.0)
        self.assertEqual(overnight["High"], 2100.0)
        self.assertEqual(overnight["Low"], 2000.0)
        self.assertEqual(overnight["Close"], 2057.0)

        # The candle at 05:30 is excluded from overnight and begins Asia.
        asia = sessions[sessions["Session"] == "Asia (05:30-13:30)"].iloc[0]
        self.assertEqual(asia["Open"], 3010.0)
        self.assertEqual(asia["Close"], 3050.0)

        # The exact 01:30 candle is included; the preceding 01:00 candle is not.
        start = cycle_start - pd.Timedelta(hours=4)
        self.assertEqual(overnight["Open"], frame.loc[frame["Date"] == start, "open"].iloc[0])
        self.assertNotEqual(overnight["Open"], frame.loc[frame["Date"] == start - pd.Timedelta(minutes=30), "open"].iloc[0])

    def test_macro_bias_uses_the_amt_cycle_preceding_the_overnight_segment(self):
        helpers, frame, _ = _fixture()
        sessions = helpers.calculate_session_journey(frame, np.nan, np.nan)
        overnight = sessions.iloc[0]

        # The proper reference cycle trades around 1,000-1,100, so a 2,057
        # close is above its VAH. The immediately preceding calendar segment
        # trades around 5,000 and would give a different, incorrect result.
        self.assertEqual(overnight["Macro Bias"], "🟢 Bullish (Above Prior VAH)")

    def test_macro_bias_is_na_when_preceding_cycle_has_a_missing_candle(self):
        helpers, frame, cycle_start = _fixture()
        missing_reference_time = cycle_start - pd.Timedelta(hours=47, minutes=30)
        frame = frame[frame["Date"] != missing_reference_time].reset_index(drop=True)

        sessions = helpers.calculate_session_journey(frame, np.nan, np.nan)
        self.assertEqual(sessions.iloc[0]["Session"], "Prior US Overnight (01:30–05:30)")
        self.assertEqual(sessions.iloc[0]["Macro Bias"], "N/A")

    def test_overnight_micro_bias_uses_its_own_value_profile(self):
        helpers, frame, _ = _fixture()
        sessions = helpers.calculate_session_journey(frame, np.nan, np.nan)
        overnight = sessions.iloc[0]

        expected_vah, expected_val, expected_poc = helpers.calculate_tpo(
            frame[(frame["Date"] >= frame["Date"].iloc[88]) & (frame["Date"] < frame["Date"].iloc[96])]
        )
        self.assertEqual(overnight["VAH"], expected_vah)
        self.assertEqual(overnight["VAL"], expected_val)
        self.assertEqual(overnight["POC"], expected_poc)


if __name__ == "__main__":
    unittest.main()
