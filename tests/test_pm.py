import json
import math
import unittest

try:
    import astropy  # noqa: F401
    from scoop.science import pm
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"astropy", "numpy"}:
        raise
    pm = None

try:
    from scoop.agents.pm_tools import build_pm_tools, field_epochs
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic", "astropy", "numpy"}:
        raise
    HAS_CHAT = False
else:
    HAS_CHAT = True

from scoop.science.fields import field_from_viewer_data
from tests.field_fixture import viewer_data


@unittest.skipIf(pm is None, "astropy absent")
class PlanningTests(unittest.TestCase):
    def test_two_equal_epochs(self):
        # sqrt(2) sigma / baseline
        self.assertAlmostEqual(pm.pm_error_from_epochs([(2004.0, 1.0), (2024.0, 1.0)]), math.sqrt(2) / 20)
        self.assertEqual(pm.pm_error_from_epochs([(2004.0, 1.0)]), math.inf)

    def test_more_epochs_and_gaia_help(self):
        two = pm.pm_error_from_epochs([(2004.0, 1.0), (2024.0, 1.0)])
        # An epoch in the middle adds no leverage on the slope; one beyond the span does.
        middle = pm.pm_error_from_epochs([(2004.0, 1.0), (2014.0, 1.0), (2024.0, 1.0)])
        self.assertAlmostEqual(middle, two)
        later = pm.pm_error_from_epochs([(2004.0, 1.0), (2024.0, 1.0), (2030.0, 1.0)])
        self.assertLess(later, two)
        self.assertAlmostEqual(pm.combine_errors(0.1, 0.1), 0.1 / math.sqrt(2))
        self.assertEqual(pm.combine_errors(0.1, None, math.inf), 0.1)

    def test_bulk_requirement_inverts_bulk_error(self):
        e = pm.star_error_for_bulk(0.005, 0.025, 200, floor=0.002)
        self.assertAlmostEqual(float(pm.bulk_pm_error(0.025, e, 200, 0.002)), 0.005, places=9)
        self.assertTrue(math.isnan(pm.star_error_for_bulk(0.001, 0.025, 200, floor=0.002)))  # below the floor

    def test_year_to_reach(self):
        existing = [(2004.0, 1.0)]
        year, err = pm.year_to_reach(existing, 1.0, 0.05, start_year=2026.0)
        self.assertAlmostEqual(year, 2004 + math.sqrt(2) / 0.05, places=3)  # 2032.28
        self.assertLessEqual(err, 0.05 + 1e-9)
        now, _ = pm.year_to_reach(existing, 1.0, 1.0, start_year=2026.0)
        self.assertEqual(now, 2026.0)  # already reachable now
        never, best = pm.year_to_reach(existing, 1.0, 1e-5, start_year=2026.0)
        self.assertIsNone(never)
        self.assertGreater(best, 1e-5)

    def test_reconcile(self):
        row = lambda c, x, g: {"citation": c, "pmra": x, "pmra_err": 0.01, "pmdec": 0.0, "pmdec_err": 0.01, "group": g}  # noqa: E731
        rows = [row("A", 0.10, "Gaia only"), row("B", 0.10, "HST only"), row("C", 0.10, "Gaia only"),
                row("D", 0.10, "HST + Gaia"), row("E", 0.20, "Gaia only")]
        r = pm.reconcile(rows)
        self.assertAlmostEqual(r["pmra"]["weighted_mean"], 0.12)
        self.assertEqual([o["citation"] for o in r["pmra"]["outliers_3sigma"]], ["E"])  # pull +8; the others -2
        self.assertEqual(r["by_method"], {"Gaia only": 3, "HST + Gaia": 1, "HST only": 1})
        self.assertAlmostEqual(r["pmdec"]["chi2"], 0.0)
        loose = pm.reconcile(rows, floor=0.1)
        self.assertLess(loose["pmra"]["chi2"], r["pmra"]["chi2"])
        self.assertTrue(r["notes"])


