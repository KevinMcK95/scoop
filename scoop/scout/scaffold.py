"""Turning an accepted gap into reviewable files in the repository.

This is the "smoothly integrates" half of the scout, and it is deliberately
plain code: the model supplies the facts it verified, this module decides where
they go and in what shape. A proposal becomes up to four things:

- ``skills/surveys/<slug>.md`` — a draft skill in the exact template from
  ``skills/meta/new_survey_skill.md``, so every data agent can read it the same
  way as the reviewed ones;
- ``smoke_tests/<slug>_probe.py`` — the probe that re-checks the facts, runnable
  on release day or whenever the skill looks stale;
- ``scoop/sources/<slug>.py`` — a registered data source, which is all the field
  pipeline needs to start returning the survey's rows (``scoop/sources`` is
  discovered automatically);
- a row in ``skills/index.md``, so agents find the skill at all.

Three rules keep this safe to run unattended: nothing is overwritten, every path
is checked to be inside the checkout, and everything is written with
``status: draft`` so an agent using it must tell the user it is unreviewed. The
probe and the data source are written only when the proposal carries a real TAP
endpoint, because a source module that cannot query is worse than none.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

SLUG = re.compile(r"[a-z][a-z0-9_]{1,40}")
# Text that goes into the generated Python (string literals, f-strings, comments) can
# neither end the literal nor start an expression or a new line.
TEXT = re.compile(r"[^\x00-\x1f\x7f\"\\{}]{1,200}")
URL = re.compile(r"https://[^\s\"'\\{}]+")
SECTIONS = (
    ("what_is_here", "What's here"),
    ("what_is_not_here", "What's NOT here"),
    ("release_status", "Release status"),
    ("coverage_and_budget", "Coverage check and budget"),
    ("how_to_query", "How to query"),
    ("gotchas", "Gotchas"),
    ("defaults", "Default decisions and what to tell the user"),
    ("sanity_checks", "Sanity checks"),
    ("cite", "Cite and acknowledge"),
    ("not_yet_verified", "Not yet verified"),
)
REQUIRED_SECTIONS = tuple(key for key, _ in SECTIONS)


class ScaffoldError(Exception):
    """A proposal that cannot be written, stated so the model can fix it."""


@dataclass(frozen=True)
class Service:
    """The TAP endpoint the generated data source and probe will query."""

    tap_url: str
    table: str
    ra_column: str
    dec_column: str
    id_column: str
    extra_columns: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not URL.fullmatch(self.tap_url or ""):
            raise ScaffoldError("tap_url must be an https URL read from the archive's own docs")
        for name, value in (("table", self.table), ("ra_column", self.ra_column),
                            ("dec_column", self.dec_column), ("id_column", self.id_column)):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", value or ""):
                raise ScaffoldError(f"{name} must be an identifier read from the service's schema, "
                                    f"got {value!r}")
        for column in self.extra_columns:
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_.]*", column):
                raise ScaffoldError(f"extra column {column!r} is not an identifier")

    @property
    def select(self) -> str:
        columns = [self.id_column, self.ra_column, self.dec_column, *self.extra_columns]
        return ", ".join(dict.fromkeys(columns))


@dataclass(frozen=True)
class Proposal:
    """One dataset the scout wants SCOOP to cover, with the facts it verified."""

    slug: str
    survey: str
    release: str
    description: str
    bibcode: str
    sources_checked: tuple[str, ...]
    sections: dict[str, str]
    used_by: tuple[str, ...] = ("imaging",)
    service: Service | None = None
    probe_ra: float | None = None
    probe_dec: float | None = None
    probe_radius_deg: float = 0.1
    probe_field: str = "a field with a known answer"

    def __post_init__(self) -> None:
        if not SLUG.fullmatch(self.slug):
            raise ScaffoldError(f"slug must be lowercase snake_case, got {self.slug!r}")
        for name in ("survey", "release", "bibcode", "probe_field"):
            if not TEXT.fullmatch(getattr(self, name) or ""):
                raise ScaffoldError(f"{name} must be one line of plain text without double quotes, "
                                    f"backslashes or braces, got {getattr(self, name)!r}")
        if not self.sources_checked:
            raise ScaffoldError("sources_checked is empty: a skill may only state facts that were "
                                "fetched from the archive in this scan")
        missing = [key for key in REQUIRED_SECTIONS if not (self.sections.get(key) or "").strip()]
        if missing:
            raise ScaffoldError(f"these sections are required and empty: {', '.join(missing)}. "
                                "'Not yet verified' may say 'Nothing outstanding.' but must not be blank.")
        if self.service is not None and (self.probe_ra is None or self.probe_dec is None):
            raise ScaffoldError("a proposal with a TAP service needs probe_ra and probe_dec: the "
                                "position you actually queried while verifying it")


@dataclass
class Written:
    """What one proposal produced, for the report and the pull request."""

    slug: str
    paths: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def _checked(root: Path, relative: str) -> Path:
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ScaffoldError(f"{relative} is outside the checkout")
    if path.exists():
        raise ScaffoldError(f"{relative} already exists; the scout never overwrites. "
                            "Propose a different slug, or update the existing file by hand.")
    return path


def skill_markdown(proposal: Proposal, today: date) -> str:
    body = "\n\n".join(f"## {heading}\n\n{proposal.sections[key].strip()}"
                       for key, heading in SECTIONS)
    sources = "\n  ".join(proposal.sources_checked)
    return (
        "---\n"
        f"name: {proposal.slug}\n"
        f"description: {proposal.description.strip()}\n"
        f"used_by: [{', '.join(proposal.used_by)}]\n"
        "status: draft\n"
        f"sources_checked: {today.isoformat()} (\n  {sources}\n  )\n"
        f"proposed_by: dataset scout, {today.isoformat()}, from {proposal.bibcode}\n"
        "---\n\n"
        f"# {proposal.survey}\n\n"
        "> **Draft.** Written by the dataset scout from the archive's own pages and the release\n"
        f"> paper ([{proposal.bibcode}](https://ui.adsabs.harvard.edu/abs/{proposal.bibcode})).\n"
        "> No person has reviewed it. An agent using this skill must tell the user:\n"
        f"> \"Using a new, unreviewed skill for {proposal.survey}; results may be incomplete.\"\n\n"
        f"{body}\n")


SOURCE_TEMPLATE = '''"""{survey} ({release}), queried on demand.

