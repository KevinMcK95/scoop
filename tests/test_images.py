import unittest

from scoop.images import SURVEYS, cutout_params, data_url

try:
    from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
    from scoop.chat.graph import shorten_old_results, with_images
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core"}:
        raise
    HAS_CHAT = False
else:
    HAS_CHAT = True


class CutoutTests(unittest.TestCase):
    def test_parameters_are_validated(self):
        params = cutout_params(260.1795, 57.9119, 0.05, "CDS/P/DSS2/color")
        self.assertEqual(params, {"hips": "CDS/P/DSS2/color", "width": 512, "height": 512, "projection": "TAN",
                                  "fov": 0.05, "ra": 260.1795, "dec": 57.9119, "format": "jpg"})
        for bad in ((400, 0, 0.1, "CDS/P/DSS2/color"), (10, 0, 0, "CDS/P/DSS2/color"), (10, 0, 30, "CDS/P/DSS2/color"),
                    (10, 0, 0.1, "http://evil.example/x"), (True, 0, 0.1, "CDS/P/DSS2/color")):
            with self.assertRaises(ValueError):
                cutout_params(*bad)
        self.assertIn("CDS/P/2MASS/color", SURVEYS)
        self.assertEqual(data_url(b"\xff\xd8"), "data:image/jpeg;base64,/9g=")


@unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
class ImagesInConversationTests(unittest.TestCase):
    def image(self, call_id):
        return ToolMessage(content="image follows", tool_call_id=call_id, name="sky_image",
                           artifact={"image": f"data:image/jpeg;base64,{call_id}"})

    def test_images_follow_the_tool_results_they_belong_to(self):
        call = AIMessage(content="", tool_calls=[{"name": "sky_image", "args": {}, "id": "a", "type": "tool_call"},
                                                 {"name": "lookup", "args": {}, "id": "b", "type": "tool_call"}])
        messages = [HumanMessage(content="what is this?"), call, self.image("a"), ToolMessage(content="x", tool_call_id="b")]
        out = with_images(messages)
        # The image message comes after both tool results, never between them.
        self.assertEqual([type(m).__name__ for m in out], ["HumanMessage", "AIMessage", "ToolMessage", "ToolMessage", "HumanMessage"])
        self.assertEqual(out[-1].content[1], {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,a"}})
        self.assertEqual(len(messages), 4)  # the stored conversation is unchanged

    def test_only_the_latest_images_are_attached(self):
        messages = []
        for i in "abc":
            messages += [AIMessage(content="", tool_calls=[{"name": "sky_image", "args": {}, "id": i, "type": "tool_call"}]),
                         self.image(i)]
        notes = [m.content for m in with_images(messages, max_images=2) if isinstance(m, HumanMessage)]
        self.assertIn("No longer attached", notes[0])
        self.assertEqual([n[1]["image_url"]["url"][-1] for n in notes[1:]], ["b", "c"])


@unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
class OldResultsTests(unittest.TestCase):
    def test_only_earlier_turns_are_shortened_for_the_model(self):
        big = "x" * 2000
        call = lambda i: AIMessage(content="", tool_calls=[{"name": "t", "args": {}, "id": i, "type": "tool_call"}])  # noqa: E731
        messages = [HumanMessage(content="q1"), call("a"), ToolMessage(content=big, tool_call_id="a"), AIMessage(content="ok"),
                    HumanMessage(content="q2"), call("b"), ToolMessage(content=big, tool_call_id="b")]
        out = shorten_old_results(messages, limit=100)
        self.assertTrue(out[2].content.startswith("x" * 100) and "shortened" in out[2].content)
        self.assertEqual(out[6].content, big)  # the current turn is untouched
        self.assertEqual(messages[2].content, big)  # and the stored thread too


if __name__ == "__main__":
    unittest.main()
