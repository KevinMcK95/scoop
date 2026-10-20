"""What a command costs: estimate it before the turn, learn from the real bill after.

A turn is estimated before the model is called (the user's text, plus the system
prompt and tool schemas that travel with it), checked against a budget, and
tracked by request id. When the turn ends the server knows the real token
counts, so the estimate is confirmed against them and a per-model correction
factor is nudged towards the truth. The next estimate uses it.

Prices are USD per million tokens. A model with no price is reported as
unknown rather than priced as some other model: a confident wrong number is
worse than "no price for this model". Set ``SCOOP_MODEL_PRICE_USD_PER_MTOK``
("input,output" or "input,output,cached") to price a model this table does not
know, such as a local or self-hosted endpoint.

``python -m scoop.cost "a prompt"`` prints one estimate.
"""
from __future__ import annotations

import json
import os
import re
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass(frozen=True)
class Price:
    """USD per million tokens. ``cached`` is the discounted rate for cache hits."""

    input: float
    output: float
    cached: float | None = None

    @property
    def cache_read(self) -> float:
        # Cache hits are billed at a fraction of the input rate; a tenth is the usual discount.
        return self.cached if self.cached is not None else self.input * 0.1


# Anthropic rates are first-party API prices; the OpenAI rows are the public list
# prices for those models. Keys are matched after normalise_model().
MODEL_PRICING: dict[str, Price] = {
    "claude-fable-5-1": Price(10.0, 50.0, 0.25),
    "claude-fable-5": Price(10.0, 50.0),
    "claude-opus-5-5": Price(4.0, 20.0, 0.20),
    "claude-opus-5": Price(5.0, 25.0),
    "claude-opus-4-8": Price(5.0, 25.0),
    "claude-sonnet-5-5": Price(2.0, 10.0, 0.20),
    "claude-sonnet-5": Price(2.0, 10.0),
    "claude-sonnet-4-6": Price(3.0, 15.0),
    "claude-haiku-5-5": Price(0.10, 0.50),
    "claude-haiku-4-5": Price(1.0, 5.0),
    "gpt-4": Price(30.0, 60.0),
    "gpt-3.5": Price(0.50, 1.50),
}

DEFAULT_MODEL = "gpt-4"
# Default budget for one command, in USD.
DEFAULT_BUDGET_USD = float(os.environ.get("AGENT_BUDGET_USD", 1.00))
DEFAULT_STATE_NAME = ".cost_checker_state.json"


def default_state_path() -> Path:
    """Where state lives when a tracker is not given a path (read per tracker, not per import)."""
    return Path(os.environ.get("COST_CHECKER_STATE_PATH", DEFAULT_STATE_NAME))

# How much of a turn the user's own text is: the rest is the system prompt, the
# tool schemas and the tool results the agent reads along the way. Callers that
# know the real overhead pass it in; this is the fallback when they don't.
DEFAULT_OVERHEAD_TOKENS = int(os.environ.get("COST_OVERHEAD_TOKENS", 6000))
# A turn is several model calls, and each resends the conversation so far.
DEFAULT_MODEL_CALLS = float(os.environ.get("COST_MODEL_CALLS", 3))
# Output is a fraction of what went in.
OUTPUT_TOKEN_SHARE = 0.15

# Learning config.
DEFAULT_CORRECTION_FACTOR = 1.0
EWMA_ALPHA = 0.25
MIN_CORRECTION_FACTOR = 0.05
# A prompt-only estimate can be an order of magnitude under a tool-heavy turn,
# so the factor has to be free to grow well past the handful of percent that a
# tight clamp would allow.
MAX_CORRECTION_FACTOR = 100.0
# Unconfirmed requests (a turn the server never reported back on) are dropped
# oldest-first past this many, so a long-running server's state file stays small.
MAX_PENDING = 64

_DATE_SUFFIX = re.compile(r"[-@]\d{6,8}$")


def normalise_model(model: str) -> str:
    """``openai/GPT-4`` and ``us.anthropic.claude-opus-5-5-20260401`` -> table keys."""
    name = str(model or "").strip().lower()
    name = name.rsplit("/", 1)[-1]  # openai/gpt-4, anthropic/claude-...
    name = name.rsplit(".", 1)[-1]  # us.anthropic.claude-...
    return _DATE_SUFFIX.sub("", name)


