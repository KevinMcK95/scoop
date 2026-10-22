"""Triage: the one step of a scan that needs a model.

Deciding whether "The Fifth Data Release of the X Survey" is a dataset SCOOP is
missing is not a string match. It needs the release paper read against what the
skills already say, the archive's own pages checked for what is actually
downloadable, and a judgment about whether a release supersedes a pinned one.
That is the whole of the model's job here; harvesting, the inventory, the file
writing and the report are code, so a wrong verdict costs a line in a report
rather than a broken pipeline.

The controls are in code, not in the prompt (as ``agentbook.guardrails`` puts
it): a step and token budget, a loop guard that refuses an identical repeated
call, a cap on how many proposals one scan may write, and a fetcher restricted
to archive domains. Every proposal is written as a draft for human review.
"""
from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

# agentbook/ is a top-level package beside scoop/, not part of the installed
# distribution, so an editable install needs the checkout root on the path
# (scoop/sources/exoplanets.py does the same for exoplanets/).
_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from agentbook.guardrails import Budget, BudgetExceeded, LoopGuard
from agentbook.loop import run_agent
from agentbook.models import Usage
from agentbook.tools import Tool, ToolError

from .candidates import Candidate
from .coverage import Coverage
from .fetch import FetchError, Fetcher
from .scaffold import REQUIRED_SECTIONS, Proposal, ScaffoldError, Service, Written, scaffold

VERDICTS = ("new", "update", "covered", "not_a_release")
PROPOSABLE = ("new", "update")
PROPOSABLE_TEXT = " or ".join(repr(verdict) for verdict in PROPOSABLE)
DEFAULT_MAX_PROPOSALS = 3  # one scan's worth of review for a person
DEFAULT_MAX_STEPS = 40


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


class CountingGuard(LoopGuard):
    """A LoopGuard that also counts calls and failures, for the scan's audit record."""

    def __init__(self, tools, scan: Scan, **kwargs):
        super().__init__(tools, **kwargs)
        self._scan = scan

    def call(self, name: str, input: dict) -> tuple[str, bool]:
        content, is_error = super().call(name, input)
        self._scan.tool_calls += 1
        self._scan.tool_failures += int(is_error)
        return content, is_error


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
                fetcher: Fetcher, max_proposals: int, today: date, dry_run: bool) -> list[Tool]:
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
        Tool("coverage_inventory",
             "What SCOOP already covers: every skill with its status, the release it is pinned "
             "to, the date its facts were last checked, whether it is queryable, and the list of "
             "registered data sources. Call this first.",
             {"type": "object", "properties": {}}, coverage_inventory),
        Tool("read_skill",
             "The full text of one existing skill, by its path from `coverage_inventory` "
             "(e.g. 'surveys/gaia.md'). Use it to check which release a skill is pinned to "
             "before calling a candidate an update.",
             {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
             read_skill),
        Tool("fetch_page",
             "Readable text of an https page on an archive, observatory or publisher domain. "
             "Use it to verify release contents, dates, table and column names, limits and "
             "citation policy. Anything else is refused with a reason; ask for a different page.",
             {"type": "object", "properties": {"url": {"type": "string"}}, "required": ["url"]},
             fetch_page),
        Tool("record_verdict",
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
        Tool("propose_dataset",
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
           max_tokens_total: int = 400_000, max_seconds: float = 1800.0, dry_run: bool = False,
           tracer=None) -> Scan:
    """Judge ``candidates`` against ``coverage`` and write up the gaps.

    Returns a ``Scan`` however the run ends: a budget stop still leaves the
    verdicts and proposals recorded before it.
    """
    today = today or date.today()
    fetcher = fetcher if fetcher is not None else Fetcher()
    scan = Scan()
    tools = CountingGuard(build_tools(coverage, candidates, scan, root=root, fetcher=fetcher,
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
    budget = Budget(max_steps=max_steps, max_tokens=max_tokens_total, max_seconds=max_seconds)
    try:
        result = run_agent(model, tools, task, system=system, max_steps=max_steps,
                           max_tokens_total=max_tokens_total, tracer=tracer,
                           on_step=budget.check)
        scan.summary, scan.steps = result.text, result.steps
        scan.usage, scan.stop_reason = result.usage, result.stop_reason
    except BudgetExceeded as exc:
        scan.stop_reason = f"budget: {exc}"
    scan.fetched, scan.refused = list(fetcher.fetched), list(fetcher.refused)
    return scan
