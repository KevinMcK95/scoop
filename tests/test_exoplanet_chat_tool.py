"""The chat's ask_exoplanet_agent tool: what it tells the agent, and what it relays."""
import asyncio
import json
import unittest
from types import SimpleNamespace

try:
    from agentbook.models import FakeModel
    from scoop.chat.exoplanet_agent import build_exoplanet_tool, field_context, relay_text
except ModuleNotFoundError as exc:  # the light environment has no chat dependencies
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic", "httpx", "openai"}:
        raise
    build_exoplanet_tool = None

FIELD = {"key": "trappist_1", "target": {"name": "TRAPPIST-1", "ra": 346.6264, "dec": -5.0435,
                                         "rhalf_arcmin": 2.0},
         "region": [], "tables": {"exoplanet_hosts": {"rows": [{"id": "TRAPPIST-1"}]}}}


def invoke(tool, **args):
    call = {"name": tool.name, "args": args, "id": "call_1", "type": "tool_call"}
    return asyncio.run(tool.ainvoke(call))


@unittest.skipIf(build_exoplanet_tool is None, "optional chat dependencies absent")
class ExoplanetChatToolTests(unittest.TestCase):
    def test_the_agent_is_told_where_the_field_is_and_what_is_loaded(self):
        text = field_context(FIELD)
        self.assertIn("trappist_1 (TRAPPIST-1), center RA 346.62640, Dec -5.04350, radius 2.0 arcmin", text)
        self.assertIn("Loaded tables: exoplanet_hosts (1 rows).", text)

    def test_a_checked_result_says_so_and_a_failed_one_lists_its_problems(self):
        ok = SimpleNamespace(text="- Claim (NASA Exoplanet Archive)", checked=True, kind="result",
                             report=SimpleNamespace(problems=[]))
        self.assertTrue(relay_text(ok).endswith("✓ Checked: every claim is quoted from the source it cites."))
        bad = SimpleNamespace(text="- Claim (2021PSJ.....2....1A)", checked=False, kind="result",
                              report=SimpleNamespace(problems=["Bibcode 2021PSJ.....2....1A was not returned by any tool."]))
        self.assertIn("⚠ Not checked", relay_text(bad))
        self.assertIn("- Bibcode 2021PSJ.....2....1A was not returned", relay_text(bad))

    def test_a_vague_constraint_comes_back_as_the_agents_question(self):
        clarify = json.dumps({"clarify": {"constraints": ["habitable"], "question": "How do you define habitable?"}})
        tool = build_exoplanet_tool(field=FIELD, make_model=lambda: FakeModel([clarify]))
        command = invoke(tool, question="Add confirmed planet hosts with constraints: habitable.")
        message = command.update["messages"][0]
        self.assertIn("How do you define habitable?", message.content)
        self.assertIn("word for word", message.content)
        self.assertNotIn("Highlighted", message.content)

    def test_without_a_model_it_says_so(self):
        tool = build_exoplanet_tool(field=FIELD, make_model=lambda: None)
        message = invoke(tool, question="Does TOI-561 b have an atmosphere?")
        self.assertIn("needs a chat model", message.content)


if __name__ == "__main__":
    unittest.main()


@unittest.skipIf(build_exoplanet_tool is None, "optional chat dependencies absent")
class AddDataOptionsTests(unittest.TestCase):
    """The chat's add_data passes options (e.g. a TIC magnitude limit) to a source's query."""

    def setUp(self):
        from scoop.chat.visual import build_map_tools

        self.calls = []

        async def add_source(key, source, options=None):
            from scoop.sources import SOURCES, option_values
            option_values(SOURCES[source], options)  # what the server checks
            self.calls.append((key, source, options))
            return 12

        self.field = {**FIELD, "tables": {**FIELD["tables"], "tic": {"rows": []}}}
        self.add_data = next(t for t in build_map_tools(field=self.field, add_source=add_source)
                             if t.name == "add_data")

    def test_options_reach_the_query_and_a_loaded_source_can_be_queried_again(self):
        result = invoke(self.add_data, source="tic", options={"tmag_max": 13})
        self.assertEqual(self.calls, [("trappist_1", "tic", {"tmag_max": 13})])
        self.assertIn("with {'tmag_max': 13", result.update["messages"][0].content)

    def test_without_options_a_loaded_source_is_not_offered_again(self):
        self.assertIn("Not available to add: 'tic'", invoke(self.add_data, source="tic").content)

    def test_an_out_of_range_option_is_explained(self):
        message = invoke(self.add_data, source="tic", options={"tmag_max": 30})
        self.assertIn("Invalid options for 'tic'", message.content)
        self.assertIn("Tmag < (default 16, 4-20)", message.content)

    def test_a_source_without_options_refuses_them(self):
        message = invoke(self.add_data, source="tess_tois", options={"tmag_max": 13})
        self.assertIn("takes no options", message.content)
