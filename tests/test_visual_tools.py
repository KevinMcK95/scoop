import unittest

try:
    from scoop.chat.visual import build_view_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic"}:
        raise
    HAS_DEPS = False
else:
    HAS_DEPS = True


@unittest.skipUnless(HAS_DEPS, "optional chat dependencies absent")
class ViewToolsTests(unittest.IsolatedAsyncioTestCase):
    async def call(self, command):
        tool = build_view_tools(layer_ids=["fixture-sources", "annotations"], source_ids=["fixture-a"])[0]
        return await tool.ainvoke({
            "name": "update_sky_view", "args": {"command": command},
            "id": "view-123", "type": "tool_call",
        })

    async def test_commands_preserve_frontend_protocol_and_only_request_display(self):
        commands = [
            {"type": "center", "ra": 10.0, "dec": 41.0, "fov": 1.0},
            {"type": "layer.visibility", "layerId": "annotations", "visible": False},
            {"type": "sources.highlight", "sourceIds": ["fixture-a"]},
            {"type": "annotation.circle", "ra": 10.0, "dec": 41.0, "radius": 2/60, "label": "2 arcmin"},
            {"type": "annotations.clear"},
        ]
        for command in commands:
            with self.subTest(command=command):
                result = await self.call(command)
                artifact = result.update["artifacts"]["view-123"]
                self.assertEqual(artifact["status"], "requested")
                self.assertEqual(artifact["command"], command)
                self.assertIn("No renderer acknowledgment", result.update["messages"][0].content)

    async def test_unknown_ids_are_correctable_errors_not_display_requests(self):
        for command in (
            {"type": "sources.highlight", "sourceIds": ["invented"]},
            {"type": "layer.visibility", "layerId": "other-session", "visible": True},
        ):
            result = await self.call(command)
            self.assertEqual(result.status, "error")
            self.assertEqual(result.tool_call_id, "view-123")

    async def test_invalid_shapes_and_executable_commands_are_rejected(self):
        for command in (
            {"type": "eval", "code": "alert(1)"},
            {"type": "center", "ra": 360.0, "dec": 0.0, "fov": 1.0},
            {"type": "center", "ra": True, "dec": 0.0, "fov": 1.0},
            {"type": "annotation.circle", "ra": 0.0, "dec": 0.0, "radius": -1.0, "label": "circle"},
            {"type": "annotation.circle", "ra": 0.0, "dec": 0.0, "radius": 1.0, "label": " "},
        ):
            with self.subTest(command=command), self.assertRaises(ValueError):
                await self.call(command)
