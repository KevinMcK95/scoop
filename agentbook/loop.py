"""The agent loop: call the model, run the tools it asks for, repeat.

`run_agent` is the whole agent. Everything else in this package is either
a tool, a model, or a check around this loop.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable

from .models import Model, ModelResponse, Usage
from .tools import ToolRegistry

DEFAULT_SYSTEM = (
    "You are a careful research assistant. Answer using the tools provided. "
    "Base every claim on a tool result and cite the model IDs you used. "
    "If the tools cannot answer part of the question, say so instead of guessing."
)


@dataclass
class AgentResult:
    """The outcome of one run.

    stop_reason is "done" (the model finished), "max_steps" or "budget".
    """
    text: str
    messages: list[dict]
    steps: int
    usage: Usage
    stop_reason: str


def run_agent(model: Model, tools: ToolRegistry, task: str | list[dict], system: str = DEFAULT_SYSTEM,
              max_steps: int = 10, max_tokens_total: int = 200_000, max_tokens: int = 4096,
              tracer=None, on_step: Callable[[int, list[dict]], None] | None = None) -> AgentResult:
    """Run the model with tools until it stops asking for them, or a limit is hit.

    `task` is the user's question, or a message list to continue from.
    """
    messages = [{"role": "user", "content": task}] if isinstance(task, str) else list(task)
    usage = Usage()
    if tracer:
        tracer.run_start(messages[-1]["content"], system, [t["name"] for t in tools.specs()])
    for step in range(1, max_steps + 1):
        response = call_model(model, system, messages, tools, max_tokens, step, tracer)
        usage = usage + response.usage
        messages.append({"role": "assistant", "content": response.content})
        if not response.tool_uses:  # no tool requests: the model is done
            return finish(response.text, messages, step, usage, "done", tracer, on_step)
        results = [call_tool(tools, block, step, tracer) for block in response.tool_uses]
        messages.append({"role": "user", "content": results})
        if on_step:
            on_step(step, messages)
        if usage.total_tokens >= max_tokens_total:
            return finish("", messages, step, usage, "budget", tracer)
    return finish("", messages, max_steps, usage, "max_steps", tracer)


def call_model(model: Model, system: str, messages: list[dict], tools: ToolRegistry,
               max_tokens: int, step: int, tracer=None) -> ModelResponse:
    """One model call, timed and traced."""
    start = time.perf_counter()
    response = model.complete(system, messages, tools.specs(), max_tokens)
    if tracer:
        tracer.model_call(step, messages, response, ms=1000 * (time.perf_counter() - start))
    return response


def call_tool(tools: ToolRegistry, block: dict, step: int, tracer=None) -> dict:
    """One tool call, timed and traced. Returns a tool_result block."""
    start = time.perf_counter()
    result = tools.run(block)
    if tracer:
        tracer.tool_call(step, block, result, ms=1000 * (time.perf_counter() - start))
    return result


def finish(text: str, messages: list[dict], steps: int, usage: Usage, stop_reason: str,
           tracer=None, on_step=None) -> AgentResult:
    """Build the result and record the end of the run."""
    if on_step:
        on_step(steps, messages)
    if tracer:
        tracer.run_end(steps, stop_reason, usage, text)
    return AgentResult(text, messages, steps, usage, stop_reason)
