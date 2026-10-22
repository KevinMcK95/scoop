"""Offline checks for the ADS-to-LangGraph publication boundary."""
import json
import unittest

try:
    import httpx
    from langchain_core.messages import AIMessage
    from scoop.agents import build_chat_graph
    from scoop.literature.ads import ADSClient
    from scoop.literature.tools import build_ads_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"httpx", "langgraph", "langchain_core", "pydantic"}:
        raise
    HAS_DEPS = False
else:
    HAS_DEPS = True


@unittest.skipUnless(HAS_DEPS, "optional literature/chat dependencies absent")
class ADSToolsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.requests = []

        def respond(request):
            self.requests.append(request)
            return httpx.Response(200, json={"response": {"numFound": 1, "docs": [{
                "bibcode": "TEST-BIBCODE", "title": ["Fixture title"],
                "author": ["Fixture Author"], "abstract": "Fixture abstract, not scientific evidence.",
                "year": "2025", "doi": ["10.0000/fixture"],
            }]}})

        self.http = httpx.AsyncClient(transport=httpx.MockTransport(respond))
        self.tools = {t.name: t for t in build_ads_tools(ADSClient("fixture-token", client=self.http))}

    async def asyncTearDown(self):
        await self.http.aclose()

    async def call(self, name, args):
        return await self.tools[name].ainvoke({
            "name": name, "args": args, "id": "call-123", "type": "tool_call",
        })

    async def test_ads_search_runs_through_real_graph(self):
        responses = iter([
            AIMessage(content="", tool_calls=[{
                "name": "ads_search", "args": {"query": "fixture"},
                "id": "graph-call", "type": "tool_call",
            }]),
            AIMessage(content="Fixture response, not a scientific claim."),
        ])

        class Model:
            def bind_tools(self, tools):
                return self

            async def ainvoke(self, messages, config=None):
                return next(responses)

        state = await build_chat_graph(Model(), list(self.tools.values())).ainvoke({
            "messages": [{"role": "user", "content": "Search fixture literature"}]
        })
        self.assertEqual(state["artifacts"]["graph-call"]["papers"][0]["title"], "Fixture title")
        self.assertEqual(len(state["messages"]), 4)

    async def test_search_publishes_json_artifact_and_matching_message(self):
        command = await self.call("ads_search", {"query": 'abs:"fixture"'})
        payload = command.update["artifacts"]["call-123"]
        self.assertEqual(payload["status"], "success")
        self.assertEqual(payload["papers"][0]["ref"]["archive"], "ADS")
        self.assertIsNone(payload["papers"][0]["citation_count"])
        self.assertNotIn("raw", payload["papers"][0])
        self.assertNotIn("fixture-token", json.dumps(payload))
        self.assertEqual(command.update["messages"][0].tool_call_id, "call-123")
        self.assertIn("TEST-BIBCODE", command.update["messages"][0].content)
        self.assertNotIn("tool_call_id", self.tools["ads_search"].tool_call_schema.model_json_schema()["properties"])

    async def test_paper_exposes_abstract_to_model(self):
        command = await self.call("ads_paper", {"id": "10.0000/fixture"})
        message = command.update["messages"][0].content
        self.assertIn("Fixture abstract", message)
        self.assertEqual(self.requests[0].url.params["q"], 'doi:"10.0000/fixture"')

    async def test_references_uses_shared_client(self):
        command = await self.call("ads_citations", {"bibcode": "TEST-BIBCODE", "direction": "references"})
        self.assertEqual(command.update["artifacts"]["call-123"]["query"], 'references("TEST-BIBCODE")')

    async def test_page_size_is_strictly_validated_before_network(self):
        for value in (True, 0, 51, "10"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                await self.call("ads_search", {"query": "fixture", "page_size": value})
        self.assertEqual(self.requests, [])

    async def test_inconsistent_arguments_return_correctable_tool_error(self):
        message = await self.call("ads_search", {
            "query": "fixture", "year_from": 2025, "year_to": 2020,
        })
        self.assertEqual(message.status, "error")
        self.assertEqual(message.tool_call_id, "call-123")
        self.assertEqual(self.requests, [])

    async def test_expected_auth_failure_is_an_artifact_not_empty(self):
        tools = {t.name: t for t in build_ads_tools(ADSClient("", client=self.http))}
        command = await tools["ads_search"].ainvoke({
            "name": "ads_search", "args": {"query": "fixture"},
            "id": "call-auth", "type": "tool_call",
        })
        payload = command.update["artifacts"]["call-auth"]
        self.assertEqual(payload["status"], "failed")
        self.assertEqual(payload["error"]["kind"], "auth")
        self.assertEqual(self.requests, [])
