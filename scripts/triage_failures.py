"""Collect what failed in SCOOP sessions and group it into problems worth fixing.

Reads the server's audit log (one JSON line per chat turn: failed tool calls with their
reasons and arguments, including those inside the data agent) and the failed field
runs it keeps (``<fields>/_runs/*.json``: arguments, error and log with traceback).
Groups them by a normalised signature (numbers, ids, quoted values and paths replaced),
most frequent first, and prints a markdown report: for each group, how often and when,
an example call to reproduce it, and for runs the traceback's last frames in SCOOP's
own code. The /triage skill (.claude/skills/triage) works from this report.

    python scripts/triage_failures.py --audit-log turns.jsonl --fields fields/ [--since 2026-10-20]
"""
from __future__ import annotations

import argparse
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

# What kind of failure it is decides what fixing it means.
CLASSES = {
    "transient": "transient (network / archive down): retry; fix only if it recurs",
    "user": "stopped by the user",
    "bug": "SCOOP bug (an exception in our code): reproduce, test, fix",
    "refused": "agent mistake or tool limit (the tool refused the call): fix the prompt, the tool's "
               "description or make the tool accept it",
}
EXCEPTION = re.compile(r"\b\w+(Error|Exception)\b: |Traceback|counting failed")
TRANSIENT = re.compile(r"ConnectError|ConnectionError|timed out|Timeout|No route to host|nodename nor servname|"
                       r"SSLEOFError|EOF occurred|HTTP 5\d\d|Error 50\d|Max retries exceeded|rate limit",
                       re.IGNORECASE)


def classify(f: dict) -> str:
    reason = str(f.get("reason", ""))
    if TRANSIENT.search(reason):
        return "transient"
    if "cancelled by the user" in reason:
        return "user"
    if f["kind"] == "run" or EXCEPTION.search(reason):
        return "bug"
    return "refused"


def signature(text: str) -> str:
    """The failure with its specifics replaced, so repeats group together."""
    s = str(text or "").strip().splitlines()[0] if text else ""
    s = re.sub(r"/[\w./-]+", "<path>", s)
    s = re.sub(r"'[^']*'|\"[^\"]*\"", "<str>", s)
    s = re.sub(r"\b[0-9a-f]{8,}\b", "<id>", s)
    s = re.sub(r"[-+]?\d+(\.\d+)?", "<n>", s)
    return re.sub(r"\s+", " ", s)[:200]


def _parse_time(text: str | None) -> datetime | None:
    if not text:
        return None
    for fmt in ("%Y%m%dT%H%M%S",):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        return datetime.fromisoformat(text)
    except ValueError:
        return None


def tool_failures(audit_log: Path, since: datetime | None) -> list[dict]:
    out = []
    if not audit_log or not audit_log.exists():
        return out
    for line in audit_log.read_text().splitlines():
        try:
            turn = json.loads(line)
        except json.JSONDecodeError:
            continue
        when = _parse_time(turn.get("timestamp"))
        if since and when and when < since:
            continue
        for f in turn.get("failures") or []:
            out.append({"kind": "tool", "tool": f.get("tool"), "via": f.get("via"), "reason": f.get("reason", ""),
                        "args": f.get("args"), "when": turn.get("timestamp"), "thread": turn.get("thread_id")})
        if turn.get("error"):
            out.append({"kind": "turn", "tool": "(turn)", "reason": turn["error"], "args": None,
                        "when": turn.get("timestamp"), "thread": turn.get("thread_id")})
    return out


def scoop_frames(log: list[str], keep: int = 4) -> list[str]:
    """The traceback's last frames in SCOOP's own files (not the libraries')."""
    frames = [line.strip() for line in log if line.strip().startswith('File "')
              and "site-packages" not in line and "<frozen" not in line]
    return frames[-keep:]


def run_failures(fields: Path, since: datetime | None) -> list[dict]:
    out = []
    runs = fields / "_runs" if fields else None
    if not runs or not runs.is_dir():
        return out
    for path in sorted(runs.glob("*.json")):
        try:
            run = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError):
            continue
        when = _parse_time(run.get("failed_utc"))
        if since and when and when < since:
            continue
        log = run.get("log") or []
        error = run.get("error") or ""
        last = next((line for line in reversed(log) if re.match(r"^\w+(Error|Exception)\b", line)), error)
        out.append({"kind": "run", "tool": "field run", "reason": last, "args": " ".join(run.get("args") or []),
                    "when": run.get("failed_utc"), "key": run.get("key"), "frames": scoop_frames(log),
                    "file": str(path)})
    return out


def report(failures: list[dict]) -> str:
    groups: dict[tuple, list[dict]] = defaultdict(list)
    for f in failures:
        groups[(f["kind"], f["tool"], signature(f["reason"]))].append(f)
    ranked = sorted(groups.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    lines = [f"# SCOOP failure triage ({len(failures)} failures, {len(ranked)} groups)", ""]
    for i, ((kind, tool, sig), items) in enumerate(ranked, 1):
        cls = classify(items[-1])
        whens = sorted(str(f["when"]) for f in items if f.get("when"))
        lines += [f"## {i}. {tool} — {len(items)}× — {CLASSES[cls]}",
                  f"- signature: `{sig}`",
                  f"- first / last: {whens[0] if whens else '?'} / {whens[-1] if whens else '?'}",
                  f"- example reason: {str(items[-1]['reason'])[:400]}"]
        if items[-1].get("via"):
            lines.append(f"- inside: {items[-1]['via']} (the data agent's own call)")
        example = next((f for f in reversed(items) if f.get("args")), None)
        if example:
            lines.append(f"- example call: `{str(example['args'])[:600]}`")
        if kind == "run":
            frames = items[-1].get("frames") or []
            if frames:
                lines.append("- traceback (SCOOP frames):")
                lines += [f"  - `{fr}`" for fr in frames]
            lines.append(f"- run record: {items[-1]['file']}")
        lines.append("")
    return "\n".join(lines)


def main(argv=None) -> None:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--audit-log", type=Path, help="the server's --audit-log file (JSON lines)")
    p.add_argument("--fields", type=Path, help="the server's --fields directory (failed runs in _runs/)")
    p.add_argument("--since", help="only failures on or after this date (YYYY-MM-DD)")
    p.add_argument("--json", action="store_true", help="print the failures as JSON instead of a report")
    args = p.parse_args(argv)
    since = datetime.fromisoformat(args.since).replace(tzinfo=timezone.utc) if args.since else None
    failures = tool_failures(args.audit_log, since) + run_failures(args.fields, since)
    print(json.dumps(failures, indent=1, default=str) if args.json else report(failures))


if __name__ == "__main__":
    main()
