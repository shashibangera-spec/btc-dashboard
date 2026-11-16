import ast
import unittest
from pathlib import Path

from pine_state_machine import STATE_NAMES, TransitionCounters, TransitionInputs, advance_auction_state


class TestTradingViewStateNames(unittest.TestCase):
    def test_state_names_match_v722_display_labels(self):
        expected = {
            0: "BUILDING",
            1: "DEVELOPMENT",
            2: "DISCOVERY ABOVE",
            3: "DISCOVERY BELOW",
            4: "PRICE ACCEPTANCE ABOVE",
            5: "PRICE ACCEPTANCE BELOW",
            6: "POC BREAK ABOVE",
            7: "POC BREAK BELOW",
            8: "NEW AREA DEVELOPING UP",
            9: "NEW AREA DEVELOPING DOWN",
            10: "FAILED DISCOVERY UP",
            11: "FAILED DISCOVERY DOWN",
            12: "ACCEPTANCE FAILURE UP",
            13: "ACCEPTANCE FAILURE DOWN",
            14: "RE-ENTRY CONFIRMED",
            15: "DISCOVERY STALLED ABOVE",
            16: "DISCOVERY STALLED BELOW",
            17: "FAILED DISCOVERY UP — CANDIDATE",
            18: "FAILED DISCOVERY DOWN — CANDIDATE",
        }
        self.assertEqual(STATE_NAMES, expected)

    def test_discovery_below_times_out_to_exact_stalled_state(self):
        x = TransitionInputs(
            bar_index=20, discovery_start_bar=14, effective_timeout=6,
            close=80.0, current_poc=85.0, prior_vah=100.0, prior_val=90.0,
            cycle_bars=21, development_min=3, inside_value_bars=0,
            accept_bars=2, failed_discovery_confirm_bars=2,
            development_stall_bars=20, reentry_persistence_bars=3,
            price_acceptance_up=False, price_acceptance_down=False,
            value_migration_up=False, value_migration_down=False,
            new_value_developing_up=False, new_value_developing_down=False,
        )
        state, _, _, _ = advance_auction_state(3, 14, TransitionCounters(), x)
        self.assertEqual(state, 16)
        self.assertEqual(STATE_NAMES[state], "DISCOVERY STALLED BELOW")

    def test_dashboard_replaces_legacy_journal_with_engine(self):
        source = Path("btc_live_dashboard.py").read_text(encoding="utf-8")
        tree = ast.parse(source)
        functions = {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}
        self.assertIn("generate_state_journal", functions)
        fn_source = ast.get_source_segment(source, functions["generate_state_journal"])
        self.assertIn("PineStateMachine", fn_source)
        self.assertIn("engine.replay(candles)", fn_source)
        self.assertNotIn("calculate_tpo(", fn_source)


if __name__ == "__main__":
    unittest.main()
