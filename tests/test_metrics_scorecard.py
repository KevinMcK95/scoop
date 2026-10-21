"""Checks for the metrics scorecard calculations."""

from pathlib import Path
import sys
import unittest

# Ensure repository root is importable
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.metrics_scorecard import compute_scorecard, read_jsonl  # noqa: E402


class ScorecardTests(unittest.TestCase):
    def test_sample_events(self):
        events = read_jsonl(REPO_ROOT / "examples" / "sample_events.jsonl")
        scorecard = compute_scorecard(events)

        self.assertEqual(int(scorecard["total_requests"]), 4)
        self.assertAlmostEqual(scorecard["task_success_rate"], 0.75)
        self.assertAlmostEqual(scorecard["budget_violation_rate"], 0.25)
        self.assertAlmostEqual(scorecard["allowed_domain_compliance"], 0.75)
        self.assertGreaterEqual(scorecard["latency_p95_ms"], scorecard["latency_p50_ms"])
        self.assertTrue(0.0 <= scorecard["tool_failure_rate"] <= 1.0)
        self.assertLessEqual(0.0, scorecard["cost_estimation_error"])


if __name__ == "__main__":
    unittest.main()
