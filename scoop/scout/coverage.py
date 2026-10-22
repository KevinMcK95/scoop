"""What SCOOP already covers, read from the repository rather than described twice.

The scout can only spot a gap if it knows what is not a gap. That knowledge
already exists in two places, so this module reads both instead of keeping a
third list that would drift:

- ``scoop/skills/**/*.md`` — one skill per survey or archive, with the release it is
  pinned to and the date its facts were last checked (``sources_checked``).
- ``scoop.sources.SOURCES`` — the data sources actually wired into the field
  pipeline. A survey with a skill but no source module is documented but not
  queryable, which is itself a gap worth reporting.

Frontmatter is parsed here rather than with PyYAML: the core package has no
runtime dependencies, and the skills only use ``key: value``, folded strings and
inline lists.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

# "DR3", "EDR3", "DR20", "Data Release 18", "IDR2".
RELEASE = re.compile(r"\b(?:E|I|P)?DR\s?\d+[A-Za-z]?\b|\bData Release\s+\d+[A-Za-z]?\b", re.IGNORECASE)
# Skill filenames say what the skill is about as well as which survey it covers;
# only the survey half makes a usable alias.
GENERIC = frozenset({"spectra", "spectrum", "survey", "surveys", "catalog", "catalogs", "catalogue",
                     "catalogues", "data", "dataset", "datasets", "imaging", "photometry",
                     "astrometry", "proposals", "proper", "motions", "new", "skill", "literature",
                     "meta", "scout", "cost", "checking", "metrics", "scorecard"})
STALE_AFTER_DAYS = 180  # a release every ~6 months is normal for the big surveys
# Skills under these directories state facts about an archive, so they go out of
# date. The operational playbooks and the meta/ skills do not, and reporting them
# as stale every month would drown the signal.
DATA_DIRS = ("surveys/", "astrometry/", "imaging/", "spectra/", "photometry/", "literature/")


@dataclass(frozen=True)
class Covered:
    """One skill: the survey it covers and how current its facts are."""

    path: str  # relative to skills/, e.g. "surveys/gaia.md"
    name: str
    description: str
    status: str  # draft | pre-release | reviewed (a skill with no status is reviewed)
    sources_checked: str | None
    checked_on: date | None
    releases: tuple[str, ...]
    aliases: tuple[str, ...]
    # A registered data source has this skill's exact name, so the pipeline can query
    # it. A skill may still be served by a differently named source, which is why the
    # inventory also lists every registered source.
    has_source_module: bool = False

    @property
    def documents_data(self) -> bool:
        return self.path.startswith(DATA_DIRS)

    def stale_days(self, today: date) -> int | None:
        """Days since its facts were last verified, or None if it never said."""
        return None if self.checked_on is None else (today - self.checked_on).days

    def is_stale(self, today: date, *, after_days: int = STALE_AFTER_DAYS) -> bool:
        """Whether its archive facts are due for re-checking. A skill that never
        recorded ``sources_checked`` counts as due: nothing says they were ever right."""
        if not self.documents_data:
            return False
        days = self.stale_days(today)
        return days is None or days > after_days


@dataclass(frozen=True)
class Coverage:
    skills: tuple[Covered, ...]
    source_modules: tuple[str, ...]
    to_write: str  # the "To write" note from scoop/skills/index.md, verbatim

    def by_name(self, name: str) -> Covered | None:
        return next((s for s in self.skills if s.name == name), None)

    def match(self, text: str) -> tuple[Covered, ...]:
        """Skills whose survey appears in ``text`` (a paper title and abstract).

        Deliberately generous: a candidate matching an existing skill is handed
        to the model with that skill, which then decides whether the paper is a
        new release of it, a different survey, or already covered.
        """
        words = {word.lower() for word in re.findall(r"[A-Za-z][A-Za-z0-9+-]*", text)}
        return tuple(s for s in self.skills if any(alias in words for alias in s.aliases))

    def stale(self, today: date, *, after_days: int = STALE_AFTER_DAYS) -> tuple[Covered, ...]:
        return tuple(s for s in self.skills if s.is_stale(today, after_days=after_days))

    def summary(self) -> dict:
        """The inventory as the model sees it."""
        return {
            "skills": [{"name": s.name, "path": s.path, "status": s.status,
                        "sources_checked": s.sources_checked, "releases": list(s.releases),
                        "data_source_with_this_name": s.has_source_module,
                        "description": s.description}
                       for s in self.skills],
            "registered_data_sources": list(self.source_modules),
            "to_write": self.to_write,
        }


def parse_frontmatter(text: str) -> dict[str, str]:
    """The leading ``---`` block as flat strings; folded continuations are joined."""
    if not text.startswith("---\n"):
        return {}
    end = text.find("\n---", 4)
    if end == -1:
        return {}
    fields: dict[str, str] = {}
    key: str | None = None
    for line in text[4:end].splitlines():
        if not line.strip():
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*):\s*(.*)", line)
        if match and not line[0].isspace():
            key, value = match.group(1), match.group(2).strip()
            fields[key] = value
        elif key is not None:
            fields[key] = (fields[key] + " " + line.strip()).strip()
    return fields


def _date(value: str | None) -> date | None:
    """The ISO date at the start of a ``sources_checked`` value, if there is one."""
    match = re.match(r"\s*(\d{4}-\d{2}-\d{2})", value or "")
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def _aliases(path: Path, name: str) -> tuple[str, ...]:
    stem = path.stem.removesuffix(".skill")  # cost-checking.skill.md
    tokens = {token for part in (stem, name) for token in re.split(r"[_\-\s.]+", part.lower())}
    return tuple(sorted(token for token in tokens if token and token not in GENERIC and len(token) > 1))


def _releases(body: str) -> tuple[str, ...]:
    seen = {re.sub(r"\s+", " ", match.group(0)).upper() for match in RELEASE.finditer(body)}
    return tuple(sorted(seen))


def read_coverage(root: Path, *, source_modules: tuple[str, ...] | None = None) -> Coverage:
    """Read the inventory from a checkout. ``root`` is the repository root."""
    skills_dir = root / "scoop" / "skills"
    if source_modules is None:
        from ..sources import SOURCES  # importing registers every source module

        source_modules = tuple(SOURCES)
    skills: list[Covered] = []
    for path in sorted(skills_dir.rglob("*.md")):
        relative = path.relative_to(skills_dir).as_posix()
        if relative in ("index.md", "README.md"):
            continue
        text = path.read_text(encoding="utf-8")
        front = parse_frontmatter(text)
        name = front.get("name") or path.stem
        aliases = _aliases(path, name)
        skills.append(Covered(
            path=relative, name=name, description=front.get("description", ""),
            status=front.get("status", "reviewed"), sources_checked=front.get("sources_checked"),
            checked_on=_date(front.get("sources_checked")), releases=_releases(text), aliases=aliases,
            has_source_module=name in source_modules,
        ))
    return Coverage(skills=tuple(skills), source_modules=tuple(sorted(source_modules)),
                    to_write=_to_write(skills_dir / "index.md"))


def _to_write(index: Path) -> str:
    if not index.exists():
        return ""
    sections = re.split(r"^##\s+", index.read_text(encoding="utf-8"), flags=re.MULTILINE)
    for section in sections:
        if section.lower().startswith("to write"):
            return re.sub(r"\s+", " ", section.split("\n", 1)[-1]).strip()
    return ""
