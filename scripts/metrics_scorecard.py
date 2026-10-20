"""Compute weekly pipeline scorecard metrics from JSONL event logs."""

from __future__ import annotations

import json
import math
import statistics
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List

REQUIRED_FIELDS = {
    "request_id",
    "timestamp",
    "task_success",
    "user_accepted",
    "estimated_cost_usd",
    "actual_cost_usd",
    "budget_exceeded",
    "domain_allowed",
    "latency_ms",
    "tool_calls_total",
    "tool_calls_failed",
    "escalated",
}


def read_jsonl(path: Path) -> List[Dict[str, Any]]:
    events: List[Dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as f:
        for line_no, line in enumerate(f, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSON on line {line_no}: {exc}") from exc
            missing = REQUIRED_FIELDS - obj.keys()
            if missing:
                raise ValueError(f"Line {line_no} missing required fields: {sorted(missing)}")
            events.append(obj)
    if not events:
        raise ValueError("No events found in file")
    return events


def _safe_div(n: float, d: float) -> float:
    return 0.0 if d == 0 else n / d


def percentile(values: Iterable[float], p: float) -> float:
    vals = sorted(values)
    if not vals:
        return 0.0
    if len(vals) == 1:
        return float(vals[0])
    idx = (len(vals) - 1) * p
    lo = math.floor(idx)
    hi = math.ceil(idx)
    if lo == hi:
        return float(vals[lo])
    frac = idx - lo
    return float(vals[lo] * (1 - frac) + vals[hi] * frac)


def compute_scorecard(events: List[Dict[str, Any]]) -> Dict[str, float]:
    total = len(events)

    task_successes = sum(1 for e in events if e["task_success"])
    user_accepted = sum(1 for e in events if e["user_accepted"])

    budget_violations = sum(1 for e in events if e["budget_exceeded"])
    allowed_domain_count = sum(1 for e in events if e["domain_allowed"])
    escalations = sum(1 for e in events if e["escalated"])

    latencies = [float(e["latency_ms"]) for e in events]
    total_cost = sum(float(e["actual_cost_usd"]) for e in events)
    avg_cost = _safe_div(total_cost, total)

    tool_calls_total = sum(int(e["tool_calls_total"]) for e in events)
    tool_calls_failed = sum(int(e["tool_calls_failed"]) for e in events)

    # Relative error; skip rows with zero actual cost to avoid exploding denominator.
    rel_errors = []
    for e in events:
        actual = float(e["actual_cost_usd"])
        estimated = float(e["estimated_cost_usd"])
        if actual > 0:
            rel_errors.append(abs(estimated - actual) / actual)
    mean_rel_error = statistics.mean(rel_errors) if rel_errors else 0.0

    planned_subtasks = sum(int(e.get("planned_subtasks", 0)) for e in events)
    completed_subtasks = sum(int(e.get("completed_subtasks", 0)) for e in events)

    risky_actions = [e for e in events if e.get("risky_action") is True]
    risky_blocked = sum(1 for e in risky_actions if e.get("risky_blocked") is True)
    risky_missed = sum(1 for e in risky_actions if e.get("risky_blocked") is False)

    citations_total = sum(int(e.get("citations_total", 0)) for e in events)
    citations_valid = sum(int(e.get("citations_valid", 0)) for e in events)

    return {
        "total_requests": float(total),
        "task_success_rate": _safe_div(task_successes, total),
        "answer_acceptance_rate": _safe_div(user_accepted, total),
        "citation_validity": _safe_div(citations_valid, citations_total),
        "cost_per_request_usd": avg_cost,
        "budget_violation_rate": _safe_div(budget_violations, total),
        "cost_estimation_error": mean_rel_error,
        "allowed_domain_compliance": _safe_div(allowed_domain_count, total),
        "latency_p50_ms": percentile(latencies, 0.50),
        "latency_p95_ms": percentile(latencies, 0.95),
        "tool_failure_rate": _safe_div(tool_calls_failed, tool_calls_total),
        "human_escalation_rate": _safe_div(escalations, total),
        "plan_completion_rate": _safe_div(completed_subtasks, planned_subtasks),
        "policy_block_precision": _safe_div(risky_blocked, len(risky_actions)),
        "policy_miss_rate": _safe_div(risky_missed, len(risky_actions)),
    }


def print_scorecard(scorecard: Dict[str, float]) -> None:
    ordered = [
        "total_requests",
        "task_success_rate",
        "answer_acceptance_rate",
        "citation_validity",
        "cost_per_request_usd",
        "budget_violation_rate",
        "cost_estimation_error",
        "allowed_domain_compliance",
        "latency_p50_ms",
        "latency_p95_ms",
        "tool_failure_rate",
        "human_escalation_rate",
        "plan_completion_rate",
        "policy_block_precision",
        "policy_miss_rate",
    ]
    for key in ordered:
        print(f"{key}: {scorecard[key]:.6f}")


def main() -> int:
    if len(sys.argv) != 2:
        print("Usage: python3 scripts/metrics_scorecard.py <events.jsonl>")
        return 2

    events_path = Path(sys.argv[1])
    if not events_path.exists():
        print(f"Error: file does not exist: {events_path}")
        return 2

    try:
        events = read_jsonl(events_path)
        scorecard = compute_scorecard(events)
    except ValueError as exc:
        print(f"Error: {exc}")
        return 1

    print_scorecard(scorecard)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
