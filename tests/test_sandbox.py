import asyncio
import subprocess
import unittest
from pathlib import Path

from scoop.runtime.sandbox import PythonSandbox, table_payload

try:
    from scoop.agents.python_tool import build_python_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langchain_core", "pydantic"}:
        raise
    HAS_CHAT = False
else:
    HAS_CHAT = True

SANDBOX = PythonSandbox()
READY = SANDBOX.problem() is None


def run(coro):
    return asyncio.run(coro)


class PayloadTests(unittest.TestCase):
    def test_rows_become_columns_and_lists(self):
        table = {"rows": [{"id": "a", "G": 18.2}, {"id": "b", "bp_rp": 0.9}]}
        self.assertEqual(table_payload(table), {"columns": ["id", "G", "bp_rp"],
                                                "rows": [["a", 18.2, None], ["b", None, 0.9]]})
        self.assertEqual(table_payload(table, ["G"]), {"columns": ["G"], "rows": [[18.2], [None]]})

    def test_missing_setup_is_reported(self):
        problem = PythonSandbox(directory=Path("/nonexistent")).problem()
        self.assertIsNotNone(problem)


class FakeSandbox:
    def __init__(self, result):
        self.result, self.calls = result, []

    async def run(self, code, tables):
        self.calls.append((code, tables))
        return self.result


@unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
class ToolTests(unittest.TestCase):
    field = {"key": "f", "tables": {"gaia": {"rows": [{"id": "1", "G": 18.0}, {"id": "2", "G": 19.0}]}}}

    def call(self, sandbox, args):
        (tool,) = build_python_tools(sandbox, self.field)
        return run(tool.ainvoke({"type": "tool_call", "name": "run_python", "id": "c1", "args": args}))

    def test_named_tables_are_passed_and_figures_attached(self):
        sandbox = FakeSandbox({"output": "18.5\n", "error": None, "figures": ["iVBOR"], "figure_count": 3})
        message = self.call(sandbox, {"code": "print(gaia.G.mean())", "tables": ["gaia"]})
        self.assertEqual(sandbox.calls[0][1], {"gaia": {"columns": ["id", "G"], "rows": [["1", 18.0], ["2", 19.0]]}})
        self.assertEqual(message.status, "success")
        self.assertIn("18.5", message.content)
        self.assertIn("2 more not shown", message.content)
        self.assertEqual(message.artifact["image"], "data:image/png;base64,iVBOR")
        self.assertEqual(message.artifact["code"], "print(gaia.G.mean())")

    def test_errors_and_unknown_tables_are_reported(self):
        sandbox = FakeSandbox({"output": "", "error": "ZeroDivisionError: division by zero", "figures": []})
        self.assertEqual(self.call(sandbox, {"code": "1/0"}).status, "error")
        unknown = self.call(sandbox, {"code": "x", "tables": ["sdss"]})
        self.assertEqual(unknown.status, "error")
        self.assertIn("gaia", unknown.content)
        self.assertEqual(len(sandbox.calls), 1)


@unittest.skipUnless(READY, "Python sandbox not set up here (needs macOS, Node and `npm run setup` in scoop/runtime/sandbox/)")
class SandboxTests(unittest.TestCase):
    """The real sandbox: computations work; files, processes and the network do not."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.sandbox = PythonSandbox(timeout=20)

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.sandbox.close())
        cls.loop.close()

    def go(self, code, tables=None):
        return self.loop.run_until_complete(self.sandbox.run(code, tables))

    def test_astropy_and_tables(self):
        result = self.go("from astropy.coordinates import SkyCoord\nimport astropy.units as u\n"
                         "c = SkyCoord(260.0517*u.deg, 57.9153*u.deg)\nprint(round(c.galactic.l.deg, 2))\n"
                         "t.x.sum()", {"t": {"columns": ["x"], "rows": [[1], [2]]}})
        self.assertIsNone(result["error"])
        self.assertEqual(result["output"].split(), ["86.37", "np.int64(3)"])

    def test_figures_are_returned(self):
        result = self.go("plt.hist([1, 2, 2, 3])\nplt.show()")
        self.assertEqual(result["output"], "")
        self.assertEqual(result["figure_count"], 1)
        self.assertTrue(result["figures"][0].startswith("iVBOR"))

    def test_no_files_processes_or_network(self):
        secret = Path.home() / ".config" / "scoop" / "settings.json"
        attempts = {
            "python open": f"open({str(secret)!r}).read()",
            "node fs read": f"import js\njs.process.getBuiltinModule('node:fs').readFileSync({str(secret)!r})",
            "node fs write": "import js\njs.process.getBuiltinModule('node:fs').writeFileSync('/tmp/scoop_escape', 'x')",
            "child process": "import js\njs.process.getBuiltinModule('node:child_process').execSync('id')",
            "fetch": "import js\njs.fetch('https://example.org')",
        }
        for name, code in attempts.items():
            with self.subTest(name):
                self.assertIsNotNone(self.go(code)["error"])
        self.assertFalse(Path("/tmp/scoop_escape").exists())

    def test_the_os_denies_network(self):
        command = self.sandbox.command()
        script = ("require('node:net').connect(9, '127.0.0.1')"
                  ".on('error', e => console.log(e.code)).on('connect', () => console.log('connected'))")
        out = subprocess.run([*command[:3], command[3], "-e", script], capture_output=True, text=True, timeout=20)
        self.assertEqual(out.stdout.strip(), "EPERM")

    def test_timeout_restarts(self):
        quick = PythonSandbox(timeout=3)
        try:
            slow = self.loop.run_until_complete(quick.run("while True: pass"))
            self.assertIn("time limit", slow["error"])
            again = self.loop.run_until_complete(quick.run("1 + 1"))
            self.assertEqual(again["output"].strip(), "2")
        finally:
            self.loop.run_until_complete(quick.close())


if __name__ == "__main__":
    unittest.main()
