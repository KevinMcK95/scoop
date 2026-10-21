"""Running a sub-agent (the data agent, the proper-motion agent) inside a chat turn.

A sub-agent is its own LangGraph loop with its own prompt and tools. The chat agent
hands it one request; it returns a report, the artifacts its tools published (map
actions, layers, field jobs: they reach the browser through the hand-off) and its
failed tool calls (for the audit log).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langgraph.errors import GraphRecursionError

from .graph import build_chat_graph


@dataclass
class SubagentResult:
    report: str
    artifacts: dict = field(default_factory=dict)
    failures: list[dict] = field(default_factory=list)


def failures(messages: list) -> list[dict]:
    """Failed tool calls: tool, reason and the arguments passed."""
    calls = {c["id"]: c for m in messages if isinstance(m, AIMessage) for c in m.tool_calls}
    return [{"tool": calls.get(m.tool_call_id, {}).get("name") or m.name,
             "reason": str(m.content)[:400],
             "args": json.dumps(calls.get(m.tool_call_id, {}).get("args"), default=str)[:1500]}
            for m in messages if isinstance(m, ToolMessage) and m.status == "error"]


async def run_subagent(model, tools: list, prompt: str, text: str, *, max_steps: int, name: str) -> SubagentResult:
    """One request to a sub-agent. A failure (the model endpoint unreachable, too many
    steps) becomes the report instead of ending the chat turn."""
    graph = build_chat_graph(model, tools, system_prompt=prompt)
    try:
        result = await graph.ainvoke({"messages": [HumanMessage(content=text)], "artifacts": {}},
                                     config={"recursion_limit": 2 * max_steps + 1})
    except GraphRecursionError:
        return SubagentResult(f"The {name} stopped after {max_steps} steps without finishing.")
    except Exception as exc:  # e.g. the model endpoint unreachable: a report, not a dead turn
        return SubagentResult(f"The {name} failed ({type(exc).__name__}: {exc or 'no message'}); nothing was "
                              "done. A ConnectError usually means this machine's network dropped; try again.")
    final = next((m for m in reversed(result["messages"]) if isinstance(m, AIMessage) and m.content), None)
    report = final.content if final and isinstance(final.content, str) else str(final.content if final else "")
    return SubagentResult(report, dict(result.get("artifacts") or {}), failures(result["messages"]))


def attach(command, result: SubagentResult):
    """Put the sub-agent's artifacts (before the hand-off's own) and failures on the
    hand-off's Command."""
    command.update["artifacts"] = {**result.artifacts, **command.update["artifacts"]}
    if result.failures:  # the audit log records them with the chat agent's call
        command.update["messages"][0].artifact = {"inner_failures": result.failures}
    return command


__all__ = ["SubagentResult", "attach", "failures", "run_subagent"]
