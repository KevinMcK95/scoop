import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import triage_failures as tri  # noqa: E402


class TriageTests(unittest.TestCase):
    def test_repeats_group_and_are_classified(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            turns = [
                {"timestamp": "2026-10-21T14:00:00+00:00", "thread_id": "t1", "failures": [
                    {"tool": "run_cone", "reason": "name must be 1-60 letters", "args": '{"label": "NGC 300: x"}'},
                    {"tool": "lookup", "reason": "Name resolver failed (ConnectError); try again"}]},
                {"timestamp": "2026-10-21T15:00:00+00:00", "thread_id": "t2", "failures": [
                    {"tool": "run_cone", "reason": "name must be 1-60 letters", "args": '{"label": "M31, 1°"}',
                     "via": "ask_data_agent"}], "error": None},
            ]
            (tmp / "turns.jsonl").write_text("\n".join(json.dumps(t) for t in turns))
            (tmp / "fields" / "_runs").mkdir(parents=True)
            (tmp / "fields" / "_runs" / "20261021T150300_trappist_1_abc.json").write_text(json.dumps({
                "key": "trappist_1", "args": ["--ra", "346.6"], "error": "ValueError: input tables for join",
                "failed_utc": "20261021T150300", "log": [
                    'File "/x/site-packages/astropy/table/operations.py", line 446, in join',
                    'File "lvdb_field.py", line 402, in query_gaia',
                    "ValueError: input tables for join must both have at least one row"]}))
            failures = tri.tool_failures(tmp / "turns.jsonl", None) + tri.run_failures(tmp / "fields", None)
            text = tri.report(failures)
        self.assertIn("## 1. run_cone — 2×", text)  # the two label failures group together
        self.assertIn("agent mistake or tool limit", text)
        self.assertIn("inside: ask_data_agent", text)
        self.assertIn("transient", text)
        self.assertIn("SCOOP bug", text)
        self.assertIn('`File "lvdb_field.py", line 402, in query_gaia`', text)  # our frame, not astropy's
        self.assertNotIn("site-packages", text)

    def test_signatures_drop_the_specifics(self):
        self.assertEqual(tri.signature("counting timed out after 300 s"), tri.signature("counting timed out after 45 s"))
        self.assertEqual(tri.signature("No run 'ab12cd34ef'"), tri.signature("No run 'ff00aa11bb'"))


if __name__ == "__main__":
    unittest.main()
