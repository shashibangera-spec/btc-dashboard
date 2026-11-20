"""Regression tests for the event-driven Telegram workflow contract."""
from pathlib import Path
import unittest

import yaml


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"


def load_workflow(name):
    # BaseLoader preserves the GitHub Actions "on" key as a string.
    return yaml.load(
        (WORKFLOWS / name).read_text(encoding="utf-8"),
        Loader=yaml.BaseLoader,
    )


class TelegramWorkflowPipelineTests(unittest.TestCase):
    def test_telegram_is_reusable_and_manually_dispatchable_but_not_scheduled(self):
        workflow = load_workflow("telegram_alerts.yml")
        triggers = workflow["on"]

        self.assertIn("workflow_call", triggers)
        self.assertIn("workflow_dispatch", triggers)
        self.assertNotIn("schedule", triggers)

        for trigger in ("workflow_call", "workflow_dispatch"):
            self.assertEqual(
                triggers[trigger]["inputs"]["test_mode"]["type"], "boolean"
            )
            self.assertEqual(
                triggers[trigger]["inputs"]["test_mode"]["default"], "false"
            )

    def test_telegram_uses_shared_non_cancelling_concurrency_group(self):
        workflow = load_workflow("telegram_alerts.yml")

        self.assertEqual(workflow["concurrency"]["group"], "btc-telegram-alerts")
        self.assertEqual(workflow["concurrency"]["cancel-in-progress"], "false")

    def test_coordinator_runs_telegram_only_after_signal_detection(self):
        workflow = load_workflow("coordinator.yml")
        triggers = workflow["on"]
        jobs = workflow["jobs"]

        # Preserve the existing external dispatcher contract; no new cron.
        self.assertEqual(list(triggers.keys()), ["workflow_dispatch"])
        self.assertNotIn("schedule", triggers)

        telegram_job = jobs["telegram-alerts"]
        self.assertEqual(telegram_job["needs"], "detect-signals")
        self.assertEqual(
            telegram_job["uses"], "./.github/workflows/telegram_alerts.yml"
        )
        self.assertEqual(telegram_job["with"]["test_mode"], "false")
        self.assertEqual(telegram_job["secrets"], "inherit")

        detector_needs = jobs["detect-signals"]["needs"]
        self.assertEqual(
            set(detector_needs),
            {"collect-amt", "collect-futures", "collect-options"},
        )

    def test_coordinator_and_alert_workflow_keep_distinct_concurrency_groups(self):
        coordinator = load_workflow("coordinator.yml")
        telegram = load_workflow("telegram_alerts.yml")

        self.assertEqual(
            coordinator["concurrency"]["group"],
            "btc-data-collection-coordinator",
        )
        self.assertNotEqual(
            coordinator["concurrency"]["group"],
            telegram["concurrency"]["group"],
        )


if __name__ == "__main__":
    unittest.main()
