"""Guardrails: checks in code, not in the prompt.

- Budget: stop on steps, tokens or wall-clock time; estimate cost.
- check_cited_ids: every model ID in the answer must appear in a tool result.
- repeated_calls / LoopGuard: spot and block the same call made again and again.
"""

from __future__ import annotations

import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field

from .models import Usage, blocks_of
from .tools import ToolRegistry

# Registry model IDs look like 2305_06364_twin_sterile_neutrino_dark_matter_v1
MODEL_ID_RE = re.compile(r"(?<![\w.\-])[A-Za-z0-9][\w.\-]*?_v\d+(?!\w)")


# ------------------------------------------------------------------ budgets


@dataclass
class Budget:
    """Limits for one run. `exceeded` returns the reason to stop, or None."""
    max_steps: int = 20
    max_tokens: int = 200_000
    max_seconds: float = 600.0
    started: float = field(default_factory=time.monotonic)

    def exceeded(self, steps: int, usage: Usage) -> str | None:
        if steps >= self.max_steps:
            return f"step limit ({steps}/{self.max_steps})"
        if usage.total_tokens >= self.max_tokens:
            return f"token budget ({usage.total_tokens}/{self.max_tokens})"
        if time.monotonic() - self.started >= self.max_seconds:
            return f"time limit ({self.max_seconds:.0f} s)"
        return None

    def check(self, step: int, messages: list[dict]) -> None:
        """Use as run_agent(..., on_step=budget.check): raise BudgetExceeded when the
        step or time limit is hit and the run would go on. on_step does not see token
        usage, so keep run_agent's max_tokens_total for the token limit."""
        if messages and messages[-1]["role"] == "assistant":
            return  # the model has answered; the run is finishing anyway
        reason = self.exceeded(step, Usage())
        if reason:
            raise BudgetExceeded(reason)


class BudgetExceeded(RuntimeError):
    """Raised by Budget.check to stop a run from on_step."""


def estimate_cost(usage: Usage, usd_per_mtok_in: float, usd_per_mtok_out: float) -> float:
    """Dollar cost of a run, given per-million-token prices you look up yourself."""
    return (usage.input_tokens * usd_per_mtok_in + usage.output_tokens * usd_per_mtok_out) / 1e6


# ------------------------------------------------------------- cited IDs


def find_model_ids(text: str) -> list[str]:
    """Registry-style model IDs mentioned in a text, in order, without duplicates."""
    return list(dict.fromkeys(MODEL_ID_RE.findall(text)))


def tool_outputs(messages: list[dict]) -> list[str]:
    """The text of every tool result in a message list."""
    return [b["content"] if isinstance(b["content"], str) else json.dumps(b["content"])
            for m in messages if m["role"] == "user"
            for b in blocks_of(m) if b["type"] == "tool_result"]


def tool_outputs_from_trace(events: list[dict]) -> list[str]:
    """The text of every tool result in a trace (see tracing.py)."""
    return [e["output"] for e in events if e["event"] == "tool_call" and not e["is_error"]]


@dataclass
class CitationReport:
    cited: list[str]
    supported: list[str]
    unsupported: list[str]

    @property
    def ok(self) -> bool:
        return not self.unsupported


def check_cited_ids(answer: str, evidence: list[dict] | list[str]) -> CitationReport:
    """Check that every model ID in `answer` appears in some tool result.

    `evidence` is a message list, or a list of tool-output strings.
    """
    outputs = evidence if evidence and isinstance(evidence[0], str) else tool_outputs(evidence)
    seen = "\n".join(outputs)
    cited = find_model_ids(answer)
    supported = [i for i in cited if i in seen]
    return CitationReport(cited, supported, [i for i in cited if i not in seen])


# ------------------------------------------------------------------ loops


def repeated_calls(messages: list[dict], max_repeats: int = 2) -> list[dict]:
    """Tool calls made with identical arguments more than `max_repeats` times."""
    counts = Counter(
        (b["name"], json.dumps(b["input"], sort_keys=True))
        for m in messages if m["role"] == "assistant"
        for b in blocks_of(m) if b["type"] == "tool_use")
    return [{"name": n, "input": json.loads(i), "count": c}
            for (n, i), c in counts.items() if c > max_repeats]


class LoopGuard(ToolRegistry):
    """A ToolRegistry that refuses a call repeated more than `max_repeats` times,
    and tells the model why, instead of running it again."""

    def __init__(self, tools=(), max_repeats: int = 2, **kwargs):
        super().__init__(tools, **kwargs)
        self.max_repeats = max_repeats
        self.counts: Counter = Counter()

    def call(self, name: str, input: dict) -> tuple[str, bool]:
        key = (name, json.dumps(input, sort_keys=True))
        self.counts[key] += 1
        if self.counts[key] > self.max_repeats:
            return (f"Refused: {name} was already called {self.counts[key] - 1} times with "
                    "these exact arguments and the result will not change. Use the earlier "
                    "result, change the arguments, or finish with what you have."), True
        return super().call(name, input)
