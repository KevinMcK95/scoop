"""Agent memory persists in SQLite: a reopened thread keeps its history across restarts."""
import tempfile
import unittest
from pathlib import Path

try:
    from langchain_core.messages import AIMessage
    from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver
    from scoop.agents import build_chat_graph
    from scoop.agents.visual import build_map_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "aiosqlite", "pydantic"}:
        raise
    HAS_DEPS = False
else:
    HAS_DEPS = True


class Echo:
    """Replies with how many messages it was shown."""

    def bind_tools(self, tools):
        return self

    async def ainvoke(self, messages, config=None):
        return AIMessage(content=f"saw {len(messages)}")


@unittest.skipUnless(HAS_DEPS, "optional chat dependencies absent")
class DurableMemoryTests(unittest.IsolatedAsyncioTestCase):
    async def turn(self, path, text):
        async with AsyncSqliteSaver.from_conn_string(str(path)) as saver:  # a fresh "server" each time
            graph = build_chat_graph(Echo(), build_map_tools(), checkpointer=saver, system_prompt="p")
            result = await graph.ainvoke({"messages": [{"role": "user", "content": text}]},
                                         config={"configurable": {"thread_id": "thread-0001"}})
            return result["messages"][-1].content

    async def test_history_survives_a_restart(self):
        path = Path(self.enterContext(tempfile.TemporaryDirectory())) / "threads.sqlite"
        self.assertEqual(await self.turn(path, "first"), "saw 2")  # system + first
        self.assertEqual(await self.turn(path, "second"), "saw 4")  # + first reply + second


if __name__ == "__main__":
    unittest.main()
