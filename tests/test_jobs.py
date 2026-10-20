import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

from scoop.server.jobs import JobManager

OK_SCRIPT = """
import argparse, json, pathlib
p = argparse.ArgumentParser(); p.add_argument("--out"); p.add_argument("--lvdb-key"); p.add_argument("--n-rhalf")
a = p.parse_args()
print("querying MAST", flush=True)
print("  [decision] Took the center from the LVDB.", flush=True)
out = pathlib.Path(a.out); out.mkdir(parents=True, exist_ok=True)
(out / f"{a.lvdb_key}_field.json").write_text(json.dumps({"n_rhalf": float(a.n_rhalf)}))
"""
ARGS_SCRIPT = """
import json, pathlib, sys
out = pathlib.Path(sys.argv[sys.argv.index("--out") + 1]); out.mkdir(parents=True, exist_ok=True)
print("args", json.dumps(sys.argv[3:]), flush=True)
(out / f"{out.name}_field.json").write_text("{}")
"""
FAIL_SCRIPT = """
import sys
print("Traceback (most recent call last):")
print("ValueError: LVDB key 'nowhere_1' not found")
sys.exit(1)
"""


class JobTests(unittest.IsolatedAsyncioTestCase):
    async def run_job(self, script_text, key="mini_1", n_rhalf=2.0):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        script = tmp / "pipeline.py"
        script.write_text(script_text)
        manager = JobManager(tmp / "fields", script, python=sys.executable, timeout_s=30)
        job = manager.start(key, n_rhalf)
        self.assertIs(manager.start(key, n_rhalf), job)  # joins the running job
        for _ in range(300):
            if job.status != "running":
                break
            await asyncio.sleep(0.02)
        return manager, job, tmp

    async def test_successful_run_reports_progress_and_writes_the_field(self):
        manager, job, tmp = await self.run_job(OK_SCRIPT, n_rhalf=3)
        view = manager.get(job.id).view()
        self.assertEqual(view["status"], "done")
        self.assertEqual(view["decisions"], ["Took the center from the LVDB."])
        self.assertIn("querying MAST", job.lines)
        self.assertTrue((tmp / "fields" / "mini_1" / "mini_1_field.json").exists())

    async def test_failed_run_keeps_the_error(self):
        _, job, _ = await self.run_job(FAIL_SCRIPT, key="nowhere_1")
        self.assertEqual(job.status, "failed")
        self.assertEqual(job.error, "ValueError: LVDB key 'nowhere_1' not found")

    async def test_cone_runs_pass_coordinates_and_derive_the_key(self):
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "pipeline.py").write_text(ARGS_SCRIPT)
        manager = JobManager(tmp / "fields", tmp / "pipeline.py", python=sys.executable, timeout_s=30)
        job = manager.start_cone(10.6847, 41.2690, 20, "M31 QSOs 20′")
        for _ in range(300):
            if job.status != "running":
                break
            await asyncio.sleep(0.02)
        self.assertEqual((job.status, job.key), ("done", "m31_qsos_20"))
        args = json.loads(next(l for l in job.lines if l.startswith("args"))[5:])
        self.assertEqual(args, ["--ra", "10.6847", "--dec", "41.269", "--radius-arcmin", "20.0",
                                "--name", "M31 QSOs 20′"])
        for bad in ((400, 0, 5, "x"), (10, 95, 5, "x"), (10, 0, 90, "x"), (10, 0, 5, "--out /tmp"),
                    (10, 0, 5, "; rm -rf"), (10, 0, 5, "___"), (True, 0, 5, "x")):
            with self.assertRaises(ValueError, msg=bad):
                manager.start_cone(*bad)

    async def test_arguments_are_validated_before_anything_runs(self):
        manager = JobManager(Path("."), Path("missing.py"))
        for key, n in (("Draco 1", 2), ("../etc", 2), ("draco_1; rm", 2), ("draco_1", 50)):
            with self.assertRaises(ValueError):
                manager.start(key, n)
        self.assertEqual(manager.jobs, {})


try:
    from astropy.table import Table
except ModuleNotFoundError:
    Table = None


@unittest.skipUnless(Table, "astropy absent")
class LVDBIndexTests(unittest.TestCase):
    def test_search_by_name_or_key(self):
        from scoop.lvdb import CACHE_NAME, LVDBIndex
        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(LVDBIndex(Path(tmp)).search("leo"))
            (Path(tmp) / "draco" / "cache").mkdir(parents=True)
            Table({"key": ["leo_1", "leo_2", "ursa_minor_1"], "name": ["Leo I", "Leo II", "Ursa Minor"],
                   "ra": [152.1, 168.4, 227.3], "dec": [12.3, 22.2, 67.2], "rhalf": [3.6, 2.5, 18.2],
                   "distance": [254.0, 233.0, 76.0]}).write(Path(tmp) / "draco" / "cache" / CACHE_NAME)
            index = LVDBIndex(Path(tmp))
            self.assertEqual([r["key"] for r in index.search("Ursa Minor")], ["ursa_minor_1"])
            self.assertEqual([r["key"] for r in index.search("leo i")], ["leo_1", "leo_2"])
            self.assertEqual(index.search("ursa_minor_1")[0]["name"], "Ursa Minor")
            self.assertEqual(index.search("  "), [])
            self.assertTrue(index.has("leo_2"))
            self.assertFalse(index.has("leo_9"))


if __name__ == "__main__":
    unittest.main()
