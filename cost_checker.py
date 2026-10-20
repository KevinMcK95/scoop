"""
cost_checker.py

Estimate the cost of a user query and check it against a budget.
Supports both user-specified and agent-default budgets.

Also supports a feedback loop:
1) estimate and track a request
2) confirm actual cost when known
3) auto-calibrate future estimates per model
"""

import json
import os
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, Optional

# Example model pricing (USD per 1K tokens)
MODEL_PRICING = {
    "gpt-4": 0.03,  # $0.03 per 1K tokens (input)
    "gpt-3.5": 0.002,
}

# Default agent budget (USD)
DEFAULT_BUDGET_USD = float(os.environ.get("AGENT_BUDGET_USD", 1.00))  # $1.00 default

# Location for persistent calibration state
STATE_PATH = Path(os.environ.get("COST_CHECKER_STATE_PATH", ".cost_checker_state.json"))

# Learning config
DEFAULT_CORRECTION_FACTOR = 1.0
EWMA_ALPHA = 0.25
MIN_CORRECTION_FACTOR = 0.25
MAX_CORRECTION_FACTOR = 4.0


def _empty_state() -> Dict[str, Any]:
    return {
        "version": 1,
        "models": {},
        "pending": {},
    }


def _load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return _empty_state()
    with STATE_PATH.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_state(state: Dict[str, Any]) -> None:
    with STATE_PATH.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)


def _get_model_state(state: Dict[str, Any], model: str) -> Dict[str, Any]:
    models = state.setdefault("models", {})
    return models.setdefault(
        model,
        {
            "correction_factor": DEFAULT_CORRECTION_FACTOR,
            "samples": 0,
            "ewma_relative_error": 0.0,
        },
    )


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))

# Simple token estimator (1 token ≈ 4 chars for English)
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)


def estimate_cost(tokens: int, model: str = "gpt-4", calibrated: bool = True) -> float:
    price_per_1k = MODEL_PRICING.get(model, MODEL_PRICING["gpt-4"])
    base_cost = (tokens / 1000) * price_per_1k
    if not calibrated:
        return base_cost
    state = _load_state()
    model_state = _get_model_state(state, model)
    factor = float(model_state.get("correction_factor", DEFAULT_CORRECTION_FACTOR))
    return base_cost * factor


def get_model_calibration(model: str = "gpt-4") -> Dict[str, float]:
    state = _load_state()
    model_state = _get_model_state(state, model)
    return {
        "correction_factor": float(model_state["correction_factor"]),
        "samples": int(model_state["samples"]),
        "ewma_relative_error": float(model_state["ewma_relative_error"]),
    }


def reset_calibration(model: Optional[str] = None) -> None:
    state = _load_state()
    if model is None:
        state = _empty_state()
    else:
        state.setdefault("models", {}).pop(model, None)
    _save_state(state)


def confirm_actual_cost(request_id: str, actual_cost_usd: float) -> Dict[str, Any]:
    if actual_cost_usd < 0:
        raise ValueError("actual_cost_usd must be non-negative")

    state = _load_state()
    pending = state.setdefault("pending", {})
    request = pending.pop(request_id, None)
    if request is None:
        raise ValueError(f"Unknown request_id: {request_id}")

    model = str(request["model"])
    base_estimate = float(request["base_estimated_cost_usd"])
    prev_adjusted_estimate = float(request["estimated_cost_usd"])
    model_state = _get_model_state(state, model)

    # Learn multiplicative correction against base estimate.
    if base_estimate > 0:
        observed_ratio = actual_cost_usd / base_estimate
    else:
        observed_ratio = DEFAULT_CORRECTION_FACTOR

    old_factor = float(model_state["correction_factor"])
    new_factor = (1 - EWMA_ALPHA) * old_factor + EWMA_ALPHA * observed_ratio
    new_factor = _clamp(new_factor, MIN_CORRECTION_FACTOR, MAX_CORRECTION_FACTOR)
    model_state["correction_factor"] = new_factor
    model_state["samples"] = int(model_state["samples"]) + 1

    if actual_cost_usd > 0:
        rel_err = abs(prev_adjusted_estimate - actual_cost_usd) / actual_cost_usd
    else:
        rel_err = 0.0
    old_err = float(model_state.get("ewma_relative_error", 0.0))
    model_state["ewma_relative_error"] = (1 - EWMA_ALPHA) * old_err + EWMA_ALPHA * rel_err

    _save_state(state)

    return {
        "request_id": request_id,
        "model": model,
        "actual_cost_usd": round(actual_cost_usd, 8),
        "previous_estimated_cost_usd": round(prev_adjusted_estimate, 8),
        "old_correction_factor": round(old_factor, 8),
        "new_correction_factor": round(new_factor, 8),
        "samples": int(model_state["samples"]),
    }


