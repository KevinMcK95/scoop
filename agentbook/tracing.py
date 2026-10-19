"""Traces: one JSON line per event, so every run can be read back and checked.

Events written by `Tracer` (all carry run_id, ts and event):

    run_start   task, system, tools
    model_call  step, n_messages, output (content blocks), stop_reason,
                input_tokens, output_tokens, ms, model (as reported by the provider)
    tool_call   step, tool_use_id, name, input, output, is_error, ms
    run_end     steps, stop_reason, input_tokens, output_tokens, final_text

Reading: `load_trace(path)`, `summarize_trace(events)`, `format_trace(events)`.
"""

from __future__ import annotations

import json
import time
import uuid
from collections import Counter
from pathlib import Path


class Tracer:
    """Append events for one run to a JSONL file."""

    def __init__(self, path: str | Path, run_id: str | None = None):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.run_id = run_id or uuid.uuid4().hex[:12]

    def log(self, event: str, **fields) -> dict:
        record = {"run_id": self.run_id, "ts": round(time.time(), 3), "event": event, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        return record

    def run_start(self, task: str, system: str, tools: list[str]) -> None:
        self.log("run_start", task=task, system=system, tools=tools)

    def model_call(self, step: int, messages: list[dict], response, ms: float) -> None:
        self.log("model_call", step=step, n_messages=len(messages), output=response.content,
                 stop_reason=response.stop_reason, input_tokens=response.usage.input_tokens,
                 output_tokens=response.usage.output_tokens, ms=round(ms, 1),
                 model=getattr(response, "model", ""))

    def tool_call(self, step: int, block: dict, result: dict, ms: float) -> None:
        self.log("tool_call", step=step, tool_use_id=block["id"], name=block["name"],
                 input=block["input"], output=result["content"],
                 is_error=result["is_error"], ms=round(ms, 1))

    def run_end(self, steps: int, stop_reason: str, usage, final_text: str) -> None:
        self.log("run_end", steps=steps, stop_reason=stop_reason,
                 input_tokens=usage.input_tokens, output_tokens=usage.output_tokens,
                 final_text=final_text)


def load_trace(path: str | Path, run_id: str | None = None) -> list[dict]:
    """Read a JSONL trace. With run_id, keep only that run; otherwise all runs."""
    events = [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()
              if line.strip()]
    return [e for e in events if run_id is None or e["run_id"] == run_id]


def last_run(events: list[dict]) -> list[dict]:
    """The events of the most recent run in a file that holds several."""
    return [e for e in events if e["run_id"] == events[-1]["run_id"]] if events else []


def summarize_trace(events: list[dict]) -> dict:
    """Counts that answer 'what happened?' at a glance."""
    models = [e for e in events if e["event"] == "model_call"]
    tools = [e for e in events if e["event"] == "tool_call"]
    ends = [e for e in events if e["event"] == "run_end"]
    return {
        "model_calls": len(models),
        "tool_calls": len(tools),
        "tool_errors": sum(e["is_error"] for e in tools),
        "tools_used": dict(Counter(e["name"] for e in tools)),
        "input_tokens": sum(e["input_tokens"] for e in models),
        "output_tokens": sum(e["output_tokens"] for e in models),
        "model_ms": round(sum(e["ms"] for e in models), 1),
        "tool_ms": round(sum(e["ms"] for e in tools), 1),
        "stop_reason": ends[-1]["stop_reason"] if ends else None,
    }


def _short(value, width: int) -> str:
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    text = " ".join(text.split())
    return text if len(text) <= width else text[: width - 3] + "..."


def format_trace(events: list[dict], width: int = 100) -> str:
    """A readable, one-line-per-event view of a trace."""
    lines = []
    for e in events:
        kind = e["event"]
        if kind == "run_start":
            lines.append(f"run {e['run_id']}  task: {_short(e['task'], width)}")
        elif kind == "model_call":
            said = [b.get("text") or f"-> {b['name']}({_short(b['input'], 60)})"
                    for b in e["output"] if b["type"] in ("text", "tool_use")]
            lines.append(f"  [{e['step']}] model  {e['input_tokens']}+{e['output_tokens']} tok "
                         f"{e['ms']:.0f} ms  {e['stop_reason']}: {_short(' | '.join(said), width)}")
        elif kind == "tool_call":
            flag = "ERROR " if e["is_error"] else ""
            lines.append(f"  [{e['step']}] tool   {e['name']} {e['ms']:.0f} ms  "
                         f"{flag}{_short(e['output'], width)}")
        elif kind == "run_end":
            lines.append(f"end: {e['stop_reason']} after {e['steps']} steps, "
                         f"{e['input_tokens'] + e['output_tokens']} tokens")
    return "\n".join(lines)


def format_messages(messages: list[dict], width: int = 90) -> str:
    """The message list, one line per block: the agent's whole state."""
    lines = []
    for i, m in enumerate(messages):
        content = m["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else content
        for b in blocks:
            if b["type"] == "text":
                body = b["text"]
            elif b["type"] == "tool_use":
                body = f"tool_use {b['id']} {b['name']}({json.dumps(b['input'])})"
            elif b["type"] == "tool_result":
                flag = " ERROR" if b.get("is_error") else ""
                body = f"tool_result {b['tool_use_id']}{flag}: {b['content']}"
            else:
                body = b["type"]
            lines.append(f"{i:>2} {m['role']:<9} {_short(body, width)}")
    return "\n".join(lines)


def print_trace(path: str | Path, run_id: str | None = None) -> None:
    """Print a trace file (the last run if run_id is not given)."""
    events = load_trace(path, run_id)
    print(format_trace(events if run_id else last_run(events)))


# --------------------------------------------------------------------------
# Optional export. UNTESTED STUB: no OpenTelemetry collector or Langfuse
# server was available when this was written. Check against current docs.
# --------------------------------------------------------------------------


def export_otel(events: list[dict], service_name: str = "agentbook") -> None:
    """Send a JSONL trace to OpenTelemetry as one span per event (UNTESTED STUB).

    Needs `pip install opentelemetry-sdk opentelemetry-exporter-otlp` and an
    OTLP endpoint (Langfuse, Phoenix and others accept OTLP). Configure the
    endpoint with the standard OTEL_EXPORTER_OTLP_* environment variables.
    """
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": service_name}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    otel = provider.get_tracer("agentbook")
    with otel.start_as_current_span("agent_run") as root:
        for e in events:
            name = e.get("name") or e["event"]
            with otel.start_as_current_span(f"{e['event']}:{name}") as span:
                for key, value in e.items():
                    span.set_attribute(f"agentbook.{key}", _short(value, 4000))
        root.set_attribute("agentbook.run_id", events[0]["run_id"] if events else "")
    provider.force_flush()
