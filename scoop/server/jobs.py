"""Background runs of the field pipeline, with progress for the browser.

Runs the field pipeline (``python -m scoop.pipeline.lvdb_field``) as a subprocess with an
argument list (no shell); its ``[decision]`` lines are the progress log. The
output lands in ``<fields>/<key>/<key>_field.json``, where FieldStore finds it.
"""
from __future__ import annotations

import asyncio
import json
import re
import sys
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path

KEY = re.compile(r"[a-z0-9_]{1,64}")
# Starts with a letter or digit, so it can never be read as a command-line flag.
NAME = re.compile(r"[^\W_][\w .+\-()′'\"]{0,59}")
MAX_CONE_ARCMIN = 60.0  # larger cones exceed the pipeline's Gaia row-limit check
MAX_LINES = 200
OPTIONAL_ARCHIVES = ("hst_jwst", "qso", "spectra")  # beyond Gaia and SIMBAD galaxies, on request
PLAN = re.compile(r"[0-9a-f]{12}")
# Symbols agents put in labels ("0.5 deg² box", "NGC 300: 30′ cone", "Leo I/II"), mapped
# to what NAME allows; anything else becomes a space.
_NAME_SYMBOLS = {"°": " deg", "²": "2", "³": "3", "″": '"', "’": "'", "‘": "'", "“": '"', "”": '"',
                 "–": "-", "—": "-", "±": "+-", "×": "x", "&": "and", "/": "-", ":": " -", ",": " ",
                 "~": "", "≈": "", "−": "-", "∈": " in ", "=": " "}


def clean_name(text) -> str:
    """A label the pipeline accepts (NAME), made from whatever the agent wrote: symbols
    mapped or dropped, spaces collapsed, at most 60 characters at a word boundary. Run
    logs showed most failed first run_cone calls were labels with ':', ',', '°', '²' or
    more than 60 characters (2026-10-21)."""
    out = "".join(_NAME_SYMBOLS.get(c, c) for c in str(text or ""))
    out = re.sub(r"[^\w .+\-()′'\"]", " ", out).replace("_", " ")
    out = re.sub(r"\s+", " ", out).strip(" .+-()'\"")
    if len(out) > 60:
        out = out[:61].rsplit(" ", 1)[0] if " " in out[:61] else out[:60]
    out = out.strip(" .+-('\"")
    if not NAME.fullmatch(out):
        raise ValueError("the name needs letters or digits (e.g. 'NGC 300 30 arcmin')")
    return out


def _key_from_name(name: str) -> str | None:
    """The field key the pipeline derives from a run's name, or None if it has no letters or digits."""
    key = re.sub(r"[^a-z0-9]+", "_", name.lower()).strip("_")
    return key if KEY.fullmatch(key) else None


@dataclass
class Job:
    id: str
    key: str
    n_rhalf: float
    args: list[str] = field(default_factory=list)
    status: str = "running"  # running | done | failed
    started: float = field(default_factory=time.monotonic)
    finished: float | None = None
    lines: list[str] = field(default_factory=list)
    decisions: list[str] = field(default_factory=list)
    error: str | None = None

    def view(self) -> dict:
        end = self.finished or time.monotonic()
        return {"id": self.id, "key": self.key, "n_rhalf": self.n_rhalf, "status": self.status,
                "elapsed_s": round(end - self.started, 1), "last": self.lines[-1] if self.lines else None,
                "decisions": self.decisions, "error": self.error}


PIPELINE = "scoop.pipeline.lvdb_field"


