import unittest
from datetime import datetime, timedelta, timezone

from pine_state_machine import (
    Candle, PineConfig, PineStateMachine, TransitionCounters,
    TransitionInputs, advance_auction_state,
)

UTC = timezone.utc


def inputs(**overrides):
    base = dict(
        bar_index=100, discovery_start_bar=90, effective_timeout=6,
        close=105.0, current_poc=105.0, prior_vah=100.0, prior_val=90.0,
        cycle_bars=10, development_min=3, inside_value_bars=0,
        accept_bars=2, failed_discovery_confirm_bars=2,
        development_stall_bars=20, reentry_persistence_bars=3,
        price_acceptance_up=False, price_acceptance_down=False,
        value_migration_up=False, value_migration_down=False,
        new_value_developing_up=False, new_value_developing_down=False,
    )
    base.update(overrides)
    return TransitionInputs(**base)


class TestPineTransitionParity(unittest.TestCase):
    def test_building_to_development_and_discovery(self):
        state, start, _, _ = advance_auction_state(
            0, 0, TransitionCounters(), inputs(close=95.0, cycle_bars=3, development_min=3)
        )
        self.assertEqual(state, 1)
        state, start, _, _ = advance_auction_state(1, start, TransitionCounters(), inputs(close=105.0))
        self.assertEqual(state, 2)
        self.assertEqual(start, 100)

    def test_discovery_above_priority_acceptance_candidate_and_timeout(self):
        state, _, _, _ = advance_auction_state(2, 90, TransitionCounters(), inputs(price_acceptance_up=True, close=95.0))
        self.assertEqual(state, 4)
        state, _, _, _ = advance_auction_state(2, 90, TransitionCounters(), inputs(close=95.0))
        self.assertEqual(state, 17)
        state, _, _, _ = advance_auction_state(2, 90, TransitionCounters(), inputs(close=105.0, bar_index=96))
        self.assertEqual(state, 15)

    def test_discovery_below_priority_acceptance_candidate_and_timeout(self):
        state, _, _, _ = advance_auction_state(3, 90, TransitionCounters(), inputs(price_acceptance_down=True, close=95.0))
        self.assertEqual(state, 5)
        state, _, _, _ = advance_auction_state(3, 90, TransitionCounters(), inputs(close=95.0))
        self.assertEqual(state, 18)
        state, _, _, _ = advance_auction_state(3, 90, TransitionCounters(), inputs(close=85.0, bar_index=96))
        self.assertEqual(state, 16)

    def test_stalled_discovery_exit_paths_preserve_source_asymmetry(self):
        state, _, _, _ = advance_auction_state(15, 90, TransitionCounters(), inputs(close=95.0))
        self.assertEqual(state, 10)
        state, _, _, _ = advance_auction_state(15, 90, TransitionCounters(), inputs(close=85.0))
        self.assertEqual(state, 3)
        # Pine has no direct above-VAH branch in state 15.
        state, _, _, _ = advance_auction_state(15, 90, TransitionCounters(), inputs(close=105.0))
        self.assertEqual(state, 15)
        state, _, _, _ = advance_auction_state(15, 90, TransitionCounters(), inputs(price_acceptance_up=True, close=105.0))
        self.assertEqual(state, 4)
        state, _, _, _ = advance_auction_state(16, 90, TransitionCounters(), inputs(close=95.0))
        self.assertEqual(state, 11)
        state, _, _, _ = advance_auction_state(16, 90, TransitionCounters(), inputs(close=105.0))
        self.assertEqual(state, 2)
        # Pine has no direct below-VAL branch in state 16.
        state, _, _, _ = advance_auction_state(16, 90, TransitionCounters(), inputs(close=85.0))
        self.assertEqual(state, 16)
        state, _, _, _ = advance_auction_state(16, 90, TransitionCounters(), inputs(price_acceptance_down=True, close=85.0))
        self.assertEqual(state, 5)

    def test_failed_discovery_states_rearm_outside_and_confirm_inside(self):
        for initial, close, expected in ((17,105,2),(17,85,3),(18,85,3),(18,105,2)):
            state, _, _, _ = advance_auction_state(initial,90,TransitionCounters(),inputs(close=close))
            self.assertEqual(state,expected)
        state, _, _, _ = advance_auction_state(17,90,TransitionCounters(),inputs(close=95,inside_value_bars=2))
        self.assertEqual(state,10)
        state, _, _, _ = advance_auction_state(18,90,TransitionCounters(),inputs(close=95,inside_value_bars=2))
        self.assertEqual(state,11)

    def test_failed_discovery_confirmed_state_reentry_and_rebreak(self):
        for initial in (10,11):
            state, _, _, _ = advance_auction_state(initial,90,TransitionCounters(),inputs(close=95,inside_value_bars=2))
            self.assertEqual(state,14)
        self.assertEqual(advance_auction_state(10,90,TransitionCounters(),inputs(close=105))[0],2)
        self.assertEqual(advance_auction_state(11,90,TransitionCounters(),inputs(close=85))[0],3)

    def test_upside_acceptance_migration_and_new_area(self):
        counters=TransitionCounters()
        state,start,_,_=advance_auction_state(2,90,counters,inputs(price_acceptance_up=True))
        self.assertEqual(state,4)
        state,start,_,_=advance_auction_state(state,start,counters,inputs(value_migration_up=True))
        self.assertEqual(state,6)
        state,start,_,_=advance_auction_state(state,start,counters,inputs(new_value_developing_up=True))
        self.assertEqual(state,8)

    def test_upside_new_area_stall_and_reentry_exits(self):
        counters=TransitionCounters(state8_bars=19)
        state,_,stalled,event=advance_auction_state(8,90,counters,inputs())
        self.assertEqual(state,12); self.assertTrue(stalled); self.assertEqual(event,"NEW AREA STALLED UP")
        self.assertEqual(counters.state8_bars,0)
        state,_,_,_=advance_auction_state(8,90,TransitionCounters(),inputs(close=100,current_poc=99))
        self.assertEqual(state,12)

    def test_downside_acceptance_migration_new_area_and_stall(self):
        counters=TransitionCounters()
        state,start,_,_=advance_auction_state(3,90,counters,inputs(close=85,price_acceptance_down=True))
        self.assertEqual(state,5)
        state,start,_,_=advance_auction_state(state,start,counters,inputs(close=85,value_migration_down=True))
        self.assertEqual(state,7)
        state,start,_,_=advance_auction_state(state,start,counters,inputs(close=85,new_value_developing_down=True))
        self.assertEqual(state,9)
        counters.state9_bars=19
        state,_,stalled,event=advance_auction_state(state,start,counters,inputs(close=85))
        self.assertEqual(state,13); self.assertTrue(stalled); self.assertEqual(event,"NEW AREA STALLED DOWN")

    def test_downside_new_area_reentry_exit(self):
        state,_,stalled,event=advance_auction_state(9,90,TransitionCounters(),inputs(close=95,current_poc=95))
        self.assertEqual(state,13); self.assertFalse(stalled); self.assertIsNone(event)

    def test_acceptance_and_migration_failure_exits(self):
        for initial,close,expected in ((4,95,12),(6,95,12),(5,95,13),(7,95,13)):
            state,_,_,_=advance_auction_state(initial,90,TransitionCounters(),inputs(close=close))
            self.assertEqual(state,expected)

    def test_state_12_and_13_have_directional_discovery_and_reentry_exits(self):
        for initial in (12,13):
            self.assertEqual(advance_auction_state(initial,90,TransitionCounters(),inputs(close=85))[0],3)
            self.assertEqual(advance_auction_state(initial,90,TransitionCounters(),inputs(close=105))[0],2)
            self.assertEqual(advance_auction_state(initial,90,TransitionCounters(),inputs(close=95,inside_value_bars=2))[0],14)

    def test_reentry_confirmation_persists_then_returns_to_development(self):
        counters=TransitionCounters(reentry_inside_bars=2)
        state,_,_,_=advance_auction_state(14,90,counters,inputs(close=95,cycle_bars=10))
        self.assertEqual(state,1); self.assertEqual(counters.reentry_inside_bars,0)

    def test_timeout_uses_pine_bar_index_elapsed(self):
        state,_,_,_=advance_auction_state(3,90,TransitionCounters(),inputs(close=85,bar_index=96,discovery_start_bar=90))
        self.assertEqual(state,16)


