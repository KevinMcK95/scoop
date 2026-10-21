import json
import math
import unittest

try:
    from scoop.chat.region_tools import MAX_GAIA_ROWS, build_region_tools
    from scoop import streams
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic", "numpy"}:
        raise
    build_region_tools = None

try:
    import numpy  # noqa: F401
except ModuleNotFoundError:
    numpy = None

from scoop.fields import field_from_viewer_data
from tests.field_fixture import viewer_data


class FakeJobs:
    def __init__(self, counts=(2007, 2007), fail=None):
        self.plans, self.counts, self.started, self.fail = {}, counts, [], fail

    def save_plan(self, spec, name):
        plan_id = f"{len(self.plans) + 1:012x}"
        self.plans[plan_id] = {"name": name, "spec": json.loads(json.dumps(spec))}
        return plan_id

    def load_plan(self, plan_id):
        return self.plans.get(plan_id)

    async def count_plan(self, plan_id):
        if self.fail:
            raise RuntimeError(self.fail)
        return {"gaia_all": self.counts[0], "gaia_with_cuts": self.counts[1], "note": "upper bound"}

    def start_region(self, plan_id, max_rows):
        self.started.append((plan_id, max_rows))

        class Job:
            id, key = "job1", "cosmos_0_5_deg2"
        return Job()


async def resolver(name):
    return {"ra": 150.11916667, "dec": 2.20583333, "name": "COSMOS field"} if name == "COSMOS field" else None


@unittest.skipIf(build_region_tools is None, "optional chat dependencies absent")
class RegionToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.jobs = FakeJobs()
        self.tools = {t.name: t for t in build_region_tools(jobs=self.jobs, resolver=resolver)}

    async def call(self, name, args, call_id="c1"):
        return await self.tools[name].ainvoke({"type": "tool_call", "name": name, "id": call_id, "args": args})

    async def test_box_by_name_is_counted_shown_and_needs_confirmation(self):
        side = math.sqrt(0.5)
        command = await self.call("plan_region", {"shape": "box", "name": "COSMOS 0.5 deg2",
                                                  "center_name": "COSMOS field", "width_deg": side})
        result = json.loads(command.update["messages"][0].content.rsplit("\nArtifact:", 1)[0])
        self.assertAlmostEqual(result["region"]["area_deg2"], 0.5, places=2)
        self.assertEqual(result["gaia_rows"], {"in_region": 2007, "with_cuts_at_most": 2007})
        self.assertIn("Sesame", result["source"])
        actions = command.update["artifacts"]["c1"]["actions"]
        self.assertEqual([a["type"] for a in actions], ["annotate", "focus"])
        refused = await self.call("run_region", {"plan_id": result["plan_id"], "user_confirmed": False}, "c2")
        self.assertEqual(refused.status, "error")
        self.assertEqual(self.jobs.started, [])
        started = await self.call("run_region", {"plan_id": result["plan_id"], "user_confirmed": True}, "c3")
        self.assertEqual(started.update["artifacts"]["c3"]["kind"], "field_job")
        self.assertEqual(self.jobs.started, [(result["plan_id"], MAX_GAIA_ROWS)])

    async def test_band_needs_its_source_and_pm_cuts_need_a_pm_track(self):
        track = [[150 + k, 20.0] for k in range(10)]
        no_source = await self.call("plan_region", {"shape": "band", "name": "x", "track": track, "width_deg": 1})
        self.assertIn("source", no_source.content)
        no_pm = await self.call("plan_region", {"shape": "band", "name": "x", "track": track, "width_deg": 1,
                                                "source": "Paper 2030, Table 2", "pm_window": [1, 1]})
        self.assertIn("proper-motion track", no_pm.content)

    async def test_failed_count_says_why_and_not_to_repeat_it(self):
        from unittest import mock
        from scoop import gaia_status
        self.jobs.fail = "counting timed out after 300 s"
        st = {"diagnosis": ["heidelberg has 2 executing and 19 queued job(s) from this IP"]}
        with mock.patch.object(gaia_status, "status", return_value=st):
            failed = await self.call("plan_region", {"shape": "box", "name": "plane box", "ra": 196.0,
                                                     "dec": -63.5, "width_deg": 3})
        self.assertEqual(failed.status, "error")
        self.assertIn("19 queued", failed.content)
        self.assertIn("Galactic plane", failed.content)  # b ~ -0.7 deg there
        self.assertIn("Do not repeat", failed.content)

    async def test_large_regions_wait_for_the_go_ahead_and_are_not_counted(self):
        est = {"sources": 14_600_000, "low": 3_000_000, "high": 25_000_000, "area_deg2": 40.0}
        tools = {t.name: t for t in build_region_tools(jobs=self.jobs, resolver=resolver, confirmed=False,
                                                       estimate=lambda region: est)}
        counted = []
        self.jobs.count_plan = lambda plan_id: counted.append(plan_id)  # never awaited if all is well
        command = await tools["plan_region"].ainvoke({"type": "tool_call", "name": "plan_region", "id": "p1", "args": {
            "shape": "box", "name": "plane box", "ra": 196.0, "dec": -63.5, "width_deg": 6}})
        result = json.loads(command.update["messages"][0].content.rsplit("\nArtifact:", 1)[0])
        self.assertTrue(result["needs_confirmation"])
        self.assertEqual(result["gaia_estimate"]["sources"], 14_600_000)
        self.assertEqual(counted, [])
        refused = await tools["run_region"].ainvoke({"type": "tool_call", "name": "run_region", "id": "r1", "args": {
            "plan_id": result["plan_id"], "user_confirmed": True}})
        self.assertEqual(refused.status, "error")  # the hand-off did not carry the user's go-ahead
        self.assertEqual(self.jobs.started, [])

    async def test_gaia_status_tool_reports_findings(self):
        from unittest import mock
        from scoop import gaia_status
        st = {"servers": {}, "queues": {}, "aborted": ["7"], "diagnosis": ["Aborted 1 stale SCOOP job(s)"]}
        with mock.patch.object(gaia_status, "status", return_value=st) as called:
            out = await self.call("gaia_status", {"clean_up": True})
        called.assert_called_once_with(True)
        self.assertEqual(json.loads(out.content)["aborted"], ["7"])

    @unittest.skipUnless(build_region_tools and streams.library_dir(), "galstreams absent")
    async def test_stream_plan_proposes_stream_frame_cuts(self):
        found = json.loads((await self.call("find_stream", {"name": "gd1"})).content)
        names = [t["track_name"] for t in found["tracks"]]
        self.assertIn("GD-1-PB18", names)
        command = await self.call("plan_region", {"shape": "stream", "name": "GD-1 part", "track_name": "GD-1-PB18",
                                                  "phi1_range": [-40, -10], "width_deg": 2})
        result = json.loads(command.update["messages"][0].content.rsplit("\nArtifact:", 1)[0])
        self.assertEqual(result["cuts_are"], "proposed (not chosen by the user yet)")
        self.assertEqual(result["cuts"]["gmax"], 20.0)
        self.assertEqual(len(result["cuts"]["pm_window"]), 2)
        self.assertIn("stream frame", result["cut_frame"])
        self.assertEqual(result["region"]["phi1_range"], [-40.0, -10.0])
        stored = self.jobs.load_plan(result["plan_id"])["spec"]
        self.assertEqual(stored["cuts"], result["cuts"])  # what was counted is what would run
        self.assertIn("pm_track", stored)


