from __future__ import annotations

import asyncio
import unittest
from typing import Annotated

try:
    from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
    from langchain_core.tools import InjectedToolCallId, tool
    from langgraph.checkpoint.memory import InMemorySaver
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core"}:
        raise
    HAS_CHAT_DEPS = False
else:
    HAS_CHAT_DEPS = True
    from scoop.chat import build_chat_graph, merge_artifacts, publish_result


class ScriptedModel:
    """Duck-typed model that records binding, messages, and forwarded config."""

    def __init__(self, *responses):
        self.responses = iter(responses)
        self.bindings = []
        self.calls = []

    def bind_tools(self, tools):
        self.bindings.append(tools)
        return self

    async def ainvoke(self, messages, config=None):
        self.calls.append((messages, config))
        return next(self.responses)


def result_tool(*, collision=False):
    @tool
    async def lookup(
        query: str, tool_call_id: Annotated[str, InjectedToolCallId]
    ):
        """Return a fake archive result for a query."""
        await asyncio.sleep(0)
        return publish_result(
            tool_call_id,
            "shared" if collision else tool_call_id,
            {"rows": [{"native_id": query}], "status": "success"},
            f"Found {query}",
        )

    return lookup


def calling(*calls):
    return AIMessage(
        content="",
        tool_calls=[
            {"name": "lookup", "args": {"query": query}, "id": call_id, "type": "tool_call"}
            for call_id, query in calls
        ],
    )


@unittest.skipUnless(HAS_CHAT_DEPS, "optional LangGraph/LangChain dependencies absent")
class ChatGraphTests(unittest.IsolatedAsyncioTestCase):
    async def test_tool_roundtrip(self):
        model = ScriptedModel(calling(("call-1", "M31")), AIMessage(content="Done"))
        tools = [result_tool()]
        graph = build_chat_graph(model, tools, system_prompt="Test prompt")
        result = await graph.ainvoke(
            {"messages": [HumanMessage(content="Search")]},
            config={"configurable": {"marker": "forwarded"}},
        )
        self.assertEqual(model.bindings, [tools])
        self.assertEqual(result["artifacts"], {
            "call-1": {"rows": [{"native_id": "M31"}], "status": "success"}
        })
        self.assertEqual(len(result["messages"]), 4)
        message = result["messages"][2]
        self.assertIsInstance(message, ToolMessage)
        self.assertEqual(message.tool_call_id, "call-1")
        self.assertIn("Artifact: call-1", message.content)
        self.assertNotIn("native_id", message.content)
        self.assertEqual(result["messages"][-1].content, "Done")
        self.assertIsInstance(model.calls[0][0][0], SystemMessage)
        self.assertEqual(model.calls[0][0][0].content, "Test prompt")
        self.assertEqual(model.calls[0][1]["configurable"]["marker"], "forwarded")
        self.assertEqual(model.calls[1][0][-1], message)
        self.assertFalse(any(isinstance(m, SystemMessage) for m in result["messages"]))

    async def test_parallel_tool_calls_merge(self):
        model = ScriptedModel(
            calling(("one", "M31"), ("two", "M32")), AIMessage(content="Both done")
        )
        result = await build_chat_graph(model, [result_tool()]).ainvoke(
            {"messages": [HumanMessage(content="Search both")]}
        )
        self.assertEqual(set(result["artifacts"]), {"one", "two"})
        self.assertEqual(result["artifacts"]["two"]["rows"], [{"native_id": "M32"}])
        self.assertEqual(
            {m.tool_call_id for m in result["messages"] if isinstance(m, ToolMessage)},
            {"one", "two"},
        )

    async def test_plain_assistant_and_clarification(self):
        for text in ("Hello!", "Which archive should I search?"):
            with self.subTest(text=text):
                model = ScriptedModel(AIMessage(content=text))
                result = await build_chat_graph(model, [result_tool()]).ainvoke(
                    {"messages": [HumanMessage(content="Hello")]}
                )
                self.assertEqual(result["artifacts"], {})
                self.assertEqual(len(result["messages"]), 2)
                self.assertEqual(result["messages"][-1].content, text)
                self.assertEqual(len(model.calls), 1)

    async def test_checkpoint_followup_and_thread_isolation(self):
        model = ScriptedModel(
            calling(("first", "M31")), AIMessage(content="Found it"),
            AIMessage(content="Here is the summary"), AIMessage(content="New conversation"),
        )
        graph = build_chat_graph(model, [result_tool()], checkpointer=InMemorySaver())
        config = {"configurable": {"thread_id": "research"}}
        first = await graph.ainvoke({"messages": [HumanMessage(content="Search")]}, config)
        followup = await graph.ainvoke(
            {"messages": [HumanMessage(content="Summarize")]}, config
        )
        self.assertEqual(followup["artifacts"], first["artifacts"])
        self.assertEqual(followup["messages"][:4], first["messages"])
        self.assertEqual(len(followup["messages"]), 6)
        self.assertTrue(any(isinstance(m, ToolMessage) for m in model.calls[2][0]))
        other = await graph.ainvoke(
            {"messages": [HumanMessage(content="Hi")]},
            {"configurable": {"thread_id": "other"}},
        )
        self.assertEqual(other["artifacts"], {})
        self.assertEqual(len(other["messages"]), 2)
        saved = await graph.aget_state(config)
        self.assertEqual(saved.values["artifacts"], first["artifacts"])
        self.assertEqual(len(saved.values["messages"]), 6)

    async def test_parallel_collision_propagates(self):
        model = ScriptedModel(calling(("one", "M31"), ("two", "M32")))
        with self.assertRaisesRegex(ValueError, "Conflicting artifact ID"):
            await build_chat_graph(model, [result_tool(collision=True)]).ainvoke(
                {"messages": [HumanMessage(content="Search")]}
            )

    async def test_invalid_arguments_can_be_corrected(self):
        invalid = AIMessage(content="", tool_calls=[
            {"name": "lookup", "args": {}, "id": "bad", "type": "tool_call"}
        ])
        model = ScriptedModel(invalid, calling(("fixed", "M31")), AIMessage(content="Done"))
        result = await build_chat_graph(model, [result_tool()]).ainvoke(
            {"messages": [HumanMessage(content="Search")]}
        )
        tool_messages = [m for m in result["messages"] if isinstance(m, ToolMessage)]
        self.assertEqual(tool_messages[0].tool_call_id, "bad")
        self.assertEqual(tool_messages[0].status, "error")
        self.assertEqual(tool_messages[1].tool_call_id, "fixed")
        self.assertEqual(set(result["artifacts"]), {"fixed"})
        self.assertEqual(result["messages"][-1].content, "Done")

    async def test_unexpected_tool_error_propagates(self):
        @tool
        async def lookup(query: str):
            """Fail unexpectedly."""
            raise RuntimeError("unexpected bug")

        model = ScriptedModel(calling(("one", "M31")))
        with self.assertRaisesRegex(RuntimeError, "unexpected bug"):
            await build_chat_graph(model, [lookup]).ainvoke(
                {"messages": [HumanMessage(content="Search")]}
            )

    async def test_requires_nonempty_tools(self):
        model = ScriptedModel()
        for tools in ([], None, ()):
            with self.subTest(tools=tools), self.assertRaises(ValueError):
                build_chat_graph(model, tools)
        self.assertEqual(model.bindings, [])


