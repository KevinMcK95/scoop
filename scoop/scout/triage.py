"""Triage: the one step of a scan that needs a model.

Deciding whether "The Fifth Data Release of the X Survey" is a dataset SCOOP is
missing is not a string match. It needs the release paper read against what the
skills already say, the archive's own pages checked for what is actually
downloadable, and a judgment about whether a release supersedes a pinned one.
That is the whole of the model's job here; harvesting, the inventory, the file
writing and the report are code, so a wrong verdict costs a line in a report
rather than a broken pipeline.

The controls are in code, not in the prompt: a step, token and wall-clock
budget, a loop guard that refuses an identical repeated call, a cap on how many
proposals one scan may write, and a fetcher restricted to archive domains. Every
proposal is written as a draft for human review.

The loop is a plain one over a LangChain chat model's ``bind_tools`` rather
than a LangGraph graph: every budget is checked between two steps, against
counts kept on the ``Scan`` itself, so a scan stopped by any of them still
reports the steps and tokens it used.
"""
from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable

import jsonschema
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool, ToolException

from .candidates import Candidate
from .coverage import Coverage
from .fetch import FetchError, Fetcher
from .scaffold import REQUIRED_SECTIONS, Proposal, ScaffoldError, Service, Written, scaffold

VERDICTS = ("new", "update", "covered", "not_a_release")
PROPOSABLE = ("new", "update")
PROPOSABLE_TEXT = " or ".join(repr(verdict) for verdict in PROPOSABLE)
DEFAULT_MAX_PROPOSALS = 3  # one scan's worth of review for a person
DEFAULT_MAX_STEPS = 40
MAX_OUTPUT_CHARS = 8000  # one tool result, as the model sees it
MAX_REPEATS = 2  # identical calls allowed before the loop guard refuses one


class ToolError(ToolException):
    """Raise this inside a tool to send a clear, actionable message to the model.

    It is a ``ToolException``, so the tool returns it as an error ToolMessage
    with exactly this text rather than ending the scan.
    """


@dataclass
class Usage:
    """Token counts for one model call, or summed over a scan."""

    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens)

    @classmethod
    def of(cls, message: BaseMessage) -> "Usage":
        """The usage a chat model reported on its reply; zero if it reported none."""
        meta = getattr(message, "usage_metadata", None) or {}
        return cls(int(meta.get("input_tokens") or 0), int(meta.get("output_tokens") or 0))


@dataclass(frozen=True)
class Verdict:
    bibcode: str
    verdict: str
    survey: str | None
    release: str | None
    reason: str

    def row(self) -> dict:
        return {"bibcode": self.bibcode, "verdict": self.verdict, "survey": self.survey,
                "release": self.release, "reason": self.reason}


@dataclass
class Scan:
    """The outcome of one triage pass."""

    verdicts: list[Verdict] = field(default_factory=list)
    written: list[Written] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)  # proposals the scaffolder refused
    summary: str = ""
    steps: int = 0
    usage: Usage = field(default_factory=Usage)
    stop_reason: str = "not run"
    fetched: list[str] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)
    tool_calls: int = 0
    tool_failures: int = 0

    def gaps(self) -> list[Verdict]:
        return [v for v in self.verdicts if v.verdict in PROPOSABLE]


class CountingGuard:
    """Runs the model's tool calls for one scan.

    It refuses a call repeated with identical arguments more than
    ``max_repeats`` times, and tells the model why, instead of running it again;
    and it counts every call and every failure (refusals included) for the
    scan's audit record.
    """

    def __init__(self, tools: list[StructuredTool], scan: Scan, max_repeats: int = MAX_REPEATS):
        self.tools = {tool.name: tool for tool in tools}
        self.max_repeats = max_repeats
        self.counts: dict[tuple[str, str], int] = {}
        self._scan = scan

    def run(self, call: dict) -> ToolMessage:
        """Run one tool call and return its ToolMessage; never raises for a tool's failure."""
        name, args, call_id = call["name"], call["args"], call["id"]
        key = (name, json.dumps(args, sort_keys=True, default=str))
        self.counts[key] = self.counts.get(key, 0) + 1
        if self.counts[key] > self.max_repeats:
            message = _error(f"Refused: {name} was already called {self.counts[key] - 1} times with "
                             "these exact arguments and the result will not change. Use the earlier "
                             "result, change the arguments, or finish with what you have.", name, call_id)
        elif name not in self.tools:
            message = _error(f"Unknown tool '{name}'. Available tools: {', '.join(self.tools)}.",
                             name, call_id)
        else:
            message = self.tools[name].invoke({"name": name, "args": args, "id": call_id,
                                               "type": "tool_call"})
        self._scan.tool_calls += 1
        self._scan.tool_failures += int(message.status == "error")
        return message


