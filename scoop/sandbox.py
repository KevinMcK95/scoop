"""A contained Python for the agent's own computations (the run_python tool).

Code runs in Pyodide (CPython compiled to WebAssembly) inside a Node process
(sandbox/runner.mjs) that is kept alive between calls. Containment is layered:

- WebAssembly: the code sees an in-memory file system holding only the tables passed in.
- Node's permission model: no file writes, no reads outside sandbox/, no child processes,
  workers or native addons.
- The OS (macOS sandbox-exec): no network, no file writes, no reads under the home
  directory except sandbox/ and Node itself.
- A time limit per call; on timeout the process is killed and restarted.

Without sandbox-exec (e.g. on Linux) the sandbox reports itself unavailable rather
than running with weaker containment. Set it up once with ``npm install`` and
``npm run setup`` in sandbox/ (downloads Pyodide and its scientific packages).
"""
from __future__ import annotations

import asyncio
import itertools
import json
import shutil
import sys
from pathlib import Path

SANDBOX_DIR = Path(__file__).resolve().parents[1] / "sandbox"
TIMEOUT = 30.0  # seconds per call
START_TIMEOUT = 60.0
MAX_ROWS = 200_000  # rows passed in, over all tables
STREAM_LIMIT = 64 * 1024 * 1024  # longest reply line (figures are base64 PNG)


def _profile(directory: Path, node_dir: Path) -> str:
    def quoted(path: Path) -> str:
        return json.dumps(str(path))

    return "\n".join([
        "(version 1)",
        "(allow default)",
        "(deny network*)",
        "(deny file-write*)",
        '(allow file-write-data (literal "/dev/null") (literal "/dev/stdout") (literal "/dev/stderr"))',
        f"(deny file-read* (subpath {quoted(Path.home())}))",
        "(allow file-read-metadata)",
        f"(allow file-read* (subpath {quoted(directory)}) (subpath {quoted(node_dir)}))",
        "(deny process-fork)",
    ])


class PythonSandbox:
    def __init__(self, directory: Path = SANDBOX_DIR, timeout: float = TIMEOUT, node: str | None = None) -> None:
        self.directory = Path(directory).resolve()
        self.timeout = timeout
        self.node = node or shutil.which("node")
        self._process: asyncio.subprocess.Process | None = None
        self._lock = asyncio.Lock()
        self._ids = itertools.count(1)

    def problem(self) -> str | None:
        """Why the sandbox cannot run here, or None."""
        if sys.platform != "darwin" or not shutil.which("sandbox-exec"):
            return "run_python needs macOS sandbox-exec for network isolation"
        if not self.node:
            return "run_python needs Node.js"
        pyodide = self.directory / "node_modules" / "pyodide"
        if not (pyodide / "pyodide.asm.wasm").exists():
            return f"run_python is not set up: run `npm install` in {self.directory}"
        if not any(pyodide.glob("numpy-*.whl")):
            return f"run_python packages are missing: run `npm run setup` in {self.directory}"
        return None

    def command(self) -> list[str]:
        node = Path(self.node).resolve()
        # The Node installation (e.g. ~/.nvm/versions/node/v24) must stay readable.
        node_dir = node.parents[1]
        return ["sandbox-exec", "-p", _profile(self.directory, node_dir),
                str(node), "--permission", f"--allow-fs-read={self.directory}", "--max-old-space-size=2048",
                str(self.directory / "runner.mjs")]

    async def _start(self) -> asyncio.subprocess.Process:
        if self._process and self._process.returncode is None:
            return self._process
        process = await asyncio.create_subprocess_exec(
            *self.command(), cwd=self.directory, stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL, limit=STREAM_LIMIT, env={"PATH": "/usr/bin:/bin", "HOME": str(self.directory)})
        try:
            line = await asyncio.wait_for(process.stdout.readline(), START_TIMEOUT)
            if not json.loads(line or b"{}").get("ready"):
                raise RuntimeError("the Python sandbox did not start")
        except BaseException:
            process.kill()
            await process.wait()
            raise
        self._process = process
        return process

    async def _stop(self) -> None:
        if self._process and self._process.returncode is None:
            self._process.kill()
            await self._process.wait()
        self._process = None

    async def run(self, code: str, tables: dict[str, dict] | None = None) -> dict:
        """Run code with tables ({name: {columns, rows}}); returns output, error and figures (base64 PNG)."""
        tables = tables or {}
        if sum(len(t["rows"]) for t in tables.values()) > MAX_ROWS:
            raise ValueError(f"at most {MAX_ROWS:,} rows can be passed in; filter first")
        request = json.dumps({"id": next(self._ids), "code": code, "tables": tables}, default=str) + "\n"
        async with self._lock:
            process = await self._start()
            try:
                process.stdin.write(request.encode())
                await process.stdin.drain()
                line = await asyncio.wait_for(process.stdout.readline(), self.timeout)
            except asyncio.TimeoutError:
                await self._stop()
                return {"output": "", "error": f"Stopped after {self.timeout:g} s (time limit); the sandbox restarted, "
                        "so earlier imports are gone.", "figures": [], "figure_count": 0}
            except BaseException:
                await self._stop()
                raise
            if not line:
                await self._stop()
                return {"output": "", "error": "The sandbox stopped (likely out of memory); it restarts on the next call.",
                        "figures": [], "figure_count": 0}
            return json.loads(line)

    async def close(self) -> None:
        async with self._lock:
            await self._stop()


def table_payload(table: dict, columns: list[str] | None = None) -> dict:
    """A field table as {columns, rows} (lists), with only the given columns if any."""
    rows = table.get("rows", [])
    names = list(columns) if columns else list(dict.fromkeys(k for row in rows for k in row))
    return {"columns": names, "rows": [[row.get(n) for n in names] for row in rows]}


__all__ = ["PythonSandbox", "SANDBOX_DIR", "table_payload"]