def estimate_actual_cost_from_usage(
    model: str,
    input_tokens: int,
    output_tokens: int = 0,
    cached_input_tokens: int = 0,
) -> float:
    """Estimate billable actual cost from usage counters.

    Note: with the current simplified pricing table, input/output/cached are priced equally.
    """
    total_tokens = max(0, int(input_tokens)) + max(0, int(output_tokens)) + max(0, int(cached_input_tokens))
    return estimate_cost(total_tokens, model=model, calibrated=False)


def run_with_auto_calibration(
    query: str,
    command_fn: Callable[[], Any],
    budget_usd: Optional[float] = None,
    model: str = "gpt-4",
    actual_cost_usd: Optional[float] = None,
    usage: Optional[Dict[str, int]] = None,
    require_calibration_signal: bool = True,
) -> Dict[str, Any]:
    """Run a command and auto-update calibration when cost ground truth is available.

        `command_fn` can be any callable that executes the request.
    You can provide either:
      - `actual_cost_usd`, or
      - `usage` with token counters (`input_tokens`, optional `output_tokens`, `cached_input_tokens`).

        If those are omitted, this function attempts to extract calibration signal from
        `command_fn` output when it is a dict:
            - `actual_cost_usd`
            - or usage fields: `input_tokens`, `output_tokens`, `cached_input_tokens`
            - or nested `usage` object with those fields.
    """
    budget_result = check_budget(query, budget_usd=budget_usd, model=model, track_request=True)
    request_id = budget_result["request_id"]

    execution_result = command_fn()

    inferred_actual_cost = actual_cost_usd

    if inferred_actual_cost is None and isinstance(execution_result, dict):
        if "actual_cost_usd" in execution_result and execution_result["actual_cost_usd"] is not None:
            inferred_actual_cost = float(execution_result["actual_cost_usd"])
        else:
            direct_usage = {
                "input_tokens": int(execution_result.get("input_tokens", 0)),
                "output_tokens": int(execution_result.get("output_tokens", 0)),
                "cached_input_tokens": int(execution_result.get("cached_input_tokens", 0)),
            }
            nested_usage = execution_result.get("usage")
            if isinstance(nested_usage, dict):
                direct_usage = {
                    "input_tokens": int(nested_usage.get("input_tokens", direct_usage["input_tokens"])),
                    "output_tokens": int(nested_usage.get("output_tokens", direct_usage["output_tokens"])),
                    "cached_input_tokens": int(
                        nested_usage.get("cached_input_tokens", direct_usage["cached_input_tokens"])
                    ),
                }
            if any(v > 0 for v in direct_usage.values()):
                usage = direct_usage

    if inferred_actual_cost is None and usage is not None:
        inferred_actual_cost = estimate_actual_cost_from_usage(
            model=model,
            input_tokens=int(usage.get("input_tokens", 0)),
            output_tokens=int(usage.get("output_tokens", 0)),
            cached_input_tokens=int(usage.get("cached_input_tokens", 0)),
        )

    calibration_feedback = None
    if inferred_actual_cost is None and require_calibration_signal:
        raise ValueError(
            "No calibration signal provided. Pass `actual_cost_usd`, `usage`, or return usage/cost from command_fn."
        )

    if inferred_actual_cost is not None:
        calibration_feedback = confirm_actual_cost(request_id, float(inferred_actual_cost))

    return {
        "budget": budget_result,
        "result": execution_result,
        "calibration_feedback": calibration_feedback,
    }


def check_budget(
    query: str,
    budget_usd: Optional[float] = None,
    model: str = "gpt-4",
    track_request: bool = True,
    request_id: Optional[str] = None,
) -> dict:
    tokens = estimate_tokens(query)
    base_est_cost = estimate_cost(tokens, model, calibrated=False)
    est_cost = estimate_cost(tokens, model, calibrated=True)
    budget = budget_usd if budget_usd is not None else DEFAULT_BUDGET_USD
    within_budget = est_cost <= budget

    resolved_request_id = request_id or str(uuid.uuid4())
    if track_request:
        state = _load_state()
        state.setdefault("pending", {})[resolved_request_id] = {
            "model": model,
            "tokens": tokens,
            "base_estimated_cost_usd": base_est_cost,
            "estimated_cost_usd": est_cost,
        }
        _save_state(state)

    return {
        "request_id": resolved_request_id,
        "tokens": tokens,
        "base_estimated_cost_usd": round(base_est_cost, 6),
        "estimated_cost_usd": round(est_cost, 6),
        "budget_usd": round(budget, 6),
        "within_budget": within_budget,
    }


if __name__ == "__main__":
    # Example usage
    import sys

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Summarize the latest arXiv paper on transformers."
    user_budget = float(sys.argv[2]) if len(sys.argv) > 2 else None
    result = check_budget(prompt, user_budget, model="gpt-4", track_request=True)
    print(
        f"Prompt: {prompt}"
        f"\nRequest ID: {result['request_id']}"
        f"\nTokens: {result['tokens']}"
        f"\nBase Estimate: ${result['base_estimated_cost_usd']}"
        f"\nCalibrated Estimate: ${result['estimated_cost_usd']} (Budget: ${result['budget_usd']})"
        f"\nWithin Budget: {result['within_budget']}"
    )
