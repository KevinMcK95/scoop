import json
import tempfile
import unittest
from pathlib import Path

try:
    from langchain_core.messages import AIMessage
    from scoop.agents.triage_agent import TriageScheduler, build_triage_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic"}:
        raise
    TriageScheduler = None


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def bind_tools(self, tools):
        self.tools = [t.name for t in tools]
        return self

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return next(self.responses)


def call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


TURNS = [
    {"timestamp": "2026-10-21T14:00:00+00:00", "thread_id": "thread0001", "request_id": "req1", "failures": [
        {"tool": "run_cone", "reason": "name must be 1-60 letters", "args": '{"label": "NGC 300: x"}'},
        {"tool": "lookup", "reason": "Name resolver failed (ConnectError); try again"}]},
]


@unittest.skipIf(TriageScheduler is None, "optional chat dependencies absent")
class TriageAgentTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (self.tmp / "turns.jsonl").write_text("\n".join(json.dumps(t) for t in TURNS))
        (self.tmp / "traces").mkdir()
        (self.tmp / "traces" / "thread0001.jsonl").write_text(json.dumps({
            "request_id": "req1", "question": "a cone around NGC 300", "steps": [
                {"kind": "tool", "name": "run_cone", "args": "{'label': 'NGC 300: x'}", "status": "error",
                 "result": "name must be 1-60 letters"}]}) + "\n")
        self.now = 10_000.0

    def scheduler(self, model):
        return TriageScheduler(model=lambda: model, audit_path=self.tmp / "turns.jsonl", fields_dir=self.tmp,
                               trace_dir=self.tmp / "traces", out_dir=self.tmp / "triage", every_s=3600,
                               idle_s=600, clock=lambda: self.now)

    async def test_runs_when_due_and_idle_on_new_failures_only(self):
        model = ScriptedModel(call("read_trace", {"thread": "thread0001", "request": "req1"}, "t1"),
                              AIMessage(content="1 group. ## 1. run_cone: labels with ':' are refused"))
        sched = self.scheduler(model)
        sched.note_activity()
        self.assertIsNone(await sched.tick())  # just used: not idle yet
        self.now += 601
        path = await sched.tick()
        self.assertIsNotNone(path)
        text = path.read_text()
        self.assertIn("run_cone: labels", text)
        self.assertIn("1 group(s)", text.splitlines()[0])  # the transient lookup failure is left out
        self.assertNotIn("Name resolver failed", text.split("<details>")[0])
        trace = next(m for m in model.calls[1] if getattr(m, "tool_call_id", None) == "t1")
        self.assertIn("a cone around NGC 300", trace.content)  # it read the failing turn's trace
        self.assertEqual(sched.reports()[0]["name"], path.name)
        # Within the hour nothing runs; after it, the same failures are not reported again.
        self.now += 60
        self.assertIsNone(await sched.tick())
        self.now += 3600
        self.assertIsNone(await sched.tick())
        self.assertEqual(len(sched.reports()), 1)

    async def test_tools_are_read_only_and_keep_out_of_private_files(self):
        tools = {t.name: t for t in build_triage_tools(trace_dir=self.tmp / "traces")}

        async def run(name, args):
            return await tools[name].ainvoke({"type": "tool_call", "name": name, "id": "x", "args": args})

        self.assertIn("def classify", (await run("read_source", {"path": "scoop/runtime/triage.py", "end": 400})).content)
        for private in ("../../etc/passwd", ".git/config", "web/node_modules/x.js", "out/turns.jsonl"):
            self.assertEqual((await run("read_source", {"path": private})).status, "error", private)
        found = await run("search_source", {"pattern": r"def clean_name"})
        self.assertIn("scoop/server/jobs.py:", found.content)
        self.assertEqual((await run("read_trace", {"thread": "../x", "request": "r"})).status, "error")


if __name__ == "__main__":
    unittest.main()