Draft: written by the dataset scout on {today} from {bibcode}; not yet reviewed.
See skills/surveys/{slug}.md for what this release holds and its gotchas.
"""
from __future__ import annotations

import asyncio
import math

from . import Column, Source, layer, register

TAP_URL = "{tap_url}"
TABLE = "{table}"
MAX_ROWS = 5000  # a result of exactly this size is truncated, not complete


def rows(data: dict) -> list[dict]:
    return data["extra"]["{slug}"]["rows"]  # KeyError until queried for this field


def _value(value):
    """Plain JSON: a VOTable cell is a numpy scalar, and may be masked or NaN."""
    if getattr(value, "mask", False):
        return None
    item = getattr(value, "item", None)
    value = item() if callable(item) else value
    return None if isinstance(value, float) and not math.isfinite(value) else value


def _cone(ra: float, dec: float, radius_deg: float) -> list[dict]:
    import pyvo

    query = (f"SELECT TOP {{MAX_ROWS}} {select} FROM {{TABLE}} "
             f"WHERE 1 = CONTAINS(POINT('ICRS', {ra_column}, {dec_column}), "
             f"CIRCLE('ICRS', {{ra}}, {{dec}}, {{radius_deg}}))")
    table = pyvo.dal.TAPService(TAP_URL).search(query).to_table()
    if len(table) == MAX_ROWS:
        raise RuntimeError(f"{survey} returned exactly {{MAX_ROWS}} rows, so the result is "
                           "truncated. Query a smaller region.")
    # Identifiers are kept as strings: a 19-digit ID does not survive a float.
    return [{{"id": str(row["{id_column}"]), "ra": float(row["{ra_column}"]),
             "dec": float(row["{dec_column}"]),
             **{{column: _value(row[column]) for column in {extra_columns!r}}}}}
            for row in table]


async def query(region: dict) -> list[dict]:
    return await asyncio.to_thread(_cone, region["ra"], region["dec"], region["radius_deg"])


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [layer("{slug}:all", "{slug}", [], "{survey}", "{label}", "#9aa7ff",
                  order=70, shape="square", size=9, hollow=True, visible=False)]


register(Source(
    name="{slug}", label="{label}", provenance="{label} (TAP: {tap_url})",
    order=70, rows=rows, layers=layers, query=query, browse={extra_columns!r},
    columns={{{columns}}}))
'''

PROBE_TEMPLATE = '''"""Probe {survey} ({release}): counts, timings and coverage in a known field.

Draft, written by the dataset scout on {today}. Re-run this when the skill looks
stale or a new release is announced, and compare the numbers with
skills/surveys/{slug}.md.

    python smoke_tests/{slug}_probe.py
