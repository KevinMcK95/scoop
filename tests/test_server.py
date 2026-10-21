from __future__ import annotations

import json
import os
import tempfile
import unittest
from typing import Annotated
from datetime import datetime, timezone
from pathlib import Path

try:
    from langchain_core.messages import AIMessage
    from langchain_core.tools import InjectedToolCallId
    from starlette.testclient import TestClient
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "starlette", "httpx"}:
        raise
    HAS_SERVER_DEPS = False
else:
    HAS_SERVER_DEPS = True
    from scoop.literature.models import ADSResult
    from scoop.models import SearchStatus
    from scoop.fields import FieldStore
    from scoop.server import create_app
    from tests.field_fixture import viewer_data

CONTEXT = {
    "viewport": {"ra": 260.05, "dec": 57.92, "fov": 1.2},
    "field": "mini_1",
    "layers": [{"id": "gaia:stars", "table": "gaia", "origin": "pipeline", "visible": True}],
}


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)
        self.calls = []

    def bind_tools(self, tools):
        self.tools = tools
        return self

    async def ainvoke(self, messages, config=None):
        self.calls.append(messages)
        return next(self.responses)


class FakeADS:
    def __init__(self):
        self.requests = []

    async def search(self, request):
        self.requests.append(request)
        return ADSResult(request=request, query=request.query, status=SearchStatus.SUCCESS,
                         retrieved_at=datetime(2026, 10, 19, tzinfo=timezone.utc),
                         papers=(), total_count=0, next_start=None)


def map_call(*actions, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{
        "name": "update_map", "args": {"actions": list(actions)}, "id": call_id, "type": "tool_call",
    }])


def field_store(tmp):
    (Path(tmp) / "mini").mkdir()
    (Path(tmp) / "mini" / "mini_1_field.json").write_text(json.dumps(viewer_data()))
    return FieldStore(Path(tmp))


def events(response):
    return [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]


def kinds(stream):
    """Event types without the per-turn cost reports, which every turn emits."""
    return [event["type"] for event in stream if event["type"] != "cost"]


_cost_state: tempfile.TemporaryDirectory | None = None


def setUpModule():
    # Apps built here default to a CostTracker; keep its state out of the checkout.
    global _cost_state
    _cost_state = tempfile.TemporaryDirectory()
    os.environ["COST_CHECKER_STATE_PATH"] = str(Path(_cost_state.name) / "costs.json")


def tearDownModule():
    os.environ.pop("COST_CHECKER_STATE_PATH", None)
    if _cost_state is not None:
        _cost_state.cleanup()


