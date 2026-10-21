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
from ..chat.data_agent import build_handoff_tool
from ..chat.pm_tools import build_pm_tools
from ..chat.region_tools import build_region_tools
from ..chat.visual import build_map_tools
from ..chat.python_tool import build_python_tools
from ..chat.kernel_tool import build_kernel_tools
from ..chat.web_tools import build_web_tools
from ..cost import CostTracker, describe as describe_cost, estimate_tokens, price_for
from ..fields import FieldStore, field_region
from ..literature.models import SearchRequest
from ..literature.serialization import result_payload
from .settings import BASE_URL
from .trace import TurnTracer

THREAD_ID = re.compile(r"[A-Za-z0-9_-]{8,64}")
MAX_MESSAGE_CHARS = 8000
# SPEC.md caps a request at 12 tool steps; each step is an agent and a tools node.
RECURSION_LIMIT = 2 * 12 + 1
MAX_CONSECUTIVE_FAILURES = 4



MODEL_LIST_TTL = 600.0  # seconds an endpoint's model list is reused


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
    # A node that ran several tools returning Commands in parallel reports a list of updates.
    node_updates = [u for value in update.values() for u in (value if isinstance(value, list) else [value])]
    for node_update in node_updates:
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
                  usage: dict[str, int], error: str | None, cost: dict | None = None) -> dict:
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
        # What this turn was estimated at before it ran, and what its tokens actually priced at.
        "estimated_cost_usd": (cost or {}).get("estimated_cost_usd"),
        "actual_cost_usd": (cost or {}).get("actual_cost_usd"),
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
    kernel: Any = None,
    figure_store: Any = None,
    trace_store: Any = None,
    settings: Any = None,
    costs: Any = None,
    make_model: Callable[[Any], Any] | None = None,
    make_ads: Callable[[Any], Any] | None = None,
    make_kernel: Callable[[Any], Any] | None = None,
    list_kernels: Callable[[], list[dict]] | None = None,
    list_models: Callable[[str, str | None], Any] | None = None,
) -> Starlette:
    """Build the API. ``model`` and ``ads_client`` may be None when unconfigured.

    The ADS client must already be active (entered) for the app's lifetime.
    Map tools are rebuilt per turn for the field and layers the browser reports. ``web`` is
    a (search, read) pair from scoop.web for the web tools; ``python`` a PythonSandbox;
    ``kernel`` an AnalysisKernel in the user's own environment (off unless given, or made
    from settings by ``make_kernel``; ``list_kernels`` names the kernels Settings may choose);
    ``list_models`` an async ``(base_url, api_key) -> models`` for the Settings model search;
    ``figure_store`` keeps tool images so the browser gets URLs instead of the images;
    ``trace_store`` keeps developer traces of each turn for the Debug tab. With
    ``audit_path``, each turn appends one JSON line (no message text). ``costs`` is a
    scoop.cost.CostTracker: it estimates each turn before it runs, holds each
    conversation's budget, and learns from the tokens the turn actually used.
    """
    checkpointer = checkpointer or InMemorySaver()
    costs = costs if costs is not None else CostTracker()
    locks: dict[str, asyncio.Lock] = {}
    # The model and ADS client can be replaced from the Settings tab while running.
    live = {"model": model, "ads": ads_client, "kernel": kernel}
    if settings is not None:
        live["model"] = model if model is not None else (make_model(settings) if make_model else None)
        live["ads"] = ads_client if ads_client is not None else (make_ads(settings) if make_ads else None)
        if kernel is None and make_kernel:
            try:
                live["kernel"] = make_kernel(settings)
            except Exception as exc:  # e.g. the saved kernel was uninstalled
                print(f"kernel: off ({exc})")

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
        return JSONResponse({**settings.public(), "kernels": list_kernels() if list_kernels else []})

    async def put_settings(request: Request) -> JSONResponse:
        if settings is None:
            return JSONResponse({"error": "Settings are not available."}, status_code=404)
        if not same_site(request) or request.headers.get("content-type", "").split(";")[0] != "application/json":
            return JSONResponse({"error": "Settings can only be changed from the SCOOP workspace."}, status_code=403)
        try:
            changes = await request.json()
            kernels = {k["name"] for k in list_kernels()} if list_kernels else set()
            if isinstance(changes, dict) and changes.get("kernel") not in (None, "", "off", *kernels):
                raise ValueError(f"no installed Jupyter kernel named {changes['kernel']!r}")
            before = (settings.get("kernel"), settings.get("kernel_cwd"))
            settings.update(changes)
        except (ValueError, TypeError) as exc:
            return _bad_request(f"Invalid settings: {exc}")
        problems = []
        if make_kernel and (settings.get("kernel"), settings.get("kernel_cwd")) != before:
            # A new kernel or working directory: the old kernel and its variables end.
            if live["kernel"] is not None:
                await live["kernel"].close()
            try:
                live["kernel"] = make_kernel(settings)
            except Exception as exc:
                live["kernel"] = None
                problems.append(f"kernel: {exc}")
        for name, make in (("model", make_model), ("ads", make_ads)):
            try:
                live[name] = make(settings) if make else live[name]
            except Exception as exc:  # e.g. the model library rejects a value
                live[name] = None
                problems.append(f"{name}: {type(exc).__name__}: {exc}")
        return JSONResponse({**settings.public(), "chat": live["model"] is not None,
                             "literature": live["ads"] is not None, "kernel_on": live["kernel"] is not None,
                             "kernels": list_kernels() if list_kernels else [], "problems": problems})

    model_lists: dict[tuple[str, bool], tuple[float, list]] = {}  # (endpoint, with key) -> (time, models)

    async def get_models(request: Request) -> JSONResponse:
        """The models an endpoint offers: the one given (?base_url=), else the saved one."""
        if settings is None or list_models is None:
            return JSONResponse({"error": "Model lists are not available."}, status_code=404)
        if not same_site(request):
            return JSONResponse({"error": "Model lists are only for the SCOOP workspace."}, status_code=403)
        base_url = request.query_params.get("base_url") or settings.get("base_url")
        if not base_url:
            return JSONResponse({"base_url": None, "models": []})
        if not BASE_URL.fullmatch(base_url):
            return _bad_request("base_url must be an https:// URL")
        # The saved API key is sent only to the saved endpoint.
        key = settings.get("api_key") if base_url == settings.get("base_url") else None
        cached = model_lists.get((base_url, bool(key)))
        if cached is None or time.monotonic() - cached[0] > MODEL_LIST_TTL:
            try:
                cached = model_lists[(base_url, bool(key))] = (time.monotonic(), await list_models(base_url, key))
            except Exception as exc:  # the endpoint is down, rejects the key, or has no /models
                return JSONResponse({"error": f"Could not list the models at {base_url} ({type(exc).__name__})."},
                                    status_code=502)
        return JSONResponse({"base_url": base_url, "models": cached[1]})

    def model_id() -> str:
        """The model id turns are priced against: what Settings holds, else what the client reports."""
        name = settings.get("model") if settings is not None else None
        if not name:
            for attribute in ("model_name", "model"):
                value = getattr(live["model"], attribute, None)
                if isinstance(value, str) and value:
                    name = value
                    break
        return name or "unknown"

    async def get_budget(request: Request) -> JSONResponse:
        """What this conversation may spend, has spent, and has left (null until it sets one)."""
        thread = request.path_params["thread"]
        if not THREAD_ID.fullmatch(thread):
            return _bad_request("thread_id must be 8-64 letters, digits, '-' or '_'")
        model = model_id()
        return JSONResponse({"budget": costs.budget(thread), "default_budget_usd": round(costs.budget_usd, 6),
                             "model": model, "priced": price_for(model) is not None})

    async def put_budget(request: Request) -> JSONResponse:
        """Set this conversation's budget. What it has already spent is kept."""
        thread = request.path_params["thread"]
        if not same_site(request):
            return JSONResponse({"error": "Budgets can only be set from the SCOOP workspace."}, status_code=403)
        try:
            if not THREAD_ID.fullmatch(thread):
                raise ValueError("thread_id must be 8-64 letters, digits, '-' or '_'")
            body = await request.json()
            return JSONResponse({"budget": costs.set_budget(thread, body["budget_usd"])})
        except (KeyError, TypeError, ValueError) as exc:
            return _bad_request(f"Invalid budget: {exc}")

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

    async def get_traces(request: Request) -> JSONResponse:
        traces = trace_store.load(request.path_params["thread"]) if trace_store else None
        if traces is None:
            return JSONResponse({"error": "Traces are not available."}, status_code=404)
        return JSONResponse({"traces": traces})

    async def delete_traces(request: Request) -> JSONResponse:
        if not same_site(request):
            return JSONResponse({"error": "Traces can only be deleted from the workspace."}, status_code=403)
        if trace_store is None or not trace_store.delete(request.path_params["thread"]):
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
            budget = body.get("budget_usd")
            if budget is not None:
                if isinstance(budget, bool) or not isinstance(budget, (int, float)) or not budget > 0:
                    raise ValueError("budget_usd must be a number greater than zero")
                costs.set_budget(thread_id, float(budget))
        except (KeyError, ValueError, TypeError) as exc:
            return _bad_request(f"Invalid chat request: {exc}")
        # Which analysis environment run_kernel uses (null: off) comes from the server, never the browser.
        context = {**context, "kernel": await live["kernel"].describe() if live["kernel"] else None}

        field = fields.get(field_key) if fields and field_key else None
        # The field this turn works on: load_field can switch it mid-turn (e.g. from the start
        # page), and the PM tools, the data agent and run_python follow it through current_field.
        turn = {"field": field}

        def read_field(key: str) -> dict | None:
            turn["field"] = fields.get(key)
            return turn["field"]

        def current_field() -> dict | None:
            return turn["field"]

        known_layers: dict = {}  # layers seen or added this turn, shared by the map and PM tools
        map_tools = build_map_tools(field=field, layers=layers, known_layers=known_layers,
                                    resolver=resolver, simbad=simbad,
                                    jobs=jobs, lvdb=lvdb, fetch_image=fetch_image,
                                    add_source=add_source if fields else None,
                                    read_field=read_field if fields else None,
                                    field_keys=[f["key"] for f in fields.keys()] if fields else [])
        async def store_rows(key: str, name: str, rows: list[dict], meta: dict) -> None:
            fields.add_rows(key, name, rows, meta)
            # The map tools hold this field object: refresh it in place so the rest of
            # this turn sees the new table.
            refreshed, current = fields.get(key), turn["field"]
            if current is not None and refreshed is not None and current.get("key") == key:
                current.clear()
                current.update(refreshed)

        # The data agent runs on the same model, for data the field doesn't have yet.
        handoff = build_handoff_tool(model=model, field=current_field, store_rows=store_rows if fields else None)
        # Proper-motion expectations and planning; the LVDB tables are cached beside the fields.
        pm_tools = build_pm_tools(field=current_field, lvdb_cache=Path(fields.root) / "lvdb_cache" if fields else None,
                                  layers=layers, known_layers=known_layers)
        # Fields over boxes, polygons and stream tracks: planned and counted, then run once confirmed.
        region_tools = build_region_tools(jobs=jobs, resolver=resolver)
        tools = [*map_tools, *region_tools, handoff, *pm_tools, *ads_tools(), *(build_web_tools(*web) if web else []),
                 *(build_python_tools(python, current_field) if python else []),
                 *(build_kernel_tools(live["kernel"], current_field) if live["kernel"] else []),
                 *(extra_tools() if extra_tools else [])]
        graph = build_chat_graph(model, tools, checkpointer=checkpointer,
                                 system_prompt=WORKSPACE_PROMPT)
        # What rides along with the user's text on every model call this turn, and so has to be
        # costed with it: the system prompt, every tool's schema, and the workspace context.
        overhead = (estimate_tokens(WORKSPACE_PROMPT) + estimate_tokens(json.dumps(context, default=str))
                    + sum(estimate_tokens(f"{getattr(tool, 'name', '')}{getattr(tool, 'description', '') or ''}"
                                          f"{json.dumps(getattr(tool, 'args', {}), default=str)}")
                          for tool in tools))
        config = {"configurable": {"thread_id": thread_id}, "recursion_limit": RECURSION_LIMIT}
        lock = locks.setdefault(thread_id, asyncio.Lock())

        async def stream() -> AsyncIterator[str]:
            if lock.locked():
                yield _sse({"type": "error", "message": "A turn is already running in this thread."})
                return
            async with lock:
                request_id, started = uuid.uuid4().hex, time.monotonic()
                # Before anything runs: what this command should cost, against what is left to spend.
                estimate = costs.estimate(text, model=model_id(), thread_id=thread_id,
                                          overhead_tokens=overhead, request_id=request_id)
                yield _sse({"type": "cost", "phase": "estimate", "note": describe_cost(estimate), **estimate})
                # An unpriced model cannot be held to a budget; the note says so instead.
                over_budget = (estimate["priced"] and estimate["budget_scope"] == "conversation"
                               and not estimate["within_budget"])
                if over_budget:
                    costs.forget(request_id)
                    left = estimate["budget"]["remaining_usd"]
                    yield _sse({"type": "error", "message":
                                f"This command is estimated at ${estimate['estimated_cost_usd']:.4f} but only "
                                f"${max(0.0, left):.4f} is left of this conversation's "
                                f"${estimate['budget']['budget_usd']:.2f} budget. Raise the budget to continue."})
                    yield _sse({"type": "done", "request_id": request_id})
                    return
                actual: dict | None = None
                tracer = TurnTracer(request_id, thread_id, text) if trace_store is not None else None
                turn_config = {**config, "callbacks": [tracer]} if tracer else config
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
                        config=turn_config, stream_mode=["messages", "updates"],
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
                    # However the turn ends, its tokens are what it cost: charge them to the
                    # conversation's budget and let the next estimate learn from them.
                    actual = costs.confirm_usage(request_id, model_id(), usage) if usage else None
                    if actual is None:
                        costs.forget(request_id)
                    # Written however the turn ends, so failures are always diagnosable.
                    if audit_path is not None:
                        record = _audit_record(request_id, thread_id, started, sent, usage, error,
                                               cost={**estimate, **(actual or {})})
                        with audit_path.open("a", encoding="utf-8") as log:
                            log.write(json.dumps(record) + "\n")
                    if tracer is not None:
                        try:
                            trace_store.save(tracer.result(error))
                        except OSError:
                            pass  # a trace is a convenience; never fail the turn over it
                if error:
                    yield _sse({"type": "error", "message": error})
                if actual is not None:
                    yield _sse({"type": "cost", "phase": "actual",
                                "note": describe_cost(estimate, actual), **actual})
                yield _sse({"type": "done", "request_id": request_id})

        return StreamingResponse(stream(), media_type="text/event-stream",
                                 headers={"Cache-Control": "no-cache"})

    return Starlette(routes=[
        Route("/api/health", health),
        Route("/api/settings", get_settings),
        Route("/api/settings", put_settings, methods=["POST"]),
        Route("/api/models", get_models),
        Route("/api/budget/{thread}", get_budget),
        Route("/api/budget/{thread}", put_budget, methods=["POST"]),
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
        Route("/api/traces/{thread}", get_traces),
        Route("/api/traces/{thread}", delete_traces, methods=["DELETE"]),
    ])


__all__ = ["create_app"]