@unittest.skipIf(pm is None, "astropy absent")
class MemberFitTests(unittest.TestCase):
    def test_zero_dispersion_gives_an_upper_limit_and_a_known_mean(self):
        from scoop.science.members import fit_1d
        rows = [{"id": str(i), "v": x, "e": 1.0} for i, x in enumerate([-1.0, 1.0] * 50)]
        fit = fit_1d(rows, "v", "e")
        self.assertAlmostEqual(fit["mean"], 0.0)
        self.assertAlmostEqual(fit["mean_err"], 0.1)  # 1/sqrt(100) with no intrinsic spread
        self.assertEqual(fit["sigma_int"], 0.0)  # scatter equals the errors exactly
        self.assertEqual(fit["sigma_int_68"][0], 0.0)
        self.assertGreater(fit["sigma_int_upper_95"], 0.0)

    def test_dispersion_above_errors_is_recovered(self):
        from scoop.science.members import fit_1d
        # Values ±3 with errors 1: intrinsic sigma^2 = 9 - 1, as maximum likelihood gives.
        rows = [{"id": str(i), "v": x, "e": 1.0} for i, x in enumerate([-3.0, 3.0] * 200)]
        fit = fit_1d(rows, "v", "e", clip_sigma=None)
        self.assertAlmostEqual(fit["sigma_int"], math.sqrt(8), places=4)
        lo, hi = fit["sigma_int_68"]
        self.assertLess(lo, math.sqrt(8))
        self.assertGreater(hi, math.sqrt(8))


def synthetic_dwarf(seed=5, n_members=300, n_foreground=600):
    """Members: a tight proper-motion clump on a red-giant-branch-like sequence near
    the centre; foreground: broad proper motions, colours and parallaxes everywhere."""
    import random
    rng = random.Random(seed)
    rows = []
    for i in range(n_members):
        g = rng.uniform(17.0, 20.5)
        e = 0.05 + 0.15 * (g - 17)
        rows.append({"id": str(4295806720000 + i), "member": True, "G": g,
                     "bp_rp": 1.0 + 0.12 * (20.5 - g) + rng.gauss(0, 0.03), "r_ell": 2 * rng.random() ** 0.5,
                     "pmra": 0.04 + rng.gauss(0, 0.02) + rng.gauss(0, e), "pmdec": -0.19 + rng.gauss(0, 0.02) + rng.gauss(0, e),
                     "pmra_error": e, "pmdec_error": e, "parallax": 1 / 80 + rng.gauss(0, e), "parallax_error": e,
                     "clean": True})
    for i in range(n_foreground):
        g = rng.uniform(16.0, 21.0)
        e = 0.05 + 0.15 * (g - 16)
        rows.append({"id": str(5295806720000 + i), "member": False, "G": g, "bp_rp": rng.uniform(0.3, 2.8),
                     "r_ell": 2 * rng.random() ** 0.5, "pmra": rng.gauss(-2, 3) + rng.gauss(0, e),
                     "pmdec": rng.gauss(-3, 3) + rng.gauss(0, e), "pmra_error": e, "pmdec_error": e,
                     "parallax": abs(rng.gauss(0.4, 0.3)) + rng.gauss(0, e), "parallax_error": e, "clean": True})
    return rows


@unittest.skipIf(pm is None, "astropy absent")
class SelectionTests(unittest.TestCase):
    def test_cells_outline_with_holes_filled(self):
        from scoop.science.members import cells_to_polygons
        from scoop.science.fields import plane_polygon_contains
        ring = {(i, j) for i in range(3) for j in range(3)} - {(1, 1)}  # a 3x3 block with a hole
        polys = cells_to_polygons(ring | {(5, 0)}, 0.0, 0.1, 10.0, 0.5)
        self.assertEqual(len(polys), 2)
        self.assertEqual(sorted(map(len, polys)), [4, 4])  # collinear corners dropped
        self.assertTrue(plane_polygon_contains(polys[0], 0.15, 10.75))  # the hole is filled
        self.assertFalse(plane_polygon_contains(polys[0], 0.35, 10.75))

    def test_recovers_members_with_and_without_a_prior(self):
        from scoop.science.members import fit_pm, select_members
        rows = synthetic_dwarf()
        for prior in ((0.04, -0.19), None):
            sel = select_members(rows, distance_kpc=80.0, pm_prior=prior, sigma_pm=0.02, r_max=2.0)
            chosen = sel["members"]
            true = sum(r["member"] for r in chosen)
            self.assertGreater(true, 0.9 * 300)
            self.assertLess(len(chosen) - true, 0.03 * len(chosen))
            self.assertTrue(sel["cmd_polygons"])
            fit = fit_pm(chosen)
            self.assertLess(abs(fit["pmra"] - 0.04), 4 * fit["pmra_err"])
            self.assertLess(abs(fit["pmdec"] + 0.19), 4 * fit["pmdec_err"])
        self.assertEqual(sel["pm_start"]["from"], "densest proper motion of the central stars")


