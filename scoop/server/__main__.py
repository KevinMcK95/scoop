"""Run the workspace API: ``python -m scoop.server``.

Configuration lives on the server; the browser never receives a secret:

- the workspace's Settings tab (saved to ~/.config/scoop/settings.json, mode 600);
- seeded from ``SCOOP_MODEL``, ``SCOOP_MODEL_BASE_URL`` (any OpenAI-compatible
  endpoint), ``SCOOP_MODEL_API_KEY`` (or ``OPENAI_API_KEY``) and ``ADS_API_TOKEN``.

A Jupyter kernel chosen in Settings (seeded from ``SCOOP_KERNEL``) turns on run_kernel:
Python in that environment, not sandboxed, working in ``SCOOP_KERNEL_CWD`` (default
~/.cache/scoop/kernel).
"""
from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path
from urllib.parse import urlsplit

import uvicorn


# OpenRouter app attribution (https://openrouter.ai/docs/app-attribution): requests
# name SCOOP and its repository, so usage shows up as this app in OpenRouter's
# analytics and public rankings. Sent only to OpenRouter. SCOOP_OPENROUTER_VISIBILITY=hidden
# keeps a newly created app out of public rankings (a fork running its own copy).
APP_URL = "https://github.com/KevinMcK95/scoop"
APP_TITLE = "SCOOP"


def attribution_headers(base_url: str | None) -> dict[str, str]:
    if not base_url or urlsplit(base_url).hostname not in ("openrouter.ai", "www.openrouter.ai"):
        return {}
    headers = {"HTTP-Referer": APP_URL, "X-OpenRouter-Title": APP_TITLE, "X-Title": APP_TITLE}
    if os.environ.get("SCOOP_OPENROUTER_VISIBILITY") == "hidden":
        headers["X-OpenRouter-App-Visibility"] = "hidden"
    return headers


def make_model(settings):
    """A chat model from settings (any OpenAI-compatible endpoint), or None."""
    name = settings.get("model")
    if not name:
        return None
    from langchain_openai import ChatOpenAI

    base_url = settings.get("base_url")
    return ChatOpenAI(model=name, base_url=base_url, api_key=settings.get("api_key") or "unset",
                      stream_usage=True, default_headers=attribution_headers(base_url) or None)


def kernel_maker(made: list):
    """Analysis kernels from settings (off without one); ``made`` collects them to close at exit."""

    def make(settings):
        from ..kernel import DEFAULT_CWD, AnalysisKernel

        name = settings.get("kernel")
        if not name or name == "off":  # "off" saved in Settings overrides SCOOP_KERNEL
            return None
        kernel = AnalysisKernel(name, cwd=settings.get("kernel_cwd") or DEFAULT_CWD)
        if kernel.problem():
            raise ValueError(kernel.problem())
        made.append(kernel)
        return kernel

    return make


def ads_maker(web):
    """ADS clients sharing the server's HTTP client (the server owns its lifetime)."""

    def make(settings):
        token = settings.get("ads_token")
        if not token:
            return None
        from ..literature.ads import ADSClient

        return ADSClient(token, client=web)

    return make


