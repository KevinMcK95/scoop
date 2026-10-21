import re
import unittest

try:
    from scoop.chat.prompt import WORKSPACE_PROMPT
    from scoop.chat.visual import build_map_tools
    from scoop.chat.python_tool import build_python_tools
    from scoop.chat.kernel_tool import build_kernel_tools
    from scoop.chat.web_tools import build_web_tools
    from scoop.literature.tools import build_ads_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic", "httpx"}:
        raise
    HAS_DEPS = False
else:
    HAS_DEPS = True



@unittest.skipUnless(HAS_DEPS, "optional chat dependencies absent")
class PromptTests(unittest.TestCase):
    def test_every_tool_the_server_provides_is_described(self):
        names = {t.name for t in build_map_tools(jobs=object(), lvdb=object(), resolver=object(), simbad=object())}
        names |= {t.name for t in build_ads_tools(object())}
        names |= {t.name for t in build_web_tools(object(), object())}
        names |= {t.name for t in build_python_tools(object())}
        names |= {t.name for t in build_kernel_tools(object())}
        missing = [n for n in sorted(names) if not re.search(rf"\b{n}\b", WORKSPACE_PROMPT)]
        self.assertEqual(missing, [], "describe these tools in scoop/chat/prompt.py")

    def test_every_map_action_is_described(self):
        for action in ("layer.add", "layer.update", "layer.remove", "focus", "focus.rows", "highlight",
                       "annotate", "annotations.clear", "select", "plot.show", "plot.hide"):
            self.assertIn(action, WORKSPACE_PROMPT)

    def test_every_context_key_is_explained(self):
        for key in ("field", "availableFields", "viewport", "survey", "layers", "highlight", "annotations",
                    "selection", "userRegions", "plot", "selectedPublication", "mapAcknowledgments", "kernel"):
            self.assertIn(key, WORKSPACE_PROMPT)


if __name__ == "__main__":
    unittest.main()
