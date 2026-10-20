"""The dataset scout: a periodic scan for datasets SCOOP is missing or has gone stale on.

The scout runs on a schedule (monthly), not per request. One scan is four steps,
three of which are plain code:

1. **Harvest** (``candidates``): ask ADS for data-release and catalogue papers
   entered since the last scan. ADS indexes arXiv eprints, so this covers both.
2. **Inventory** (``coverage``): read what SCOOP already covers, from the skills
   in ``skills/`` and the registered data sources in ``scoop.sources``.
3. **Triage** (``triage``): the one judgment call, and the only model-driven
   step. For each candidate the model decides whether it is a real data release,
   which survey it belongs to, and whether that survey (at that release) is
   already covered. For a gap it may verify facts against the archive's own
   pages and propose a dataset.
4. **Scaffold** (``scaffold``): a proposal becomes a draft skill, a smoke test
   and a registered data-source module, written to disk for a person to review
   as a pull request. Nothing the scout writes is trusted until that review.

``report`` writes a dated markdown summary of the scan, and ``state`` remembers
which bibcodes have already been judged so next month's scan does not re-propose
last month's papers.

Everything except triage runs without a model or an API key, which is what
``--dry-run`` does.
"""
from __future__ import annotations

from .candidates import CHANNELS, Candidate, harvest
from .coverage import Covered, Coverage, read_coverage
from .report import write_report
from .scaffold import Proposal, ScaffoldError, scaffold
from .state import State
from .triage import Scan, Verdict, triage

__all__ = [
    "CHANNELS",
    "Candidate",
    "Coverage",
    "Covered",
    "Proposal",
    "ScaffoldError",
    "Scan",
    "State",
    "Verdict",
    "harvest",
    "read_coverage",
    "scaffold",
    "triage",
    "write_report",
]
