import unittest
from datetime import datetime, timedelta, timezone
from independent_amt_engine import (
    AMTInputError, Candle, build_session_snapshots,
    calculate_tpo_profile, normalize_candles,
)
UTC = timezone.utc

def bar(t, lo, hi, close=None):
    close = (lo + hi) / 2 if close is None else close
    return Candle(t, t + timedelta(minutes=30) - timedelta(milliseconds=1),
                  close, hi, lo, close, 1)

class IndependentAMTTests(unittest.TestCase):
    def test_single_row(self):
        p = calculate_tpo_profile([bar(datetime(2026,1,1,tzinfo=UTC),100,100)])
        self.assertEqual((p.vah,p.poc,p.val),(125,125,125))

    def test_tpo_counts_each_bar_once_per_row(self):
        p = calculate_tpo_profile([
            bar(datetime(2026,1,1,tzinfo=UTC),100,150),
            bar(datetime(2026,1,1,0,30,tzinfo=UTC),100,100),
        ])
        self.assertEqual(dict(p.rows),{125:2,175:1})

    def test_bad_profile_inputs_rejected(self):
        with self.assertRaises(AMTInputError):
            calculate_tpo_profile([bar(datetime(2026,1,1,tzinfo=UTC),100,100)],0)

    def test_unclosed_filtered_and_rows_sorted(self):
        now=datetime(2026,1,1,1,tzinfo=UTC)
        rows=[
            {"open_time":"2026-01-01T00:30:00Z","close_time":"2026-01-01T00:59:59Z","open_price":110,"high_price":120,"low_price":100,"close_price":115},
            {"open_time":"2026-01-01T01:00:00Z","close_time":"2026-01-01T01:29:59Z","open_price":115,"high_price":125,"low_price":110,"close_price":120},
            {"open_time":"2026-01-01T00:00:00Z","close_time":"2026-01-01T00:29:59Z","open_price":100,"high_price":115,"low_price":95,"close_price":110},
        ]
        got=normalize_candles(rows,now)
        self.assertEqual(len(got),2)
        self.assertEqual(got[0].open_time.minute,0)

    def test_duplicate_rejected(self):
        row={"open_time":"2026-01-01T00:00:00Z","close_time":"2026-01-01T00:29:59Z","open_price":100,"high_price":110,"low_price":90,"close_price":105}
        with self.assertRaises(AMTInputError):
            normalize_candles([row,row],datetime(2026,1,1,1,tzinfo=UTC))

    def test_incomplete_prior_session_blocks_state(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        got=build_session_snapshots([
            bar(t,10000,10100,10050),
            bar(t+timedelta(days=1),10250,10300,10275),
        ],atr_period=1)
        self.assertEqual(got[0]["price_state"],"PRIOR_PROFILE_INCOMPLETE")
        self.assertIsNone(got[0]["prior_value"])

    def test_nonqualifying_close_resets_discovery_candidate(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),10200,10300,10200),
            bar(t+timedelta(days=1,minutes=30),10125,10175,10150),
            bar(t+timedelta(days=1,minutes=60),10200,10300,10200),
            bar(t+timedelta(days=1,minutes=90),10200,10300,10200),
        ]
        got=build_session_snapshots(bars,atr_period=1)
        self.assertEqual([x["price_state"] for x in got],[
            "DISCOVERY_CANDIDATE_ABOVE","VALUE_EXCURSION",
            "DISCOVERY_CANDIDATE_ABOVE","DISCOVERY_CONFIRMED_ABOVE",
        ])


    def test_midpoint_poc_tie_break(self):
        p=calculate_tpo_profile([bar(datetime(2026,1,1,tzinfo=UTC),100,200)])
        self.assertEqual(p.poc,175)

    def test_reentry_requires_two_completed_closes_inside_value(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=30),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=60),10000,10100,10050),
            bar(t+timedelta(days=1,minutes=90),10000,10100,10050),
        ]
        got=build_session_snapshots(bars,atr_period=1)
        self.assertEqual([x["price_state"] for x in got],[
            "DISCOVERY_CANDIDATE_ABOVE","DISCOVERY_CONFIRMED_ABOVE",
            "REENTRY_CANDIDATE","REENTRY_CONFIRMED",
        ])


    def test_missing_bar_breaks_discovery_confirmation(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=60),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=90),10250,10300,10275),
        ]
        got=build_session_snapshots(bars,atr_period=1)
        self.assertEqual([x["price_state"] for x in got],[
            "DISCOVERY_CANDIDATE_ABOVE","DISCOVERY_CANDIDATE_ABOVE",
            "DISCOVERY_CONFIRMED_ABOVE",
        ])
        self.assertTrue(got[1]["gap_before"])

    def test_two_completed_closes_confirm_discovery(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=30),10250,10300,10275),
        ]
        got=build_session_snapshots(bars,atr_period=1)
        self.assertEqual([x["price_state"] for x in got],[
            "DISCOVERY_CANDIDATE_ABOVE","DISCOVERY_CONFIRMED_ABOVE",
        ])
        self.assertEqual(got[0]["prior_profile_quality"],"COMPLETE")

if __name__=="__main__":
    unittest.main()
