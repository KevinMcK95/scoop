"""Default command path: run through budget check + automatic calibration.

This script demonstrates how every command can be executed through the same wrapper
so the estimator is updated on each run.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any, Dict

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cost_checker import run_with_auto_calibration


def simulate_provider_call(query: str, output_tokens: int) -> Dict[str, Any]:
    # Simulated model response object with usage counters.
    input_tokens = max(1, len(query) // 4)
    return {
        "ok": True,
        "text": "Simulated response",
        "usage": {
            "input_tokens": input_tokens,
            "output_tokens": max(0, int(output_tokens)),
            "cached_input_tokens": 0,
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a command with automatic budget calibration")
    parser.add_argument("query", type=str, help="User query text")
    parser.add_argument("--model", type=str, default="gpt-4")
    parser.add_argument("--budget-usd", type=float, default=1.0)
    parser.add_argument("--output-tokens", type=int, default=400)
    args = parser.parse_args()

    result = run_with_auto_calibration(
        query=args.query,
        model=args.model,
        budget_usd=args.budget_usd,
        command_fn=lambda: simulate_provider_call(args.query, args.output_tokens),
    )

    print("budget:", result["budget"])
    print("calibration_feedback:", result["calibration_feedback"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
