"""HTTP bridge between the browser workspace and the Python chat/ADS services.

The browser never holds model or ADS credentials. Chat turns stream as
server-sent events; the same ADS client serves the literature panel and the
agent's tools. View commands are published as artifacts and only become
"applied" when the browser acknowledges them on a later turn.
"""
from __future__ import annotations

import asyncio
import gzip
import json
import re
import time
import uuid
from collections.abc import AsyncIterator, Callable
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage, AIMessageChunk, HumanMessage, ToolMessage
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.errors import GraphRecursionError
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse, Response, StreamingResponse
from starlette.routing import Route

from ..chat import build_chat_graph
from ..chat.prompt import WORKSPACE_PROMPT
from ..chat.visual import build_map_tools
from ..chat.python_tool import build_python_tools
from ..chat.web_tools import build_web_tools
from ..fields import FieldStore, field_region
from ..literature.models import SearchRequest
from ..literature.serialization import result_payload

THREAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
MAX_MESSAGE_CHARS = 8000
# SPEC.md caps a request at 12 tool steps; each step is an agent and a tools node.
RECURSION_LIMIT = 2 * 12 + 1
MAX_CONSECUTIVE_FAILURES = 4



def _bad_request(message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=400)


def _with_context(text: str, context: dict) -> str:
    return f"{text}\n\n<workspace_context>\n{json.dumps(context, sort_keys=True)}\n</workspace_context>"


def _context_layers(context: dict) -> tuple[str | None, list[dict]]:
    key = context.get("field")
    layers = context.get("layers", [])
    if key is not None and not isinstance(key, str):
        raise ValueError("field must be a string key")
    if not isinstance(layers, list) or not all(
            isinstance(layer, dict) and isinstance(layer.get("id"), str) for layer in layers):
        raise ValueError("layers must be objects with string ids")
    return key, layers


def _text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") for part in content or [] if isinstance(part, dict))


def _sse(event: dict) -> str:
    return f"data: {json.dumps(event)}\n\n"


def _events(update: dict, artifacts_seen: set[str], names: dict[str, str]) -> list[dict]:
    """Project one LangGraph node update onto browser events.

    ``names`` maps tool-call ids to tool names across the turn: a tool that
    returns its own ToolMessage leaves the message's ``name`` unset.
    """
    events: list[dict] = []
    for node_update in update.values():
        if not isinstance(node_update, dict):
            continue
        for message in node_update.get("messages", []):
            if isinstance(message, AIMessage):
                text = _text(message.content)
                if text:
                    events.append({"type": "assistant", "id": message.id, "text": text})
                for call in message.tool_calls:
                    names[call["id"]] = call["name"]
                    events.append({"type": "tool_call", "id": call["id"], "name": call["name"],
                                   "args": call["args"]})
            elif isinstance(message, ToolMessage):
                event = {"type": "tool_result", "id": message.tool_call_id,
                         "name": message.name or names.get(message.tool_call_id), "status": message.status,
                         "summary": message.content}
                # Shown with the step: what run_python ran and printed, and any image a tool returned.
                artifact = message.artifact if isinstance(message.artifact, dict) else {}
                for key in ("image", "code", "output", "error"):
                    if isinstance(artifact.get(key), str):
                        event[key] = artifact[key]
                events.append(event)
        for artifact_id, payload in node_update.get("artifacts", {}).items():
            if artifact_id not in artifacts_seen:
                artifacts_seen.add(artifact_id)
                events.append({"type": "artifact", "id": artifact_id, "payload": payload})
    return events


def _reason(text: str) -> str:
    """The useful part of a tool error: argument-validation errors start by echoing the call."""
    marker = " with error:"
    return (text[text.index(marker) + len(marker):] if marker in text else text).strip()[:400]


def _audit_record(request_id: str, thread_id: str, started: float, events: list[dict],
                  usage: dict[str, int], error: str | None) -> dict:
    """One line per turn, named after the metrics_schema.json fields on origin/radha.

    Judgment fields (task_success, user_accepted, citations_valid) are left for
    evaluation to add; the server cannot know them when the turn ends.
    """
    results = [event for event in events if event["type"] == "tool_result"]
    return {
        "request_id": request_id,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "thread_id": thread_id,
        "latency_ms": round((time.monotonic() - started) * 1000),
        "tool_calls_total": len(results),
        "tool_calls_failed": sum(1 for event in results if event["status"] == "error"),
        "tools": [event["name"] for event in results],
        # Why tool calls failed (the tool's own short message), for improving prompts and tools.
        "failures": [{"tool": event["name"], "reason": _reason(str(event["summary"]))}
                     for event in results if event["status"] == "error"],
        "input_tokens": usage.get("input_tokens", 0),
        "cached_input_tokens": usage.get("cache_read", 0),
        "model_calls": usage.get("model_calls", 0),
        "output_tokens": usage.get("output_tokens", 0),
        "error": error,
    }


