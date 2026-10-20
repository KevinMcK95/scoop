"""Developer traces of chat turns, for the workspace's Debug tab.

A LangChain callback records what each turn did, the way an observability
service would but kept on this machine: every model call (what was sent, what
came back, tokens and cache hits, duration) and every tool call (arguments,
result, duration, status). Traces are saved per conversation as JSON lines in a
directory only the user can read, and deleted with the conversation.

Unlike the audit log, traces hold message text: questions, field rows in tool
results, code. Images are replaced by a note of their size.
"""
from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage, ToolMessage

DEFAULT_DIR = Path.home() / ".config" / "scoop" / "traces"
THREAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
MAX_TEXT = 20_000  # characters kept of any one message or result
MAX_TRACES = 50  # per conversation, newest kept


def _cut(text: str) -> str:
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + f"\n[… {len(text) - MAX_TEXT:,} more characters]"


def _content(content: Any) -> str:
    """Message content as text; images become a size note."""
    if isinstance(content, str):
        return _cut(re.sub(r"data:image/[a-z]+;base64,[A-Za-z0-9+/=]+",
                           lambda m: f"[image, {len(m.group(0)) * 3 // 4 // 1024} KB]", content))
    parts = []
    for part in content or []:
        if isinstance(part, dict) and part.get("type") == "text":
            parts.append(part.get("text", ""))
        elif isinstance(part, dict) and part.get("type") in ("image_url", "image"):
            url = (part.get("image_url") or {}).get("url", "") if isinstance(part.get("image_url"), dict) else ""
            parts.append(f"[image, {len(url) * 3 // 4 // 1024} KB]")
        else:
            parts.append(str(part))
    return _cut("\n".join(parts))


def _message(message: BaseMessage) -> dict:
    out = {"role": message.type, "content": _content(message.content)}
    if getattr(message, "tool_calls", None):
        out["tool_calls"] = [{"name": c["name"], "args": c["args"], "id": c.get("id")} for c in message.tool_calls]
    if isinstance(message, ToolMessage):
        out["tool_call_id"] = message.tool_call_id
    return out


