"""The triage agent: turns what failed in recent sessions into suggested changes.

The server runs it on a timer (TriageScheduler): at most once per interval (an hour by
default), only after the workspace has been idle for a while, and only when there are
failures it hasn't reported yet (scoop.triage groups them). It reads the failures, the
traces of the turns they happened in, failed runs' tracebacks and SCOOP's source, all
read-only, and writes a dated report: for each problem, the cause with file and line,
a suggested change and a test that would catch it. It never edits code, pushes or
queries archives; a person (or the /triage skill in Claude Code) turns the suggestions
they accept into tested fixes.

Reports quote traces, which hold the user's questions and data, so they are kept beside
the traces (owner-only), not in the repository.
"""
from __future__ import annotations

import asyncio
import json
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Callable

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from pydantic import Field

from . import triage
from .chat.subagent import run_subagent

REPO = Path(__file__).resolve().parents[1]
MAX_STEPS = 30
MAX_GROUPS = 4  # per run, most frequent first; the rest wait for the next run
STEPS_PER_GROUP = 5
# Source files the agent may not read (secrets, generated or vendored trees).
PRIVATE = re.compile(r"(^|/)(\.git|node_modules|\.svelte-kit|__pycache__|out|ws_[a-z]+)(/|$)|"
                     r"(^|/)\.env|settings[^/]*\.json$|\.(sqlite|db|pem|key)$")
TEXT_SUFFIXES = {".py", ".md", ".ts", ".svelte", ".json", ".toml", ".yml", ".yaml", ".txt", ".css", ".html", ".js"}

TRIAGE_PROMPT = """\
You are SCOOP's triage agent. You get groups of failures from recent SCOOP sessions \
(failed tool calls of the chat agent and its sub-agents, failed field runs) and work out \
what to change so they stop happening. You only read: the traces of the turns they \
happened in, failed runs' records, and SCOOP's source. You never change anything; a \
person reviews your suggestions.

For each group, most frequent first:
1. Read the evidence: read_trace for the turn (what the agent was asked, which call \
failed and why, what it did next), read_run for a field run's traceback. Look at the \
code the error points to (search_source, read_source). Check git_log on that file: if a \
recent commit already fixed it, say so and stop.
2. Decide what kind of problem it is: a SCOOP bug (an exception in our code), an agent \
mistake the tool refused (the cause is usually the tool's description, an agent's \
prompt, or a tool that could accept a harmless variant), a transient outage that recurs \
(make the step non-fatal, retry, or say so faster), or nothing to change (the user \
stopped it, a one-off network drop).
3. Suggest the smallest change that removes the cause, in the code's own style: the \
file and function, what to change (a short diff sketch), and a unit test that fails now \
and passes after (no network in tests). Never suggest weakening a check (Gaia IDs stay \
exact integers, TLS verification stays on) or changing a default the user chose.

Every claim cites evidence: a file and line, a trace step, a run record. If you cannot \
find the cause, say what you checked and what would tell. Budget: about {per_group} \
tool calls per group ({max_steps} in all); when a group's budget is spent, write up \
what you found (with a lower confidence) and move on. Always finish with the report.

# Report (markdown, your final message)
A one-line summary, then per group: `## <n>. <tool>: <short title>`, **Evidence** (counts, \
first/last time, the failing call), **Cause** (file:line), **Suggested change**, **Test**, \
**Confidence** (high / medium / low, and why). End with groups you left alone and why.
""".replace("{max_steps}", str(MAX_STEPS)).replace("{per_group}", str(STEPS_PER_GROUP))


def _ok(text: str, tool_call_id: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=tool_call_id)


def _error(text: str, tool_call_id: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=tool_call_id, status="error")


def _readable(relative: str, repo: Path) -> Path | None:
    path = (repo / relative.strip().lstrip("/")).resolve()
    rel = path.relative_to(repo.resolve()).as_posix() if path.is_relative_to(repo.resolve()) else None
    if rel is None or PRIVATE.search(rel) or not path.is_file() or path.suffix not in TEXT_SUFFIXES:
        return None
    return path