@unittest.skipUnless(HAS_SERVER_DEPS, "optional server dependencies absent")
class ServerTests(unittest.TestCase):
    def post_chat(self, client, message="Circle M31", thread_id="thread-0001", context=CONTEXT):
        return client.post("/api/chat", json={
            "thread_id": thread_id, "message": message, "context": context,
        })

    def test_health_reports_unconfigured_services(self):
        client = TestClient(create_app())
        self.assertEqual(client.get("/api/health").json(), {"chat": False, "literature": False})
        self.assertEqual(self.post_chat(client).status_code, 503)
        self.assertEqual(client.post("/api/literature/search", json={"query": "M31"}).status_code, 503)

    def test_chat_streams_map_action_artifact_and_writes_audit(self):
        highlight = {"type": "highlight", "table": "gaia", "ids": ["1004"], "reason": "Gaia-CRF3 QSO"}
        model = ScriptedModel(map_call(highlight), AIMessage(content="Highlighted the QSO."))
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "turns.jsonl"
            client = TestClient(create_app(model=model, audit_path=audit, fields=field_store(tmp)))
            stream = events(self.post_chat(client))
            record = json.loads(audit.read_text())

        self.assertEqual(kinds(stream), ["tool_call", "tool_result", "artifact", "assistant", "done"])
        # No budget was set, so the turn is not costed at all.
        self.assertFalse(any(event["type"] == "cost" for event in stream))
        self.assertIsNone(record["estimated_cost_usd"])
        artifact = next(event for event in stream if event["type"] == "artifact")
        self.assertEqual(artifact["payload"]["status"], "requested")
        self.assertEqual(artifact["payload"]["actions"], [highlight])
        self.assertEqual(stream[-1]["request_id"], record["request_id"])
        self.assertEqual((record["tool_calls_total"], record["tool_calls_failed"]), (1, 0))
        self.assertNotIn("Circle M31", json.dumps(record))
        # The model sees the browser context, delimited as data, under the workspace prompt.
        self.assertIn("<workspace_context>", model.calls[0][-1].content)
        from scoop.chat.prompt import WORKSPACE_PROMPT
        self.assertEqual(model.calls[0][0].content, WORKSPACE_PROMPT)

    def test_unknown_row_id_is_a_failed_tool_call(self):
        highlight = {"type": "highlight", "table": "gaia", "ids": ["invented"]}
        model = ScriptedModel(map_call(highlight), AIMessage(content="That source is unknown."))
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "turns.jsonl"
            stream = events(self.post_chat(TestClient(create_app(model=model, fields=field_store(tmp), audit_path=audit))))
            record = json.loads(audit.read_text())
        self.assertEqual(record["failures"][0]["tool"], "update_map")
        self.assertIn("Unknown gaia ids", record["failures"][0]["reason"])
        from scoop.server.app import _reason
        self.assertEqual(_reason("Error invoking tool 'x' with kwargs {...} with error:\n shape: bad\n"), "shape: bad")
        result = next(event for event in stream if event["type"] == "tool_result")
        self.assertEqual(result["status"], "error")
        self.assertFalse(any(event["type"] == "artifact" for event in stream))

    def test_a_conversation_budget_is_estimated_against_and_drawn_down(self):
        from scoop.cost import CostTracker

        answer = AIMessage(content="Draco is a dwarf spheroidal.",
                           usage_metadata={"input_tokens": 120_000, "output_tokens": 800, "total_tokens": 120_800})
        model = ScriptedModel(answer)
        model.model_name = "claude-opus-5-5"
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(model=model, costs=CostTracker(Path(tmp) / "costs.json")))
            self.assertIsNone(client.get("/api/budget/thread-0001").json()["budget"])
            opened = client.post("/api/budget/thread-0001", json={"budget_usd": 5})
            self.assertEqual(opened.json()["budget"]["remaining_usd"], 5.0)
            stream = events(self.post_chat(client, message="What is Draco?"))

        costs_sent = [event for event in stream if event["type"] == "cost"]
        estimate, actual = costs_sent[0], costs_sent[-1]
        self.assertEqual([event["phase"] for event in costs_sent], ["estimate", "actual"])
        # The estimate is checked against what this conversation has left, not the server default.
        self.assertEqual((estimate["budget_usd"], estimate["budget_scope"]), (5.0, "conversation"))
        self.assertTrue(estimate["priced"] and estimate["within_budget"])
        # 120k input at $4/MTok and 800 output at $20/MTok.
        billed = (120_000 * 4 + 800 * 20) / 1e6
        self.assertAlmostEqual(actual["actual_cost_usd"], billed, places=8)
        self.assertAlmostEqual(actual["budget"]["remaining_usd"], round(5 - billed, 6), places=6)
        self.assertEqual(actual["budget"]["turns"], 1)
        self.assertIn("left of this conversation", actual["note"])

    def test_without_a_budget_nothing_is_costed_or_calibrated(self):
        from scoop.cost import CostTracker

        answer = AIMessage(content="ok", usage_metadata={"input_tokens": 1000, "output_tokens": 10, "total_tokens": 1010})
        model = ScriptedModel(answer)
        model.model_name = "claude-opus-5-5"
        with tempfile.TemporaryDirectory() as tmp:
            state = Path(tmp) / "costs.json"
            stream = events(self.post_chat(TestClient(create_app(model=model, costs=CostTracker(state)))))
            self.assertFalse(state.exists())  # no estimate, no pending request, no calibration
        self.assertFalse(any(event["type"] == "cost" for event in stream))
        self.assertEqual(kinds(stream), ["assistant", "done"])

    def test_a_command_the_remaining_budget_cannot_cover_does_not_run(self):
        from scoop.cost import CostTracker

        model = ScriptedModel(AIMessage(content="never reached"))
        model.model_name = "claude-opus-5-5"
        with tempfile.TemporaryDirectory() as tmp:
            costs = CostTracker(Path(tmp) / "costs.json")
            costs.set_budget("thread-0001", 0.000001)
            stream = events(self.post_chat(TestClient(create_app(model=model, costs=costs))))
            self.assertEqual(costs.budget("thread-0001")["spent_usd"], 0.0)

        self.assertEqual(kinds(stream), ["error", "done"])
        self.assertIn("Raise the budget", stream[1]["message"])
        self.assertEqual(model.calls, [])  # nothing was sent to the model

    def test_budget_requests_are_validated(self):
        client = TestClient(create_app(model=ScriptedModel()))
        self.assertEqual(client.get("/api/budget/bad id").status_code, 400)
        self.assertEqual(client.post("/api/budget/thread-0001", json={"budget_usd": 0}).status_code, 400)
        self.assertEqual(client.post("/api/budget/thread-0001", json={}).status_code, 400)
        self.assertEqual(self.post_chat(client, message="hi", context={**CONTEXT}).status_code, 200)
        self.assertEqual(client.post("/api/chat", json={"thread_id": "thread-0001", "message": "hi",
                                                        "budget_usd": -1}).status_code, 400)
        self.assertEqual(client.post("/api/budget/thread-0001", json={"budget_usd": 1},
                                     headers={"origin": "https://evil.example"}).status_code, 403)

    def test_rejects_invalid_requests(self):
        client = TestClient(create_app(model=ScriptedModel()))
        self.assertEqual(self.post_chat(client, thread_id="bad id").status_code, 400)
        self.assertEqual(self.post_chat(client, message=" ").status_code, 400)
        self.assertEqual(self.post_chat(client, context={"layers": [{"id": 1}]}).status_code, 400)
        self.assertEqual(self.post_chat(client, context={"field": 7}).status_code, 400)

    def test_fields_are_listed_and_served_with_default_layers(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(fields=field_store(tmp)))
            self.assertEqual(client.get("/api/fields").json()["fields"][0]["key"], "mini_1")
            field = client.get("/api/fields/mini_1").json()
            self.assertIn("gaia:stars", [layer["id"] for layer in field["layers"]])
            self.assertEqual(client.get("/api/fields/nope").status_code, 404)
            zipped = client.get("/api/fields/mini_1", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(zipped.headers["content-encoding"], "gzip")
            self.assertEqual(zipped.json()["key"], "mini_1")

    def test_the_api_root_points_to_the_workspace(self):
        page = TestClient(create_app()).get("/")
        self.assertEqual(page.status_code, 200)
        self.assertIn("127.0.0.1:5173", page.text)

    def test_fields_with_nan_are_served_as_strict_json(self):
        # An LVDB object without a proper motion (NGC 300) has NaN pmra: Python writes it,
        # browsers' JSON.parse rejects it, so the field failed to load.
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mini").mkdir()
            data = viewer_data()
            data["target"] = {**data["target"], "pmra": float("nan"), "pmdec": float("inf")}
            (Path(tmp) / "mini" / "mini_1_field.json").write_text(json.dumps(data))
            client = TestClient(create_app(fields=FieldStore(Path(tmp))))
            text = client.get("/api/fields/mini_1").text
            self.assertNotIn("NaN", text)
            self.assertNotIn("Infinity", text)
            target = json.loads(text, parse_constant=lambda c: self.fail(f"non-standard {c}"))["target"]
            self.assertIsNone(target["pmra"])
            self.assertIsNone(target["pmdec"])

    def test_assistant_text_streams_before_the_complete_message(self):
        from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

        class Streaming(GenericFakeChatModel):
            def bind_tools(self, tools, **kwargs):
                return self

        model = Streaming(messages=iter([AIMessage(content="Draco is a dwarf spheroidal.")]))
        stream = events(self.post_chat(TestClient(create_app(model=model))))
        deltas = [e for e in stream if e["type"] == "assistant_delta"]
        final = next(e for e in stream if e["type"] == "assistant")
        self.assertGreater(len(deltas), 1)
        self.assertEqual("".join(d["text"] for d in deltas), final["text"])
        self.assertEqual({d["id"] for d in deltas}, {final["id"]})
        self.assertLess(stream.index(deltas[-1]), stream.index(final))

    def test_repeated_tool_failures_stop_the_turn_and_are_logged(self):
        bad = {"type": "highlight", "table": "gaia", "ids": ["invented"]}
        model = ScriptedModel(*[map_call(bad, call_id=f"call-{i}") for i in range(10)])
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "turns.jsonl"
            client = TestClient(create_app(model=model, fields=field_store(tmp), audit_path=audit))
            stream = events(self.post_chat(client))
            record = json.loads(audit.read_text())
        self.assertEqual(len(model.calls), 4)  # stopped, not 12 steps
        self.assertIn("Stopped after 4 failed tool calls in a row (update_map: No actions sent", stream[-2]["message"])
        self.assertEqual((record["tool_calls_failed"], len(record["failures"])), (4, 4))
        self.assertIn("Stopped after 4", record["error"])

    def test_the_model_sees_a_sky_image_after_asking_for_it(self):
        ask = AIMessage(content="", tool_calls=[{"name": "sky_image", "args": {"ra": 260.0, "dec": 57.9, "fov_arcmin": 2},
                                                 "id": "img-1", "type": "tool_call"}])
        model = ScriptedModel(ask, AIMessage(content="An edge-on galaxy."))

        async def fetch_image(ra, dec, fov, survey):
            return b"jpeg"

        stream = events(self.post_chat(TestClient(create_app(model=model, fetch_image=fetch_image))))
        second_call = model.calls[1]
        image = second_call[-1]
        self.assertEqual(image.content[1]["image_url"]["url"], "data:image/jpeg;base64,anBlZw==")
        self.assertEqual((stream[-2]["type"], stream[-2]["text"]), ("assistant", "An edge-on galaxy."))

    def test_run_python_code_output_and_figure_reach_the_browser(self):
        ask = AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "print(gaia.G.median())", "tables": ["gaia"]},
                                                 "id": "py-1", "type": "tool_call"}])
        model = ScriptedModel(ask, AIMessage(content="Median G is 19.1."))

        class Sandbox:
            async def run(self, code, tables):
                self.tables = tables
                return {"output": "19.1\n", "error": None, "figures": ["iVBOR"], "figure_count": 1}

        sandbox = Sandbox()
        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(model=model, fields=field_store(tmp), python=sandbox))
            stream = events(self.post_chat(client))
        result = next(e for e in stream if e["type"] == "tool_result")
        self.assertEqual((result["name"], result["status"]), ("run_python", "success"))
        self.assertEqual((result["code"], result["output"]), ("print(gaia.G.median())", "19.1\n"))
        self.assertEqual(result["image"], "data:image/png;base64,iVBOR")
        self.assertEqual(list(sandbox.tables), ["gaia"])
        self.assertEqual(model.calls[1][-1].content[1]["image_url"]["url"], "data:image/png;base64,iVBOR")

    def test_run_kernel_is_offered_only_when_a_kernel_is_given(self):
        ask = AIMessage(content="", tool_calls=[{"name": "run_kernel", "args": {"code": "imap.shape"},
                                                 "id": "k-1", "type": "tool_call"}])
        model = ScriptedModel(ask, AIMessage(content="The map is 3 x 1000 x 2000."))

        facts = {"name": "analysis", "display_name": "Analysis", "language": "python",
                 "python": "/envs/analysis/bin/python", "working_directory": "/scratch/scoop"}

        class Kernel:
            async def run(self, code, tables):
                return {"output": "(3, 1000, 2000)\n", "error": None, "figures": [], "figure_count": 0}

            async def describe(self):
                return facts

        stream = events(self.post_chat(TestClient(create_app(model=model, kernel=Kernel()))))
        result = next(e for e in stream if e["type"] == "tool_result")
        self.assertEqual((result["name"], result["status"], result["output"]), ("run_kernel", "success", "(3, 1000, 2000)\n"))
        # The agent is told which environment its code runs in.
        self.assertIn(json.dumps({"kernel": facts}, sort_keys=True)[1:-1], model.calls[0][-1].content)

        model = ScriptedModel(ask, AIMessage(content="No kernel."))
        client = TestClient(create_app(model=model))
        # The browser cannot claim a kernel: the server's answer (off) replaces it.
        stream = events(self.post_chat(client, message="shape?", context={"kernel": facts}))
        result = next(e for e in stream if e["type"] == "tool_result")
        self.assertEqual(result["status"], "error")
        self.assertIn('"kernel": null', model.calls[0][-1].content)

    def test_figures_are_stored_and_served_by_url(self):
        from scoop.server.figures import FigureStore

        ask = AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "plt.plot([1])"},
                                                 "id": "call_py/../1", "type": "tool_call"}])
        model = ScriptedModel(ask, AIMessage(content="Plotted."))

        class Sandbox:
            async def run(self, code, tables):
                return {"output": "", "error": None, "figures": ["iVBORw0KGgo="], "figure_count": 1}

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(model=model, python=Sandbox(), figure_store=FigureStore(Path(tmp))))
            stream = events(self.post_chat(client))
            url = next(e for e in stream if e["type"] == "tool_result")["image"]
            self.assertEqual(url, "/api/figures/thread-0001/call_py1.png")
            figure = client.get(url)
            self.assertEqual((figure.status_code, figure.headers["content-type"]), (200, "image/png"))
            self.assertEqual(figure.content, bytes.fromhex("89504e470d0a1a0a"))
            self.assertEqual((Path(tmp) / "thread-0001" / "call_py1.png").stat().st_mode & 0o777, 0o600)
            # The model still saw the image itself.
            self.assertTrue(model.calls[1][-1].content[1]["image_url"]["url"].startswith("data:image/png;base64,"))
            for bad in ("/api/figures/thread-0001/..%2Fx.png", "/api/figures/short/a.png", "/api/figures/thread-0001/a.gif"):
                self.assertEqual(client.get(bad).status_code, 404)
            self.assertEqual(client.delete("/api/figures/thread-0001", headers={"origin": "https://evil.example"}).status_code, 403)
            self.assertEqual(client.delete("/api/figures/thread-0001").status_code, 200)
            self.assertEqual(client.get(url).status_code, 404)

    def test_model_failure_is_reported_in_stream(self):
        class Broken(ScriptedModel):
            async def ainvoke(self, messages, config=None):
                raise RuntimeError("provider down")

        stream = events(self.post_chat(TestClient(create_app(model=Broken()))))
        self.assertEqual(stream[-2], {"type": "error", "message": "RuntimeError: provider down"})
        self.assertEqual(stream[-1]["type"], "done")

    def test_field_jobs_start_and_report(self):
        class Job:
            def view(self):
                return {"id": "job42", "key": "leo_1", "status": "running"}

        class Jobs:
            def start(self, key, n_rhalf):
                if key != "leo_1":
                    raise ValueError("bad key")
                return Job()

            def get(self, job_id):
                return Job() if job_id == "job42" else None

        client = TestClient(create_app(jobs=Jobs()))
        self.assertEqual(client.post("/api/jobs", json={"key": "leo_1"}).json()["status"], "running")
        self.assertEqual(client.post("/api/jobs", json={"key": "../x"}).status_code, 400)
        self.assertEqual(client.get("/api/jobs/job42").json()["key"], "leo_1")
        self.assertEqual(client.get("/api/jobs/nope").status_code, 404)
        self.assertEqual(TestClient(create_app()).post("/api/jobs", json={"key": "leo_1"}).status_code, 503)

    def test_literature_search_uses_shared_client(self):
        ads = FakeADS()
        client = TestClient(create_app(ads_client=ads))
        body = client.post("/api/literature/search", json={"query": "abs:M31", "page_size": 5}).json()
        self.assertEqual((body["service"], body["status"], body["papers"]), ("ADS", "success", []))
        self.assertEqual(ads.requests[0].page_size, 5)
        bad = client.post("/api/literature/search", json={"query": "M31", "page_size": 500})
        self.assertEqual(bad.status_code, 400)



