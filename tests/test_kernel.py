import asyncio
import tempfile
import unittest
from pathlib import Path

from scoop.runtime.kernel import AnalysisKernel, _truncate, available_kernels

try:
    from scoop.agents.kernel_tool import build_kernel_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langchain_core", "pydantic"}:
        raise
    HAS_CHAT = False
else:
    HAS_CHAT = True

# A real kernel needs jupyter_client and ipykernel's default "python3" kernel.
READY = AnalysisKernel("python3").problem() is None


def run(coro):
    return asyncio.run(coro)


class KernelSetupTests(unittest.TestCase):
    def test_missing_kernel_is_reported(self):
        problem = AnalysisKernel("no-such-kernel-for-scoop").problem()
        self.assertIsNotNone(problem)

    @unittest.skipUnless(READY, "no jupyter_client or python3 kernel")
    def test_installed_kernels_are_listed(self):
        self.assertIn("python3", [k["name"] for k in available_kernels()])

    @unittest.skipUnless(READY, "no jupyter_client or python3 kernel")
    def test_the_agent_is_told_the_environment(self):
        facts = run(AnalysisKernel("python3", cwd=Path("/tmp/scoop-k")).describe())
        self.assertEqual((facts["name"], facts["working_directory"]), ("python3", str(Path("/tmp/scoop-k").resolve())))
        self.assertEqual(facts["language"], "python")
        # The built-in python3 spec names a bare "python"; installed ones give a full path.
        self.assertTrue(facts["python"] is None or Path(facts["python"]).is_absolute())

    def test_long_output_keeps_both_ends(self):
        text = _truncate("a" * 50 + "b" * 50, limit=20)
        self.assertTrue(text.startswith("a" * 10) and text.endswith("b" * 10))
        self.assertIn("80 characters omitted", text)


class FakeKernel:
    def __init__(self, result):
        self.result, self.calls, self.restarts = result, [], 0

    async def run(self, code, tables):
        self.calls.append((code, tables))
        return self.result

    async def restart(self):
        self.restarts += 1


@unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
class ToolTests(unittest.TestCase):
    field = {"key": "f", "tables": {"act": {"rows": [{"id": "1", "snr": 5.1}, {"id": "2", "snr": 7.3}]}}}

    def call(self, kernel, args):
        (tool,) = build_kernel_tools(kernel, self.field)
        return run(tool.ainvoke({"type": "tool_call", "name": "run_kernel", "id": "c1", "args": args}))

    def test_tables_restart_and_figure(self):
        kernel = FakeKernel({"output": "6.2\n", "error": None, "figures": ["iVBOR"], "figure_count": 2})
        message = self.call(kernel, {"code": "print(act.snr.mean())", "tables": ["act"], "restart": True})
        self.assertEqual(kernel.restarts, 1)
        self.assertEqual(kernel.calls[0][1], {"act": {"columns": ["id", "snr"], "rows": [["1", 5.1], ["2", 7.3]]}})
        self.assertEqual(message.status, "success")
        self.assertIn("1 more not shown", message.content)
        self.assertEqual(message.artifact["image"], "data:image/png;base64,iVBOR")

    def test_unknown_table_is_an_error(self):
        message = self.call(FakeKernel({}), {"code": "1", "tables": ["planck"]})
        self.assertEqual(message.status, "error")
        self.assertIn("act", message.content)


@unittest.skipUnless(READY, "no jupyter_client or python3 kernel")
class LiveKernelTests(unittest.TestCase):
    def test_state_tables_errors_figures_and_timeout(self):
        async def scenario():
            with tempfile.TemporaryDirectory() as tmp:
                kernel = AnalysisKernel("python3", cwd=Path(tmp), timeout=3)
                try:
                    first = await kernel.run("x = 40\nprint('hi')")
                    kept = await kernel.run("x + 2")
                    table = await kernel.run("print(act.snr.sum(), list(tables))",
                                             {"act": {"columns": ["id", "snr"], "rows": [["1", 5.0], ["2", 7.0]]}})
                    failed = await kernel.run("1 / 0")
                    figure = await kernel.run("import matplotlib\nmatplotlib.use('module://matplotlib_inline.backend_inline')\n"
                                              "import matplotlib.pyplot as plt\nplt.plot([1, 2])")
                    slow = await kernel.run("import time\ntime.sleep(30)")
                    after = await kernel.run("print(x)")
                    leftovers = list((Path(tmp) / ".scoop_tables").iterdir())
                    await kernel.restart()
                    fresh = await kernel.run("'x' in globals()")
                finally:
                    await kernel.close()
                return first, kept, table, failed, figure, slow, after, leftovers, fresh

        first, kept, table, failed, figure, slow, after, leftovers, fresh = run(scenario())
        self.assertEqual((first["output"], first["error"]), ("hi\n", None))
        self.assertEqual(kept["output"].strip(), "42")
        self.assertEqual(table["output"].strip(), "12.0 ['act']")
        self.assertIn("ZeroDivisionError", failed["error"])
        self.assertEqual(figure["figure_count"], 1)
        self.assertIn("Interrupted after 3 s", slow["error"])
        self.assertEqual(after["output"].strip(), "40")
        self.assertEqual(leftovers, [])
        self.assertEqual(fresh["output"].strip(), "False")


if __name__ == "__main__":
    unittest.main()
