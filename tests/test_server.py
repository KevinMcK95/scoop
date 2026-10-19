from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

try:
    from langchain_core.messages import AIMessage
    from starlette.testclient import TestClient
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "starlette", "httpx"}:
        raise
    HAS_SERVER_DEPS = False
else:
    HAS_SERVER_DEPS = True
    from scoop.literature.models import ADSResult
    from scoop.models import SearchStatus
    from scoop.server import create_app

CONTEXT = {
    "viewport": {"ra": 10.68, "dec": 41.27, "fov": 1.2},
    "sourceIds": ["fx-src-a7"],
    "map": {"layers": [{"id": "annotations", "visible": True}]},
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


def view_call(command, call_id="call-1"):
    return AIMessage(content="", tool_calls=[{
        "name": "update_sky_view", "args": {"command": command}, "id": call_id, "type": "tool_call",
    }])


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

    def test_chat_streams_view_request_artifact_and_writes_audit(self):
        circle = {"type": "annotation.circle", "ra": 10.68, "dec": 41.27, "radius": 1.0, "label": "1 deg"}
        model = ScriptedModel(view_call(circle), AIMessage(content="Requested a circle."))
        with tempfile.TemporaryDirectory() as tmp:
            audit = Path(tmp) / "turns.jsonl"
            client = TestClient(create_app(model=model, audit_path=audit))
            stream = events(self.post_chat(client))
            record = json.loads(audit.read_text())

        kinds = [event["type"] for event in stream]
        self.assertEqual(kinds, ["tool_call", "tool_result", "artifact", "assistant", "done"])
        artifact = stream[2]
        self.assertEqual(artifact["payload"]["status"], "requested")
        self.assertEqual(artifact["payload"]["command"], circle)
        self.assertEqual(stream[-1]["request_id"], record["request_id"])
        self.assertEqual((record["tool_calls_total"], record["tool_calls_failed"]), (1, 0))
        self.assertNotIn("Circle M31", json.dumps(record))
        # The model sees the browser context, delimited as data.
        self.assertIn("<workspace_context>", model.calls[0][-1].content)

    def test_unknown_source_id_is_a_failed_tool_call(self):
        highlight = {"type": "sources.highlight", "sourceIds": ["invented"]}
        model = ScriptedModel(view_call(highlight), AIMessage(content="That source is unknown."))
        stream = events(self.post_chat(TestClient(create_app(model=model))))
        result = next(event for event in stream if event["type"] == "tool_result")
        self.assertEqual(result["status"], "error")
        self.assertFalse(any(event["type"] == "artifact" for event in stream))

    def test_rejects_invalid_requests(self):
        client = TestClient(create_app(model=ScriptedModel()))
        self.assertEqual(self.post_chat(client, thread_id="bad id").status_code, 400)
        self.assertEqual(self.post_chat(client, message=" ").status_code, 400)
        self.assertEqual(self.post_chat(client, context={"sourceIds": [1]}).status_code, 400)

    def test_model_failure_is_reported_in_stream(self):
        class Broken(ScriptedModel):
            async def ainvoke(self, messages, config=None):
                raise RuntimeError("provider down")

        stream = events(self.post_chat(TestClient(create_app(model=Broken()))))
        self.assertEqual(stream[-2], {"type": "error", "message": "RuntimeError: provider down"})
        self.assertEqual(stream[-1]["type"], "done")

    def test_literature_search_uses_shared_client(self):
        ads = FakeADS()
        client = TestClient(create_app(ads_client=ads))
        body = client.post("/api/literature/search", json={"query": "abs:M31", "page_size": 5}).json()
        self.assertEqual((body["service"], body["status"], body["papers"]), ("ADS", "success", []))
        self.assertEqual(ads.requests[0].page_size, 5)
        bad = client.post("/api/literature/search", json={"query": "M31", "page_size": 500})
        self.assertEqual(bad.status_code, 400)


if __name__ == "__main__":
    unittest.main()