def _price_from_env() -> Price | None:
    raw = os.environ.get("SCOOP_MODEL_PRICE_USD_PER_MTOK", "").strip()
    if not raw:
        return None
    try:
        parts = [float(piece) for piece in raw.split(",")]
    except ValueError:
        return None
    if len(parts) == 2:
        return Price(parts[0], parts[1])
    return Price(parts[0], parts[1], parts[2]) if len(parts) == 3 else None


def price_for(model: str) -> Price | None:
    """The price of a model, or None when nothing here knows it."""
    name = normalise_model(model)
    if name in MODEL_PRICING:
        return MODEL_PRICING[name]
    # A longest-prefix match, so a dated or sized variant still prices.
    matches = [key for key in MODEL_PRICING if name.startswith(key)]
    if matches:
        return MODEL_PRICING[max(matches, key=len)]
    return _price_from_env()


def estimate_tokens(text: str) -> int:
    """Tokens in a piece of text (English averages about four characters each)."""
    return max(1, len(text) // 4)


def cost_from_usage(model: str, input_tokens: int = 0, output_tokens: int = 0,
                    cached_input_tokens: int = 0) -> float | None:
    """Price real token counters. None when the model has no price."""
    price = price_for(model)
    if price is None:
        return None
    # Providers report cache hits inside the input count; charge them once, at the lower rate.
    cached = max(0, int(cached_input_tokens))
    fresh = max(0, max(0, int(input_tokens)) - cached)
    return (fresh * price.input + cached * price.cache_read
            + max(0, int(output_tokens)) * price.output) / 1_000_000


def _empty_state() -> dict[str, Any]:
    return {"version": 1, "models": {}, "pending": {}, "threads": {}}


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


class CostTracker:
    """Estimates, budget checks and per-model calibration, kept in one JSON file.

    Safe to share: every read-modify-write is held under one lock and the file
    is replaced atomically, so concurrent turns cannot interleave into a
    half-written state file.
    """

    def __init__(self, path: Path | str | None = None, budget_usd: float = DEFAULT_BUDGET_USD) -> None:
        self.path = Path(path) if path is not None else default_state_path()
        self.budget_usd = float(budget_usd)
        self._lock = threading.Lock()

    # -- state -------------------------------------------------------------
    def _load(self) -> dict[str, Any]:
        try:
            state = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return _empty_state()
        return state if isinstance(state, dict) else _empty_state()

    def _save(self, state: dict[str, Any]) -> None:
        pending = state.setdefault("pending", {})
        # Dicts keep insertion order, so the oldest unconfirmed requests go first.
        for key in list(pending)[:max(0, len(pending) - MAX_PENDING)]:
            pending.pop(key, None)
        temp = self.path.with_suffix(self.path.suffix + f".{os.getpid()}.tmp")
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(temp, self.path)
        except OSError:
            temp.unlink(missing_ok=True)  # a cost estimate is never worth failing a turn over

    def _model_state(self, state: dict[str, Any], model: str) -> dict[str, Any]:
        models = state.setdefault("models", {})
        return models.setdefault(normalise_model(model), {
            "correction_factor": DEFAULT_CORRECTION_FACTOR, "samples": 0, "ewma_relative_error": 0.0,
        })

    def calibration(self, model: str = DEFAULT_MODEL) -> dict[str, float]:
        with self._lock:
            entry = self._model_state(self._load(), model)
        return {"correction_factor": float(entry["correction_factor"]), "samples": int(entry["samples"]),
                "ewma_relative_error": float(entry["ewma_relative_error"])}

    def reset(self, model: str | None = None) -> None:
        with self._lock:
            if model is None:
                self._save(_empty_state())
            else:
                state = self._load()
                state.setdefault("models", {}).pop(normalise_model(model), None)
                self._save(state)

    # -- per-conversation budgets -----------------------------------------
    def set_budget(self, thread_id: str, budget_usd: float) -> dict[str, Any]:
        """Set what a conversation may spend, keeping whatever it has spent so far."""
        if not isinstance(thread_id, str) or not thread_id:
            raise ValueError("thread_id must be a nonempty string")
        budget = float(budget_usd)
        if not budget > 0:
            raise ValueError("budget_usd must be greater than zero")
        with self._lock:
            state = self._load()
            entry = state.setdefault("threads", {}).setdefault(thread_id, {"spent_usd": 0.0, "turns": 0})
            entry["budget_usd"] = budget
            self._save(state)
        return self._view(thread_id, entry)

    def budget(self, thread_id: str) -> dict[str, Any] | None:
        """What this conversation set, spent and has left; None when it never set a budget."""
        with self._lock:
            entry = self._load().get("threads", {}).get(thread_id)
        return self._view(thread_id, entry) if isinstance(entry, dict) else None

    def clear_budget(self, thread_id: str) -> None:
        with self._lock:
            state = self._load()
            if state.setdefault("threads", {}).pop(thread_id, None) is not None:
                self._save(state)

    @staticmethod
    def _view(thread_id: str, entry: dict[str, Any]) -> dict[str, Any]:
        budget = float(entry.get("budget_usd", 0.0))
        spent = float(entry.get("spent_usd", 0.0))
        return {"thread_id": thread_id, "budget_usd": round(budget, 6), "spent_usd": round(spent, 6),
                "remaining_usd": round(budget - spent, 6), "turns": int(entry.get("turns", 0))}

    def _record_spend(self, state: dict[str, Any], thread_id: str | None, amount: float) -> dict[str, Any] | None:
        """Charge a turn to its conversation. Called with the lock already held."""
        if not thread_id:
            return None
        entry = state.setdefault("threads", {}).get(thread_id)
        if not isinstance(entry, dict):
            return None  # no budget was set for this conversation; nothing to draw down
        entry["spent_usd"] = float(entry.get("spent_usd", 0.0)) + max(0.0, float(amount))
        entry["turns"] = int(entry.get("turns", 0)) + 1
        return self._view(thread_id, entry)

    # -- estimating --------------------------------------------------------
    def estimate(
        self,
        query: str,
        model: str = DEFAULT_MODEL,
        thread_id: str | None = None,
        budget_usd: float | None = None,
        overhead_tokens: int = DEFAULT_OVERHEAD_TOKENS,
        model_calls: float = DEFAULT_MODEL_CALLS,
        track_request: bool = True,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        """What this command looks like it will cost, before anything runs.

        ``overhead_tokens`` is everything that travels with the user's text
        (system prompt, tool schemas, workspace context); ``model_calls`` is how
        many times a turn is expected to call the model, each resending the
        conversation. The learned correction factor is applied on top, so the
        estimate converges on what turns actually cost even though this
        arithmetic cannot see which tools the agent will choose to call.

        With a ``thread_id`` whose conversation set a budget, the estimate is
        checked against what that conversation has left rather than the
        server-wide default.
        """
        prompt_tokens = estimate_tokens(query)
        input_tokens = int((prompt_tokens + max(0, int(overhead_tokens))) * max(1.0, float(model_calls)))
        output_tokens = int(input_tokens * OUTPUT_TOKEN_SHARE)
        base = cost_from_usage(model, input_tokens=input_tokens, output_tokens=output_tokens)
        resolved_id = request_id or uuid.uuid4().hex

        with self._lock:
            state = self._load()
            factor = float(self._model_state(state, model)["correction_factor"])
            estimate = None if base is None else base * factor
            entry = state.get("threads", {}).get(thread_id) if thread_id else None
            ledger = self._view(thread_id, entry) if isinstance(entry, dict) else None
            if budget_usd is not None:
                budget = float(budget_usd)
            elif ledger is not None:
                budget = ledger["remaining_usd"]
            else:
                budget = self.budget_usd
            if track_request:
                state.setdefault("pending", {})[resolved_id] = {
                    "model": normalise_model(model), "tokens": input_tokens + output_tokens,
                    "base_estimated_cost_usd": base, "estimated_cost_usd": estimate,
                    **({"thread_id": thread_id} if thread_id else {}),
                }
                self._save(state)

        return {
            "request_id": resolved_id,
            "model": model,
            "prompt_tokens": prompt_tokens,
            "tokens": input_tokens + output_tokens,
            "estimated_input_tokens": input_tokens,
            "estimated_output_tokens": output_tokens,
            "base_estimated_cost_usd": base if base is None else round(base, 6),
            "estimated_cost_usd": estimate if estimate is None else round(estimate, 6),
            "correction_factor": round(factor, 4),
            "budget_usd": round(budget, 6),
            # What the budget applies to: a conversation's remaining allowance, or the server default.
            "budget_scope": "conversation" if ledger is not None else "default",
            "budget": ledger,
            # An unpriced model is not "within budget": nothing was checked.
            "within_budget": estimate is not None and estimate <= budget,
            "priced": estimate is not None,
        }

    # -- learning ----------------------------------------------------------
    def confirm(self, request_id: str, actual_cost_usd: float) -> dict[str, Any]:
        """Record what a tracked request really cost and calibrate the model."""
        if actual_cost_usd < 0:
            raise ValueError("actual_cost_usd must be non-negative")
        with self._lock:
            state = self._load()
            request = state.setdefault("pending", {}).pop(request_id, None)
            if request is None:
                raise ValueError(f"Unknown request_id: {request_id}")

            model = str(request["model"])
            base = request.get("base_estimated_cost_usd")
            previous = request.get("estimated_cost_usd")
            entry = self._model_state(state, model)
            old_factor = float(entry["correction_factor"])

            if base is None:
                # The model had no price when it was estimated: nothing to calibrate against.
                ledger = self._record_spend(state, request.get("thread_id"), actual_cost_usd)
                self._save(state)
                return {"request_id": request_id, "model": model,
                        "actual_cost_usd": round(float(actual_cost_usd), 8),
                        "previous_estimated_cost_usd": None, "budget": ledger,
                        "old_correction_factor": round(old_factor, 8),
                        "new_correction_factor": round(old_factor, 8), "samples": int(entry["samples"])}

            base = float(base)
            ratio = actual_cost_usd / base if base > 0 else DEFAULT_CORRECTION_FACTOR
            new_factor = _clamp((1 - EWMA_ALPHA) * old_factor + EWMA_ALPHA * ratio,
                                MIN_CORRECTION_FACTOR, MAX_CORRECTION_FACTOR)
            entry["correction_factor"] = new_factor
            entry["samples"] = int(entry["samples"]) + 1

            previous = old_factor * base if previous is None else float(previous)
            error = abs(previous - actual_cost_usd) / actual_cost_usd if actual_cost_usd > 0 else 0.0
            entry["ewma_relative_error"] = ((1 - EWMA_ALPHA) * float(entry.get("ewma_relative_error", 0.0))
                                            + EWMA_ALPHA * error)
            ledger = self._record_spend(state, request.get("thread_id"), actual_cost_usd)
            self._save(state)

        return {"request_id": request_id, "model": model,
                "actual_cost_usd": round(float(actual_cost_usd), 8),
                "previous_estimated_cost_usd": round(previous, 8), "budget": ledger,
                "old_correction_factor": round(old_factor, 8),
                "new_correction_factor": round(new_factor, 8), "samples": int(entry["samples"])}

    def confirm_usage(self, request_id: str, model: str, usage: dict[str, int]) -> dict[str, Any] | None:
        """Confirm a request from a turn's token counters. None when unpriced or untracked.

        ``usage`` is LangChain's ``usage_metadata`` shape, accumulated over the
        turn: ``input_tokens``, ``output_tokens`` and ``cache_read``.
        """
        actual = cost_from_usage(
            model,
            input_tokens=int(usage.get("input_tokens", 0) or 0),
            output_tokens=int(usage.get("output_tokens", 0) or 0),
            cached_input_tokens=int(usage.get("cache_read", usage.get("cached_input_tokens", 0)) or 0),
        )
        if actual is None:
            self.forget(request_id)
            return None
        try:
            return {**self.confirm(request_id, actual), "actual_cost_usd": round(actual, 8)}
        except ValueError:
            return None  # the request was pruned or already confirmed

    def forget(self, request_id: str) -> None:
        """Drop a tracked request without calibrating (a turn that never billed)."""
        with self._lock:
            state = self._load()
            if state.setdefault("pending", {}).pop(request_id, None) is not None:
                self._save(state)


# A module-level tracker so the simple functions below keep working unchanged.
_default = CostTracker()


def check_budget(query: str, budget_usd: float | None = None, model: str = DEFAULT_MODEL,
                 track_request: bool = True, request_id: str | None = None, **kwargs: Any) -> dict[str, Any]:
    return _default.estimate(query, model=model, budget_usd=budget_usd, track_request=track_request,
                             request_id=request_id, **kwargs)


def estimate_cost(tokens: int, model: str = DEFAULT_MODEL, calibrated: bool = True) -> float | None:
    """What ``tokens`` input tokens cost on a model, calibrated unless asked otherwise."""
    price = price_for(model)
    if price is None:
        return None
    base = max(0, int(tokens)) / 1_000_000 * price.input
    return base * _default.calibration(model)["correction_factor"] if calibrated else base


def estimate_actual_cost_from_usage(model: str, input_tokens: int, output_tokens: int = 0,
                                    cached_input_tokens: int = 0) -> float | None:
    return cost_from_usage(model, input_tokens, output_tokens, cached_input_tokens)


def confirm_actual_cost(request_id: str, actual_cost_usd: float) -> dict[str, Any]:
    return _default.confirm(request_id, actual_cost_usd)


def get_model_calibration(model: str = DEFAULT_MODEL) -> dict[str, float]:
    return _default.calibration(model)


def reset_calibration(model: str | None = None) -> None:
    _default.reset(model)


def run_with_auto_calibration(
    query: str,
    command_fn: Callable[[], Any],
    budget_usd: float | None = None,
    model: str = DEFAULT_MODEL,
    actual_cost_usd: float | None = None,
    usage: dict[str, int] | None = None,
    require_calibration_signal: bool = True,
    tracker: CostTracker | None = None,
    **kwargs: Any,
) -> dict[str, Any]:
    """Run a command, then calibrate from whatever cost truth came back.

    Pass ``actual_cost_usd`` or ``usage`` (``input_tokens``, ``output_tokens``,
    ``cached_input_tokens``), or return either from ``command_fn`` as a dict —
    at the top level or under a nested ``usage`` key.
    """
    costs = tracker or _default
    budget_result = costs.estimate(query, model=model, budget_usd=budget_usd, **kwargs)
    request_id = budget_result["request_id"]

    execution_result = command_fn()
    actual = actual_cost_usd

    if actual is None and isinstance(execution_result, dict):
        if execution_result.get("actual_cost_usd") is not None:
            actual = float(execution_result["actual_cost_usd"])
        else:
            source = execution_result.get("usage")
            source = source if isinstance(source, dict) else execution_result
            found = {key: int(source.get(key, 0) or 0)
                     for key in ("input_tokens", "output_tokens", "cached_input_tokens")}
            if any(value > 0 for value in found.values()):
                usage = found

    if actual is None and usage is not None:
        actual = cost_from_usage(model, input_tokens=int(usage.get("input_tokens", 0)),
                                 output_tokens=int(usage.get("output_tokens", 0)),
                                 cached_input_tokens=int(usage.get("cached_input_tokens", 0)))

    if actual is None:
        costs.forget(request_id)
        if require_calibration_signal:
            raise ValueError(
                "No calibration signal provided. Pass `actual_cost_usd`, `usage`, or return "
                "usage/cost from command_fn (an unpriced model also lands here)."
            )
        return {"budget": budget_result, "result": execution_result, "calibration_feedback": None}

    return {"budget": budget_result, "result": execution_result,
            "calibration_feedback": costs.confirm(request_id, float(actual))}


def describe(estimate: dict[str, Any], actual: dict[str, Any] | None = None) -> str:
    """One line for a person: what the command should cost, or what it cost and what is left."""
    if actual is not None and actual.get("actual_cost_usd") is not None:
        text = f"This command cost ${actual['actual_cost_usd']:.4f}"
        ledger = actual.get("budget")
        if ledger:
            text += (f" · ${max(0.0, ledger['remaining_usd']):.4f} left of this conversation's "
                     f"${ledger['budget_usd']:.2f} budget")
            if ledger["remaining_usd"] <= 0:
                text += " (budget spent)"
        return text
    if not estimate.get("priced"):
        return (f"No price is known for {estimate.get('model')}, so this command cannot be costed. "
                "Set SCOOP_MODEL_PRICE_USD_PER_MTOK to price it.")
    scope = "left in this conversation" if estimate.get("budget_scope") == "conversation" else "budget"
    text = (f"Estimated cost ${estimate['estimated_cost_usd']:.4f} "
            f"of ${estimate['budget_usd']:.4f} {scope}")
    if not estimate.get("within_budget"):
        text += " — over budget"
    return text


__all__ = ["CostTracker", "MODEL_PRICING", "Price", "check_budget", "confirm_actual_cost", "cost_from_usage",
           "default_state_path", "describe", "estimate_actual_cost_from_usage", "estimate_cost", "estimate_tokens",
           "get_model_calibration", "normalise_model", "price_for", "reset_calibration",
           "run_with_auto_calibration"]


if __name__ == "__main__":
    import sys

    prompt = sys.argv[1] if len(sys.argv) > 1 else "Summarize the latest arXiv paper on transformers."
    model = sys.argv[2] if len(sys.argv) > 2 else DEFAULT_MODEL
    budget = float(sys.argv[3]) if len(sys.argv) > 3 else None
    result = check_budget(prompt, budget, model=model)
    print(f"Prompt: {prompt}"
          f"\nModel: {result['model']} (correction factor {result['correction_factor']}×)"
          f"\nRequest ID: {result['request_id']}"
          f"\nTokens: {result['estimated_input_tokens']} in + {result['estimated_output_tokens']} out"
          f"\nBase estimate: {result['base_estimated_cost_usd']}"
          f"\n{describe(result)}")