class JobManager:
    """``script`` replaces the installed pipeline module (tests run a stand-in)."""

    def __init__(self, fields_root: Path, script: Path | None = None, python: str = sys.executable,
                 timeout_s: float = 900.0) -> None:
        self.root, self.python, self.timeout_s = Path(fields_root), python, timeout_s
        self.script = Path(script) if script is not None else None
        self.plans = self.root / "region_plans"
        self.jobs: dict[str, Job] = {}
        self._tasks: set[asyncio.Task] = set()

    def get(self, job_id: str) -> Job | None:
        return self.jobs.get(job_id)

    def start(self, key: str, n_rhalf: float = 2.0) -> Job:
        """Start (or join an already running) pipeline run for an LVDB key."""
        if not KEY.fullmatch(key):
            raise ValueError("LVDB keys are lowercase letters, digits and underscores")
        if not (isinstance(n_rhalf, (int, float)) and 0.5 <= n_rhalf <= 10):
            raise ValueError("n_rhalf must be between 0.5 and 10")
        return self._launch(key, float(n_rhalf), ["--lvdb-key", key, "--n-rhalf", str(float(n_rhalf))])

    def start_cone(self, ra: float, dec: float, radius_arcmin: float, name: str,
                   include: list[str] | tuple = ()) -> Job:
        """Start a run over a circular cone around any position. It fetches Gaia and SIMBAD
        galaxies; ``include`` adds optional archives (hst_jwst, qso, spectra)."""
        number = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)  # noqa: E731
        if not (number(ra) and 0 <= ra < 360 and number(dec) and -90 <= dec <= 90):
            raise ValueError("ra must be in [0, 360) and dec in [-90, 90] degrees")
        if not (number(radius_arcmin) and 0 < radius_arcmin <= MAX_CONE_ARCMIN):
            raise ValueError(f"radius must be in (0, {MAX_CONE_ARCMIN:g}] arcmin")
        name = clean_name(name)
        key = _key_from_name(name)
        if key is None:
            raise ValueError("name must contain letters or digits")
        extras = sorted(set(include or ()))
        if set(extras) - set(OPTIONAL_ARCHIVES):
            raise ValueError(f"unknown archives {sorted(set(extras) - set(OPTIONAL_ARCHIVES))}; "
                             f"optional ones: {list(OPTIONAL_ARCHIVES)}")
        return self._launch(key, 1.0, ["--ra", repr(float(ra)), "--dec", repr(float(dec)),
                                       "--radius-arcmin", repr(float(radius_arcmin)), "--name", name,
                                       *(["--include", ",".join(extras)] if extras else [])])

    def find(self, ref: str) -> Job | None:
        """A job by id, or the latest job for a field key."""
        if ref in self.jobs:
            return self.jobs[ref]
        matches = [j for j in self.jobs.values() if j.key == ref]
        return max(matches, key=lambda j: j.started) if matches else None

    def recent_failures(self, within_s: float = 3600.0) -> list[dict]:
        """Runs that failed in the last ``within_s`` seconds, newest first: what an agent
        should know about before it starts (or retries) a field."""
        now = time.monotonic()
        failed = [j for j in self.jobs.values() if j.status == "failed" and j.finished and now - j.finished < within_s]
        return [{"job": j.id, "key": j.key, "error": j.error, "minutes_ago": round((now - j.finished) / 60, 1)}
                for j in sorted(failed, key=lambda j: j.finished, reverse=True)]

    # ── Regions: a plan is a saved spec (with cuts), counted before it is run ──
    def save_plan(self, spec: dict, name: str) -> str:
        """Store a region spec (validated by the caller) under a new plan id."""
        name = clean_name(name)
        plan_id = uuid.uuid4().hex[:12]
        self.plans.mkdir(parents=True, exist_ok=True)
        (self.plans / f"{plan_id}.json").write_text(json.dumps({"name": name, "spec": spec}))
        return plan_id

    def load_plan(self, plan_id: str) -> dict | None:
        path = self.plans / f"{plan_id}.json"
        return json.loads(path.read_text()) if PLAN.fullmatch(plan_id or "") and path.exists() else None

    def _command(self) -> list[str]:
        return [self.python, str(self.script.resolve())] if self.script else [self.python, "-m", PIPELINE]

    def _cwd(self) -> str:
        if self.script:
            return str(self.script.parent)
        self.root.mkdir(parents=True, exist_ok=True)
        return str(self.root)

    async def count_plan(self, plan_id: str, timeout_s: float = 300.0) -> dict:
        """Gaia row counts for a plan (the pipeline's --count-only), without building a field."""
        if self.load_plan(plan_id) is None:
            raise ValueError(f"no region plan {plan_id!r}")
        process = await asyncio.create_subprocess_exec(
            *self._command(), "--out", str(self.plans / f"{plan_id}_count"),
            "--region", str(self.plans / f"{plan_id}.json"), "--count-only", cwd=self._cwd(),
            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        try:
            async with asyncio.timeout(timeout_s):
                output = (await process.communicate())[0].decode(errors="replace")
        except TimeoutError:
            await _stop(process)
            raise RuntimeError(f"counting timed out after {timeout_s:.0f} s (a slow or unanswering Gaia server, "
                               f"or a region with millions of stars)")
        line = next((l for l in output.splitlines() if l.startswith("COUNTS ")), None)
        if line is None:
            tail = [l for l in output.splitlines() if l.strip()][-3:]
            raise RuntimeError("counting failed: " + " | ".join(tail))
        return json.loads(line[len("COUNTS "):])

    def start_region(self, plan_id: str, max_gaia_rows: int) -> Job:
        plan = self.load_plan(plan_id)
        if plan is None:
            raise ValueError(f"no region plan {plan_id!r}")
        key = _key_from_name(plan["name"])
        if key is None:
            raise ValueError("the plan's name must contain letters or digits")
        return self._launch(key, 1.0, ["--region", str(self.plans / f"{plan_id}.json"), "--name", plan["name"],
                                       "--max-gaia-rows", str(int(max_gaia_rows))], timeout_s=max(self.timeout_s, 2400))

    def _launch(self, key: str, n_rhalf: float, args: list[str], timeout_s: float | None = None) -> Job:
        for job in self.jobs.values():
            if job.key == key and job.status == "running":
                return job
        job = Job(id=uuid.uuid4().hex[:12], key=key, n_rhalf=n_rhalf, args=args)
        self.jobs[job.id] = job
        task = asyncio.get_running_loop().create_task(self._run(job, timeout_s or self.timeout_s))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return job

    async def _run(self, job: Job, timeout_s: float) -> None:
        out = (self.root / job.key).resolve()
        try:
            process = await asyncio.create_subprocess_exec(
                *self._command(), "--out", str(out), *job.args, cwd=self._cwd(),
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
            async with asyncio.timeout(timeout_s):
                async for raw in process.stdout:
                    line = raw.decode(errors="replace").rstrip()
                    if not line:
                        continue
                    job.lines = (job.lines + [line])[-MAX_LINES:]
                    if "[decision]" in line:
                        job.decisions.append(line.split("[decision]", 1)[1].strip())
                code = await process.wait()
            if code == 0 and (out / f"{job.key}_field.json").exists():
                job.status = "done"
            else:
                job.status = "failed"
                job.error = next((l for l in reversed(job.lines) if "Error" in l), job.lines[-1] if job.lines else
                                 f"exit code {code}")
        except TimeoutError:
            await _stop(process)
            job.status, job.error = "failed", f"timed out after {timeout_s:.0f} s"
        except OSError as exc:
            job.status, job.error = "failed", f"could not start the pipeline: {exc}"
        finally:
            job.finished = time.monotonic()
            if job.status == "failed":
                self._keep_failure(job)

    def _keep_failure(self, job: Job) -> None:
        """A failed run's record and log under <fields>/_runs/, for triage after restarts."""
        try:
            runs = self.root / "_runs"
            runs.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y%m%dT%H%M%S", time.gmtime())
            (runs / f"{stamp}_{job.key}_{job.id}.json").write_text(json.dumps({
                "job": job.id, "key": job.key, "args": job.args, "error": job.error,
                "elapsed_s": round(job.finished - job.started, 1), "failed_utc": stamp,
                "decisions": job.decisions, "log": job.lines}, indent=1))
        except OSError:
            pass  # keeping the record must never break the run's own reporting


async def _stop(process) -> None:
    """SIGTERM first, so the pipeline aborts its server-side TAP jobs; kill if it lingers."""
    try:
        process.terminate()
        await asyncio.wait_for(process.wait(), 20)
    except (ProcessLookupError, TimeoutError):
        try:
            process.kill()
        except ProcessLookupError:
            pass


__all__ = ["Job", "JobManager"]
