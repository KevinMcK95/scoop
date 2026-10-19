"""Simple checks for metrics scorecard calculations (no external test framework)."""

from pathlib import Path
import sys

# Ensure repository root is importable
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from scripts.metrics_scorecard import compute_scorecard, read_jsonl  # noqa: E402


def run() -> None:
    events = read_jsonl(REPO_ROOT / "examples" / "sample_events.jsonl")
    scorecard = compute_scorecard(events)

    assert int(scorecard["total_requests"]) == 4
    assert abs(scorecard["task_success_rate"] - 0.75) < 1e-9
    assert abs(scorecard["budget_violation_rate"] - 0.25) < 1e-9
    assert abs(scorecard["allowed_domain_compliance"] - 0.75) < 1e-9
    assert scorecard["latency_p95_ms"] >= scorecard["latency_p50_ms"]
    assert 0.0 <= scorecard["tool_failure_rate"] <= 1.0
    assert 0.0 <= scorecard["cost_estimation_error"]

    print("metrics scorecard tests passed")


if __name__ == "__main__":
    run()