def _error(text: str, name: str, call_id: str) -> ToolMessage:
    return ToolMessage(content=text, name=name, tool_call_id=call_id, status="error")


def _format(result: Any) -> str:
    """Tool results go to the model as text; dicts and lists become JSON, cut to MAX_OUTPUT_CHARS."""
    text = result if isinstance(result, str) else json.dumps(result, indent=1, ensure_ascii=False,
                                                             default=str)
    if len(text) <= MAX_OUTPUT_CHARS:
        return text
    return (text[:MAX_OUTPUT_CHARS] + f"\n[... truncated: showing {MAX_OUTPUT_CHARS} of {len(text)} "
            "characters. Ask for a narrower section or a more specific query.]")


def make_tool(name: str, description: str, schema: dict, fn: Callable[..., Any]) -> StructuredTool:
    """A LangChain tool whose arguments are checked against ``schema`` before ``fn`` runs.

    The model sees ``schema`` unchanged. Invalid arguments, a ``ToolError`` and
    any other exception all come back to the model as an error ToolMessage it
    can read and act on; a tool never ends the scan.
    """
    def run(**args: Any) -> str:
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError as exc:
            where = ".".join(str(part) for part in exc.path) or "arguments"
            raise ToolError(f"Invalid arguments for {name} ({where}): {exc.message}. "
                            f"Expected: {json.dumps(schema.get('properties', {}))}") from exc
        try:
            result = fn(**args)
        except ToolError:
            raise
        except Exception as exc:  # any other bug becomes a readable error, not a crash
            raise ToolError(f"{name} failed with {type(exc).__name__}: {exc}") from exc
        return _format(result)

    return StructuredTool.from_function(func=run, name=name, description=description,
                                        args_schema=schema, handle_tool_error=True)


SYSTEM = """You are SCOOP's dataset scout. Once a month you read the data-release and
catalogue papers that appeared since the last scan and decide which of them SCOOP is
missing, then write a draft skill (and, where you can verify a TAP service, a data
source) so the pipeline can use the dataset.

Work in this order:

1. Call `coverage_inventory` to see what SCOOP already covers. Each candidate also
   lists `related_skills`: skills whose survey its title or abstract mentions. Call
   `read_skill` on those to see which release each is pinned to. `related_skills` is a
   keyword match, so it is a hint, not an answer — an empty list does not prove a gap.
2. Give every candidate in the task exactly one `record_verdict` call:
   - `not_a_release`: the paper does not announce a public dataset anyone can download
     (a science result, a method paper, a proposal, a simulation with no public files).
   - `covered`: SCOOP already covers this survey at this release or later.
   - `update`: SCOOP covers this survey, but at an older release than the paper
     announces; the existing skill needs re-verifying.
   - `new`: a public dataset SCOOP has no skill for.
   Judge from the abstract first; fetch pages only for candidates you may propose.
3. For the best `new` or `update` candidates (at most the limit stated in the task),
   verify them against the archive and call `propose_dataset`.

Rules that are not negotiable:

- Every fact in a proposal must come from a page you fetched with `fetch_page` in this
  scan, or from the release paper's abstract. Never from memory. Table names, column
  names, row limits and release dates are read from the service or its documentation.
- List the URLs you used in `sources_checked`. A proposal with facts you did not fetch
  is worse than no proposal.
- Only give `service` when you have read the TAP endpoint, the table name and the
  column names from the archive's own schema or documentation. Leave it out otherwise;
  a skill without a data source is a perfectly good result.
- Put everything you could not confirm in the `not_yet_verified` section. It is required.
- Prefer the big public survey releases an astronomer would actually want (new imaging
  or spectroscopic surveys, major data releases, large value-added catalogues) over
  single-object or one-off tables.

Finish with a short plain-text summary: what you judged, what you proposed, and what a
reviewer should check first.

The template every skill must follow, and the ground rules behind it, are below. Follow
it exactly; the section keys of `propose_dataset` map onto its headings.

---
{playbook}
"""

TASK = """Scan window: {since} to {until}. Today is {today}.

You may write at most {max_proposals} proposal(s) in this scan.

{dry_run_note}Candidates harvested from ADS ({count}):

{candidates}
"""

DRY_RUN_NOTE = ("This is a dry run: `propose_dataset` will validate a proposal and report "
                "what it would write, but will not write anything. Record verdicts as usual.\n\n")


