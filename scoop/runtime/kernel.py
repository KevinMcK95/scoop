"""A Jupyter kernel in the user's own analysis environment (the run_kernel tool).

Unlike the Pyodide sandbox (scoop/runtime/sandbox.py), this is real Python with whatever the
environment has installed, with access to the files and
network the user has, and with variables kept between calls: a map loaded once stays
in memory for the follow-up questions. It is therefore **not contained**. It is off
unless a kernel is chosen in the Settings tab (or ``SCOOP_KERNEL=<kernel name>``), and
its working directory (``SCOOP_KERNEL_CWD``) is where the agent is told to write.

Expose an environment as a kernel once with
``python -m ipykernel install --user --name <name>`` inside that environment.
Needs ``jupyter_client`` in the server's environment (``pip install -e '.[kernel]'``).
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import uuid
from pathlib import Path

TIMEOUT = 300.0  # seconds per call; on timeout the kernel is interrupted, not restarted
START_TIMEOUT = 60.0
MAX_OUTPUT = 20_000  # characters of printed output returned to the model
MAX_ROWS = 200_000  # field rows passed in, over all tables
DEFAULT_CWD = Path.home() / ".cache" / "scoop" / "kernel"
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def _truncate(text: str, limit: int = MAX_OUTPUT) -> str:
    if len(text) <= limit:
        return text
    half = limit // 2
    return f"{text[:half]}\n… [{len(text) - limit:,} characters omitted] …\n{text[-half:]}"


def available_kernels() -> list[dict]:
    """The Jupyter kernels installed for this user: [{name, display_name}], or [] without jupyter_client."""
    try:
        from jupyter_client.kernelspec import KernelSpecManager
    except ModuleNotFoundError:
        return []
    specs = KernelSpecManager().get_all_specs()
    return sorted(({"name": name, "display_name": spec["spec"].get("display_name") or name}
                   for name, spec in specs.items()), key=lambda k: k["name"])


class AnalysisKernel:
    def __init__(self, kernel_name: str, cwd: Path = DEFAULT_CWD, timeout: float = TIMEOUT) -> None:
        self.kernel_name = kernel_name
        self.cwd = Path(cwd).expanduser().resolve()
        self.timeout = timeout
        self._manager = None
        self._client = None
        self._lock = asyncio.Lock()
        self._facts: dict | None = None

    def problem(self) -> str | None:
        """Why the kernel cannot run here, or None."""
        try:
            from jupyter_client.kernelspec import KernelSpecManager, NoSuchKernel
        except ModuleNotFoundError:
            return "run_kernel needs jupyter_client: pip install -e '.[kernel]'"
        try:
            KernelSpecManager().get_kernel_spec(self.kernel_name)
        except NoSuchKernel:
            return (f"no Jupyter kernel named {self.kernel_name!r}: run "
                    f"`python -m ipykernel install --user --name {self.kernel_name}` in that environment")
        return None

    def _facts_now(self) -> dict:
        """The kernel's display name and interpreter, from its spec (the kernel is not started)."""
        from jupyter_client.kernelspec import KernelSpecManager

        spec = KernelSpecManager().get_kernel_spec(self.kernel_name)
        python = spec.argv[0] if spec.argv and os.path.isabs(spec.argv[0]) else None
        return {"display_name": spec.display_name, "language": spec.language, "python": python}

    async def describe(self) -> dict:
        """What the agent is told each turn: which environment its code runs in, and where it writes."""
        if self._facts is None:
            try:
                self._facts = await asyncio.to_thread(self._facts_now)
            except Exception:  # an unreadable spec should not stop the turn
                self._facts = {"display_name": self.kernel_name, "language": None, "python": None}
        return {"name": self.kernel_name, **self._facts, "working_directory": str(self.cwd)}

    async def _start(self):
        if self._client is not None and await self._manager.is_alive():
            return self._client
        from jupyter_client.manager import AsyncKernelManager

        self.cwd.mkdir(parents=True, exist_ok=True)
        manager = AsyncKernelManager(kernel_name=self.kernel_name)
        await manager.start_kernel(cwd=str(self.cwd))
        client = manager.client()
        client.start_channels()
        try:
            await client.wait_for_ready(timeout=START_TIMEOUT)
        except BaseException:
            client.stop_channels()
            await manager.shutdown_kernel(now=True)
            raise
        self._manager, self._client = manager, client
        return client

    async def _stop(self) -> None:
        if self._client is not None:
            self._client.stop_channels()
        if self._manager is not None and await self._manager.is_alive():
            await self._manager.shutdown_kernel(now=True)
        self._manager = self._client = None

    async def _settle(self, client, tries: int = 20) -> None:
        """After an interrupt, wait until the kernel runs requests again (it aborts those
        that arrive while it is still unwinding the interrupted one)."""
        for _ in range(tries):
            try:
                reply = await client.execute_interactive("pass", silent=True, store_history=False, timeout=5)
            except TimeoutError:
                continue
            if reply["content"].get("status") != "aborted":
                return
            await asyncio.sleep(0.2)

    def _table_preamble(self, tables: dict[str, dict]) -> str:
        """Code that loads the field tables as DataFrames, from a file in the working directory."""
        directory = self.cwd / ".scoop_tables"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{uuid.uuid4().hex}.json"
        path.write_text(json.dumps(tables, default=str))
        return (f"import json as _json, pathlib as _pathlib, pandas as _pd\n"
                f"_path = _pathlib.Path({str(path)!r})\n"
                f"tables = {{_n: _pd.DataFrame(_t['rows'], columns=_t['columns']) "
                f"for _n, _t in _json.loads(_path.read_text()).items()}}\n"
                f"_path.unlink()\n"
                f"globals().update(tables)\n")

    async def run(self, code: str, tables: dict[str, dict] | None = None) -> dict:
        """Run code in the kernel; returns output, error and figures (base64 PNG), like the sandbox."""
        tables = tables or {}
        if sum(len(t["rows"]) for t in tables.values()) > MAX_ROWS:
            raise ValueError(f"at most {MAX_ROWS:,} rows can be passed in; filter first")
        output: list[str] = []
        figures: list[str] = []
        errors: list[str] = []

        def hook(message: dict) -> None:
            kind, content = message["header"]["msg_type"], message["content"]
            if kind == "stream":
                output.append(content["text"])
            elif kind in ("execute_result", "display_data"):
                data = content.get("data", {})
                if "image/png" in data:
                    figures.append(data["image/png"].strip())
                elif "text/plain" in data:
                    output.append(data["text/plain"] + "\n")
            elif kind == "error":
                errors.append(ANSI.sub("", "\n".join(content.get("traceback") or
                                                     [f"{content['ename']}: {content['evalue']}"])))

        async with self._lock:
            client = await self._start()
            try:
                if tables:
                    await client.execute_interactive(self._table_preamble(tables), store_history=False,
                                                     timeout=START_TIMEOUT, output_hook=hook)
                if not errors:
                    reply = await client.execute_interactive(code, timeout=self.timeout, output_hook=hook)
                    if reply["content"].get("status") == "aborted":
                        errors.append("The kernel did not run this code (aborted); run it again.")
            except TimeoutError:
                await self._manager.interrupt_kernel()
                await self._settle(client)
                errors.append(f"Interrupted after {self.timeout:g} s (time limit). The kernel and its "
                              "variables are kept; split the work or save intermediate results.")
            except BaseException:
                await self._stop()
                raise
            if not await self._manager.is_alive():
                await self._stop()
                errors.append("The kernel died (likely out of memory); it restarts on the next call, "
                              "so earlier variables are gone.")
        return {"output": _truncate("".join(output)), "error": "\n".join(errors) or None,
                "figures": figures[:1], "figure_count": len(figures)}

    async def restart(self) -> None:
        async with self._lock:
            await self._stop()

    async def close(self) -> None:
        await self.restart()


__all__ = ["AnalysisKernel", "DEFAULT_CWD", "available_kernels"]