def lvdb_tables():
    from astropy.table import Table
    comb = Table(rows=[{"key": "mini_1", "name": "Mini", "ra": 260.05, "dec": 57.92, "distance": 80.0,
                        "distance_em": 1.0, "distance_ep": 1.0, "vlos_sigma": 9.5, "vlos_sigma_em": 0.3,
                        "vlos_sigma_ep": 0.3, "pmra": 0.04, "pmra_em": 0.01, "pmra_ep": 0.01, "pmdec": -0.19,
                        "pmdec_em": 0.01, "pmdec_ep": 0.01, "vlos_systemic": -290.0, "vlos_systemic_em": 1.0,
                        "vlos_systemic_ep": 1.0, "rhalf": 9.6, "ref_vlos": "X", "ref_proper_motion": "Y"}])
    pms = Table(rows=[{"key": "mini_1", "ref": "2020ApJ...1A", "ref_cite": "", "pmra": 0.04, "pmra_em": 0.01,
                       "pmra_ep": 0.01, "pmdec": -0.19, "pmdec_em": 0.01, "pmdec_ep": 0.01, "pmra_pmdec_corr": 0.0,
                       "method": "GAIA_EDR3", "citation": "A et al. 2020", "comments": "EDR3"},
                      {"key": "mini_1", "ref": "2021ApJ...2B", "ref_cite": "", "pmra": 0.05, "pmra_em": 0.02,
                       "pmra_ep": 0.02, "pmdec": -0.18, "pmdec_em": 0.02, "pmdec_ep": 0.02, "pmra_pmdec_corr": 0.0,
                       "method": "HST", "citation": "B et al. 2021", "comments": "~8 year baseline"}])
    return comb, pms


