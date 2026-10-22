"""Run every check on a SCOOP result file or field and write a report.

Usage (from the repository root)::

    python -m evaluation.run_checks evaluation/runs/M87/result.json
    python -m evaluation.run_checks draco_1 --root out          # a pipeline field, by key
    python -m evaluation.run_checks out/draco/draco_1_field.json

For a result file, writes ``report.md`` and ``report.json`` next to it. For a
field, writes ``<key>_verify_report.md`` / ``.json`` (and the converted
``<key>_verify_result.json``) next to the field. If the result contains
injected fakes (from ``gather_example.py --inject-fakes``), the report also
scores how many of them the checks caught.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

from evaluation.checks import FAIL, INFO, PASS, SKIP, WARN, record_key
from evaluation.geometry import Region

STATUSES = [PASS, FAIL, WARN, SKIP]


def _cell(text, width=160):
    text = str(text).replace("|", "\\|").replace("\n", " ")
    return text if len(text) <= width else text[: width - 1] + "…"


def score_fakes(result, results):
    """Which injected fakes (and dropped sources) were caught by a failing check."""
    # Link checks report by URL, so planted links are keyed by their URL.
    planted = {(r["native_id"] if r.get("kind") == "link" else record_key(r)): r["_injected"]
               for r in result["records"] if "_injected" in r}
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


def write_report(result, results, out_dir: Path, name: str = "report", source=None):
    q = result["query"]
    kinds = Counter(r.get("kind") for r in result["records"])
    by_check = defaultdict(Counter)
    for r in results:
        if r.status != INFO:
            by_check[r.check][r.status] += 1

    lines = [
        f"# Evaluation report: {q['target']}",
        "",
        f"- **Region:** {Region.from_query(q).describe() if q.get('ra') is not None else 'none (no sky position)'}",
        f"- **Result:** `{source}` ({result.get('generated_by', 'unknown source')})",
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

    notes = [r for r in results if r.status == INFO]
    if notes:
        lines += ["", "## Sampling", ""] + [f"- **{r.record}:** {r.detail}" for r in notes]

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

    if result.get("notes"):
        lines += ["", "## Notes from the source", ""] + [f"- {_cell(n, 400)}" for n in result["notes"]]

    md_path = Path(out_dir) / f"{name}.md"
    md_path.write_text("\n".join(lines) + "\n")
    json_path = Path(out_dir) / f"{name}.json"
    json_path.write_text(json.dumps([r.to_dict() for r in results], indent=1))
    return md_path, json_path, fakes, false_alarms


def main(argv=None):
    from evaluation.verify import verify_field, verify_result_file

    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("target", help="a result.json, a *_field.json, or a field key (e.g. draco_1)")
    parser.add_argument("--root", default="out", help="where to look for field keys (default out)")
    parser.add_argument("--timeout", type=float, default=300,
                        help="seconds each check may take before it is skipped (default 300)")
    parser.add_argument("--only", nargs="+", help="run only checks whose names start with these")
    parser.add_argument("--sample", type=float, default=100,
                        help="per archive, check at most this many records (or this fraction, if < 1) against "
                             "the archive; 0 checks every record (default 100)")
    parser.add_argument("--seed", type=int, default=0, help="random seed for --sample (default 0)")
    parser.add_argument("--benchmark", type=Path, default=Path(__file__).parent / "benchmark.json",
                        help="benchmark file with must-include lists (default evaluation/benchmark.json)")
    args = parser.parse_args(argv)

    benchmark = json.loads(args.benchmark.read_text()) if args.benchmark.is_file() else None
    kw = dict(timeout=args.timeout, only=args.only, benchmark=benchmark, sample=args.sample or None, seed=args.seed)
    path = Path(args.target)
    if path.is_file() and not path.name.endswith("_field.json"):
        summary = verify_result_file(path, **kw)
    else:
        summary = verify_field(args.target, root=args.root, **kw)

    print("\n" + ", ".join(f"{summary['totals'].get(s, 0)} {s}" for s in STATUSES))
    if "injected" in summary:
        inj = summary["injected"]
        print(f"injected fakes caught: {inj['caught']} of {inj['planted']}; "
              f"real records failing: {inj['real_records_failing']}")
    print(f"wrote {summary['report']}")


if __name__ == "__main__":
    main()
