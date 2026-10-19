"""Run every check on a SCOOP result file and write a report.

Usage (from the repository root)::

    python -m evaluation.run_checks evaluation/runs/M87/result.json

Writes ``report.md`` and ``report.json`` next to the result file. If the result
contains injected fakes (from ``gather_example.py --inject-fakes``), the report
also scores how many of them the checks caught.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from .checks import FAIL, PASS, SKIP, WARN, record_key, run_all
from .timeouts import set_socket_timeout

STATUSES = [PASS, FAIL, WARN, SKIP]


def _cell(text, width=160):
    text = str(text).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def score_fakes(result, results):
    """Which injected fakes (and dropped sources) were caught by a failing check."""
    planted = {record_key(r): r["_injected"] for r in result["records"] if "_injected" in r}
    planted.update({f"Gaia DR3:{sid}": "real Gaia source dropped from the result"
                    for sid in result.get("_injected_dropped", [])})
    failed_by = defaultdict(set)
    for r in results:
        if r.status == FAIL:
            failed_by[r.record].add(r.check)
    rows = [(why, key, sorted(failed_by.get(key, ()))) for key, why in planted.items()]
    real_keys = {record_key(r) for r in result["records"] if "_injected" not in r}
    false_alarms = sorted(k for k in failed_by if k in real_keys)
    return rows, false_alarms


def write_report(result, results, result_path: Path):
    q = result["query"]
    kinds = Counter(r.get("kind") for r in result["records"])
    by_check = defaultdict(Counter)
    for r in results:
        by_check[r.check][r.status] += 1

    lines = [
        f"# Evaluation report: {q['target']} (radius {q['radius_arcmin']} arcmin)",
        "",
        f"- **Result:** `{result_path}` ({result.get('generated_by', 'unknown source')})",
        f"- **Checked:** {datetime.now(timezone.utc).isoformat(timespec='seconds')}",
        f"- **Records:** {len(result['records'])} ("
        + ", ".join(f"{n} {k}" for k, n in kinds.most_common()) + ")",
        "",
        "## Summary",
        "",
        "| Check | " + " | ".join(STATUSES) + " |",
        "| --- |" + " ---: |" * len(STATUSES),
    ]
    for check, counts in by_check.items():
        lines.append(f"| {check} | " + " | ".join(str(counts.get(s, 0)) for s in STATUSES) + " |")

    for status, title in [(FAIL, "Failures"), (WARN, "Warnings"), (SKIP, "Skipped")]:
        rows = [r for r in results if r.status == status]
        if not rows:
            continue
        lines += ["", f"## {title} ({len(rows)})", "", "| Check | Record | Detail |", "| --- | --- | --- |"]
        lines += [f"| {r.check} | `{_cell(r.record, 60)}` | {_cell(r.detail)} |" for r in rows]

    fakes, false_alarms = score_fakes(result, results)
    if fakes:
        caught = sum(1 for _, _, by in fakes if by)
        lines += [
            "",
            "## Scoring the checks against injected fakes",
            "",
            f"**Caught {caught} of {len(fakes)}** planted problems. "
            f"Real records flagged as failing: {len(false_alarms)}.",
            "",
            "| Planted problem | Record | Caught by |",
            "| --- | --- | --- |",
        ]
        lines += [f"| {_cell(why)} | `{_cell(key, 60)}` | {', '.join(by) or '**missed**'} |"
                  for why, key, by in fakes]
        if false_alarms:
            lines += ["", "Real records that failed a check:", ""] + [f"- `{k}`" for k in false_alarms]

    md_path = result_path.with_name("report.md")
    md_path.write_text("\n".join(lines) + "\n")
    json_path = result_path.with_name("report.json")
    json_path.write_text(json.dumps([r.to_dict() for r in results], indent=1))
    return md_path, json_path, fakes, false_alarms


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("result", type=Path, help="path to a result.json")
    parser.add_argument("--timeout", type=float, default=300,
                        help="seconds each check may take before it is skipped (default 300)")
    parser.add_argument("--benchmark", type=Path, default=Path(__file__).parent / "benchmark.json",
                        help="benchmark file with must-include lists (default evaluation/benchmark.json)")
    args = parser.parse_args(argv)

    set_socket_timeout()
    result = json.loads(args.result.read_text())
    benchmark = json.loads(args.benchmark.read_text()) if args.benchmark.is_file() else None
    print(f"checking {args.result} ({len(result['records'])} records)")
    results = run_all(result, args.result.parent, timeout=args.timeout, benchmark=benchmark)
    md_path, json_path, fakes, false_alarms = write_report(result, results, args.result)

    totals = Counter(r.status for r in results)
    print("\n" + ", ".join(f"{totals.get(s, 0)} {s}" for s in STATUSES))
    if fakes:
        caught = sum(1 for _, _, by in fakes if by)
        print(f"injected fakes caught: {caught} of {len(fakes)}; real records failing: {len(false_alarms)}")
    print(f"wrote {md_path} and {json_path.name}")


if __name__ == "__main__":
    main()
