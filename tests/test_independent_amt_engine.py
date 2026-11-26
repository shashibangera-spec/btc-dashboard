import unittest
import json
from pathlib import Path
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

    def test_zero_duration_candle_rejected(self):
        row={"open_time":"2026-01-01T00:00:00Z","close_time":"2026-01-01T00:00:00Z",
             "open_price":100,"high_price":110,"low_price":90,"close_price":105}
        with self.assertRaises(AMTInputError):
            Candle.from_record(row)

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
        # Isolate the fixed $50 floor: the first breakout candle's true range
        # includes the overnight gap and would otherwise raise the ATR buffer.
        got=build_session_snapshots(bars,atr_period=1,atr_multiplier=0)
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

    def test_repeated_confirmed_label_is_not_a_new_discovery_episode(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=30),10250,10300,10275),
            bar(t+timedelta(days=1,minutes=60),10000,10100,10050),
            bar(t+timedelta(days=1,minutes=90),10250,10300,10275),
        ]
        got=build_session_snapshots(bars,atr_period=1,atr_multiplier=0)
        self.assertEqual([x["price_state"] for x in got],[
            "DISCOVERY_CANDIDATE_ABOVE","DISCOVERY_CONFIRMED_ABOVE",
            "REENTRY_CANDIDATE","DISCOVERY_CONFIRMED_ABOVE",
        ])
        self.assertEqual([x["discovery_event_start"] for x in got],[False,True,False,False])

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

    def test_initial_upside_breakout_is_not_mislabeled_as_retest(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),10000,11100,11000),
            bar(t+timedelta(days=1,minutes=30),10000,11100,11000),
        ]
        got=build_session_snapshots(bars,atr_period=1)
        self.assertIsNone(got[0]["boundary_retest"])
        self.assertEqual(got[1]["boundary_retest"],"RETEST_RECLAIM_ABOVE")

    def test_initial_downside_breakout_is_not_mislabeled_as_retest(self):
        t=datetime(2026,1,1,0,tzinfo=UTC)
        bars=[bar(t+timedelta(minutes=30*i),10000,10100,10050) for i in range(48)]
        bars += [
            bar(t+timedelta(days=1),8900,10100,9000),
            bar(t+timedelta(days=1,minutes=30),8900,10100,9000),
        ]
        got=build_session_snapshots(bars,atr_period=1)
        self.assertIsNone(got[0]["boundary_retest"])
        self.assertEqual(got[1]["boundary_retest"],"RETEST_REJECTION_BELOW")

    def test_balance_evidence_requires_two_poc_side_crossings(self):
        t = datetime(2026, 1, 1, 0, tzinfo=UTC)
        prior = [bar(t + timedelta(minutes=30*i), 9900, 10100, 10000) for i in range(48)]
        current = [
            bar(t + timedelta(days=1), 10050, 10050, 10050),
            bar(t + timedelta(days=1, minutes=30), 9950, 9950, 9950),
            bar(t + timedelta(days=1, minutes=60), 10050, 10050, 10050),
            bar(t + timedelta(days=1, minutes=90), 9950, 9950, 9950),
        ]
        got = build_session_snapshots(prior + current, atr_period=1)
        self.assertEqual([x["poc_side_crossings_in_5"] for x in got], [0, 1, 2, 3])
        self.assertEqual([x["balance_evidence"] for x in got], [False, False, True, True])
        self.assertTrue(all(x["price_state"] == "IN_VALUE" for x in got))

    def test_first_observed_bar_after_session_reset_is_marked_gap(self):
        t = datetime(2026, 1, 1, 0, tzinfo=UTC)
        prior = [bar(t + timedelta(minutes=30*i), 9900, 10100, 10000) for i in range(48)]
        current = [
            bar(t + timedelta(days=1, minutes=30), 10050, 10050, 10050),
            bar(t + timedelta(days=1, minutes=60), 9950, 9950, 9950),
        ]
        got = build_session_snapshots(prior + current, atr_period=1)
        self.assertTrue(got[0]["session_start_gap"])
        self.assertTrue(got[0]["gap_before"])
        self.assertFalse(got[1]["session_start_gap"])
        self.assertFalse(got[1]["gap_before"])

    def test_gap_resets_balance_crossing_window(self):
        t = datetime(2026, 1, 1, 0, tzinfo=UTC)
        prior = [bar(t + timedelta(minutes=30*i), 9900, 10100, 10000) for i in range(48)]
        current = [
            bar(t + timedelta(days=1), 10050, 10050, 10050),
            bar(t + timedelta(days=1, minutes=30), 9950, 9950, 9950),
            bar(t + timedelta(days=1, minutes=90), 10050, 10050, 10050),
            bar(t + timedelta(days=1, minutes=120), 9950, 9950, 9950),
        ]
        got = build_session_snapshots(prior + current, atr_period=1)
        self.assertEqual([x["poc_side_crossings_in_5"] for x in got], [0, 1, 0, 1])
        self.assertFalse(any(x["balance_evidence"] for x in got))
        self.assertTrue(got[2]["gap_before"])

    def test_historical_raw_archive_replay_counts(self):
        fixture = Path(__file__).parent / "fixtures" / "btcusdt_spot_30m_2026-11-01_2026-11-26.json"
        data = json.loads(fixture.read_text(encoding="utf-8"))
        candles = normalize_candles(
            data["candles"],
            datetime(2026, 11, 26, 7, 0, tzinfo=UTC),
        )
        self.assertEqual(len(candles), 1212)

        snapshots = build_session_snapshots(candles)
        self.assertEqual(len(snapshots), 1164)
        self.assertTrue(all(x["prior_profile_quality"] == "COMPLETE" for x in snapshots))
        self.assertFalse(any(x["gap_before"] for x in snapshots))

        # Emit a reproducible episode ledger with same-session outcome timing.
        episode_rows = [(i, x) for i, x in enumerate(snapshots) if x["discovery_event_start"]]
        rapid_reentries = 0
        no_reentry_by_session_end = 0
        opposite_discovery_transitions = 0
        for event_number, (i, event) in enumerate(episode_rows):
            end = len(snapshots)
            for k in range(i + 1, len(snapshots)):
                if snapshots[k]["session_date_ist"] != event["session_date_ist"]:
                    end = k
                    break
                if snapshots[k]["discovery_event_start"]:
                    end = k
                    break
            segment = snapshots[i:end]
            reentry_offsets = [k for k, x in enumerate(segment) if k > 0 and x["price_state"] == "REENTRY_CONFIRMED"]
            direction = "ABOVE" if event["price_state"].endswith("_ABOVE") else "BELOW"
            opposite_direction = "BELOW" if direction == "ABOVE" else "ABOVE"
            # The next discovery event is the exclusive end of this episode's
            # observation window. Inspect that boundary row separately so an
            # opposite-side transition is not silently excluded from the ledger.
            next_event = snapshots[end] if end < len(snapshots) else None
            opposite_at = (
                end - i if next_event is not None
                and next_event["session_date_ist"] == event["session_date_ist"]
                and next_event["discovery_event_start"]
                and next_event["price_state"] == f"DISCOVERY_CONFIRMED_{opposite_direction}"
                else None
            )
            if opposite_at is not None:
                opposite_discovery_transitions += 1
            separation_offsets = [k for k, x in enumerate(segment)
                                  if x["value_state"] == f"VALUE_SEPARATED_{direction}"]
            reentry_at = reentry_offsets[0] if reentry_offsets else None
            separation_at = separation_offsets[0] if separation_offsets else None
            if reentry_at is not None and reentry_at <= 4:
                rapid_reentries += 1
            if reentry_at is None:
                no_reentry_by_session_end += 1
            print(
                "EPISODE",
                event["candle_open_time_utc"],
                event["price_state"],
                "close=", event["close"],
                "prior=", (event["prior_value"]["vah"], event["prior_value"]["poc"], event["prior_value"]["val"]),
                "developing=", (event["developing_value"]["vah"], event["developing_value"]["poc"], event["developing_value"]["val"]),
                "value_state=", event["value_state"],
                "bars_until_reentry=", reentry_at,
                "bars_until_opposite_discovery=", opposite_at,
                "bars_until_value_separation=", separation_at,
                "final_value_state=", segment[-1]["value_state"],
                "bars_in_episode_window=", len(segment),
                "last_state=", segment[-1]["price_state"],
            )
        separated_after_confirmation = sum(
            any(x["value_state"] == ("VALUE_SEPARATED_ABOVE" if event["price_state"].endswith("_ABOVE")
                                     else "VALUE_SEPARATED_BELOW")
                for x in snapshots[i:end])
            for i, event in episode_rows
            for end in [next((k for k in range(i+1, len(snapshots))
                              if snapshots[k]["session_date_ist"] != event["session_date_ist"]
                              or snapshots[k]["discovery_event_start"]), len(snapshots))]
        )
        print("EPISODE_SUMMARY", "rapid_reentries_within_4_bars=", rapid_reentries,
              "no_reentry_by_session_end_or_next_episode=", no_reentry_by_session_end,
              "episodes_with_same_side_value_separation=", separated_after_confirmation,
              "opposite_discovery_transitions=", opposite_discovery_transitions)
        self.assertEqual(rapid_reentries, 5)
        self.assertEqual(opposite_discovery_transitions, 2)
        self.assertEqual(no_reentry_by_session_end, 13)
        self.assertEqual(separated_after_confirmation, 12)
        events = [x for x in snapshots if x["discovery_event_start"]]
        self.assertEqual(sum(
            x["value_state"] == ("VALUE_SEPARATED_ABOVE" if x["price_state"].endswith("_ABOVE")
                                 else "VALUE_SEPARATED_BELOW")
            for x in events
        ), 7)
        self.assertEqual(len(events), 30)
        self.assertEqual(sum(x["price_state"].endswith("_ABOVE") for x in events), 6)
        self.assertEqual(sum(x["price_state"].endswith("_BELOW") for x in events), 24)
        self.assertEqual(sum(x["price_state"] == "REENTRY_CONFIRMED" for x in snapshots), 17)
        self.assertEqual(sum(x["boundary_retest"] is not None for x in snapshots), 53)
        retest_rows = [(i, x["boundary_retest"]) for i, x in enumerate(snapshots)
                       if x["boundary_retest"] is not None]
        retest_sequences = [
            (i, label) for n, (i, label) in enumerate(retest_rows)
            if n == 0 or retest_rows[n-1][1] != label or retest_rows[n-1][0] != i-1
        ]
        print("RETEST_SUMMARY", "labelled_candles=", len(retest_rows),
              "sequence_starts=", len(retest_sequences),
              "above_sequence_starts=", sum(label == "RETEST_RECLAIM_ABOVE" for _, label in retest_sequences),
              "below_sequence_starts=", sum(label == "RETEST_REJECTION_BELOW" for _, label in retest_sequences))
        self.assertEqual(len(retest_sequences), 35)
        self.assertEqual(sum(label == "RETEST_RECLAIM_ABOVE" for _, label in retest_sequences), 6)
        self.assertEqual(sum(label == "RETEST_REJECTION_BELOW" for _, label in retest_sequences), 29)
        for i, label in retest_sequences:
            item = snapshots[i]
            prior = item["prior_value"]
            boundary = prior["vah"] if label == "RETEST_RECLAIM_ABOVE" else prior["val"]
            print("RETEST", item["candle_open_time_utc"], label,
                  "boundary=", boundary, "low=", item["low"],
                  "high=", item["high"], "close=", item["close"])
        self.assertEqual(sum(x["balance_evidence"] for x in snapshots), 0)
        index_by_time = {x["candle_open_time_utc"]: i for i, x in enumerate(snapshots)}

        def state_at(open_time, offset=0):
            return snapshots[index_by_time[open_time] + offset]["price_state"]

        # A downside confirmation that quickly re-entered and later reversed.
        nov4 = "2026-11-04T03:00:00+00:00"
        self.assertEqual(state_at(nov4), "DISCOVERY_CONFIRMED_BELOW")
        self.assertEqual(state_at(nov4, 1), "VALUE_EXCURSION")
        self.assertEqual(state_at(nov4, 2), "REENTRY_CANDIDATE")
        self.assertEqual(state_at(nov4, 3), "REENTRY_CONFIRMED")
        self.assertEqual(state_at(nov4, 8), "DISCOVERY_CONFIRMED_ABOVE")

        # Two other fast re-entry examples on opposite directions.
        nov17 = "2026-11-17T10:30:00+00:00"
        self.assertEqual(state_at(nov17, 1), "REENTRY_CANDIDATE")
        self.assertEqual(state_at(nov17, 2), "REENTRY_CONFIRMED")
        nov22 = "2026-11-22T16:30:00+00:00"
        self.assertEqual(state_at(nov22, 1), "REENTRY_CANDIDATE")
        self.assertEqual(state_at(nov22, 2), "REENTRY_CONFIRMED")

        # Contrasting sustained downside/upside discovery examples.
        nov21 = "2026-11-21T01:30:00+00:00"
        self.assertEqual(state_at(nov21, 8), "DISCOVERY_CONFIRMED_BELOW")
        nov26 = "2026-11-26T00:30:00+00:00"
        self.assertEqual(state_at(nov26, 8), "DISCOVERY_CONFIRMED_ABOVE")

if __name__=="__main__":
    unittest.main()