@unittest.skipUnless(HAS_CHAT_DEPS, "optional LangGraph/LangChain dependencies absent")
class ArtifactTests(unittest.TestCase):
    def test_publish_copies_full_json_payload(self):
        payload = {"nested": [{"null": None, "bool": True, "number": 1.5}], "text": "α"}
        command = publish_result("call", "result", payload, "One result")
        self.assertEqual(command.update["artifacts"], {"result": payload})
        payload["nested"][0]["number"] = 99
        self.assertEqual(command.update["artifacts"]["result"]["nested"][0]["number"], 1.5)
        self.assertEqual(command.update["messages"][0].tool_call_id, "call")
        self.assertIn("result", command.update["messages"][0].content)

    def test_reducer_replay_collision_and_no_mutation(self):
        left = {"one": {"items": [1]}}
        right = {"two": {"items": [2]}}
        merged = merge_artifacts(left, right)
        self.assertEqual(merge_artifacts(merged, left), merged)
        merged["one"]["items"].append(3)
        self.assertEqual(left, {"one": {"items": [1]}})
        self.assertEqual(right, {"two": {"items": [2]}})
        with self.assertRaisesRegex(ValueError, "Conflicting artifact ID"):
            merge_artifacts(left, {"one": {"items": [9]}})

    def test_replay_distinguishes_json_booleans_from_numbers(self):
        with self.assertRaisesRegex(ValueError, "Conflicting artifact ID"):
            merge_artifacts({"one": {"value": True}}, {"one": {"value": 1}})
        self.assertEqual(
            merge_artifacts({"one": {"a": 1, "b": 2}}, {"one": {"b": 2, "a": 1}}),
            {"one": {"a": 1, "b": 2}},
        )

    def test_strict_json_validation(self):
        cycle = {}
        cycle["self"] = cycle
        invalid = [
            {1: "bad"}, {"nested": [{2: "bad"}]}, {"x": float("nan")},
            {"x": float("inf")}, {"x": -float("inf")}, {"x": object()},
            {"x": (1, 2)}, {"x": {1, 2}}, cycle, [], None,
        ]
        for payload in invalid:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                publish_result("call", "result", payload, "Summary")
            with self.subTest(reducer_payload=payload), self.assertRaises(ValueError):
                merge_artifacts({}, {"result": payload})

    def test_nonempty_ids_and_summary(self):
        for index in (0, 1, 3):
            for invalid in ("", "  ", None, 123):
                args = ["call", "result", {}, "Summary"]
                args[index] = invalid
                with self.subTest(index=index, invalid=invalid), self.assertRaises(ValueError):
                    publish_result(*args)
        with self.assertRaises(ValueError):
            merge_artifacts({}, {"": {}})


if __name__ == "__main__":
    unittest.main()