def build_triage_tools(*, repo: Path = REPO, runs_dir: Path | None = None, trace_dir: Path | None = None) -> list:
    """Read-only tools over SCOOP's source, failed runs and traces."""

    @tool
    async def read_source(path: str, tool_call_id: Annotated[str, InjectedToolCallId],
                          start: Annotated[int, Field(ge=1)] = 1,
                          end: Annotated[int, Field(ge=1)] = 200):
        """Lines start..end (at most 300) of a SCOOP source file, by repository path
        (e.g. "smoke_tests/lvdb_field.py"), with line numbers."""
        target = _readable(path, repo)
        if target is None:
            return _error(f"Can't read {path!r} (not a source file in the repository, or private).", tool_call_id)
        lines = target.read_text(errors="replace").splitlines()
        end = min(end, start + 299, len(lines))
        return _ok("\n".join(f"{i}: {lines[i - 1]}" for i in range(start, end + 1))
                   + (f"\n[{len(lines)} lines in all]" if end < len(lines) else ""), tool_call_id)

    @tool
    async def search_source(pattern: str, tool_call_id: Annotated[str, InjectedToolCallId],
                            glob: str = "**/*.py"):
        """Lines matching a regular expression in SCOOP's source (file:line: text), at
        most 60; glob narrows the files (default every .py file)."""
        try:
            rx = re.compile(pattern)
        except re.error as exc:
            return _error(f"Bad pattern: {exc}", tool_call_id)
        hits = []
        for path in sorted(repo.glob(glob)):
            rel = path.relative_to(repo).as_posix()
            if PRIVATE.search(rel) or not path.is_file() or path.suffix not in TEXT_SUFFIXES:
                continue
            for i, line in enumerate(path.read_text(errors="replace").splitlines(), 1):
                if rx.search(line):
                    hits.append(f"{rel}:{i}: {line.strip()[:200]}")
                    if len(hits) >= 60:
                        return _ok("\n".join(hits) + "\n[more matches not shown]", tool_call_id)
        return _ok("\n".join(hits) or "No matches.", tool_call_id)

    @tool
    async def git_log(path: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """The last 8 commits that touched a file (hash, date, author, subject): to see
        whether a failure was already fixed after it happened."""
        target = _readable(path, repo)
        if target is None:
            return _error(f"Can't read {path!r}.", tool_call_id)
        out = await asyncio.to_thread(subprocess.run, ["git", "log", "-8", "--format=%h %cI %an %s", "--",
                                                       target.relative_to(repo.resolve()).as_posix()],
                                      cwd=repo, capture_output=True, text=True, timeout=20)
        return _ok(out.stdout.strip() or "(no commits, or not a git checkout)", tool_call_id)

    @tool
    async def read_run(name: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """A failed field run's record (arguments, error, decisions, the log's last 80
        lines with the traceback), by its file name in the report."""
        if runs_dir is None:
            return _error("No failed-run records on this server.", tool_call_id)
        path = (runs_dir / Path(name).name)
        if not path.is_file():
            return _error(f"No run record {name!r}.", tool_call_id)
        run = json.loads(path.read_text())
        run["log"] = (run.get("log") or [])[-80:]
        return _ok(json.dumps(run, default=str)[:12000], tool_call_id)

    @tool
    async def read_trace(thread: str, request: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """The trace of one chat turn (the question, then each model and tool step: tool
        names, arguments, results and status, the sub-agents' steps included), by the
        thread and request ids in the report."""
        if trace_dir is None or not re.fullmatch(r"[A-Za-z0-9_-]{8,64}", thread):
            return _error("No traces on this server.", tool_call_id)
        path = trace_dir / f"{thread}.jsonl"
        if not path.is_file():
            return _error(f"No traces for thread {thread!r} (deleted with the conversation?).", tool_call_id)
        for line in path.read_text().splitlines():
            try:
                t = json.loads(line)
            except ValueError:
                continue
            if t.get("request_id") == request:
                steps = [{k: (str(v)[:1500] if k in ("args", "result", "reply") else v)
                          for k, v in s.items() if k in ("kind", "name", "args", "result", "status", "reply")}
                         for s in t.get("steps", [])]
                return _ok(json.dumps({"question": str(t.get("question"))[:2000], "error": t.get("error"),
                                       "steps": steps}, default=str)[:20000], tool_call_id)
        return _error(f"No trace of request {request!r} in that thread.", tool_call_id)

    return [read_source, search_source, git_log, read_run, read_trace]


def _iso(ts: float) -> str:
    return datetime.fromtimestamp(ts, timezone.utc).isoformat(timespec="seconds")


class TriageScheduler:
    """Decides when the triage agent runs and keeps its reports.

    Due when ``every_s`` have passed since the last check and the workspace has been
    idle ``idle_s`` (no chat turn), so it never competes with the user for the model;
    runs the agent only for failure groups not reported before (bugs and refused calls,
    and transient failures that recur ``recurring`` times)."""

    def __init__(self, *, model: Callable[[], object | None], audit_path: Path | None, fields_dir: Path | None,
                 trace_dir: Path | None, out_dir: Path, every_s: float = 3600, idle_s: float = 600,
                 recurring: int = 5, repo: Path = REPO, clock: Callable[[], float] = time.time) -> None:
        self.model, self.audit_path, self.fields_dir, self.trace_dir = model, audit_path, fields_dir, trace_dir
        self.out_dir, self.every_s, self.idle_s, self.recurring = Path(out_dir), every_s, idle_s, recurring
        self.repo, self.clock = repo, clock
        self.last_activity = 0.0
        self.running = False

    # ── state: when it last checked, and which groups it already reported ──
    def _state_path(self) -> Path:
        return self.out_dir / "state.json"

    def state(self) -> dict:
        try:
            return json.loads(self._state_path().read_text())
        except (OSError, ValueError):
            return {"last_check": None, "reported": []}

    def _save_state(self, state: dict) -> None:
        self.out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self._state_path().write_text(json.dumps(state, indent=1))

    def note_activity(self) -> None:
        self.last_activity = self.clock()

    def due(self) -> bool:
        last = self.state().get("last_check")
        last_ts = datetime.fromisoformat(last).timestamp() if last else 0.0
        now = self.clock()
        return (not self.running and now - last_ts >= self.every_s and now - self.last_activity >= self.idle_s)

    def new_groups(self, since: datetime | None, reported: set[str]) -> list[dict]:
        found = triage.groups(triage.collect(self.audit_path, self.fields_dir, since))
        worth = [g for g in found if g["class"] in ("bug", "refused")
                 or (g["class"] == "transient" and len(g["items"]) >= self.recurring)]
        return [g for g in worth if g["key"] not in reported][:MAX_GROUPS]

    async def tick(self) -> Path | None:
        """One check: run the agent if due and there is something new. Returns the
        report written, if any."""
        if not self.due():
            return None
        state = self.state()
        since = datetime.fromisoformat(state["last_check"]) if state.get("last_check") else None
        now = self.clock()
        fresh = self.new_groups(since, set(state.get("reported", [])))
        model = self.model()
        if not fresh or model is None:
            self._save_state({**state, "last_check": _iso(now)})
            return None
        self.running = True
        try:
            failures = [f for g in fresh for f in g["items"]]
            text = ("Failures since " + (since.isoformat(timespec="seconds") if since else "the logs began")
                    + f", grouped:\n\n{triage.report(failures)}")
            tools = build_triage_tools(repo=self.repo, trace_dir=self.trace_dir,
                                       runs_dir=self.fields_dir / "_runs" if self.fields_dir else None)
            result = await run_subagent(model, tools, TRIAGE_PROMPT, text, max_steps=MAX_STEPS, name="triage agent")
        finally:
            self.running = False
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y%m%dT%H%M%S")
        self.out_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = self.out_dir / f"{stamp}.md"
        header = (f"<!-- SCOOP triage report: {len(fresh)} group(s), {len(failures)} failure(s) since "
                  f"{since.isoformat(timespec='seconds') if since else 'the start of the logs'} -->\n")
        path.write_text(header + result.report + "\n\n---\n\n<details><summary>Failure groups</summary>\n\n"
                        + triage.report(failures) + "\n</details>\n")
        path.chmod(0o600)
        finished = not result.report.startswith("The triage agent ")  # stopped or failed: retry them
        self._save_state({"last_check": _iso(now) if finished else state.get("last_check"),
                          "reported": sorted(set(state.get("reported", [])) | ({g["key"] for g in fresh}
                                                                                if finished else set()))})
        return path

    async def loop(self, check_every_s: float = 300) -> None:
        while True:
            await asyncio.sleep(check_every_s)
            try:
                written = await self.tick()
                if written:
                    print(f"triage: wrote {written}")
            except Exception as exc:  # the timer must outlive any one failed check
                print(f"triage: check failed ({type(exc).__name__}: {exc})")

    def reports(self) -> list[dict]:
        out = []
        for path in sorted(self.out_dir.glob("*.md"), reverse=True):
            first = path.read_text().splitlines()[0] if path.stat().st_size else ""
            out.append({"name": path.name, "summary": first.removeprefix("<!-- ").removesuffix(" -->")})
        return out

    def report(self, name: str) -> str | None:
        path = self.out_dir / Path(name).name
        return path.read_text() if path.suffix == ".md" and path.is_file() else None


__all__ = ["TRIAGE_PROMPT", "TriageScheduler", "build_triage_tools"]