def create_app(
    *,
    model: Any = None,
    ads_client: Any = None,
    extra_tools: Callable[[], list] | None = None,
    checkpointer: Any = None,
    audit_path: Path | None = None,
    fields: FieldStore | None = None,
    resolver: Any = None,
    simbad: Any = None,
    jobs: Any = None,
    lvdb: Any = None,
    fetch_image: Any = None,
    web: tuple[Any, Any] | None = None,
    python: Any = None,
    figure_store: Any = None,
    settings: Any = None,
    make_model: Callable[[Any], Any] | None = None,
    make_ads: Callable[[Any], Any] | None = None,
) -> Starlette:
    """Build the API. ``model`` and ``ads_client`` may be None when unconfigured.

    The ADS client must already be active (entered) for the app's lifetime.
    Map tools are rebuilt per turn for the field and layers the browser reports. ``web`` is
    a (search, read) pair from scoop.web for the web tools; ``python`` a PythonSandbox;
    ``figure_store`` keeps tool images so the browser gets URLs instead of the images. With
    ``audit_path``, each turn appends one JSON line (no message text).
    """
    checkpointer = checkpointer or InMemorySaver()
    locks: dict[str, asyncio.Lock] = {}
    # The model and ADS client can be replaced from the Settings tab while running.
    live = {"model": model, "ads": ads_client}
    if settings is not None:
        live["model"] = model if model is not None else (make_model(settings) if make_model else None)
        live["ads"] = ads_client if ads_client is not None else (make_ads(settings) if make_ads else None)

    def ads_tools() -> list:
        if live["ads"] is None:
            return []
        from ..literature.tools import build_ads_tools
        return build_ads_tools(live["ads"])

    async def health(request: Request) -> JSONResponse:
        return JSONResponse({"chat": live["model"] is not None, "literature": live["ads"] is not None})

    def same_site(request: Request) -> bool:
        """Settings change only from this app's own pages (or non-browser clients)."""
        origin = request.headers.get("origin")
        if origin is None:
            return True
        return bool(re.fullmatch(r"http://(127\.0\.0\.1|localhost)(:\d+)?", origin))

    async def get_settings(request: Request) -> JSONResponse:
        if settings is None:
            return JSONResponse({"error": "Settings are not available."}, status_code=404)
        return JSONResponse(settings.public())

    async def put_settings(request: Request) -> JSONResponse:
        if settings is None:
            return JSONResponse({"error": "Settings are not available."}, status_code=404)
        if not same_site(request) or request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"error": "Settings can only be changed from the SCOOP workspace."}, status_code=403)
        try:
            settings.update(await request.json())
        except (ValueError, TypeError) as exc:
            return _bad_request(f"Invalid settings: {exc}")
        problems = []
        for name, make in (("model", make_model), ("ads", make_ads)):
            try:
                live[name] = make(settings) if make else live[name]
            except Exception as exc:  # e.g. the model library rejects a value
                live[name] = None
                problems.append(f"{name}: {type(exc).__name__}: {exc}")
        return JSONResponse({**settings.public(), "chat": live["model"] is not None,
                             "literature": live["ads"] is not None, "problems": problems})

    async def list_fields(request: Request) -> JSONResponse:
        return JSONResponse({"fields": fields.keys() if fields else []})

    async def get_field(request: Request) -> JSONResponse:
        field = fields.get(request.path_params["key"]) if fields else None
        if field is None:
            return JSONResponse({"error": "No such field."}, status_code=404)
        body = json.dumps(field, separators=(",", ":")).encode()
        # Fields reach several MB (M31's inner 10′: ~9 MB) and compress well.
        if "gzip" in request.headers.get("accept-encoding", ""):
            return Response(gzip.compress(body, compresslevel=5), media_type="application/json",
                            headers={"Content-Encoding": "gzip", "Vary": "Accept-Encoding"})
        return Response(body, media_type="application/json")

    async def get_figure(request: Request) -> Response:
        found = figure_store.path(request.path_params["thread"], request.path_params["name"]) if figure_store else None
        if found is None:
            return JSONResponse({"error": "No such figure."}, status_code=404)
        path, media_type = found
        # Each tool call's figure has its own name and never changes.
        return FileResponse(path, media_type=media_type, headers={"Cache-Control": "private, max-age=31536000, immutable"})

    async def delete_figures(request: Request) -> JSONResponse:
        if not same_site(request):
            return JSONResponse({"error": "Figures can only be deleted from the workspace."}, status_code=403)
        if figure_store is None or not figure_store.delete(request.path_params["thread"]):
            return JSONResponse({"error": "No such conversation."}, status_code=404)
        return JSONResponse({"deleted": True})

    async def start_job(request: Request) -> JSONResponse:
        if jobs is None:
            return JSONResponse({"error": "Running new fields is not available."}, status_code=503)
        try:
            body = await request.json()
            if "ra" in body:
                job = jobs.start_cone(body["ra"], body["dec"], body["radius_arcmin"], body["name"])
            else:
                key, n_rhalf = body["key"], body.get("n_rhalf", 2.0)
                if lvdb is not None and lvdb.has(key) is False:
                    raise ValueError(f"No LVDB object {key!r}.")
                job = jobs.start(key, n_rhalf)
        except (KeyError, TypeError, ValueError) as exc:
            return _bad_request(f"Cannot run field: {exc}")
        return JSONResponse(job.view())

    async def add_source(key: str, name: str) -> int:
        """Run a data source's query for a field's region and keep its rows beside the field."""
        from ..sources import SOURCES

        source = SOURCES.get(name)
        field = fields.get(key) if fields else None
        if field is None or source is None or source.query is None:
            raise KeyError(f"no field {key!r} or no queryable data source {name!r}")
        rows = await source.query(field_region(field))
        fields.add_rows(key, name, rows)
        return len(rows)

    async def list_sources(request: Request) -> JSONResponse:
        from ..sources import SOURCES

        return JSONResponse({"sources": [{"name": s.name, "label": s.label, "provenance": s.provenance,
                                          "queryable": s.query is not None} for s in SOURCES.values()]})

    async def query_source(request: Request) -> JSONResponse:
        key, name = request.path_params["key"], request.path_params["name"]
        try:
            added = await add_source(key, name)
        except KeyError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
        except Exception as exc:  # the archive failing is an outcome, not a crash
            return JSONResponse({"error": f"{type(exc).__name__}: {exc}"}, status_code=502)
        return JSONResponse({"field": key, "source": name, "rows": added})

    async def get_job(request: Request) -> JSONResponse:
        job = jobs.get(request.path_params["id"]) if jobs else None
        if job is None:
            return JSONResponse({"error": "No such job."}, status_code=404)
        return JSONResponse(job.view())

    async def literature_search(request: Request) -> JSONResponse:
        ads_client = live["ads"]
        if ads_client is None:
            return JSONResponse({"error": "ADS is not configured on the server."}, status_code=503)
        try:
            body = await request.json()
            search = SearchRequest(**body)
        except (ValueError, TypeError) as exc:
            return _bad_request(f"Invalid search: {exc}")
        return JSONResponse(result_payload(await ads_client.search(search)))

    async def chat(request: Request) -> StreamingResponse | JSONResponse:
        model = live["model"]
        if model is None:
            return JSONResponse({"error": "No chat model is configured. Set one in Settings."},
                                status_code=503)
        try:
            body = await request.json()
            thread_id, text = body["thread_id"], body["message"]
            context = body.get("context") or {}
            if not isinstance(thread_id, str) or not THREAD_ID.fullmatch(thread_id):
                raise ValueError("thread_id must be 8-64 letters, digits, '-' or '_'")
            if not isinstance(text, str) or not text.strip() or len(text) > MAX_MESSAGE_CHARS:
                raise ValueError(f"message must be 1-{MAX_MESSAGE_CHARS} characters")
            if not isinstance(context, dict):
                raise ValueError("context must be an object")
            field_key, layers = _context_layers(context)
        except (KeyError, ValueError, TypeError) as exc:
            return _bad_request(f"Invalid chat request: {exc}")

        field = fields.get(field_key) if fields and field_key else None
        map_tools = build_map_tools(field=field, layers=layers, resolver=resolver, simbad=simbad,
                                    jobs=jobs, lvdb=lvdb, fetch_image=fetch_image,
                                    add_source=add_source if fields else None,
                                    field_keys=[f["key"] for f in fields.keys()] if fields else [])
        tools = [*map_tools, *ads_tools(), *(build_web_tools(*web) if web else []),
                 *(build_python_tools(python, field) if python else []),
                 *(extra_tools() if extra_tools else [])]
        graph = build_chat_graph(model, tools, checkpointer=checkpointer,
                                 system_prompt=WORKSPACE_PROMPT)
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
        lock = locks.setdefault(thread_id, asyncio.Lock())

        async def stream() -> AsyncIterator[str]:
            if lock.locked():
                yield _sse({"type": "error", "message": "A turn is already running in this thread."})
                return
            async with lock:
                request_id, started = uuid.uuid4().hex, time.monotonic()
                seen: set[str] = set()
                names: dict[str, str] = {}
                sent: list[dict] = []
                usage: dict[str, int] = {}
                error = None
                failures = 0  # consecutive failed tool calls
                try:
                    # "messages" streams model tokens as they arrive; "updates" carries
                    # each finished step (complete text, tool calls, artifacts).
                    async for mode, chunk in graph.astream(
                        {"messages": [HumanMessage(content=_with_context(text, context))]},
                        config=config, stream_mode=["messages", "updates"],
                    ):
                        if mode == "messages":
                            token, meta = chunk
                            delta = _text(token.content) if isinstance(token, AIMessageChunk) else ""
                            if delta and meta.get("langgraph_node") == "agent":
                                yield _sse({"type": "assistant_delta", "id": token.id, "text": delta})
                            continue
                        update = chunk
                        for message in update.get("agent", {}).get("messages", []):
                            meta = getattr(message, "usage_metadata", None) or {}
                            for key, value in meta.items():
                                if isinstance(value, int):
                                    usage[key] = usage.get(key, 0) + value
                            # Prompt-cache hits (billed at a discount) and the number of model calls.
                            cached = (meta.get("input_token_details") or {}).get("cache_read")
                            if isinstance(cached, int):
                                usage["cache_read"] = usage.get("cache_read", 0) + cached
                            usage["model_calls"] = usage.get("model_calls", 0) + 1
                        for event in _events(update, seen, names):
                            if figure_store is not None and isinstance(event.get("image"), str):
                                event["image"] = figure_store.save(thread_id, event["id"], event["image"]) or event["image"]
                            sent.append(event)
                            yield _sse(event)
                            if event["type"] == "tool_result":
                                failures = failures + 1 if event["status"] == "error" else 0
                        # Circuit breaker: a model retrying a broken call burns the step budget.
                        if failures >= MAX_CONSECUTIVE_FAILURES:
                            last = next(e for e in reversed(sent) if e["type"] == "tool_result")
                            error = (f"Stopped after {failures} failed tool calls in a row "
                                     f"({last['name']}: {str(last['summary'])[:200]})")
                            break
                except GraphRecursionError:
                    error = "Stopped: tool-step limit reached."
                except asyncio.CancelledError:
                    error = "cancelled by the user"  # the browser closed the stream (Stop)
                    raise
                except Exception as exc:  # surface to the UI; keep the server alive
                    error = f"{type(exc).__name__}: {exc}"
                finally:
                    # Written however the turn ends, so failures are always diagnosable.
                    if audit_path is not None:
                        record = _audit_record(request_id, thread_id, started, sent, usage, error)
                        with audit_path.open("a", encoding="utf-8") as log:
                            log.write(json.dumps(record) + "\n")
                if error:
                    yield _sse({"type": "error", "message": error})
                yield _sse({"type": "done", "request_id": request_id})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return Starlette(routes=[
        Route("/api/health", health),
        Route("/api/settings", get_settings),
        Route("/api/settings", put_settings, methods=["POST"]),
        Route("/api/fields", list_fields),
        Route("/api/fields/{key}", get_field),
        Route("/api/fields/{key}/sources/{name}", query_source, methods=["POST"]),
        Route("/api/sources", list_sources),
        Route("/api/jobs", start_job, methods=["POST"]),
        Route("/api/jobs/{id}", get_job),
        Route("/api/literature/search", literature_search, methods=["POST"]),
        Route("/api/chat", chat, methods=["POST"]),
        Route("/api/figures/{thread}/{name}", get_figure),
        Route("/api/figures/{thread}", delete_figures, methods=["DELETE"]),
    ])


__all__ = ["create_app"]
