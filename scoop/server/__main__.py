"""Run the workspace API: ``python -m scoop.server``.

Configuration comes from the environment, never from the browser:

- ``ADS_API_TOKEN``: enables the literature panel and ADS tools.
- ``SCOOP_MODEL``: enables chat. Any OpenAI-compatible endpoint, for example
  ``SCOOP_MODEL_BASE_URL=https://inference.flatironinstitute.org/v1``.
- ``SCOOP_MODEL_API_KEY``: falls back to ``OPENAI_API_KEY``.
"""
from __future__ import annotations

import argparse
import contextlib
import os
from pathlib import Path

import uvicorn


def _model():
    name = os.environ.get("SCOOP_MODEL")
    if not name:
        return None
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(
        model=name,
        base_url=os.environ.get("SCOOP_MODEL_BASE_URL"),
        api_key=os.environ.get("SCOOP_MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY"),
        stream_usage=True,
    )


async def _serve(host: str, port: int, audit_path: Path | None) -> None:
    from . import create_app

    async with contextlib.AsyncExitStack() as stack:
        ads = None
        if os.environ.get("ADS_API_TOKEN"):
            from ..literature.ads import ADSClient

            ads = await stack.enter_async_context(ADSClient(os.environ["ADS_API_TOKEN"]))
        model = _model()
        print(f"chat: {'on (' + os.environ['SCOOP_MODEL'] + ')' if model else 'off (set SCOOP_MODEL)'}; "
              f"literature: {'on' if ads else 'off (set ADS_API_TOKEN)'}")
        app = create_app(model=model, ads_client=ads, audit_path=audit_path)
        await uvicorn.Server(uvicorn.Config(app, host=host, port=port)).serve()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8787)
    parser.add_argument("--audit-log", type=Path, default=Path("out/turns.jsonl"),
                        help="append one JSON line per chat turn")
    args = parser.parse_args()
    args.audit_log.parent.mkdir(parents=True, exist_ok=True)
    import asyncio

    asyncio.run(_serve(args.host, args.port, args.audit_log))


if __name__ == "__main__":
    main()
