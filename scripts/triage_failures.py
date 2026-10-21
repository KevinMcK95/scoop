"""Print what failed in SCOOP sessions, grouped into problems worth fixing (scoop.triage).

    python scripts/triage_failures.py --audit-log turns.jsonl --fields fields/ [--since 2026-10-20] [--json]
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scoop import triage  # noqa: E402


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--audit-log", type=Path, help="the server's --audit-log file (JSON lines)")
    p.add_argument("--fields", type=Path, help="the server's --fields directory (failed runs in _runs/)")
    p.add_argument("--since", help="only failures on or after this date (YYYY-MM-DD)")
    p.add_argument("--json", action="store_true", help="print the failures as JSON instead of a report")
    args = p.parse_args(argv)
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
    failures = triage.collect(args.audit_log, args.fields, since)
    print(json.dumps(failures, indent=1, default=str) if args.json else triage.report(failures))


if __name__ == "__main__":
    main()