@unittest.skipIf(numpy is None, "numpy absent")
class StreamFieldTests(unittest.TestCase):
    def test_band_fields_get_stream_coordinates_and_presets(self):
        from scoop.regions import Region
        data = viewer_data()
        t = data["target"]
        track = [[t["ra"] - 1 + 0.1 * k, t["dec"]] for k in range(21)]
        pm = [[0.0, -0.2]] * 21
        band = Region.from_spec({"kind": "band", "track": track, "width_deg": 0.5, "pm_track": pm})
        data["query_region"] = {**band.to_spec(), "cuts": {"gmax": 20}}
        field = field_from_viewer_data(data)
        gaia = field["tables"]["gaia"]
        self.assertIn("phi1", gaia["columns"])
        self.assertIn("dpm_phi2", gaia["columns"])
        self.assertEqual(gaia["units"]["pm_phi1"], "mas/yr")
        self.assertIn("Δμ (stream frame)", [p["label"] for p in gaia["presets"]])
        row = next(r for r in gaia["rows"] if "pm_phi1" in r)
        # Along an east-west track the stream frame is (nearly) RA/Dec: mu_phi1 ~ pmra.
        self.assertAlmostEqual(row["pm_phi1"], row["pmra"], delta=0.02 * (abs(row["pmra"]) + 1))
        self.assertEqual(field["query_region"]["cuts"], {"gmax": 20})

    @unittest.skipIf(build_region_tools is None, "optional chat dependencies absent")
    def test_region_coverage_uses_the_region(self):
        from scoop.chat.visual import _coverage
        from scoop.regions import Region
        box = Region.from_spec({"kind": "box", "ra": 150.0, "dec": 2.0, "width_deg": 1.0, "height_deg": 1.0})
        field = {"query_region": box.to_spec()}
        self.assertEqual(_coverage(field, 150.0, 2.0, 0.1)[0], "inside")
        self.assertEqual(_coverage(field, 150.45, 2.0, 0.1)[0], "partial")
        self.assertEqual(_coverage(field, 152.0, 2.0, 0.1)[0], "outside")


if __name__ == "__main__":
    unittest.main()