@unittest.skipUnless(HAS_CHAT and pm is not None, "optional chat dependencies or astropy absent")
class PmToolTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.field = field_from_viewer_data(viewer_data())
        self.tools = {t.name: t for t in build_pm_tools(field=self.field, lvdb_cache=None,
                                                        loader=lambda cache: lvdb_tables())}

    async def run_tool(self, name, args):
        return await self.tools[name].ainvoke({"type": "tool_call", "name": name, "id": "x", "args": args})

    def test_epochs_come_from_every_footprint_table(self):
        epochs = field_epochs(self.field, 260.01, 57.91)  # inside both fixture footprints
        self.assertEqual([(e["id"], e["year"]) for e in epochs], [("j8x101", 2004.6), ("jw01234", 2023.3)])

    async def test_new_epoch(self):
        result = json.loads((await self.run_tool("pm_new_epoch", {
            "year": 2027.0, "position_error_mas": 0.5, "existing_position_error_mas": 0.5,
            "ra": 260.01, "dec": 57.91, "n_stars": 100})).content)
        self.assertEqual(result["n_existing_visits"], 2)
        self.assertAlmostEqual(result["per_star_pm_error_now_mas_yr"], math.sqrt(2) * 0.5 / (2023.3 - 2004.6))
        self.assertLess(result["per_star_pm_error_with_new_epoch_mas_yr"], result["per_star_pm_error_now_mas_yr"])
        self.assertIn("sample", result["with_new_epoch"])
        self.assertTrue(any("not modelled" in a for a in result["assumptions"]))

    async def test_wait_time_and_unreachable_goals(self):
        ok = json.loads((await self.run_tool("pm_wait_time", {
            "goal": "per_star", "target": 0.01, "position_error_mas": 0.5, "existing_position_error_mas": 0.5,
            "ra": 260.01, "dec": 57.91})).content)
        self.assertTrue(ok["reachable"])
        self.assertGreater(ok["earliest_new_epoch_year"], 2026)
        bad = json.loads((await self.run_tool("pm_wait_time", {
            "goal": "dispersion", "target": 0.01, "position_error_mas": 0.5, "existing_position_error_mas": 0.5,
            "ra": 260.01, "dec": 57.91, "n_stars": 10})).content)
        self.assertFalse(bad["reachable"])
        self.assertIn("too few stars", bad["why"])

    async def test_published_and_expectations(self):
        pub = json.loads((await self.run_tool("pm_published", {})).content)
        self.assertEqual(len(pub["measurements"]), 2)
        self.assertEqual(pub["agreement"]["by_method"], {"Gaia only": 1, "HST only": 1})
        exp = json.loads((await self.run_tool("pm_expectations", {"n_stars": 100})).content)
        self.assertAlmostEqual(exp["internal_dispersion_uas_yr"][0], 9.5 / (pm.K * 80.0) * 1e3, delta=0.3)
        self.assertEqual(exp["best_published"]["citation"], "A et al. 2020")

    def test_exposures_group_into_visits_and_instruments_filter(self):
        from scoop.agents.pm_tools import visits
        exposures = [{"instrument": "CFHT MegaPrime", "year": 2017.50, "id": "a", "table": "c"},
                     {"instrument": "CFHT MegaPrime", "year": 2017.51, "id": "b", "table": "c"},  # 4 days later
                     {"instrument": "CFHT MegaPrime", "year": 2019.00, "id": "c", "table": "c"},
                     {"instrument": "HST ACS/WFC", "year": 2004.6, "id": "h", "table": "o"}]
        v = visits(exposures)
        self.assertEqual([(x["instrument"], x["exposures"]) for x in v],
                         [("HST ACS/WFC", 1), ("CFHT MegaPrime", 2), ("CFHT MegaPrime", 1)])
        self.assertEqual([x["instrument"] for x in visits(exposures, ["HST"])], ["HST ACS/WFC"])

    async def test_per_instrument_errors(self):
        args = {"year": 2027.0, "position_error_mas": 0.5, "existing_position_error_mas": 0.5,
                "ra": 260.01, "dec": 57.91}
        equal = json.loads((await self.run_tool("pm_new_epoch", args)).content)
        worse = json.loads((await self.run_tool("pm_new_epoch", {
            **args, "position_errors_by_instrument": {"ACS": 5.0}})).content)
        self.assertGreater(worse["per_star_pm_error_now_mas_yr"], equal["per_star_pm_error_now_mas_yr"])
        self.assertEqual({g["instrument"]: g["position_error_mas"] for g in worse["existing_by_instrument"]},
                         {"HST ACS/WFC": 5.0, "JWST NIRCAM/IMAGE": 0.5})

    async def test_fit_members(self):
        import random
        rng = random.Random(3)
        gaia = self.field["tables"]["gaia"]
        template = gaia["rows"][0]
        gaia["rows"] = [{**template, "id": str(4295806720 + i), "pmra": 0.04 + rng.gauss(0, 0.05) + rng.gauss(0, 0.2),
                         "pmdec": -0.19 + rng.gauss(0, 0.05) + rng.gauss(0, 0.2), "pmra_error": 0.2,
                         "pmdec_error": 0.2, "clean": i % 10 != 0, "rv": -290 + rng.gauss(0, 9.5) + rng.gauss(0, 2),
                         "rv_err": 2.0} for i in range(400)]
        gaia["rows"].append({**template, "id": "4295806720999", "pmra": 8.0, "pmdec": 8.0, "pmra_error": 0.2,
                             "pmdec_error": 0.2, "clean": True})  # a foreground star
        result = json.loads((await self.run_tool("fit_members", {
            "filter": [{"column": "G", "op": "<", "value": 99}], "floor_mas_yr": 0.01})).content)
        fit = result["proper_motion"]
        self.assertEqual(result["dropped"]["not clean"], 40)
        self.assertEqual(fit["clipped_ids"], ["4295806720999"])
        self.assertLess(abs(fit["pmra"] - 0.04), 4 * fit["pmra_err"])
        self.assertAlmostEqual(fit["pmra_err"], math.hypot(fit["pmra_err_stat"], 0.01))
        self.assertLess(fit["sigma_int_68"][0], 0.07)
        self.assertGreater(fit["sigma_int_68"][1], 0.03)
        self.assertAlmostEqual(result["line_of_sight_velocity_kms"]["mean"], -290, delta=3)
        self.assertEqual(result["line_of_sight_velocity_kms"]["lvdb"]["vlos_systemic"], -290.0)
        self.assertIn("lvdb_adopted", result["compared_with"])
        self.assertIn("published_weighted_mean", result["compared_with"])
        self.assertAlmostEqual(result["at_distance"]["sigma_int_kms"], pm.K * 80.0 * fit["sigma_int"])
        self.assertTrue(any("no pmra_pmdec_corr" in n for n in result["notes"]))
        empty = await self.run_tool("fit_members", {"filter": [{"column": "G", "op": ">", "value": 99}]})
        self.assertEqual(empty.status, "error")

    async def test_auto_members_selects_fits_and_shows_a_layer(self):
        self.field["tables"]["gaia"]["rows"] = synthetic_dwarf()
        self.field["n_rhalf"] = 2.0
        literature = [{"citation": "Z et al. 2030", "pmra": 0.05, "pmra_err": 0.01, "pmdec": -0.2, "pmdec_err": 0.01}]
        command = await self.tools["auto_members"].ainvoke({"type": "tool_call", "name": "auto_members", "id": "c1",
                                                            "args": {"literature": literature}})
        action = command.update["artifacts"]["c1"]["actions"][0]
        self.assertEqual((action["type"], action["id"]), ("layer.add", "agent:members:gaia"))
        ids = next(c["value"] for c in action["filter"] if c["op"] == "in")
        self.assertTrue(all(isinstance(i, str) for i in ids))  # Gaia IDs stay exact strings
        self.assertTrue(any(c["op"] == "lasso" for c in action["filter"]))
        from scoop.science.fields import matches, validate_filter
        validate_filter(action["filter"], {"id", *self.field["tables"]["gaia"]["columns"]})
        shown = [r for r in self.field["tables"]["gaia"]["rows"] if matches(r, action["filter"])]
        self.assertEqual(len(shown), len(ids))  # the layer is exactly the members
        result = json.loads(command.update["messages"][0].content.rsplit("\nArtifact:", 1)[0])
        self.assertEqual(result["priors"]["distance"], {"value": 80.0, "from": "LVDB", "unit": "kpc"})
        self.assertIn("LVDB", result["priors"]["pm"]["from"])
        self.assertIn("Z et al. 2030", result["compared_with"])
        self.assertNotIn(ids[0], command.update["messages"][0].content)  # no ID list for the model to copy
        # A second run updates the layer instead of adding another.
        again = await self.tools["auto_members"].ainvoke({"type": "tool_call", "name": "auto_members", "id": "c2",
                                                          "args": {"use_literature_pm": False, "show": True}})
        self.assertEqual(again.update["artifacts"]["c2"]["actions"][0]["type"], "layer.update")

    async def test_map_tools_can_update_the_layer_auto_members_added(self):
        from scoop.agents.visual import build_map_tools
        self.field["tables"]["gaia"]["rows"] = synthetic_dwarf()
        known: dict = {}
        tools = {t.name: t for t in build_pm_tools(field=self.field, lvdb_cache=None, known_layers=known,
                                                   loader=lambda cache: lvdb_tables())}
        update_map = next(t for t in build_map_tools(field=self.field, known_layers=known) if t.name == "update_map")
        await tools["auto_members"].ainvoke({"type": "tool_call", "name": "auto_members", "id": "c1", "args": {}})
        result = await update_map.ainvoke({"type": "tool_call", "name": "update_map", "id": "c2", "args": {
            "actions": [{"type": "layer.update", "layerId": "agent:members:gaia", "visible": True}]}})
        self.assertIn("agent:members:gaia", result.update["artifacts"]["c2"]["actions"][0]["layerId"])

    def test_prompt_names_every_pm_tool(self):
        from scoop.agents.pm_agent import PM_AGENT_PROMPT  # the proper-motion agent calls them
        for name in self.tools:
            self.assertIn(name, PM_AGENT_PROMPT)


if __name__ == "__main__":
    unittest.main()