@unittest.skipUnless(HAS_SERVER_DEPS, "optional server dependencies absent")
class StartPageTests(unittest.TestCase):
    def test_tools_follow_a_field_loaded_mid_turn(self):
        # From the start page there is no field; after load_field, the proper-motion agent's
        # tools (like the map tools) must work on the newly loaded field in the same turn.
        try:
            from scoop import pm
            from tests.test_pm import lvdb_tables
        except ModuleNotFoundError:
            self.skipTest("astropy absent")
        model = ScriptedModel(
            AIMessage(content="", tool_calls=[{"name": "load_field", "args": {"key": "mini_1"}, "id": "c1",
                                               "type": "tool_call"}]),
            AIMessage(content="", tool_calls=[{"name": "ask_pm_agent", "args": {"request": "what's published?"},
                                               "id": "c2", "type": "tool_call"}]),
            # The proper-motion agent's own steps (it runs on the same model).
            AIMessage(content="", tool_calls=[{"name": "pm_published", "args": {}, "id": "p1", "type": "tool_call"}]),
            AIMessage(content="Two published measurements."),
            AIMessage(content="Done."))
        with tempfile.TemporaryDirectory() as tmp:
            store = field_store(tmp)
            cache = Path(tmp) / "lvdb_cache"
            cache.mkdir()
            comb, pms = lvdb_tables()
            comb.write(cache / f"lvdb_{pm.LVDB_RELEASE}_comb_all.ecsv")
            pms.write(cache / f"lvdb_{pm.LVDB_RELEASE}_pm_overview.csv", format="ascii.csv")
            context = {**CONTEXT, "field": None, "layers": []}
            stream = events(ServerTests.post_chat(self, TestClient(create_app(model=model, fields=store)),
                                                  context=context))
        handoff = next(e for e in stream if e["type"] == "tool_result" and e["id"] == "c2")
        self.assertEqual(handoff["status"], "success", handoff["summary"])
        self.assertNotIn("inner_failures", handoff)
        sub_steps = model.calls[3]  # the PM agent's second call sees its pm_published result
        published = next(m for m in sub_steps if getattr(m, "tool_call_id", None) == "p1")
        self.assertEqual(published.status, "success", published.content)
        self.assertIn('"object": "Mini"', published.content)