def _playbook(root: Path) -> str:
    path = root / "scoop" / "skills" / "meta" / "new_survey_skill.md"
    if not path.exists():
        raise FileNotFoundError(f"{path} is the scout's playbook and is missing")
    return path.read_text(encoding="utf-8")


def _service(value: dict | None) -> Service | None:
    if not value:
        return None
    columns = value.get("extra_columns") or []
    if not isinstance(columns, list) or any(not isinstance(c, str) for c in columns):
        raise ToolError("extra_columns must be a list of column names")
    return Service(tap_url=value.get("tap_url", ""), table=value.get("table", ""),
                   ra_column=value.get("ra_column", ""), dec_column=value.get("dec_column", ""),
                   id_column=value.get("id_column", ""), extra_columns=tuple(columns))


def build_tools(coverage: Coverage, candidates: tuple[Candidate, ...], scan: Scan, *, root: Path,
                fetcher: Fetcher, max_proposals: int, today: date, dry_run: bool) -> list[StructuredTool]:
    """The scout's tools, bound to one scan's state."""
    by_bibcode = {c.bibcode: c for c in candidates}
    accepted = [0]  # proposals that passed validation, whether or not they were written

    def coverage_inventory() -> dict:
        return coverage.summary()

    def read_skill(path: str) -> str:
        skill = (root / "scoop" / "skills" / path).resolve()
        if not skill.is_relative_to((root / "scoop" / "skills").resolve()) or not skill.exists():
            raise ToolError(f"No skill at scoop/skills/{path}. Use a path from `coverage_inventory`.")
        return skill.read_text(encoding="utf-8")

    def fetch_page(url: str) -> str:
        try:
            return fetcher.get(url)
        except FetchError as exc:
            raise ToolError(str(exc)) from exc

    def record_verdict(bibcode: str, verdict: str, reason: str, survey: str | None = None,
                       release: str | None = None) -> str:
        if bibcode not in by_bibcode:
            raise ToolError(f"{bibcode} is not one of this scan's candidates.")
        if verdict not in VERDICTS:
            raise ToolError(f"verdict must be one of {', '.join(VERDICTS)}.")
        if any(v.bibcode == bibcode for v in scan.verdicts):
            raise ToolError(f"{bibcode} already has a verdict; one per candidate.")
        scan.verdicts.append(Verdict(bibcode, verdict, survey, release, reason))
        remaining = [c.bibcode for c in candidates if not any(v.bibcode == c.bibcode
                                                              for v in scan.verdicts)]
        return (f"Recorded {verdict} for {bibcode}. "
                + (f"{len(remaining)} candidate(s) still need a verdict." if remaining
                   else "Every candidate now has a verdict."))

    def propose_dataset(slug: str, survey: str, release: str, description: str, bibcode: str,
                        sources_checked: list, sections: dict, used_by: list | None = None,
                        service: dict | None = None, probe_ra: float | None = None,
                        probe_dec: float | None = None, probe_radius_deg: float = 0.1,
                        probe_field: str = "a field with a known answer") -> dict:
        judged = next((v for v in scan.verdicts if v.bibcode == bibcode), None)
        if judged is None or judged.verdict not in PROPOSABLE:
            raise ToolError(f"Record a {PROPOSABLE_TEXT} verdict for {bibcode} before proposing it.")
        if accepted[0] >= max_proposals:
            raise ToolError(f"This scan's limit of {max_proposals} proposal(s) is used up. "
                            "Finish with a summary of the gaps you did not write up.")
        unfetched = [url for url in sources_checked if url not in fetcher.fetched]
        if unfetched:
            raise ToolError(
                f"These sources_checked URLs were not fetched in this scan: {', '.join(unfetched)}. "
                "Fetch a page before citing it, or remove it from the list.")
        try:
            proposal = Proposal(
                slug=slug, survey=survey, release=release, description=description, bibcode=bibcode,
                sources_checked=tuple(sources_checked), sections=dict(sections),
                used_by=tuple(used_by or ("imaging",)), service=_service(service),
                probe_ra=probe_ra, probe_dec=probe_dec, probe_radius_deg=probe_radius_deg,
                probe_field=probe_field)
        except ScaffoldError as exc:
            raise ToolError(str(exc)) from exc
        if dry_run:
            accepted[0] += 1
            scan.rejected.append(f"{slug}: validated but not written (dry run)")
            return {"dry_run": True, "would_write": [f"scoop/skills/surveys/{slug}.md"]
                    + ([f"scoop/sources/{slug}.py", f"smoke_tests/{slug}_probe.py"]
                       if proposal.service else []),
                    "remaining_proposals": max_proposals - accepted[0]}
        try:
            written = scaffold(root, proposal, today=today)
        except ScaffoldError as exc:
            scan.rejected.append(f"{slug}: {exc}")
            raise ToolError(str(exc)) from exc
        accepted[0] += 1
        scan.written.append(written)
        return {"written": written.paths, "notes": written.notes,
                "remaining_proposals": max_proposals - accepted[0]}

    sections_schema = {
        "type": "object",
        "description": "One markdown block per heading of the skill template.",
        "properties": {key: {"type": "string"} for key in REQUIRED_SECTIONS},
        "required": list(REQUIRED_SECTIONS),
    }
    service_schema = {
        "type": "object",
        "description": ("The TAP service to generate a data source from. Give it only when you "
                        "have read all five values from the archive's schema or docs."),
        "properties": {
            "tap_url": {"type": "string", "description": "https TAP base URL"},
            "table": {"type": "string", "description": "fully qualified table name"},
            "ra_column": {"type": "string"}, "dec_column": {"type": "string"},
            "id_column": {"type": "string", "description": "the survey's own source identifier"},
            "extra_columns": {"type": "array", "items": {"type": "string"},
                              "description": "up to ~8 useful columns, e.g. magnitudes, redshift"},
        },
        "required": ["tap_url", "table", "ra_column", "dec_column", "id_column"],
    }
    return [
        make_tool("coverage_inventory",
                  "What SCOOP already covers: every skill with its status, the release it is pinned "
                  "to, the date its facts were last checked, whether it is queryable, and the list of "
                  "registered data sources. Call this first.",
                  {"type": "object", "properties": {}}, coverage_inventory),
        make_tool("read_skill",
                  "The full text of one existing skill, by its path from `coverage_inventory` "
                  "(e.g. 'surveys/gaia.md'). Use it to check which release a skill is pinned to "
                  "before calling a candidate an update.",
                  {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
                  read_skill),
        make_tool("fetch_page",
                  "Readable text of an https page on an archive, observatory or publisher domain. "
                  "Use it to verify release contents, dates, table and column names, limits and "
                  "citation policy. Anything else is refused with a reason; ask for a different page.",
                  {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
                  fetch_page),
        make_tool("record_verdict",
                  "Your decision about one candidate. Every candidate needs exactly one. "
                  f"verdict is one of: {', '.join(VERDICTS)}. `reason` is one sentence a reviewer "
                  "will read in the scan report.",
                  {"type": "object",
                   "properties": {"bibcode": {"type": "string"},
                                  "verdict": {"type": "string", "enum": list(VERDICTS)},
                                  "reason": {"type": "string"},
                                  "survey": {"type": "string", "description": "the survey it belongs to"},
                                  "release": {"type": "string", "description": "e.g. 'DR3', 'EDR1'"}},
                   "required": ["bibcode", "verdict", "reason"]},
                  record_verdict),
        make_tool("propose_dataset",
                  "Write a draft skill for a dataset SCOOP is missing, plus a data source and a probe "
                  "if you give `service`. `slug` is lowercase snake_case and becomes the filenames. "
                  "`sections` must fill every heading of the template. `sources_checked` must list "
                  "only URLs you fetched in this scan. Nothing is overwritten and everything is "
                  "written as a draft for a person to review.",
                  {"type": "object",
                   "properties": {
                       "slug": {"type": "string", "pattern": "^[a-z][a-z0-9_]{1,40}$"},
                       "survey": {"type": "string", "description": "display name, e.g. 'Euclid Q1'"},
                       "release": {"type": "string"},
                       "description": {"type": "string",
                                       "description": "what it holds and when to load it; one or two sentences"},
                       "bibcode": {"type": "string", "description": "the candidate this came from"},
                       "sources_checked": {"type": "array", "items": {"type": "string"}, "minItems": 1},
                       "sections": sections_schema,
                       "used_by": {"type": "array", "items": {"type": "string"},
                                   "description": "data agents, e.g. ['imaging', 'spectra']"},
                       "service": service_schema,
                       "probe_ra": {"type": "number", "description": "RA you queried while verifying"},
                       "probe_dec": {"type": "number"},
                       "probe_radius_deg": {"type": "number"},
                       "probe_field": {"type": "string", "description": "what that field is"},
                   },
                   "required": ["slug", "survey", "release", "description", "bibcode",
                                "sources_checked", "sections"]},
                  propose_dataset),
    ]


def triage(model, candidates: tuple[Candidate, ...], coverage: Coverage, *, root: Path,
           since: date, until: date, today: date | None = None, fetcher: Fetcher | None = None,
           max_proposals: int = DEFAULT_MAX_PROPOSALS, max_steps: int = DEFAULT_MAX_STEPS,
           max_tokens_total: int = 400_000, max_seconds: float = 1800.0,
           dry_run: bool = False) -> Scan:
    """Judge ``candidates`` against ``coverage`` and write up the gaps.

    ``model`` is a LangChain chat model (anything with ``bind_tools`` whose
    result has ``invoke``). Returns a ``Scan`` however the run ends: a budget
    stop still leaves the verdicts and proposals recorded before it, and the
    steps and tokens it took.
    """
    today = today or date.today()
    fetcher = fetcher if fetcher is not None else Fetcher()
    scan = Scan()
    guard = CountingGuard(build_tools(coverage, candidates, scan, root=root, fetcher=fetcher,
                                      max_proposals=max_proposals, today=today, dry_run=dry_run),
                          scan)
    system = SYSTEM.format(playbook=_playbook(root))
    # Each candidate is annotated with the skills whose survey it mentions, so the
    # model checks a short list with read_skill instead of scanning the inventory.
    briefs = [{**candidate.brief(),
               "related_skills": [skill.path for skill
                                  in coverage.match(f"{candidate.title} {candidate.abstract or ''}")]}
              for candidate in candidates]
    task = TASK.format(
        since=since.isoformat(), until=until.isoformat(), today=today.isoformat(),
        max_proposals=max_proposals, count=len(candidates),
        dry_run_note=DRY_RUN_NOTE if dry_run else "",
        candidates=json.dumps(briefs, indent=1, ensure_ascii=False))
    # Fetching archive pages makes a scan slow by design, so the wall-clock limit is
    # generous; the step and token limits are what actually bound the cost.
    run_loop(model.bind_tools(list(guard.tools.values())), guard, scan,
             [SystemMessage(content=system), HumanMessage(content=task)],
             max_steps=max_steps, max_tokens_total=max_tokens_total, max_seconds=max_seconds)
    scan.fetched, scan.refused = list(fetcher.fetched), list(fetcher.refused)
    return scan


def run_loop(bound_model, guard: CountingGuard, scan: Scan, messages: list[BaseMessage], *,
             max_steps: int, max_tokens_total: int, max_seconds: float) -> None:
    """Call the model, run the tools it asks for, repeat, until it answers or a budget is spent.

    One step is one model call and the tool calls it asked for. ``scan.steps``
    and ``scan.usage`` are updated after every model call, so they are right
    however the loop ends. The budgets are checked once a step's tool results
    are in, which is the only point where going on would cost another call; a
    reply with no tool calls ends the scan as "done" whatever the budgets say.
    """
    started = time.monotonic()
    for step in range(1, max_steps + 1):
        response = bound_model.invoke(messages)
        scan.steps = step
        scan.usage = scan.usage + Usage.of(response)
        messages.append(response)
        calls = _tool_calls(response)
        if not calls:  # no tool requests: the model is done
            scan.summary, scan.stop_reason = response.text, "done"
            return
        messages.extend(guard.run(call) for call in calls)
        reason = _exceeded(step, scan.usage, time.monotonic() - started, max_steps=max_steps,
                           max_tokens=max_tokens_total, max_seconds=max_seconds)
        if reason:
            scan.stop_reason = f"budget: {reason}"
            return


def _exceeded(steps: int, usage: Usage, seconds: float, *, max_steps: int, max_tokens: int,
              max_seconds: float) -> str | None:
    """The reason to stop, or None."""
    if steps >= max_steps:
        return f"step limit ({steps}/{max_steps})"
    if usage.total_tokens >= max_tokens:
        return f"token budget ({usage.total_tokens}/{max_tokens})"
    if seconds >= max_seconds:
        return f"time limit ({max_seconds:.0f} s)"
    return None


def _tool_calls(response: BaseMessage) -> list[dict]:
    """The reply's tool calls, in order.

    A call whose arguments were not valid JSON (``invalid_tool_calls``) is kept
    with ``{"_invalid_json": raw}`` as its arguments, so it fails and the
    model is told to fix them; every call the model made gets a ToolMessage
    back, as the providers require.
    """
    if not isinstance(response, AIMessage):
        return []
    calls = [{"name": c["name"], "args": c["args"], "id": c["id"]} for c in response.tool_calls]
    calls += [{"name": c.get("name") or "", "args": {"_invalid_json": c.get("args")},
               "id": c.get("id") or ""}
              for c in response.invalid_tool_calls]
    return calls