"""
from __future__ import annotations

import sys
import time

import pyvo

TAP_URL = "{tap_url}"
TABLE = "{table}"
FIELD = ({probe_ra!r}, {probe_dec!r}, {probe_radius_deg!r})  # {probe_field}
EMPTY_FIELD = (0.0, -89.9, 0.1)  # expected to be outside the footprint


def count(service: pyvo.dal.TAPService, ra: float, dec: float, radius_deg: float) -> tuple[int, float]:
    query = (f"SELECT COUNT(*) AS n FROM {{TABLE}} WHERE 1 = CONTAINS("
             f"POINT('ICRS', {ra_column}, {dec_column}), "
             f"CIRCLE('ICRS', {{ra}}, {{dec}}, {{radius_deg}}))")
    started = time.monotonic()
    rows = service.search(query).to_table()
    return int(rows["n"][0]), time.monotonic() - started


def main() -> int:
    service = pyvo.dal.TAPService(TAP_URL)
    tables = [name for name in service.tables.keys() if TABLE.split(".")[-1] in name]
    print(f"tables matching {{TABLE!r}}: {{tables or 'NONE — the table name is wrong'}}")

    n, seconds = count(service, *FIELD)
    print(f"{{n}} rows in the known field {{FIELD}} in {{seconds:.1f}} s")

    outside, seconds = count(service, *EMPTY_FIELD)
    print(f"{{outside}} rows outside the footprint {{EMPTY_FIELD}} in {{seconds:.1f}} s")

    problems = []
    if not tables:
        problems.append(f"no table named {{TABLE}} at {{TAP_URL}}")
    if n == 0:
        problems.append(f"the known field {{FIELD}} is empty; check the footprint and the columns")
    if outside > 0:
        problems.append(f"{{outside}} rows in a field expected to have no coverage")
    for problem in problems:
        print(f"PROBLEM: {{problem}}")
    print("OK" if not problems else "NEEDS A LOOK")
    return 1 if problems else 0


if __name__ == "__main__":
    sys.exit(main())
'''


def _index_row(proposal: Proposal) -> str:
    return (f"| [{proposal.slug}](surveys/{proposal.slug}.md) | {proposal.description.strip()} "
            f"*(draft, written by the dataset scout)* |\n")


def add_index_row(index: Path, proposal: Proposal) -> bool:
    """Add the skill to the surveys table in ``skills/index.md``. False if already listed."""
    text = index.read_text(encoding="utf-8")
    if f"surveys/{proposal.slug}.md" in text:
        return False
    lines = text.splitlines(keepends=True)
    start = next((i for i, line in enumerate(lines) if line.startswith("## Surveys and archives")), None)
    if start is None:
        raise ScaffoldError("skills/index.md has no 'Surveys and archives' section to add a row to")
    end = start
    for i in range(start, len(lines)):
        if lines[i].startswith("|"):
            end = i + 1
        elif end > start and lines[i].strip() == "":
            break
    lines.insert(end, _index_row(proposal))
    index.write_text("".join(lines), encoding="utf-8")
    return True


def scaffold(root: Path, proposal: Proposal, *, today: date | None = None) -> Written:
    """Write a proposal's files. Raises ``ScaffoldError`` without writing anything partial."""
    today = today or date.today()
    written = Written(slug=proposal.slug)
    files = {f"skills/surveys/{proposal.slug}.md": skill_markdown(proposal, today)}
    if proposal.service is None:
        written.notes.append(
            "No TAP endpoint was verified, so no data-source module or probe was written: "
            "the skill documents the dataset but SCOOP cannot query it yet.")
    else:
        service, columns = proposal.service, proposal.service.extra_columns
        # "Euclid Q1" with release "Q1" should not become "Euclid Q1 Q1".
        label = (proposal.survey if proposal.release.lower() in proposal.survey.lower()
                 else f"{proposal.survey} {proposal.release}")
        fill = {"survey": proposal.survey, "release": proposal.release, "label": label,
                "slug": proposal.slug,
                "today": today.isoformat(), "bibcode": proposal.bibcode, "tap_url": service.tap_url,
                "table": service.table, "select": service.select, "ra_column": service.ra_column,
                "dec_column": service.dec_column, "id_column": service.id_column,
                "extra_columns": tuple(columns),
                "columns": ", ".join(f'"{column}": Column()' for column in columns),
                "probe_ra": proposal.probe_ra, "probe_dec": proposal.probe_dec,
                "probe_radius_deg": proposal.probe_radius_deg, "probe_field": proposal.probe_field}
        files[f"scoop/sources/{proposal.slug}.py"] = SOURCE_TEMPLATE.format(**fill)
        files[f"smoke_tests/{proposal.slug}_probe.py"] = PROBE_TEMPLATE.format(**fill)
        written.notes.append(
            f"Run `python smoke_tests/{proposal.slug}_probe.py` before trusting the data source: "
            "the scout verified the schema but has not confirmed these counts independently.")
    # Every path is checked before anything is written, so a clash leaves no half-written proposal.
    paths = {relative: _checked(root, relative) for relative in files}
    for relative, path in paths.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(files[relative], encoding="utf-8")
        written.paths.append(relative)
    if add_index_row(root / "skills" / "index.md", proposal):
        written.paths.append("skills/index.md")
    return written
