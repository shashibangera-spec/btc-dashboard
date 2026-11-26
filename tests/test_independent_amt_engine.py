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
        aligned_at_confirmation = 0
        aligned_within_4_bars = 0
        aligned_any_window = 0
        opposite_value_any_window = 0
        persistence_rows = []
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
            value_direction = "UP" if direction == "ABOVE" else "DOWN"
            opposite_value_direction = "DOWN" if value_direction == "UP" else "UP"
            aligned_states = {
                f"BROAD_MIGRATION_{value_direction}", f"POC_MIGRATION_{value_direction}",
                f"PARTIAL_MIGRATION_{value_direction}", f"VALUE_SEPARATED_{direction}",
            }
            opposite_states = {
                f"BROAD_MIGRATION_{opposite_value_direction}", f"POC_MIGRATION_{opposite_value_direction}",
                f"PARTIAL_MIGRATION_{opposite_value_direction}", f"VALUE_SEPARATED_{opposite_direction}",
            }
            alignment_offsets = [k for k, x in enumerate(segment) if x["value_state"] in aligned_states]
            opposite_value_offsets = [k for k, x in enumerate(segment) if x["value_state"] in opposite_states]
            separation_offsets = [k for k, x in enumerate(segment)
                                  if x["value_state"] == f"VALUE_SEPARATED_{direction}"]
            reentry_at = reentry_offsets[0] if reentry_offsets else None
            separation_at = separation_offsets[0] if separation_offsets else None
            alignment_at = alignment_offsets[0] if alignment_offsets else None

            # Keep discovery-active behaviour separate from post-re-entry
            # profile drift. The confirmed re-entry candle begins the latter.
            active_end = reentry_at if reentry_at is not None else len(segment)
            active_segment = segment[:active_end]
            post_reentry_segment = segment[reentry_at:] if reentry_at is not None else []
            active_same_offsets = [
                k for k, x in enumerate(active_segment)
                if x["value_state"] in aligned_states
            ]
            active_opposite_offsets = [
                k for k, x in enumerate(active_segment)
                if x["value_state"] in opposite_states
            ]
            post_same_offsets = [
                k for k, x in enumerate(post_reentry_segment, start=reentry_at or 0)
                if x["value_state"] in aligned_states
            ]
            post_opposite_offsets = [
                k for k, x in enumerate(post_reentry_segment, start=reentry_at or 0)
                if x["value_state"] in opposite_states
            ]
            first_four_after = segment[1:5]
            same_in_first_four_after = any(x["value_state"] in aligned_states
                                           for x in first_four_after)
            def level_move(row, key):
                current = row["developing_value"][key]
                prior = row["prior_value"][key]
                return "UP" if current > prior else "DOWN" if current < prior else "FLAT"

            def level_signature(row):
                return tuple(level_move(row, key) for key in ("vah", "poc", "val"))

            confirmation_signature = level_signature(event)
            def relative_signature(signature):
                return tuple(
                    "WITH" if move == value_direction else
                    "AGAINST" if move != "FLAT" else "FLAT"
                    for move in signature
                )
            first_same_row = (
                active_segment[active_same_offsets[0]] if active_same_offsets else None
            )
            first_same_signature = level_signature(first_same_row) if first_same_row else None
            active_relative_signatures = [relative_signature(level_signature(row))
                                          for row in active_segment]
            active_poc_only_offsets = [
                k for k, sig in enumerate(active_relative_signatures)
                if sig[1] == "WITH" and sig[0] != "WITH" and sig[2] != "WITH"
            ]
            active_poc_one_edge_offsets = [
                k for k, sig in enumerate(active_relative_signatures)
                if sig[1] == "WITH" and (sig[0] == "WITH") != (sig[2] == "WITH")
            ]
            active_poc_full_offsets = [
                k for k, sig in enumerate(active_relative_signatures)
                if sig == ("WITH", "WITH", "WITH")
            ]
            full_first_four_offsets = [k for k in active_poc_full_offsets if 1 <= k <= 4]
            full_after_first_four_offsets = [k for k in active_poc_full_offsets if k >= 5]
            active_bars_after_first_four = max(0, len(active_segment) - 5)
            # Descriptive price-path audit: compare completed-candle close
            # progress on broad-alignment bars with the immediately preceding
            # completed close. Positive means movement in discovery direction.
            aligned_price_steps = []
            other_price_steps = []
            for offset, row in enumerate(active_segment):
                previous_close = snapshots[i + offset - 1]["close"] if i + offset > 0 else None
                if previous_close is None:
                    continue
                signed_step = (
                    row["close"] - previous_close
                    if direction == "ABOVE" else previous_close - row["close"]
                )
                if row["value_state"] in {
                    f"BROAD_MIGRATION_{value_direction}",
                    f"VALUE_SEPARATED_{direction}",
                }:
                    aligned_price_steps.append(signed_step)
                else:
                    other_price_steps.append(signed_step)
            active_edges_with_poc_not_offsets = [
                k for k, sig in enumerate(active_relative_signatures)
                if sig[1] != "WITH" and (sig[0] == "WITH" or sig[2] == "WITH")
            ]
            active_poc_only = bool(active_poc_only_offsets)
            active_poc_one_edge = bool(active_poc_one_edge_offsets)
            active_poc_full = bool(active_poc_full_offsets)
            active_edges_with_poc_not = bool(active_edges_with_poc_not_offsets)
            persistence_rows.append({
                "confirmation_signature": confirmation_signature,
                "confirmation_relative_signature": relative_signature(confirmation_signature),
                "first_same_signature": first_same_signature,
                "first_same_relative_signature": relative_signature(first_same_signature)
                    if first_same_signature else None,
                "active_poc_only": active_poc_only,
                "active_poc_one_edge": active_poc_one_edge,
                "active_poc_full": active_poc_full,
                "full_at_confirmation": 0 in active_poc_full_offsets,
                "full_within_first_four_after": bool(full_first_four_offsets),
                "full_after_first_four": bool(full_after_first_four_offsets),
                "full_first_offset": active_poc_full_offsets[0]
                    if active_poc_full_offsets else None,
                "full_first_offset_after_four": full_after_first_four_offsets[0]
                    if full_after_first_four_offsets else None,
                "active_full_bar_count": len(active_poc_full_offsets),
                "active_bar_count": len(active_segment),
                "active_bars_after_first_four": active_bars_after_first_four,
                "active_full_bars_after_first_four": len(full_after_first_four_offsets),
                "aligned_price_steps": aligned_price_steps,
                "other_price_steps": other_price_steps,
                "aligned_price_net": round(sum(aligned_price_steps), 2),
                "other_price_net": round(sum(other_price_steps), 2),
                "active_edges_with_poc_not": active_edges_with_poc_not,
                "active_poc_one_edge_first": active_poc_one_edge_offsets[0]
                    if active_poc_one_edge_offsets else None,
                "active_edges_without_poc_first": active_edges_with_poc_not_offsets[0]
                    if active_edges_with_poc_not_offsets else None,
                "event_time": event["candle_open_time_utc"],
                "event_direction": direction,
                "reentry_at": reentry_at,
                "rapid": reentry_at is not None and reentry_at <= 4,
                "slow_reentry": reentry_at is not None and reentry_at > 4,
                "no_reentry": reentry_at is None,
                "same_at_confirmation": event["value_state"] in aligned_states,
                "same_first_four_after": same_in_first_four_after,
                "same_while_active": bool(active_same_offsets),
                "opposite_while_active": bool(active_opposite_offsets),
                "active_reversal": bool(active_same_offsets and active_opposite_offsets
                                        and min(active_same_offsets) < min(active_opposite_offsets)),
                "active_opposite_to_same_recovery": bool(
                    active_same_offsets and active_opposite_offsets
                    and min(active_opposite_offsets) < min(active_same_offsets)
                ),
                "same_after_reentry": bool(post_same_offsets),
                "opposite_after_reentry": bool(post_opposite_offsets),
            })
            # Focused candle-by-candle trace for the three active opposite-
            # migration episodes, the two no-alignment cases, and the
            # 16 Nov post-re-entry opposite migration case.
            focus_times = {
                "2026-11-02T16:00:00+00:00",
                "2026-11-04T06:30:00+00:00",
                "2026-11-08T21:30:00+00:00",
                "2026-11-16T12:30:00+00:00",
            }
            if event["candle_open_time_utc"] in focus_times:
                for offset, row in enumerate(segment):
                    print(
                        "FOCUS_CANDLE", event["candle_open_time_utc"],
                        "offset=", offset,
                        "time=", row["candle_open_time_utc"],
                        "price_state=", row["price_state"],
                        "ohlc=", (row["open"], row["high"], row["low"], row["close"]),
                        "value_state=", row["value_state"],
                        "developing=",
                        (row["developing_value"]["vah"], row["developing_value"]["poc"],
                         row["developing_value"]["val"]),
                    )
            print(
                "VALUE_PERSISTENCE",
                event["candle_open_time_utc"],
                "reentry_at=", reentry_at,
                "first_same_active=", active_same_offsets[0] if active_same_offsets else None,
                "first_opposite_active=", active_opposite_offsets[0] if active_opposite_offsets else None,
                "same_after_reentry=", post_same_offsets[0] if post_same_offsets else None,
                "opposite_after_reentry=", post_opposite_offsets[0] if post_opposite_offsets else None,
                "same_first_four_after=", same_in_first_four_after,
            )
            if event["value_state"] in aligned_states:
                aligned_at_confirmation += 1
            if alignment_at is not None:
                aligned_any_window += 1
                if alignment_at <= 4:
                    aligned_within_4_bars += 1
            if opposite_value_offsets:
                opposite_value_any_window += 1
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
                "bars_until_directional_value_alignment=", alignment_at,
                "bars_until_value_separation=", separation_at,
                "opposite_value_alignment_seen=", bool(opposite_value_offsets),
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
        from collections import Counter
        confirmation_signatures = Counter(x["confirmation_signature"] for x in persistence_rows)
        first_same_signatures = Counter(x["first_same_signature"] for x in persistence_rows
                                        if x["first_same_signature"] is not None)
        confirmation_relative = Counter(x["confirmation_relative_signature"] for x in persistence_rows)
        first_same_relative = Counter(x["first_same_relative_signature"] for x in persistence_rows
                                      if x["first_same_relative_signature"] is not None)
        print("LEVEL_DIRECTION_AT_CONFIRMATION", sorted(confirmation_signatures.items()))
        print("LEVEL_DIRECTION_RELATIVE_TO_DISCOVERY_AT_CONFIRMATION", sorted(confirmation_relative.items()))
        print("LEVEL_DIRECTION_AT_FIRST_SAME_MIGRATION", sorted(first_same_signatures.items()))
        print("LEVEL_DIRECTION_RELATIVE_TO_DISCOVERY_AT_FIRST_SAME_MIGRATION", sorted(first_same_relative.items()))
        print("BROAD_MIGRATION_TIMING",
              "full_at_confirmation=", sum(x["full_at_confirmation"] for x in persistence_rows),
              "full_within_first_four_after=", sum(x["full_within_first_four_after"] for x in persistence_rows),
              "full_after_first_four=", sum(x["full_after_first_four"] for x in persistence_rows),
              "full_any_active=", sum(x["active_poc_full"] for x in persistence_rows))
        print("BROAD_MIGRATION_AFTER_FOUR_DETAILS", [
            (x["event_time"], x["full_first_offset"], x["full_first_offset_after_four"],
             x["active_full_bar_count"], x["active_bar_count"],
             x["active_full_bars_after_first_four"], x["active_bars_after_first_four"])
            for x in persistence_rows if x["full_after_first_four"]
        ])
        for label, rows in (
            ("BROAD_ALIGNED", [x for x in persistence_rows]),
        ):
            aligned_steps = [step for x in rows for step in x["aligned_price_steps"]]
            other_steps = [step for x in rows for step in x["other_price_steps"]]
            def step_summary(values):
                return {
                    "n": len(values),
                    "with_direction": sum(v > 0 for v in values),
                    "against_direction": sum(v < 0 for v in values),
                    "unchanged": sum(v == 0 for v in values),
                    "net_signed_close_change": round(sum(values), 2),
                    "median_signed_step": round(sorted(values)[len(values)//2], 2) if values else None,
                }
            print("PRICE_PROGRESS_BY_VALUE_STATE", label,
                  "broad_aligned=", step_summary(aligned_steps),
                  "other_active_states=", step_summary(other_steps))
        broad_episode_nets = [x["aligned_price_net"] for x in persistence_rows
                              if x["aligned_price_steps"]]
        other_episode_nets = [x["other_price_net"] for x in persistence_rows
                              if x["other_price_steps"]]
        print("PRICE_PROGRESS_EPISODE_NETS",
              "broad_aligned=", {
                  "episodes": len(broad_episode_nets),
                  "positive": sum(v > 0 for v in broad_episode_nets),
                  "negative": sum(v < 0 for v in broad_episode_nets),
                  "zero": sum(v == 0 for v in broad_episode_nets),
                  "median": sorted(broad_episode_nets)[len(broad_episode_nets)//2]
                      if broad_episode_nets else None,
              },
              "other_active_states=", {
                  "episodes": len(other_episode_nets),
                  "positive": sum(v > 0 for v in other_episode_nets),
                  "negative": sum(v < 0 for v in other_episode_nets),
                  "zero": sum(v == 0 for v in other_episode_nets),
                  "median": sorted(other_episode_nets)[len(other_episode_nets)//2]
                      if other_episode_nets else None,
              })
        print("BROAD_MIGRATION_ACTIVE_BAR_TOTALS",
              "full_active_bars=", sum(x["active_full_bar_count"] for x in persistence_rows),
              "all_active_bars=", sum(x["active_bar_count"] for x in persistence_rows),
              "full_after_four_bars=", sum(x["active_full_bars_after_first_four"] for x in persistence_rows),
              "bars_after_four=", sum(x["active_bars_after_first_four"] for x in persistence_rows))
        print("ACTIVE_PROFILE_SHAPE_COUNTS",
              "poc_only=", sum(x["active_poc_only"] for x in persistence_rows),
              "poc_plus_one_edge=", sum(x["active_poc_one_edge"] for x in persistence_rows),
              "poc_plus_both_edges=", sum(x["active_poc_full"] for x in persistence_rows),
              "edge_alignment_without_poc=", sum(x["active_edges_with_poc_not"] for x in persistence_rows))
        print("ACTIVE_PROFILE_SHAPE_EPISODES_POC_PLUS_ONE_EDGE", [
            (x["event_time"], x["event_direction"], x["active_poc_one_edge_first"])
            for x in persistence_rows if x["active_poc_one_edge"]
        ])
        print("ACTIVE_PROFILE_SHAPE_EPISODES_EDGES_WITHOUT_POC", [
            (x["event_time"], x["event_direction"], x["active_edges_without_poc_first"])
            for x in persistence_rows if x["active_edges_with_poc_not"]
        ])
        for x in persistence_rows:
            for label, offset in (
                ("POC_PLUS_ONE_EDGE", x["active_poc_one_edge_first"]),
                ("EDGES_WITHOUT_POC", x["active_edges_without_poc_first"]),
            ):
                if offset is not None:
                    row = snapshots[next(i for i, candidate in enumerate(snapshots)
                                          if candidate["candle_open_time_utc"] == x["event_time"])]
                    event_index = snapshots.index(row)
                    trace = snapshots[event_index + offset]
                    direction_for_event = "UP" if x["event_direction"] == "ABOVE" else "DOWN"
                    signature_for_trace = level_signature(trace)
                    relative_for_trace = tuple(
                        "WITH" if move == direction_for_event else
                        "AGAINST" if move != "FLAT" else "FLAT"
                        for move in signature_for_trace
                    )
                    print("ACTIVE_PROFILE_SHAPE_DETAIL", label, x["event_time"],
                          "offset=", offset, "time=", trace["candle_open_time_utc"],
                          "state=", trace["value_state"],
                          "relative=", relative_for_trace,
                          "prior=", (trace["prior_value"]["vah"], trace["prior_value"]["poc"],
                                     trace["prior_value"]["val"]),
                          "developing=", (trace["developing_value"]["vah"],
                                          trace["developing_value"]["poc"],
                                          trace["developing_value"]["val"]))
        for cohort_name, cohort in (
            ("RAPID_REENTRY", [x for x in persistence_rows if x["rapid"]]),
            ("SLOW_REENTRY", [x for x in persistence_rows if x["slow_reentry"]]),
            ("NO_REENTRY_IN_WINDOW", [x for x in persistence_rows if x["no_reentry"]]),
            ("LONGER_OR_NO_REENTRY_COMBINED", [x for x in persistence_rows if not x["rapid"]]),
        ):
            print("VALUE_PERSISTENCE_COHORT", cohort_name, "n=", len(cohort),
                  "same_at_confirmation=", sum(x["same_at_confirmation"] for x in cohort),
                  "same_first_four_after=", sum(x["same_first_four_after"] for x in cohort),
                  "same_while_active=", sum(x["same_while_active"] for x in cohort),
                  "opposite_while_active=", sum(x["opposite_while_active"] for x in cohort),
                  "active_same_to_opposite_reversals=", sum(x["active_reversal"] for x in cohort),
                  "active_opposite_to_same_recoveries=", sum(x["active_opposite_to_same_recovery"] for x in cohort),
                  "same_after_reentry=", sum(x["same_after_reentry"] for x in cohort),
                  "opposite_after_reentry=", sum(x["opposite_after_reentry"] for x in cohort),
                  "broad_after_first_four_episodes=", sum(x["full_after_first_four"] for x in cohort),
                  "broad_active_bars_after_four=", sum(x["active_full_bars_after_first_four"] for x in cohort),
                  "eligible_active_bars_after_four=", sum(x["active_bars_after_first_four"] for x in cohort))
        print("EPISODE_SUMMARY", "rapid_reentries_within_4_bars=", rapid_reentries,
              "no_reentry_by_session_end_or_next_episode=", no_reentry_by_session_end,
              "episodes_with_same_side_value_separation=", separated_after_confirmation,
              "opposite_discovery_transitions=", opposite_discovery_transitions)
        print("VALUE_ALIGNMENT_SUMMARY", "aligned_at_confirmation=", aligned_at_confirmation,
              "aligned_within_4_bars=", aligned_within_4_bars,
              "aligned_any_time_in_window=", aligned_any_window,
              "opposite_value_alignment_any_time=", opposite_value_any_window,
              "no_directional_value_alignment=", len(episode_rows)-aligned_any_window)
        self.assertEqual(rapid_reentries, 5)
        self.assertEqual(opposite_discovery_transitions, 2)
        self.assertEqual(no_reentry_by_session_end, 13)
        self.assertEqual(separated_after_confirmation, 12)
        self.assertEqual(aligned_at_confirmation, 27)
        self.assertEqual(aligned_within_4_bars, 27)
        self.assertEqual(aligned_any_window, 28)
        self.assertEqual(opposite_value_any_window, 4)
        self.assertEqual(sum(x["same_while_active"] for x in persistence_rows), 28)
        self.assertEqual(sum(x["opposite_while_active"] for x in persistence_rows), 3)
        self.assertEqual(sum(x["active_reversal"] for x in persistence_rows), 0)
        self.assertEqual(sum(x["active_opposite_to_same_recovery"] for x in persistence_rows), 1)
        self.assertEqual(sum(x["same_after_reentry"] for x in persistence_rows), 17)
        self.assertEqual(sum(x["opposite_after_reentry"] for x in persistence_rows), 1)
        self.assertEqual(sum(x["active_poc_only"] for x in persistence_rows), 0)
        self.assertEqual(sum(x["full_at_confirmation"] for x in persistence_rows), 24)
        self.assertEqual(sum(x["full_within_first_four_after"] for x in persistence_rows), 27)
        self.assertEqual(sum(x["full_after_first_four"] for x in persistence_rows), 21)
        self.assertEqual(sum(x["active_full_bar_count"] for x in persistence_rows), 502)
        self.assertEqual(sum(x["active_bar_count"] for x in persistence_rows), 579)
        self.assertEqual(sum(x["active_full_bars_after_first_four"] for x in persistence_rows), 385)
        self.assertEqual(sum(x["active_bars_after_first_four"] for x in persistence_rows), 440)
        self.assertEqual(sum(x["active_poc_one_edge"] for x in persistence_rows), 5)
        self.assertEqual(sum(x["active_poc_full"] for x in persistence_rows), 28)
        self.assertEqual(sum(x["active_edges_with_poc_not"] for x in persistence_rows), 3)
        self.assertEqual(
            sorted(x["event_time"] for x in persistence_rows if x["active_poc_one_edge"]),
            ["2026-11-04T06:30:00+00:00", "2026-11-12T11:30:00+00:00",
             "2026-11-16T12:30:00+00:00", "2026-11-17T16:00:00+00:00",
             "2026-11-18T14:00:00+00:00"],
        )
        self.assertEqual(
            sorted(x["event_time"] for x in persistence_rows if x["active_edges_with_poc_not"]),
            ["2026-11-02T16:00:00+00:00", "2026-11-04T06:30:00+00:00",
             "2026-11-08T21:30:00+00:00"],
        )
        self.assertEqual(confirmation_signatures[("DOWN", "DOWN", "DOWN")], 20)
        self.assertEqual(confirmation_signatures[("DOWN", "DOWN", "FLAT")], 1)
        self.assertEqual(confirmation_signatures[("DOWN", "DOWN", "UP")], 2)
        self.assertEqual(confirmation_signatures[("FLAT", "UP", "DOWN")], 1)
        self.assertEqual(confirmation_signatures[("UP", "UP", "FLAT")], 1)
        self.assertEqual(confirmation_signatures[("UP", "UP", "UP")], 5)
        self.assertEqual(confirmation_relative[("WITH", "WITH", "WITH")], 24)
        self.assertEqual(confirmation_relative[("WITH", "WITH", "AGAINST")], 2)
        self.assertEqual(confirmation_relative[("WITH", "WITH", "FLAT")], 1)
        self.assertEqual(confirmation_relative[("FLAT", "AGAINST", "WITH")], 1)
        self.assertEqual(confirmation_relative[("AGAINST", "AGAINST", "AGAINST")], 1)
        self.assertEqual(confirmation_relative[("AGAINST", "AGAINST", "FLAT")], 1)
        rapid_cohort = [x for x in persistence_rows if x["rapid"]]
        slow_cohort = [x for x in persistence_rows if x["slow_reentry"]]
        no_reentry_cohort = [x for x in persistence_rows if x["no_reentry"]]
        longer_cohort = [x for x in persistence_rows if not x["rapid"]]
        self.assertEqual(len(rapid_cohort), 5)
        self.assertEqual(len(slow_cohort), 12)
        self.assertEqual(len(no_reentry_cohort), 13)
        self.assertEqual(sum(x["same_at_confirmation"] for x in rapid_cohort), 5)
        self.assertEqual(sum(x["same_first_four_after"] for x in rapid_cohort), 5)
        self.assertEqual(sum(x["same_while_active"] for x in rapid_cohort), 5)
        self.assertEqual(sum(x["opposite_while_active"] for x in rapid_cohort), 0)
        self.assertEqual(len(longer_cohort), 25)
        self.assertEqual(sum(x["same_at_confirmation"] for x in longer_cohort), 22)
        self.assertEqual(sum(x["same_first_four_after"] for x in longer_cohort), 22)
        self.assertEqual(sum(x["same_while_active"] for x in longer_cohort), 23)
        self.assertEqual(sum(x["opposite_while_active"] for x in longer_cohort), 3)
        self.assertEqual(sum(x["same_at_confirmation"] for x in slow_cohort), 12)
        self.assertEqual(sum(x["same_first_four_after"] for x in slow_cohort), 12)
        self.assertEqual(sum(x["same_while_active"] for x in slow_cohort), 12)
        self.assertEqual(sum(x["opposite_while_active"] for x in slow_cohort), 0)
        self.assertEqual(sum(x["same_after_reentry"] for x in slow_cohort), 12)
        self.assertEqual(sum(x["same_at_confirmation"] for x in no_reentry_cohort), 10)
        self.assertEqual(sum(x["same_first_four_after"] for x in no_reentry_cohort), 10)
        self.assertEqual(sum(x["same_while_active"] for x in no_reentry_cohort), 11)
        self.assertEqual(sum(x["opposite_while_active"] for x in no_reentry_cohort), 3)
        self.assertEqual(sum(x["active_opposite_to_same_recovery"] for x in no_reentry_cohort), 1)
        self.assertEqual(sum(x["same_after_reentry"] for x in no_reentry_cohort), 0)
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



class HistoricalPricePathAuditTests(unittest.TestCase):
    def test_all_discovery_episodes_price_path_and_acceptance(self):
        fixture_path = Path(__file__).parent / "fixtures" / "btcusdt_spot_30m_2026-11-01_2026-11-26.json"
        fixture = json.loads(fixture_path.read_text())
        candles = normalize_candles(
            fixture["candles"],
            datetime(2026, 11, 27, 0, 0, tzinfo=UTC),
        )
        snapshots = build_session_snapshots(candles)
        events = [(i, row) for i, row in enumerate(snapshots)
                  if row["discovery_event_start"]]
        self.assertEqual(len(candles), 1212)
        self.assertEqual(len(snapshots), 1164)
        self.assertEqual(len(events), 30)

        episode_rows = []
        for event_number, (start, event) in enumerate(events):
            next_event = events[event_number + 1][0] if event_number + 1 < len(events) else len(snapshots)
            session_end = next(
                (i for i in range(start + 1, len(snapshots))
                 if snapshots[i]["session_date_ist"] != event["session_date_ist"]),
                len(snapshots),
            )
            boundary = min(next_event, session_end)
            reentry_index = next(
                (i for i in range(start + 1, boundary)
                 if snapshots[i]["price_state"] == "REENTRY_CONFIRMED"),
                None,
            )
            end = reentry_index if reentry_index is not None else boundary
            path = snapshots[start:end]
            direction = 1 if event["price_state"].endswith("_ABOVE") else -1
            anchor = event["close"]
            prior = event["prior_value"]
            signed_closes = [(row["close"] - anchor) * direction for row in path]
            favourable_close = max(0.0, max(signed_closes))
            adverse_close = max(0.0, max(-x for x in signed_closes))
            if direction > 0:
                favourable_wick = max(0.0, max(row["high"] - anchor for row in path))
                adverse_wick = max(0.0, max(anchor - row["low"] for row in path))
                outside = [row["close"] > prior["vah"] for row in path]
                beyond_buffer = [
                    row["close"] > prior["vah"] + row["discovery_buffer"] for row in path
                ]
            else:
                favourable_wick = max(0.0, max(anchor - row["low"] for row in path))
                adverse_wick = max(0.0, max(row["high"] - anchor for row in path))
                outside = [row["close"] < prior["val"] for row in path]
                beyond_buffer = [
                    row["close"] < prior["val"] - row["discovery_buffer"] for row in path
                ]
            directional_steps = [
                (path[i]["close"] - path[i - 1]["close"]) * direction
                for i in range(1, len(path))
            ]
            first_inside = next(
                (i for i, row in enumerate(path)
                 if prior["val"] <= row["close"] <= prior["vah"]),
                None,
            )
            net_close = signed_closes[-1]
            episode_rows.append({
                "time": event["candle_open_time_utc"],
                "direction": "UP" if direction > 0 else "DOWN",
                "bars": len(path),
                "net_close": net_close,
                "favourable_close": favourable_close,
                "adverse_close": adverse_close,
                "favourable_wick": favourable_wick,
                "adverse_wick": adverse_wick,
                "outside_closes": sum(outside),
                "beyond_buffer_closes": sum(beyond_buffer),
                "first_inside_offset": first_inside,
                "reentry_confirmed": reentry_index is not None,
                "with_steps": sum(x > 0 for x in directional_steps),
                "against_steps": sum(x < 0 for x in directional_steps),
            })

        rapid_reentries = sum(
            row["reentry_confirmed"] and row["bars"] <= 4 for row in episode_rows
        )
        confirmed_reentries = sum(row["reentry_confirmed"] for row in episode_rows)
        positive_net = sum(row["net_close"] > 0 for row in episode_rows)
        negative_net = sum(row["net_close"] < 0 for row in episode_rows)
        total_active_bars = sum(row["bars"] for row in episode_rows)
        total_outside_closes = sum(row["outside_closes"] for row in episode_rows)
        total_beyond_buffer = sum(row["beyond_buffer_closes"] for row in episode_rows)
        print("HISTORICAL_PRICE_PATH_AUDIT", {
            "episodes": len(episode_rows),
            "active_bars": total_active_bars,
            "confirmed_reentries": confirmed_reentries,
            "rapid_reentries_within_four_bars": rapid_reentries,
            "positive_episode_net": positive_net,
            "negative_episode_net": negative_net,
            "zero_episode_net": len(episode_rows) - positive_net - negative_net,
            "outside_prior_value_closes": total_outside_closes,
            "beyond_buffer_closes": total_beyond_buffer,
            "episode_rows": episode_rows,
        })
        self.assertEqual(confirmed_reentries, 17)
        self.assertEqual(rapid_reentries, 5)
        self.assertEqual(positive_net + negative_net, 30)
        self.assertEqual(total_active_bars, 579)

if __name__=="__main__":
    unittest.main()
