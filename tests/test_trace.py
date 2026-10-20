import json
import tempfile
import unittest
from pathlib import Path

try:
    from langchain_core.language_models.fake_chat_models import FakeMessagesListChatModel
    from langchain_core.messages import AIMessage
    from starlette.testclient import TestClient
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "starlette", "httpx"}:
        raise
    HAS_DEPS = False
else:
    HAS_DEPS = True
    from scoop.server import create_app
    from scoop.server.trace import TraceStore

    class ToolModel(FakeMessagesListChatModel):
        """A real LangChain chat model (so callbacks fire) that ignores the tools it is given."""

        def bind_tools(self, tools, **kwargs):
            return self


@unittest.skipUnless(HAS_DEPS, "optional server dependencies absent")
class TraceTests(unittest.TestCase):
    def test_a_turn_is_traced_and_served(self):
        ask = AIMessage(content="", tool_calls=[{"name": "run_python", "args": {"code": "print(2 + 2)"},
                                                 "id": "py-1", "type": "tool_call"}],
                        usage_metadata={"input_tokens": 900, "output_tokens": 20, "total_tokens": 920,
                                        "input_token_details": {"cache_read": 800}})
        model = ToolModel(responses=[ask, AIMessage(content="It is 4.")])

        class Sandbox:
            async def run(self, code, tables):
                return {"output": "4\n", "error": None, "figures": ["iVBORw0KGgo="], "figure_count": 1}

        with tempfile.TemporaryDirectory() as tmp:
            client = TestClient(create_app(model=model, python=Sandbox(), trace_store=TraceStore(Path(tmp))))
            client.post("/api/chat", json={"thread_id": "thread-0001", "message": "What is 2+2?", "context": {}})
            traces = client.get("/api/traces/thread-0001").json()["traces"]
            self.assertEqual((Path(tmp) / "thread-0001.jsonl").stat().st_mode & 0o777, 0o600)

            (trace,) = traces
            self.assertEqual(trace["question"], "What is 2+2?")
            self.assertIsNone(trace["error"])
            self.assertEqual([s["kind"] for s in trace["steps"]], ["model", "tool", "model"])
            first, tool, second = trace["steps"]
            # The first call shows the whole input, system prompt first; the next only what was added.
            self.assertEqual(first["sent"][0]["role"], "system")
            self.assertIn("What is 2+2?", first["sent"][-1]["content"])
            self.assertEqual(first["reply"]["tool_calls"][0]["name"], "run_python")
            self.assertEqual(first["usage"], {"input": 900, "output": 20, "cached": 800})
            self.assertEqual((tool["name"], tool["status"], tool["args"]["code"]), ("run_python", "success", "print(2 + 2)"))
            self.assertIn("4", tool["result"])
            self.assertTrue(tool["image"])
            self.assertEqual([m["role"] for m in second["sent"]], ["tool", "human"])  # the result, then the figure
            self.assertIn("[image", second["sent"][1]["content"])
            self.assertNotIn("base64", json.dumps(trace))
            self.assertEqual(second["reply"]["content"], "It is 4.")
            self.assertEqual(trace["totals"]["tool_calls"], 1)
            self.assertGreaterEqual(tool["duration_ms"], 0)

            self.assertEqual(client.get("/api/traces/bad").status_code, 404)
            self.assertEqual(client.delete("/api/traces/thread-0001", headers={"origin": "https://evil.example"}).status_code, 403)
            client.delete("/api/traces/thread-0001")
            self.assertEqual(client.get("/api/traces/thread-0001").json()["traces"], [])

    def test_streamed_model_names_are_not_repeated(self):
        from scoop.server.trace import _model_name

        self.assertEqual(_model_name("openai/gpt-6-lunaopenai/gpt-6-luna"), "openai/gpt-6-luna")
        self.assertEqual(_model_name("openai/gpt-6-luna"), "openai/gpt-6-luna")
        self.assertIsNone(_model_name(None))

    def test_only_the_newest_traces_are_kept(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = TraceStore(Path(tmp))
            for i in range(55):
                store.save({"thread_id": "thread-0001", "n": i})
            kept = store.load("thread-0001")
            self.assertEqual((len(kept), kept[0]["n"], kept[-1]["n"]), (50, 54, 5))


if __name__ == "__main__":
    unittest.main()
