"""End-to-end spot checks of SCOOP's agents against a running workspace server.

Each check (evaluation/spot_checks.json) is one real chat turn in a fresh conversation:
the question, the field the map shows (or none, as from the start page), and what must
happen: which hand-offs and tools the chat agent calls (and must not), which map
actions and artifacts appear, patterns the answer must match, how many tool calls may
fail (sub-agents' included) and how long it may take. The checks are also the demos
(docs/demos.md): a run is a rehearsal, and its transcripts are a fallback to show when
the network is down.

Complements the data checks in this folder (verify.py: are the records real, in the
field, correct?) with behaviour checks: does a real question reach the right agent and
come back as a correct, shown answer?

    python -m evaluation.spot_checks                       # all checks, server at 127.0.0.1:8787
    python -m evaluation.spot_checks --only draco_pm fresh_cone --out out/spot_checks
"""
from __future__ import annotations

import argparse
import json
import re
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx

HERE = Path(__file__).resolve().parent
CHECKS = HERE / "spot_checks.json"


def run_turn(server: str, message: str, field: str | None, timeout_s: float) -> dict:
    """One chat turn in a new conversation: every event the browser would get."""
    thread = uuid.uuid4().hex
    context = {"field": field, "layers": [], "availableFields": []}
    if field:
        context["layers"] = [{"id": "gaia:stars", "table": "gaia", "origin": "pipeline", "visible": True}]
    events, started = [], time.monotonic()
    with httpx.stream("POST", f"{server}/api/chat", json={"thread_id": thread, "message": message,
                                                          "context": context},
                      timeout=httpx.Timeout(timeout_s, connect=10)) as response:
        if response.status_code != 200:
            return {"thread": thread, "seconds": 0.0, "events": [],
                    "error": f"HTTP {response.status_code}: {response.read().decode()[:300]}"}
        for line in response.iter_lines():
            if line.startswith("data: "):
                events.append(json.loads(line[6:]))
    return {"thread": thread, "seconds": round(time.monotonic() - started, 1), "events": events,
            "error": next((e["message"] for e in events if e["type"] == "error"), None)}


def summarize(turn: dict) -> dict:
    ev = turn["events"]
    actions = [a.get("type") for e in ev if e["type"] == "artifact"
               for a in (e["payload"].get("actions") or []) if isinstance(a, dict)]
    failures = [{"tool": e.get("name"), "reason": str(e.get("summary"))[:200]}
                for e in ev if e["type"] == "tool_result" and e.get("status") == "error"]
    failures += [{"tool": f.get("tool"), "via": e.get("name"), "reason": str(f.get("reason"))[:200]}
                 for e in ev if e["type"] == "tool_result" for f in e.get("inner_failures", [])]
    texts = [e["text"] for e in ev if e["type"] == "assistant" and e.get("text")]
    return {"calls": [e["name"] for e in ev if e["type"] == "tool_call"],
            "artifacts": [e["payload"].get("kind") for e in ev if e["type"] == "artifact"],
            "actions": actions, "failures": failures, "answer": texts[-1] if texts else ""}


def judge(check: dict, turn: dict) -> dict:
    """Each expectation as a (name, passed, detail) result."""
    s, x = summarize(turn), check.get("expect", {})
    results = []

    def add(name, ok, detail=""):
        results.append({"check": name, "passed": bool(ok), "detail": detail})

    add("turn completed", turn["error"] is None and s["answer"], turn["error"] or "")
    for tool in x.get("calls", []):
        add(f"calls {tool}", tool in s["calls"], f"called: {s['calls']}")
    for tool in x.get("not_calls", []):
        add(f"does not call {tool}", tool not in s["calls"])
    for action in x.get("actions", []):
        add(f"map action {action}", action in s["actions"], f"actions: {sorted(set(s['actions']))}")
    for kind in x.get("artifacts", []):
        add(f"artifact {kind}", kind in s["artifacts"])
    for kind in x.get("no_artifacts", []):
        add(f"no {kind}", kind not in s["artifacts"])
    for pattern in x.get("text", []):
        add(f"answer matches /{pattern}/", re.search(pattern, s["answer"], re.IGNORECASE))
    limit = x.get("max_failures", 0)
    add(f"at most {limit} failed tool call(s)", len(s["failures"]) <= limit,
        "; ".join(f"{f['tool']}: {f['reason']}" for f in s["failures"]))
    if x.get("max_seconds"):
        add(f"within {x['max_seconds']} s", turn["seconds"] <= x["max_seconds"], f"{turn['seconds']} s")
    return {"id": check["id"], "title": check["title"], "seconds": turn["seconds"],
            "passed": all(r["passed"] for r in results), "results": results, "summary": s}


def report(outcomes: list[dict], server: str) -> str:
    ok = sum(o["passed"] for o in outcomes)
    lines = [f"# SCOOP spot checks: {ok}/{len(outcomes)} passed",
             f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} · server {server}", "",
             "| Check | Result | Time | Tools called |", "| --- | --- | --- | --- |"]
    for o in outcomes:
        lines.append(f"| {o['title']} | {'✅ pass' if o['passed'] else '❌ fail'} | {o['seconds']} s | "
                     f"{', '.join(dict.fromkeys(o['summary']['calls'])) or '—'} |")
    for o in outcomes:
        lines += ["", f"## {o['title']} ({o['id']})"]
        lines += [f"- {'✅' if r['passed'] else '❌'} {r['check']}" + (f" — {r['detail']}" if r["detail"] and not r["passed"]
                                                                     else "") for r in o["results"]]
        lines += ["", "<details><summary>Answer</summary>", "", o["summary"]["answer"].strip(), "", "</details>"]
    return "\n".join(lines) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--server", default="http://127.0.0.1:8787")
    p.add_argument("--only", nargs="*", help="check ids to run (default: all)")
    p.add_argument("--out", type=Path, default=Path("out") / "spot_checks",
                   help="where the report and transcripts go")
    p.add_argument("--timeout", type=float, default=300, help="seconds one turn may take before it is cut off")
    args = p.parse_args(argv)
    checks = json.loads(CHECKS.read_text())["checks"]
    if args.only:
        checks = [c for c in checks if c["id"] in args.only]
    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
    outcomes = []
    for check in checks:
        print(f"- {check['id']}: {check['message'][:70]}…", flush=True)
        try:
            turn = run_turn(args.server, check["message"], check.get("field"), args.timeout)
        except httpx.HTTPError as exc:
            turn = {"thread": None, "seconds": 0.0, "events": [], "error": f"{type(exc).__name__}: {exc}"}
        outcome = judge(check, turn)
        outcomes.append(outcome)
        # The whole transcript, to replay or show if the network is down during a demo.
        (args.out / f"{stamp}_{check['id']}.json").write_text(json.dumps({**check, **turn}, indent=1, default=str))
        print(f"  {'pass' if outcome['passed'] else 'FAIL'} ({outcome['seconds']} s)", flush=True)
    path = args.out / f"{stamp}_report.md"
    path.write_text(report(outcomes, args.server))
    print(f"report: {path}")
    return 0 if all(o["passed"] for o in outcomes) else 1


if __name__ == "__main__":
    raise SystemExit(main())
