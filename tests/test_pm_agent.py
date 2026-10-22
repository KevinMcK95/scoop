import json
import re
import unittest
from pathlib import Path

try:
    from langchain_core.messages import AIMessage
    from scoop.agents.pm_agent import PM_AGENT_PROMPT, build_pm_handoff_tool
    from scoop.agents.pm_tools import build_pm_tools
    from scoop.agents.prompt import WORKSPACE_PROMPT
    from scoop.agents.tools import publish_result
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic", "numpy"}:
        raise
    build_pm_handoff_tool = None

from scoop.science.fields import field_from_viewer_data
from tests.field_fixture import viewer_data


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        self.tools = [t.name for t in tools]
        return self

    async def ainvoke(self, messages, config=None):
        self.last = messages
        return next(self.responses)


def call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


MEMBERS = {"id": "user:members:gaia", "table": "gaia", "label": "Members", "origin": "user", "count": 2,
           "filter": [{"column": "bp_rp", "op": "lasso", "value": {"y": "G", "polygons": [[[0, 15], [2, 15], [2, 21]]]}}]}


@unittest.skipIf(build_pm_handoff_tool is None, "optional chat dependencies absent")
class PmAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_pm_agent_sees_the_members_and_its_layers_reach_the_map(self):
        from langchain_core.tools import tool, InjectedToolCallId
        from typing import Annotated
        field = field_from_viewer_data(viewer_data())
        seen = {}

        @tool
        async def auto_members(tool_call_id: Annotated[str, InjectedToolCallId], use_cmd: bool = True):
            """Select and fit members."""
            seen["called"] = True
            return publish_result(tool_call_id, tool_call_id, {"kind": "map_actions", "status": "requested",
                                  "actions": [{"type": "layer.add", "layerId": "agent:members:gaia"}]},
                                  "Fitted 2 members.")

        @tool
        async def fit_members(filter: list, tool_call_id: Annotated[str, InjectedToolCallId]):
            """Fit a sample."""
            return "error: no stars"

        model = ScriptedModel(call("auto_members", {}, "p1"), call("fit_members", {"filter": []}, "p2"),
                              AIMessage(content="Bulk motion 0.04 ± 0.01 mas/yr."))
        handoff = build_pm_handoff_tool(model=model, field=field, tools=[auto_members, fit_members],
                                        layers=[MEMBERS, {"id": "gaia:stars", "origin": "pipeline"}])
        result = await handoff.ainvoke({"type": "tool_call", "name": "ask_pm_agent", "id": "h1",
                                        "args": {"request": "find members and fit them"}})
        context = json.loads(model.last[1].content.split("<context>", 1)[1].rsplit("</context>", 1)[0])
        self.assertEqual([l["id"] for l in context["user_layers"]], ["user:members:gaia"])  # not pipeline layers
        self.assertEqual(context["user_layers"][0]["filter"], MEMBERS["filter"])
        self.assertTrue(seen["called"])
        self.assertEqual(result.update["artifacts"]["p1"]["kind"], "map_actions")  # its layer reaches the browser
        self.assertIn("Bulk motion 0.04", result.update["messages"][0].content)

    async def test_no_field_no_pm_agent(self):
        handoff = build_pm_handoff_tool(model=ScriptedModel(), field=None, tools=[])
        result = await handoff.ainvoke({"type": "tool_call", "name": "ask_pm_agent", "id": "h", "args": {"request": "x"}})
        self.assertEqual(result.status, "error")

    def test_prompts_describe_every_pm_tool_and_the_hand_off(self):
        names = [t.name for t in build_pm_tools(field=None, lvdb_cache=Path("."))]
        for name in [*names, "describe_field", "summarize", "ads_search", "ads_paper"]:
            self.assertTrue(re.search(rf"\b{name}\b", PM_AGENT_PROMPT), name)
            if name not in ("describe_field", "summarize", "ads_search", "ads_paper"):
                self.assertNotRegex(WORKSPACE_PROMPT, rf"\b{name}\b")  # the chat agent no longer calls them
        self.assertRegex(WORKSPACE_PROMPT, r"\bask_pm_agent\b")


if __name__ == "__main__":
    unittest.main()