async def _serve(host: str, port: int, audit_path: Path | None, fields_dir: Path, settings_path: Path,
                 threads_path: Path) -> None:
    import httpx

    from ..fields import FieldStore
    from ..images import cutout_fetcher
    from ..kernel import available_kernels
    from ..sandbox import PythonSandbox
    from ..web import page_reader, web_searcher
    from ..lookup import simbad_query
    from ..lvdb import LVDBIndex
    from ..resolve import sesame_resolver
    from ..cost import DEFAULT_BUDGET_USD, CostTracker
    from . import create_app
    from .figures import FigureStore
    from .jobs import JobManager
    from .trace import TraceStore
    from .settings import Settings

    # Environment variables seed settings; values saved from the Settings tab win.
    settings = Settings(settings_path, defaults={
        "model": os.environ.get("SCOOP_MODEL"), "base_url": os.environ.get("SCOOP_MODEL_BASE_URL"),
        "api_key": os.environ.get("SCOOP_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        "ads_token": os.environ.get("ADS_API_TOKEN"), "kernel": os.environ.get("SCOOP_KERNEL"),
        "kernel_cwd": os.environ.get("SCOOP_KERNEL_CWD")})
    async with contextlib.AsyncExitStack() as stack:
        web = await stack.enter_async_context(httpx.AsyncClient(follow_redirects=True))
        # Conversations' agent state survives restarts (reopened History threads keep their memory).
        from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

        threads_path.parent.mkdir(parents=True, exist_ok=True)
        os.chmod(threads_path.parent, 0o700)  # settings, conversations, figures and traces: owner only
        checkpointer = await stack.enter_async_context(AsyncSqliteSaver.from_conn_string(str(threads_path)))
        os.chmod(threads_path, 0o600)
        fields = FieldStore(fields_dir)
        python = PythonSandbox()
        if python.problem():
            print(f"python: off ({python.problem()})")
            python = None
        else:
            print("python: on (Pyodide sandbox, no network)")
            stack.push_async_callback(python.close)
        kernels: list = []  # every kernel made (Settings can switch them); all closed at exit

        async def close_kernels() -> None:
            for kernel in kernels:
                await kernel.close()

        stack.push_async_callback(close_kernels)
        script = Path(__file__).resolve().parents[2] / "smoke_tests" / "lvdb_field.py"
        jobs = JobManager(fields_dir, script) if script.exists() else None
        app = create_app(audit_path=audit_path, fields=fields, settings=settings, make_model=make_model,
                         checkpointer=checkpointer, costs=CostTracker(settings_path.with_name("costs.json")),
                         make_ads=ads_maker(web), resolver=sesame_resolver(web), simbad=simbad_query(web),
                         jobs=jobs, lvdb=LVDBIndex(fields_dir), fetch_image=cutout_fetcher(web),
                         web=(web_searcher(web), page_reader(web)), python=python,
                         make_kernel=kernel_maker(kernels), list_kernels=available_kernels,
                         figure_store=FigureStore(settings_path.parent / "figures"),
                         trace_store=TraceStore(settings_path.parent / "traces"))
        print(f"chat: {settings.get('model') or 'off (set a model in Settings)'}; "
              f"literature: {'on' if settings.get('ads_token') else 'off (set an ADS token in Settings)'}")
        print(f"kernel: {f'on ({kernels[0].kernel_name!r}, NOT sandboxed; working in {kernels[0].cwd})' if kernels else 'off (choose one in Settings)'}")
        print(f"fields in {fields_dir}: {[f['key'] for f in fields.keys()] or 'none'}")
        print(f"settings file: {settings_path}; conversations: {threads_path}")
        print(f"budgets and cost calibration: {settings_path.with_name('costs.json')} "
              f"(default ${DEFAULT_BUDGET_USD:.2f} per command until a conversation sets its own)")
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()


# Defaults resolve from the repository root, so the server works from any directory.
ROOT = Path(__file__).resolve().parents[2]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--audit-log", type=Path, default=ROOT / "out" / "turns.jsonl",
                        help="append one JSON line per chat turn")
    parser.add_argument("--settings", type=Path, default=None,
                        help="settings file (default ~/.config/scoop/settings.json)")
    parser.add_argument("--threads", type=Path, default=None,
                        help="agent memory database (default ~/.config/scoop/threads.sqlite)")
    parser.add_argument("--fields", type=Path, default=ROOT / "out",
                        help="directory of smoke-test outputs (<dir>/<key>_field.json)")
    args = parser.parse_args()
    args.audit_log.parent.mkdir(parents=True, exist_ok=True)
    import asyncio

    from .settings import DEFAULT_PATH

    asyncio.run(_serve(args.host, args.port, args.audit_log, args.fields, args.settings or DEFAULT_PATH,
                       args.threads or DEFAULT_PATH.with_name("threads.sqlite")))


if __name__ == "__main__":
    main()