def _model_name(name: Any) -> str | None:
    """A streamed reply's model name can arrive concatenated once per chunk."""
    if not isinstance(name, str) or not name:
        return None
    for size in range(1, len(name) // 2 + 1):
        if len(name) % size == 0 and name == name[:size] * (len(name) // size):
            return name[:size]
    return name


def _tool_output(output: Any) -> tuple[str, str, bool]:
    """(text, status, has_image) of what a tool returned (a ToolMessage, a Command or text)."""
    if hasattr(output, "update") and isinstance(getattr(output, "update", None), dict):
        messages = [m for m in output.update.get("messages", []) if isinstance(m, ToolMessage)]
        output = messages[0] if messages else json.dumps(output.update, default=str)
    if isinstance(output, ToolMessage):
        image = isinstance(output.artifact, dict) and isinstance(output.artifact.get("image"), str)
        return _content(output.content), output.status or "success", image
    return _content(str(output)), "success", False


class TurnTracer(AsyncCallbackHandler):
    """Collects one turn's model and tool steps; ``result()`` gives the trace."""

    def __init__(self, request_id: str, thread_id: str, question: str) -> None:
        self.request_id, self.thread_id, self.question = request_id, thread_id, question
        self.started_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
        self.t0 = time.monotonic()
        self.steps: list[dict] = []
        self._open: dict[Any, tuple[dict, float]] = {}
        self._seen = 0  # messages of the conversation already recorded

    def _ms(self, t: float | None = None) -> int:
        return round(((t or time.monotonic()) - self.t0) * 1000)

    def _start(self, run_id, step: dict) -> None:
        now = time.monotonic()
        step["start_ms"] = self._ms(now)
        self.steps.append(step)
        self._open[run_id] = (step, now)

    def _finish(self, run_id) -> dict | None:
        step, began = self._open.pop(run_id, (None, 0.0))
        if step is not None:
            step["duration_ms"] = round((time.monotonic() - began) * 1000)
        return step

    async def on_chat_model_start(self, serialized, messages, *, run_id, **kwargs) -> None:
        batch = messages[0] if messages else []
        # The first call shows everything sent (system prompt, history, this question);
        # later calls only what was added since (tool results, images).
        new = batch[self._seen:] if self._seen <= len(batch) else batch
        self._seen = len(batch)
        params = kwargs.get("invocation_params") or {}
        self._start(run_id, {"kind": "model", "sent_count": len(batch), "sent": [_message(m) for m in new],
                             "model": params.get("model") or params.get("model_name")
                             or (kwargs.get("metadata") or {}).get("ls_model_name")})

    async def on_llm_end(self, response, *, run_id, **kwargs) -> None:
        step = self._finish(run_id)
        if step is None:
            return
        generation = response.generations[0][0] if response.generations and response.generations[0] else None
        message = getattr(generation, "message", None)
        if message is not None:
            self._seen += 1  # the reply becomes part of the next call's input
            step["reply"] = _message(message)
            usage = getattr(message, "usage_metadata", None) or {}
            step["usage"] = {"input": usage.get("input_tokens"), "output": usage.get("output_tokens"),
                             "cached": (usage.get("input_token_details") or {}).get("cache_read")}
            if not step.get("model"):
                step["model"] = _model_name((message.response_metadata or {}).get("model_name"))

    async def on_llm_error(self, error, *, run_id, **kwargs) -> None:
        step = self._finish(run_id)
        if step is not None:
            step["error"] = f"{type(error).__name__}: {error}"

    async def on_tool_start(self, serialized, input_str, *, run_id, inputs=None, **kwargs) -> None:
        args = inputs if isinstance(inputs, dict) else input_str
        self._start(run_id, {"kind": "tool", "name": (serialized or {}).get("name") or kwargs.get("name"),
                             "args": json.loads(json.dumps(args, default=str))})

    async def on_tool_end(self, output, *, run_id, **kwargs) -> None:
        step = self._finish(run_id)
        if step is not None:
            step["result"], step["status"], image = _tool_output(output)
            if image:
                step["image"] = True

    async def on_tool_error(self, error, *, run_id, **kwargs) -> None:
        step = self._finish(run_id)
        if step is not None:
            step["status"], step["result"] = "error", f"{type(error).__name__}: {error}"

    def result(self, error: str | None = None) -> dict:
        models = [s for s in self.steps if s["kind"] == "model"]
        tools = [s for s in self.steps if s["kind"] == "tool"]
        total = lambda key: sum((s.get("usage") or {}).get(key) or 0 for s in models)  # noqa: E731
        return {
            "request_id": self.request_id, "thread_id": self.thread_id, "started_at": self.started_at,
            "duration_ms": self._ms(), "question": _cut(self.question), "error": error,
            "totals": {"model_calls": len(models), "input_tokens": total("input"), "output_tokens": total("output"),
                       "cached_tokens": total("cached"), "tool_calls": len(tools),
                       "tool_failures": sum(s.get("status") == "error" for s in tools)},
            "steps": self.steps,
        }


class TraceStore:
    def __init__(self, directory: Path = DEFAULT_DIR) -> None:
        self.directory = Path(directory)

    def _path(self, thread_id: str) -> Path | None:
        return self.directory / f"{thread_id}.jsonl" if THREAD_ID.fullmatch(thread_id) else None

    def save(self, trace: dict) -> None:
        path = self._path(trace["thread_id"])
        if path is None:
            return
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(self.directory, 0o700)
        lines = path.read_text().splitlines()[-(MAX_TRACES - 1):] if path.exists() else []
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write("\n".join([*lines, json.dumps(trace, default=str)]) + "\n")

    def load(self, thread_id: str) -> list[dict] | None:
        """This conversation's traces, newest first (None for an invalid id)."""
        path = self._path(thread_id)
        if path is None:
            return None
        if not path.exists():
            return []
        traces = []
        for line in path.read_text().splitlines():
            try:
                traces.append(json.loads(line))
            except ValueError:
                continue
        return traces[::-1]

    def delete(self, thread_id: str) -> bool:
        path = self._path(thread_id)
        if path is None:
            return False
        path.unlink(missing_ok=True)
        return True


__all__ = ["DEFAULT_DIR", "TraceStore", "TurnTracer"]
