from __future__ import annotations

import json
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

        kinds = [event["type"] for event in stream]
        self.assertEqual(kinds, ["tool_call", "tool_result", "artifact", "assistant", "done"])
        artifact = stream[2]
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
        # From the start page there is no field; after load_field, the PM tools (like the
        # map tools) must work on the newly loaded field in the same turn.
        try:
            from scoop import pm
            from tests.test_pm import lvdb_tables
        except ModuleNotFoundError:
            self.skipTest("astropy absent")
        model = ScriptedModel(
            AIMessage(content="", tool_calls=[{"name": "load_field", "args": {"key": "mini_1"}, "id": "c1",
                                               "type": "tool_call"}]),
            AIMessage(content="", tool_calls=[{"name": "pm_published", "args": {}, "id": "c2", "type": "tool_call"}]),
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
        published = next(e for e in stream if e["type"] == "tool_result" and e["id"] == "c2")
        self.assertEqual(published["status"], "success", published["summary"])
        self.assertIn('"object": "Mini"', published["summary"])


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
