"""Cost estimation, budget checks and calibration (scoop/cost.py)."""
from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path

from scoop import cost
from scoop.cost import CostTracker


class PricingTests(unittest.TestCase):
    def test_model_ids_normalise_to_table_keys(self):
        self.assertEqual(cost.normalise_model("openai/GPT-4"), "gpt-4")
        self.assertEqual(cost.normalise_model("us.anthropic.claude-opus-5-5-20260401"), "claude-opus-5-5")

    def test_a_dated_variant_prices_as_its_family(self):
        self.assertEqual(cost.price_for("claude-opus-5-5-20260401"), cost.MODEL_PRICING["claude-opus-5-5"])

    def test_an_unknown_model_has_no_price_rather_than_a_borrowed_one(self):
        self.assertIsNone(cost.price_for("some-local-llm"))
        self.assertIsNone(cost.cost_from_usage("some-local-llm", input_tokens=1000))

    def test_an_unknown_model_can_be_priced_from_the_environment(self):
        os.environ["SCOOP_MODEL_PRICE_USD_PER_MTOK"] = "2,10"
        try:
            self.assertEqual(cost.cost_from_usage("some-local-llm", input_tokens=1_000_000), 2.0)
        finally:
            del os.environ["SCOOP_MODEL_PRICE_USD_PER_MTOK"]

    def test_cache_hits_are_billed_once_at_the_discounted_rate(self):
        # 1M input tokens of which 1M were cache hits, on a $4/$20 model with $0.20 cache reads.
        self.assertAlmostEqual(
            cost.cost_from_usage("claude-opus-5-5", input_tokens=1_000_000, cached_input_tokens=1_000_000), 0.20)
        self.assertAlmostEqual(
            cost.cost_from_usage("claude-opus-5-5", input_tokens=1_000_000, output_tokens=1_000_000), 24.0)


class TrackerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.costs = CostTracker(Path(self.tmp.name) / "state.json", budget_usd=1.0)

    def test_an_estimate_covers_the_prompt_and_what_travels_with_it(self):
        estimate = self.costs.estimate("Circle M31", model="gpt-4", overhead_tokens=5000, model_calls=2)
        self.assertEqual(estimate["estimated_input_tokens"], 2 * (5000 + len("Circle M31") // 4))
        self.assertGreater(estimate["estimated_cost_usd"], 0)
        self.assertTrue(estimate["priced"])

    def test_a_large_prompt_can_exceed_the_budget(self):
        estimate = self.costs.estimate("A" * 400_000, model="gpt-4", budget_usd=0.001)
        self.assertFalse(estimate["within_budget"])

    def test_an_unpriced_model_is_reported_not_guessed(self):
        estimate = self.costs.estimate("Circle M31", model="some-local-llm")
        self.assertIsNone(estimate["estimated_cost_usd"])
        self.assertFalse(estimate["priced"])
        self.assertFalse(estimate["within_budget"])
        self.assertIn("No price", cost.describe(estimate))

    def test_underestimating_raises_the_correction_factor(self):
        estimate = self.costs.estimate("Circle M31", model="gpt-4")
        before = self.costs.calibration("gpt-4")["correction_factor"]
        feedback = self.costs.confirm(estimate["request_id"], estimate["estimated_cost_usd"] * 3)
        after = self.costs.calibration("gpt-4")
        self.assertGreater(feedback["new_correction_factor"], before)
        self.assertEqual(after["samples"], 1)
        # And the next estimate of the same command is higher than the first.
        self.assertGreater(self.costs.estimate("Circle M31", model="gpt-4")["estimated_cost_usd"],
                           estimate["estimated_cost_usd"])

    def test_a_turns_token_counters_confirm_the_estimate(self):
        estimate = self.costs.estimate("Circle M31", model="claude-opus-5-5")
        actual = self.costs.confirm_usage(estimate["request_id"], "claude-opus-5-5",
                                          {"input_tokens": 120_000, "output_tokens": 2_000, "cache_read": 100_000})
        # 20k fresh at $4, 100k cached at $0.20, 2k out at $20.
        self.assertAlmostEqual(actual["actual_cost_usd"], (20_000 * 4 + 100_000 * 0.2 + 2_000 * 20) / 1e6, places=8)
        self.assertEqual(self.costs.calibration("claude-opus-5-5")["samples"], 1)

    def test_confirming_an_unpriced_model_drops_the_request_quietly(self):
        estimate = self.costs.estimate("Circle M31", model="some-local-llm")
        self.assertIsNone(self.costs.confirm_usage(estimate["request_id"], "some-local-llm", {"input_tokens": 10}))
        self.assertIsNone(self.costs.confirm_usage(estimate["request_id"], "some-local-llm", {"input_tokens": 10}))

    def test_an_unknown_request_cannot_be_confirmed(self):
        with self.assertRaises(ValueError):
            self.costs.confirm("not-a-request", 0.01)

    def test_unconfirmed_requests_do_not_pile_up(self):
        for index in range(cost.MAX_PENDING + 10):
            self.costs.estimate(f"query {index}", model="gpt-4")
        state = json.loads((Path(self.tmp.name) / "state.json").read_text())
        self.assertLessEqual(len(state["pending"]), cost.MAX_PENDING)

    def test_a_conversation_budget_is_checked_and_drawn_down(self):
        self.assertIsNone(self.costs.budget("thread-1"))
        ledger = self.costs.set_budget("thread-1", 0.50)
        self.assertEqual(ledger["remaining_usd"], 0.50)

        estimate = self.costs.estimate("Circle M31", model="claude-opus-5-5", thread_id="thread-1")
        self.assertEqual(estimate["budget_usd"], 0.50)
        self.assertEqual(estimate["budget_scope"], "conversation")

        actual = self.costs.confirm_usage(estimate["request_id"], "claude-opus-5-5",
                                          {"input_tokens": 25_000, "output_tokens": 1_000})
        spent = actual["actual_cost_usd"]
        self.assertAlmostEqual(self.costs.budget("thread-1")["remaining_usd"], round(0.50 - spent, 6), places=6)
        self.assertEqual(self.costs.budget("thread-1")["turns"], 1)
        # The next estimate is checked against what is left, not the original budget.
        self.assertAlmostEqual(
            self.costs.estimate("Circle M31", model="claude-opus-5-5", thread_id="thread-1")["budget_usd"],
            round(0.50 - spent, 6), places=6)
        self.assertIn("left of this conversation", cost.describe(estimate, actual))

    def test_a_conversation_without_a_budget_falls_back_to_the_default(self):
        estimate = self.costs.estimate("Circle M31", model="gpt-4", thread_id="thread-2")
        self.assertEqual(estimate["budget_usd"], 1.0)
        self.assertEqual(estimate["budget_scope"], "default")
        self.assertIsNone(estimate["budget"])

    def test_a_budget_must_be_positive(self):
        with self.assertRaises(ValueError):
            self.costs.set_budget("thread-3", 0)

    def test_raising_a_budget_keeps_what_was_already_spent(self):
        self.costs.set_budget("thread-4", 0.10)
        estimate = self.costs.estimate("Circle M31", model="gpt-4", thread_id="thread-4")
        self.costs.confirm_usage(estimate["request_id"], "gpt-4", {"input_tokens": 1_000})
        spent = self.costs.budget("thread-4")["spent_usd"]
        self.assertGreater(spent, 0)
        self.assertEqual(self.costs.set_budget("thread-4", 1.0)["spent_usd"], spent)

    def test_the_wrapper_calibrates_from_a_commands_own_usage(self):
        before = self.costs.calibration("gpt-4")["samples"]
        run = cost.run_with_auto_calibration(
            query="Summarize this paper and list three key findings.", model="gpt-4", budget_usd=1.0,
            command_fn=lambda: {"ok": True, "usage": {"input_tokens": 1200, "output_tokens": 600}},
            tracker=self.costs)
        self.assertIsNotNone(run["calibration_feedback"])
        self.assertEqual(self.costs.calibration("gpt-4")["samples"], before + 1)

    def test_the_wrapper_requires_a_cost_signal(self):
        with self.assertRaises(ValueError):
            cost.run_with_auto_calibration(query="No usage signal", model="gpt-4", budget_usd=1.0,
                                           command_fn=lambda: {"ok": True}, tracker=self.costs)


if __name__ == "__main__":
    unittest.main()