@unittest.skipUnless(HAS_SERVER_DEPS, "optional server dependencies absent")
class ParallelCommandEventTests(unittest.IsolatedAsyncioTestCase):
    async def test_artifacts_from_parallel_tool_commands_reach_the_browser(self):
        # Several tools returning Commands in one step stream as a list of updates; the
        # artifacts (e.g. a map layer next to an ADS lookup) must not be dropped.
        from langchain_core.messages import HumanMessage
        from langchain_core.tools import tool
        from langgraph.graph import START, StateGraph
        from langgraph.prebuilt import ToolNode

        from scoop.chat import ChatState, publish_result
        from scoop.server.app import _events

        def make(name):
            @tool(name)
            async def publish(tool_call_id: Annotated[str, InjectedToolCallId]):
                """Publish an artifact."""
                return publish_result(tool_call_id, tool_call_id, {"kind": name}, f"{name} done")
            return publish

        graph = StateGraph(ChatState)
        graph.add_node("tools", ToolNode([make("a"), make("b")]))
        graph.add_edge(START, "tools")
        calls = AIMessage(content="", tool_calls=[{"name": "a", "args": {}, "id": "1"}, {"name": "b", "args": {}, "id": "2"}])
        events, seen = [], set()
        async for update in graph.compile().astream({"messages": [HumanMessage("hi"), calls], "artifacts": {}},
                                                    stream_mode="updates"):
            events += _events(update, seen, {"1": "a", "2": "b"})
        self.assertEqual(sorted(e["payload"]["kind"] for e in events if e["type"] == "artifact"), ["a", "b"])
        self.assertEqual(sorted(e["name"] for e in events if e["type"] == "tool_result"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()


class CleanNameTests(unittest.TestCase):
    def test_agent_labels_are_cleaned_not_refused(self):
        try:
            from scoop.server.jobs import clean_name
        except ModuleNotFoundError:
            self.skipTest("server dependencies absent")
        self.assertEqual(clean_name("COSMOS 0.5 deg² box"), "COSMOS 0.5 deg2 box")
        self.assertEqual(clean_name("Leo I/II, combined"), "Leo I-II combined")
        self.assertEqual(clean_name("GD-1 (PWB18) φ1∈[−42.8,−22.8]"), "GD-1 (PWB18) φ1 in -42.8 -22.8")
        self.assertLessEqual(len(clean_name("x " * 80)), 60)
        with self.assertRaises(ValueError):
            clean_name(":::")
