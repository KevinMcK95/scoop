"""The scan's two outputs for people and for metrics.

``write_report`` writes a dated markdown page under ``docs/scout/``: what was
harvested, what was judged and why, what was written, and what a reviewer should
check first. It is the body of the monthly pull request, and it is written even
when a scan finds nothing, because "nothing new this month" is a useful record.

``audit_record`` emits one JSON line in the shape ``evaluation/scorecard/metrics_schema.json``
requires, so a scan appears in the weekly scorecard
(``evaluation/scorecard/metrics_scorecard.py``) alongside ordinary requests.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import date, datetime, timezone
from pathlib import Path

from .candidates import Candidate
from .coverage import STALE_AFTER_DAYS, Coverage
from .triage import Scan

# USD per million tokens for a model scoop.runtime.cost does not price. Override from the CLI.
USD_PER_MTOK_IN = 3.0
USD_PER_MTOK_OUT = 15.0

VERDICT_ORDER = {"new": 0, "update": 1, "covered": 2, "not_a_release": 3}


def _cost(scan: Scan, usd_in: float, usd_out: float) -> float:
    return round((scan.usage.input_tokens * usd_in + scan.usage.output_tokens * usd_out) / 1e6, 4)


def _table(header: tuple[str, ...], rows: list[tuple[str, ...]]) -> str:
    if not rows:
        return "_None._\n"
    lines = ["| " + " | ".join(header) + " |", "|" + "|".join("---" for _ in header) + "|"]
    lines += ["| " + " | ".join(cell.replace("|", "\\|") for cell in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def report_markdown(*, scan: Scan, candidates: tuple[Candidate, ...], coverage: Coverage,
                    since: date, until: date, today: date, warnings: tuple[str, ...],
                    model: str, dry_run: bool, usd_in: float = USD_PER_MTOK_IN,
                    usd_out: float = USD_PER_MTOK_OUT, stale_after_days: int = STALE_AFTER_DAYS) -> str:
    titles = {c.bibcode: c for c in candidates}
    verdicts = sorted(scan.verdicts, key=lambda v: (VERDICT_ORDER.get(v.verdict, 9), v.bibcode))
    unjudged = [c for c in candidates if not any(v.bibcode == c.bibcode for v in scan.verdicts)]
    counts = {name: sum(1 for v in scan.verdicts if v.verdict == name) for name in VERDICT_ORDER}

    parts = [
        f"# Dataset scout, {today.isoformat()}\n",
        f"Window **{since.isoformat()} to {until.isoformat()}** "
        f"(ADS `entdate`). Model: `{model}`."
        + (" **Dry run: nothing was written.**" if dry_run else "") + "\n",
        f"{len(candidates)} candidate(s) harvested, {len(scan.verdicts)} judged "
        f"({counts['new']} new, {counts['update']} update, {counts['covered']} already covered, "
        f"{counts['not_a_release']} not a release). "
        f"{len(scan.written)} proposal(s) written. Stopped: {scan.stop_reason}.\n",
    ]

    parts.append("## Gaps\n")
    gaps = [v for v in verdicts if v.verdict in ("new", "update")]
    parts.append(_table(("Verdict", "Survey", "Release", "Paper", "Why"),
                        [(v.verdict, v.survey or "—", v.release or "—",
                          f"[{v.bibcode}]({titles[v.bibcode].url})", v.reason) for v in gaps]))

    parts.append("\n## Written this scan\n")
    if scan.written:
        for written in scan.written:
            parts.append(f"### `{written.slug}`\n")
            parts.append("".join(f"- `{path}`\n" for path in written.paths))
            parts.append("".join(f"- **Check:** {note}\n" for note in written.notes))
            parts.append("\n")
    else:
        parts.append("_Nothing._\n")

    if scan.rejected:
        parts.append("\n## Proposals that were not written\n")
        parts.append("".join(f"- {reason}\n" for reason in scan.rejected))

    parts.append("\n## Everything judged\n")
    parts.append(_table(("Verdict", "Paper", "Title", "Why"),
                        [(v.verdict, f"[{v.bibcode}]({titles[v.bibcode].url})",
                          titles[v.bibcode].title, v.reason) for v in verdicts]))
    if unjudged:
        parts.append(f"\n{len(unjudged)} candidate(s) were never judged "
                     "(the scan stopped early); they stay unjudged for the next scan:\n")
        parts.append("".join(f"- [{c.bibcode}]({c.url}) — {c.title}\n" for c in unjudged))

    parts.append("\n## Skills whose facts are due for re-checking\n")
    parts.append(_table(("Skill", "Status", "Last checked", "Days"),
                        [(f"`{s.name}`", s.status, (s.sources_checked or "never").split(" ")[0],
                          str(s.stale_days(today)) if s.stale_days(today) is not None else "—")
                         for s in coverage.stale(today, after_days=stale_after_days)]))
    parts.append("\nRegistered data sources: "
                 + ", ".join(f"`{name}`" for name in coverage.source_modules) + "\n")
    if coverage.to_write:
        parts.append(f"\nStill on the index's own to-write list: {coverage.to_write}\n")

    parts.append("\n## Scan cost and controls\n")
    parts.append(
        f"- {scan.steps} step(s), {scan.tool_calls} tool call(s), "
        f"{scan.tool_failures} of them failed.\n"
        f"- {scan.usage.input_tokens} input and {scan.usage.output_tokens} output tokens, "
        f"about ${_cost(scan, usd_in, usd_out):.4f} at ${usd_in}/${usd_out} per Mtok.\n"
        f"- {len(scan.fetched)} page(s) fetched, {len(scan.refused)} refused by the allowlist "
        "or robots.txt.\n")
    if scan.fetched:
        parts.append("\n<details><summary>Pages fetched</summary>\n\n"
                     + "".join(f"- {url}\n" for url in scan.fetched) + "\n</details>\n")
    if scan.refused:
        parts.append("\nRefused: " + ", ".join(scan.refused) + "\n")
    if warnings:
        parts.append("\n## Harvest warnings\n" + "".join(f"- {w}\n" for w in warnings))

    if scan.summary:
        parts.append("\n## The scout's own summary\n\n" + scan.summary.strip() + "\n")

    parts.append(
        "\n---\n\nEverything written by this scan is `status: draft`. An agent may use a draft "
        "skill but must tell the user it is unreviewed. Review the files above, run the probe, "
        "and change `status` to `reviewed` in the same pull request that approves it.\n")
    # Surround every heading with a blank line, whichever section wrote it.
    text = re.sub(r"(?<!\n)\n(#{1,6} )", r"\n\n\1", "".join(parts))
    return re.sub(r"(\n#{1,6} [^\n]*\n)(?!\n)", r"\1\n", text)


def write_report(root: Path, text: str, today: date) -> Path:
    path = root / "docs" / "scout" / f"{today.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def audit_record(*, scan: Scan, candidates: tuple[Candidate, ...], latency_ms: float,
                 usd_in: float = USD_PER_MTOK_IN, usd_out: float = USD_PER_MTOK_OUT,
                 estimated_cost_usd: float = 0.0, request_id: str | None = None) -> dict:
    """One scan as a metrics event (see ``evaluation/scorecard/metrics_schema.json``).

    ``user_accepted`` is False until someone merges the pull request, which the
    scan cannot know; ``escalated`` is True when the scan found a gap, because a
    gap is exactly what needs a person.
    """
    return {
        "request_id": request_id or uuid.uuid4().hex,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "task_success": scan.stop_reason == "done" and len(scan.verdicts) == len(candidates),
        "user_accepted": False,
        "estimated_cost_usd": round(estimated_cost_usd, 4),
        "actual_cost_usd": _cost(scan, usd_in, usd_out),
        "budget_exceeded": scan.stop_reason.startswith("budget") or scan.stop_reason == "max_steps",
        "domain_allowed": not scan.refused,
        "latency_ms": round(latency_ms),
        "tool_calls_total": scan.tool_calls,
        "tool_calls_failed": scan.tool_failures,
        "escalated": bool(scan.gaps()),
        "planned_subtasks": len(candidates),
        "completed_subtasks": len(scan.verdicts),
        "risky_action": bool(scan.written),
        "risky_blocked": bool(scan.rejected),
    }


def append_audit(path: Path, record: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as log:
        log.write(json.dumps(record) + "\n")
