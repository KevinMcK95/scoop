"""Tools: a name, a description, a JSON schema, and a Python function.

The `ToolRegistry` does the work between the model and your functions:
it lists the tools for the model, checks the arguments against the schema,
runs the function, and turns every outcome (including failures) into a
tool_result block the model can read. A tool never crashes the loop.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable, Iterable

import jsonschema

from .models import tool_result_block

DEFAULT_MAX_OUTPUT_CHARS = 8000


class ToolError(Exception):
    """Raise this inside a tool to send a clear, actionable message to the model."""


@dataclass
class Tool:
    """A function the model may call.

    The description is a prompt: say what the tool returns, when to use it
    and what to do if it fails.
    """
    name: str
    description: str
    input_schema: dict
    fn: Callable[..., Any]

    def spec(self) -> dict:
        """The tool as the model sees it (Anthropic format)."""
        return {"name": self.name, "description": self.description,
                "input_schema": self.input_schema}


def truncate(text: str, max_chars: int) -> str:
    """Cut `text` to `max_chars`, with a note the model can see and act on."""
    if len(text) <= max_chars:
        return text
    return (text[:max_chars] + f"\n[... truncated: showing {max_chars} of {len(text)} "
            "characters. Ask for a narrower section or a more specific query.]")


def format_result(result: Any) -> str:
    """Tool results go to the model as text; dicts and lists become JSON."""
    if isinstance(result, str):
        return result
    return json.dumps(result, indent=1, ensure_ascii=False, default=str)


class ToolRegistry:
    """The set of tools an agent may use."""

    def __init__(self, tools: Iterable[Tool] = (), max_output_chars: int = DEFAULT_MAX_OUTPUT_CHARS):
        self.tools: dict[str, Tool] = {}
        self.max_output_chars = max_output_chars
        for t in tools:
            self.add(t)

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def specs(self) -> list[dict]:
        """Tool specs to send with every model call."""
        return [t.spec() for t in self.tools.values()]

    def call(self, name: str, input: dict) -> tuple[str, bool]:
        """Run one tool. Returns (content, is_error); never raises."""
        tool = self.tools.get(name)
        if tool is None:
            return f"Unknown tool '{name}'. Available tools: {', '.join(self.tools)}.", True
        try:
            jsonschema.validate(input, tool.input_schema)
        except jsonschema.ValidationError as e:
            where = ".".join(str(p) for p in e.path) or "arguments"
            return (f"Invalid arguments for {name} ({where}): {e.message}. "
                    f"Expected: {json.dumps(tool.input_schema.get('properties', {}))}"), True
        try:
            result = tool.fn(**input)
        except ToolError as e:
            return str(e), True
        except Exception as e:  # any other bug becomes a readable error, not a crash
            return f"{name} failed with {type(e).__name__}: {e}", True
        return truncate(format_result(result), self.max_output_chars), False

    def run(self, block: dict) -> dict:
        """Run a tool_use block and return the matching tool_result block."""
        content, is_error = self.call(block["name"], block["input"])
        return tool_result_block(block["id"], content, is_error)
