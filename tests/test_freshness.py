import json
import os
import tempfile
import time
import unittest
from pathlib import Path

from scoop.freshness import field_freshness, freshness, scout_status


class FreshnessTests(unittest.TestCase):
    def test_old_fields_and_new_sources_are_worth_mentioning(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / "skills" / "surveys").mkdir(parents=True)
            (tmp / "scoop" / "sources").mkdir(parents=True)
            field_path = tmp / "draco_1_field.json"
            field_path.write_text("{}")
            built = time.time() - 40 * 86400
            os.utime(field_path, (built, built))
            (tmp / "skills" / "surveys" / "euclid.md").write_text("new")  # written after the field
            for module in ("tic", "tess_tois", "exoplanets", "mast_tso"):
                (tmp / "scoop" / "sources" / f"{module}.py").write_text("")
            out = field_freshness({"tables": {"gaia": {}, "tic": {}}}, field_path, repo=tmp)
        self.assertTrue(out["old"])
        self.assertEqual(out["age_days"], 40.0)
        names = [s["name"] for s in out["new_sources"]]
        self.assertIn("tess_tois", names)
        self.assertNotIn("tic", names)  # the field already has it
        self.assertEqual(out["new_skills"], ["euclid"])
        self.assertTrue(out["worth_mentioning"])

    def test_a_fresh_field_is_not_mentioned_and_the_scout_is_checked(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            field_path = tmp / "f_field.json"
            field_path.write_text("{}")
            (tmp / ".scout_state.json").write_text(json.dumps({"last_scan": "2026-01-01"}))
            now = time.mktime((2026, 1, 11, 0, 0, 0, 0, 0, 0))
            self.assertFalse(scout_status(tmp, now)["overdue"])
            self.assertTrue(scout_status(tmp, now + 40 * 86400)["overdue"])
            out = freshness({"tables": {}}, field_path, repo=tmp, now=field_path.stat().st_mtime + 60)
        self.assertFalse(out["field"]["old"])
        self.assertFalse(scout_status(Path("/nonexistent"))["overdue"])  # unknown, so not nagged about


if __name__ == "__main__":
    unittest.main()
