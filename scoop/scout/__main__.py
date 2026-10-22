"""Run one dataset scan.

    python -m scoop.scout --dry-run              # harvest and report, no model, no writes
    python -m scoop.scout --max-proposals 2      # the full scan, writing draft skills

The scan is incremental: with no ``--since`` it picks up where the last one
stopped (``.scout_state.json``), so running it monthly covers every month and
running it late covers the gap. Needs ``ADS_API_TOKEN``, and (unless
``--dry-run``) ``ANTHROPIC_API_KEY`` or the credentials of whatever
``--model-provider`` you choose.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from datetime import date
from pathlib import Path

from ..runtime.cost import price_for
from ..literature.ads import ADSClient
from .candidates import harvest
from .coverage import STALE_AFTER_DAYS, read_coverage
from .report import (USD_PER_MTOK_IN, USD_PER_MTOK_OUT, append_audit, audit_record,
                     report_markdown, write_report)
from .state import DEFAULT_WINDOW_DAYS, State
from .triage import DEFAULT_MAX_PROPOSALS, DEFAULT_MAX_STEPS, Scan, triage

ROOT = Path(__file__).resolve().parents[2]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="python -m scoop.scout", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--root", type=Path, default=ROOT, help="the checkout to scan and write into")
    parser.add_argument("--since", type=date.fromisoformat,
                        help="start of the ADS entry-date window (default: the last scan)")
    parser.add_argument("--until", type=date.fromisoformat, default=date.today(),
                        help="end of the window (default: today)")
    parser.add_argument("--window-days", type=int, default=DEFAULT_WINDOW_DAYS,
                        help="how far back to look when there is no previous scan")
    parser.add_argument("--state", type=Path, help="state file (default: <root>/.scout_state.json)")
    parser.add_argument("--audit", type=Path, help="JSONL metrics log to append to "
                        "(default: <root>/out/scout_events.jsonl)")
    parser.add_argument("--dry-run", action="store_true",
                        help="harvest, judge nothing with a model, and write only the report")
    parser.add_argument("--no-report", action="store_true", help="skip the markdown report")
    parser.add_argument("--max-proposals", type=int, default=DEFAULT_MAX_PROPOSALS,
                        help="draft skills one scan may write")
    parser.add_argument("--max-steps", type=int, default=DEFAULT_MAX_STEPS)
    parser.add_argument("--max-tokens", type=int, default=400_000)
    parser.add_argument("--limit-per-channel", type=int, default=50,
                        help="papers to take from each ADS query")
    parser.add_argument("--stale-days", type=int, default=STALE_AFTER_DAYS,
                        help="age at which a skill's facts are reported as due for re-checking")
    parser.add_argument("--model", default="claude-opus-5-5")
    parser.add_argument("--model-provider", choices=("anthropic", "openai"), default="anthropic")
    parser.add_argument("--usd-per-mtok-in", type=float, default=None,
                        help="default: the model's price in scoop.runtime.cost")
    parser.add_argument("--usd-per-mtok-out", type=float, default=None)
    parser.add_argument("--json", action="store_true", help="print the scan summary as JSON")
    args = parser.parse_args(argv)
    price = price_for(args.model)
    if args.usd_per_mtok_in is None:
        args.usd_per_mtok_in = price.input if price else USD_PER_MTOK_IN
    if args.usd_per_mtok_out is None:
        args.usd_per_mtok_out = price.output if price else USD_PER_MTOK_OUT
    return args


# The longest reply a scan needs is one propose_dataset call with every template section.
MAX_OUTPUT_TOKENS = 4096


def build_model(provider: str, name: str):
    """The LangChain chat model for ``--model-provider``.

    ChatAnthropic reads ``ANTHROPIC_API_KEY``; ChatOpenAI reads ``OPENAI_API_KEY``
    and, for an OpenAI-compatible server, ``OPENAI_BASE_URL``.
    """
    if provider == "anthropic":
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(model=name, max_tokens=MAX_OUTPUT_TOKENS)
    from langchain_openai import ChatOpenAI

    return ChatOpenAI(model=name, max_tokens=MAX_OUTPUT_TOKENS)


async def _harvest(since: date, until: date, limit: int, skip: frozenset[str]):
    async with ADSClient() as ads:
        return await harvest(ads, since=since, until=until, limit_per_channel=limit, skip=skip)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    root: Path = args.root.resolve()
    state_path = args.state or root / ".scout_state.json"
    audit_path = args.audit or root / "out" / "scout_events.jsonl"
    state = State.load(state_path)
    until: date = args.until
    since: date = args.since or state.window(until, days=args.window_days)
    if since > until:
        print(f"--since {since} is after --until {until}", file=sys.stderr)
        return 2
    if not os.environ.get("ADS_API_TOKEN"):
        print("ADS_API_TOKEN is not set; the scout harvests candidates from ADS.", file=sys.stderr)
        return 2

    started = time.monotonic()
    candidates, warnings = asyncio.run(
        _harvest(since, until, args.limit_per_channel, frozenset(state.verdicts)))
    coverage = read_coverage(root)
    print(f"{len(candidates)} new candidate(s) in {since}..{until}; "
          f"{len(state.verdicts)} bibcode(s) already judged in earlier scans.", file=sys.stderr)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)

    scan = Scan(stop_reason="dry run" if args.dry_run else "no candidates")
    if candidates and not args.dry_run:
        scan = triage(build_model(args.model_provider, args.model), candidates, coverage, root=root,
                      since=since, until=until, today=until, max_proposals=args.max_proposals,
                      max_steps=args.max_steps, max_tokens_total=args.max_tokens)
    latency_ms = (time.monotonic() - started) * 1000

    report_path = None
    if not args.no_report:
        text = report_markdown(scan=scan, candidates=candidates, coverage=coverage, since=since,
                               until=until, today=until, warnings=warnings, model=args.model,
                               dry_run=args.dry_run, usd_in=args.usd_per_mtok_in,
                               usd_out=args.usd_per_mtok_out, stale_after_days=args.stale_days)
        report_path = write_report(root, text, until)

    record = audit_record(scan=scan, candidates=candidates, latency_ms=latency_ms,
                          usd_in=args.usd_per_mtok_in, usd_out=args.usd_per_mtok_out)
    append_audit(audit_path, record)

    if not args.dry_run:
        # Only a real scan advances the window: a dry run must not hide candidates
        # from the scan that judges them.
        for verdict in scan.verdicts:
            state.verdicts[verdict.bibcode] = verdict.verdict
        for written in scan.written:
            state.proposed[written.slug] = until.isoformat()
        state.last_scan = until.isoformat()
        state.save(state_path)

    summary = {
        "window": [since.isoformat(), until.isoformat()],
        "candidates": len(candidates),
        "judged": len(scan.verdicts),
        "gaps": [v.row() for v in scan.gaps()],
        "written": [{"slug": w.slug, "paths": w.paths, "notes": w.notes} for w in scan.written],
        "stale_skills": [s.name for s in coverage.stale(until, after_days=args.stale_days)],
        "stop_reason": scan.stop_reason,
        "cost_usd": record["actual_cost_usd"],
        "report": str(report_path) if report_path else None,
        "warnings": list(warnings),
    }
    print(json.dumps(summary, indent=1) if args.json else _human(summary))
    return 0


def _human(summary: dict) -> str:
    lines = [f"Scanned {summary['window'][0]}..{summary['window'][1]}: "
             f"{summary['candidates']} candidate(s), {summary['judged']} judged, "
             f"{len(summary['gaps'])} gap(s), {len(summary['written'])} written "
             f"(${summary['cost_usd']}, stopped: {summary['stop_reason']})."]
    for gap in summary["gaps"]:
        lines.append(f"  {gap['verdict']:<6} {gap['survey'] or '?'} {gap['release'] or ''} "
                     f"[{gap['bibcode']}] — {gap['reason']}")
    for written in summary["written"]:
        lines.append(f"  wrote {written['slug']}: {', '.join(written['paths'])}")
    if summary["stale_skills"]:
        lines.append(f"  due for re-checking: {', '.join(summary['stale_skills'])}")
    if summary["report"]:
        lines.append(f"  report: {summary['report']}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