class TestPineProfileAndCycle(unittest.TestCase):
    def test_profile_midpoint_tie_break_and_value_area(self):
        start=datetime(2026,1,1,tzinfo=UTC)
        bars=[Candle(start,100,150,100,125),Candle(start+timedelta(minutes=30),150,200,150,175)]
        result=PineStateMachine(PineConfig()).replay(bars)[-1]
        self.assertLessEqual(result.developing_val,result.developing_poc)
        self.assertLessEqual(result.developing_poc,result.developing_vah)

    def test_atr_wilder_rma_seeded_by_full_window(self):
        start=datetime(2026,1,1,1,tzinfo=UTC)
        engine=PineStateMachine(PineConfig(atr_length=20))
        for i in range(20):
            result=engine.process_bar(Candle(start+timedelta(minutes=30*i),100,110,100,105))
        self.assertAlmostEqual(result.atr20,10.0)
        result=engine.process_bar(Candle(start+timedelta(minutes=600),100,130,100,120))
        self.assertAlmostEqual(result.atr20,11.0)

    def test_cycle_boundary_saves_previous_profile_and_resets(self):
        start=datetime(2026,1,1,tzinfo=UTC)
        engine=PineStateMachine(PineConfig())
        for i in range(48):
            engine.process_bar(Candle(start+timedelta(minutes=30*i),1000,1100,950,1025))
        result=engine.process_bar(Candle(start+timedelta(days=1),1000,1050,975,1025))
        self.assertIsNotNone(result.prior_vah); self.assertIsNotNone(result.prior_val)
        self.assertEqual(result.cycle_bars,1); self.assertEqual(result.state_code,0)


if __name__ == "__main__":
    unittest.main()
