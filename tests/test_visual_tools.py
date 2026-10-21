import json
import unittest

try:
    from scoop.chat.visual import build_map_tools
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic"}:
        raise
    HAS_DEPS = False
else:
    HAS_DEPS = True

from scoop.fields import default_layers, field_from_viewer_data
from tests.field_fixture import viewer_data


@unittest.skipUnless(HAS_DEPS, "optional chat dependencies absent")
class MapToolsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.field = field_from_viewer_data(viewer_data())
        self.layers = [{"id": layer["id"], "table": layer["table"], "origin": "pipeline"}
                       for layer in default_layers(self.field)]
        self.layers.append({"id": "agent:bright", "table": "gaia", "origin": "agent"})
        self.adql, self.simbad_down, self.started, self.fetched = [], False, [], []

    def tools(self, field=True):
        async def resolver(name):
            return {"ra": 260.04, "dec": 57.92, "name": "Mini", "otype": "dSph",
                    "resolver": "Simbad"} if name == "Mini" else None

        async def simbad(adql):
            self.adql.append(adql)
            if self.simbad_down:
                raise ConnectionError("timeout")
            if "has_ref" in adql:
                return [{"bibcode": "2020ApJ...1A", "ref_year": 2020, "title": "A star"}]
            return [{"oid": 7, "main_id": "V* XX Dra", "ra": 260.04, "dec": 57.9, "otype": "RR*",
                     "nbref": 12, "dist": 0.0002}]

        class Jobs:
            def start(inner, key, n_rhalf):
                self.started.append((key, n_rhalf))
                return type("Job", (), {"id": "job42"})()

            def start_cone(inner, ra, dec, radius, name):
                self.started.append((ra, dec, radius, name))
                return type("Job", (), {"id": "job43", "key": "mini_5_arcmin"})()

        class Index:
            def has(inner, key):
                return key in ("mini_1", "leo_1")

            def search(inner, text, limit=10):
                return [{"key": "leo_1", "name": "Leo I"}] if "leo" in text.lower() else []

        async def fetch_image(ra, dec, fov, survey):
            self.fetched.append((ra, dec, round(fov, 6), survey))
            return b"\xff\xd8jpeg"

        tools = build_map_tools(field=self.field if field else None, layers=self.layers,
                                field_keys=["mini_1"], resolver=resolver, simbad=simbad,
                                jobs=Jobs(), lvdb=Index(), fetch_image=fetch_image)
        return {tool.name: tool for tool in tools}

    async def call(self, name, args, field=True):
        return await self.tools(field)[name].ainvoke(
            {"name": name, "args": args, "id": "call-1", "type": "tool_call"})

    async def test_valid_actions_are_requested_with_match_counts(self):
        actions = [
            {"type": "layer.add", "id": "agent:recent", "table": "observations", "label": "After 2020",
             "filter": [{"column": "year", "op": ">", "value": 2020}], "style": {"color": "#ffd84d"}},
            {"type": "layer.update", "layerId": "gaia:rrlyrae", "visible": True},
            {"type": "layer.remove", "layerId": "agent:bright"},
            {"type": "focus", "ra": 260.0, "dec": 57.9, "fov": 0.5},
            {"type": "focus.rows", "table": "gaia", "ids": ["1001", "1004"]},
            {"type": "highlight", "table": "gaia", "ids": ["1004"], "reason": "Gaia-CRF3 QSO"},
            {"type": "annotate", "shape": "label", "ra": 260.04, "dec": 57.9, "text": "QSO",
             "table": "gaia", "rowId": "1004"},
            {"type": "annotations.clear"},
            {"type": "annotate", "shape": "polygon", "ra": 260.02, "dec": 57.9, "text": "box",
             "points": [[260.0, 57.89], [260.04, 57.89], [260.04, 57.91], [260.0, 57.91]]},
            {"type": "select", "table": "milliquas", "id": "J1720+5757"},
            {"type": "plot.show", "table": "gaia", "x": "bp_rp", "y": "G", "invert_y": True},
            {"type": "plot.hide"},
        ]
        result = await self.call("update_map", {"actions": actions})
        payload = result.update["artifacts"]["call-1"]
        self.assertEqual(payload["kind"], "map_actions")
        self.assertEqual([a["type"] for a in payload["actions"]], [a["type"] for a in actions])
        summary = result.update["messages"][0].content
        self.assertIn("'agent:recent': 1 rows match", summary)
        near = await self.call("update_map", {"actions": [
            {"type": "layer.add", "id": "agent:near", "table": "gaia", "label": "Within 1.5′",
             "filter": [{"column": "position", "op": "within",
                         "value": {"ra": 260.02, "dec": 57.9, "radius": 0.006}}]}]})
        self.assertIn("'agent:near': 3 rows match", near.update["messages"][0].content)
        self.assertIn("Not displayed until the browser acknowledges", summary)

    async def test_common_shape_names_are_understood(self):
        result = await self.call("update_map", {"actions": [
            {"type": "layer.add", "id": "agent:qso", "table": "gaia", "label": "QSOs", "style": {"shape": "Diamond"}}]})
        self.assertEqual(result.update["artifacts"]["call-1"]["actions"][0]["style"]["shape"], "rhomb")
        with self.assertRaises(ValueError):
            await self.call("update_map", {"actions": [
                {"type": "layer.add", "id": "agent:q", "table": "gaia", "label": "Q", "style": {"shape": "hexagon"}}]})

    async def test_actions_naming_unknown_things_are_correctable_errors(self):
        for action, expected in (
            ({"type": "highlight", "table": "gaia", "ids": ["9999"]}, "Unknown gaia ids"),
            ({"type": "highlight", "table": "stars", "ids": []}, "Unknown table"),
            ({"type": "layer.update", "layerId": "nope", "visible": True}, "Unknown layer"),
            ({"type": "layer.remove", "layerId": "gaia:stars"}, "can be hidden"),
            ({"type": "layer.add", "id": "gaia:stars", "table": "gaia", "label": "x"}, "already exists"),
            ({"type": "layer.add", "id": "x", "table": "gaia", "label": "x",
              "filter": [{"column": "mag", "op": "<", "value": 19}]}, "unknown column 'mag'"),
            ({"type": "annotate", "shape": "label", "ra": 1.0, "dec": 1.0, "text": "x", "rowId": "1001"},
             "needs both table and rowId"),
            ({"type": "plot.show", "table": "gaia", "x": "clean"}, "'clean' is not a numeric column"),
            ({"type": "annotate", "shape": "polygon", "ra": 1.0, "dec": 1.0, "text": "x"}, "needs points"),
            ({"type": "annotate", "shape": "circle", "ra": 1.0, "dec": 1.0, "text": "x"}, "needs a radius"),
            ({"type": "plot.show", "table": "gaia", "x": "G", "y": "mag"}, "'mag' is not a numeric column"),
        ):
            with self.subTest(action=action):
                result = await self.call("update_map", {"actions": [action]})
                self.assertEqual(result.status, "error")
                self.assertIn(expected, result.content)

    async def test_a_bad_action_sends_nothing(self):
        result = await self.call("update_map", {"actions": [
            {"type": "focus", "ra": 1.0, "dec": 1.0, "fov": 1.0},
            {"type": "select", "table": "gaia", "id": "invented"}]})
        self.assertEqual(result.status, "error")
        self.assertIn("No actions sent", result.content)

    async def test_a_rejected_layer_can_be_added_on_retry(self):
        update_map = self.tools()["update_map"]
        add = {"type": "layer.add", "id": "agent:new", "table": "gaia", "label": "New"}
        calls = ([add, {"type": "select", "table": "gaia", "id": "invented"}], [add])
        results = [await update_map.ainvoke({"name": "update_map", "args": {"actions": actions},
                                             "id": f"call-{i}", "type": "tool_call"})
                   for i, actions in enumerate(calls)]
        self.assertEqual(results[0].status, "error")
        self.assertNotIn("already exists", str(results[1]))  # sent, not rejected
        self.assertEqual(type(results[1]).__name__, "Command")

    async def test_malformed_and_executable_actions_are_rejected(self):
        for action in (
            {"type": "eval", "code": "alert(1)"},
            {"type": "focus", "ra": 360.0, "dec": 0.0, "fov": 1.0},
            {"type": "focus", "ra": True, "dec": 0.0, "fov": 1.0},
            {"type": "annotate", "shape": "circle", "ra": 0.0, "dec": 0.0, "radius": -1.0, "text": "c"},
            {"type": "annotate", "shape": "label", "ra": 0.0, "dec": 0.0, "text": " "},
            {"type": "layer.add", "id": "x", "table": "gaia", "label": "x", "style": {"color": "red"}},
        ):
            with self.subTest(action=action), self.assertRaises(ValueError):
                await self.call("update_map", {"actions": [action]})

    async def test_describe_field_summarizes_and_finds_rows(self):
        overview = json.loads((await self.call("describe_field", {})).content)
        self.assertEqual(overview["tables"]["gaia"]["rows"], 5)
        self.assertIn("decisions", overview)
        rows = json.loads((await self.call("describe_field", {
            "table": "gaia", "filter": [{"column": "G", "op": "<", "value": 19.5}]})).content)
        self.assertEqual(rows["matching"], 2)
        self.assertEqual({r["id"] for r in rows["rows"]}, {"1001", "1004"})

    async def region(self, **args):
        result = await self.call("region", args)
        return result if result.status == "error" else json.loads(result.content)

    async def test_region_around_a_row_lists_nearest_rows_and_covering_footprints(self):
        report = await self.region(table="gaia", row_id="1004", radius_arcmin=0.5)
        self.assertEqual(report["center"]["from"], "gaia 1004")
        gaia = report["tables"]["gaia"]
        self.assertEqual(gaia["count"], 3)
        self.assertEqual([r["id"] for r in gaia["nearest"]][0], "1004")
        self.assertEqual(gaia["nearest"][0]["sep_arcsec"], 0)
        self.assertNotIn("polygon", json.dumps(report["tables"]))
        self.assertEqual(report["layer_filter"][0]["op"], "within")

    async def test_region_by_name_reports_footprints_and_coverage(self):
        inside = await self.region(name="Mini", radius_arcmin=0.5)
        self.assertEqual(inside["center"]["from"], "Simbad: Mini (dSph)")
        self.assertEqual(inside["coverage"], "inside")
        footprints = inside["footprints"]["observations"]
        self.assertEqual(footprints["containing_center"], ["j8x101"])
        self.assertEqual(footprints["overlapping"], 2)
        self.assertEqual(footprints["overlapping_by"], {"HST ACS/WFC": 1, "JWST NIRCAM/IMAGE": 1})
        partial = await self.region(ra=260.04, dec=57.92, radius_arcmin=2)
        self.assertEqual(partial["coverage"], "partial")
        outside = await self.region(ra=10.0, dec=0.0, radius_arcmin=1)
        self.assertEqual(outside["coverage"], "outside")
        self.assertEqual(outside["tables"]["gaia"]["count"], 0)
        self.assertIn("rather than reporting zero", outside["coverage_note"])

    async def test_region_needs_one_resolvable_center(self):
        for args, expected in (({"radius_arcmin": 1}, "exactly one center"),
                               ({"radius_arcmin": 1, "ra": 1.0, "name": "Mini"}, "exactly one center"),
                               ({"radius_arcmin": 1, "ra": 1.0}, "both ra and dec"),
                               ({"radius_arcmin": 1, "name": "Nowhere"}, "could not resolve"),
                               ({"radius_arcmin": 1, "table": "gaia", "row_id": "9"}, "No positioned row"),
                               ({"radius_arcmin": 1, "ra": 1.0, "dec": 1.0, "tables": ["stars"]}, "Unknown tables")):
            with self.subTest(args=args):
                result = await self.region(**args)
                self.assertEqual(result.status, "error")
                self.assertIn(expected, result.content)

    async def test_match_points_inside_footprints(self):
        data = viewer_data()
        # Inside both footprints, inside HST only, and outside both.
        decs = {"1001": 57.905, "1004": 57.92, "1002": 57.95, "1003": 57.85, "1005": 57.85}
        for rows in data["gaia"].values():
            for row in rows:
                row["dec"] = decs[row["data"]["Gaia DR3"]]
        self.field = field_from_viewer_data(data)
        report = json.loads((await self.call("match", {"points": "gaia"})).content)
        self.assertEqual(report["footprints"], "observations")
        self.assertEqual((report["points_considered"], report["footprints_considered"], report["matched"]), (5, 2, 2))
        self.assertEqual({r["id"]: r["covering"] for r in report["rows"]},
                         {"1001": ["j8x101", "jw01234"], "1004": ["j8x101"]})
        self.assertEqual(report["rows"][0]["baseline_yr"], 18.7)
        self.assertEqual(report["rows"][1]["baseline_yr"], 0.0)
        self.assertEqual(report["points_by_instrument"], {"HST ACS/WFC": 2, "JWST NIRCAM/IMAGE": 1})
        self.assertEqual(report["layer_filter"][0]["value"], ["1001", "1004"])

        qsos = json.loads((await self.call("match", {
            "points": "gaia", "points_filter": [{"column": "quaia", "op": "==", "value": True}],
            "footprints_filter": [{"column": "tel", "op": "==", "value": "JWST"}]})).content)
        self.assertEqual((qsos["points_considered"], qsos["footprints_considered"], qsos["matched"]), (1, 1, 0))

        for args, expected in (({"points": "stars"}, "Unknown table"),
                               ({"points": "gaia", "footprints": "gaia"}, "no footprints"),
                               ({"points": "gaia", "points_filter": [{"column": "mag", "op": "<", "value": 1}]},
                                "Invalid filter on gaia")):
            result = await self.call("match", args)
            self.assertEqual(result.status, "error")
            self.assertIn(expected, result.content)

    async def test_summarize_filtered_columns_with_units(self):
        result = json.loads((await self.call("summarize", {
            "table": "gaia", "columns": ["G", "clean"],
            "filter": [{"column": "star", "op": "==", "value": True}]})).content)
        self.assertEqual(result["rows"], 2)
        g, clean = result["columns"]
        self.assertEqual((g["median"], g["unit"]), (19.2, "mag"))
        self.assertEqual((clean["true"], clean["false"]), (1, 1))
        rv = json.loads((await self.call("summarize", {
            "table": "spectra", "columns": ["rv"], "error_column": "rv_err"})).content)["columns"][0]
        self.assertEqual((rv["n"], rv["missing"]), (1, 1))
        self.assertEqual(rv["weighted_mean"]["value"], -291.5)
        for args, expected in (({"table": "stars", "columns": ["G"]}, "Unknown table"),
                               ({"table": "gaia", "columns": ["mag"]}, "Unknown columns")):
            bad = await self.call("summarize", args)
            self.assertEqual(bad.status, "error")
            self.assertIn(expected, bad.content)

    async def test_lookup_reports_simbad_objects_and_references(self):
        found = json.loads((await self.call("lookup", {"table": "gaia", "row_id": "1005"})).content)
        self.assertEqual(found["center"], {"ra": 260.05, "dec": 57.9, "from": "gaia 1005"})
        self.assertEqual(found["objects"][0]["main_id"], "V* XX Dra")
        self.assertEqual(found["objects"][0]["sep_arcsec"], 0.72)
        self.assertEqual(found["references"][0]["bibcode"], "2020ApJ...1A")
        self.assertIn("CIRCLE('ICRS', 260.05, 57.9, 0.00138", self.adql[0])
        # No field is needed for a position or a name.
        by_coords = json.loads((await self.call("lookup", {"ra": 10.0, "dec": 0.0, "papers": 0}, field=False)).content)
        self.assertNotIn("references", by_coords)
        self.simbad_down = True
        down = await self.call("lookup", {"name": "Mini"})
        self.assertEqual(down.status, "error")
        self.assertIn("don't guess", down.content)

    async def test_overlapping_footprints_with_baselines(self):
        report = json.loads((await self.call("overlaps", {
            "filter_a": [{"column": "tel", "op": "==", "value": "HST"}],
            "filter_b": [{"column": "tel", "op": "==", "value": "JWST"}]})).content)
        self.assertEqual((report["table"], report["footprints_a"], report["footprints_b"], report["pairs"]),
                         ("observations", 1, 1, 1))
        pair = report["longest"][0]
        self.assertEqual((pair["a"], pair["b"], pair["years"]), ("j8x101", "jw01234", 18.7))
        self.assertEqual(report["by_instruments"], {"HST ACS/WFC ↔ JWST NIRCAM/IMAGE":
                                                    {"pairs": 1, "max_years": 18.7, "longest": ["j8x101", "jw01234"]}})
        self.assertEqual(report["layer_filter"][0]["value"], ["j8x101", "jw01234"])
        none = json.loads((await self.call("overlaps", {"min_years": 20})).content)
        self.assertEqual(none["pairs"], 0)
        bad = await self.call("overlaps", {"table": "gaia"})
        self.assertEqual(bad.status, "error")
        self.field["tables"] = {"gaia": self.field["tables"]["gaia"]}
        none_loaded = await self.call("overlaps", {})
        self.assertEqual((none_loaded.status, none_loaded.content), ("error", "This field has no footprint tables."))

    async def test_run_cone_resolves_a_name_and_starts_a_job(self):
        started = await self.call("run_cone", {"name": "Mini", "radius_arcmin": 5})
        self.assertEqual(started.update["artifacts"]["call-1"],
                         {"kind": "field_job", "job": "job43", "key": "mini_5_arcmin"})
        self.assertEqual(self.started, [(260.04, 57.92, 5.0, "Mini 5 arcmin")])
        self.assertIn("Simbad: Mini (dSph)", started.update["messages"][0].content)
        for args in ({"radius_arcmin": 5}, {"name": "Nowhere", "radius_arcmin": 5}):
            self.assertEqual((await self.call("run_cone", args)).status, "error")
        with self.assertRaises(ValueError):
            await self.call("run_cone", {"ra": 1.0, "dec": 1.0, "radius_arcmin": 90})

    async def test_sky_image_returns_the_picture_as_an_artifact(self):
        result = await self.call("sky_image", {"table": "gaia", "row_id": "1004", "fov_arcmin": 3,
                                               "survey": "CDS/P/2MASS/color"})
        self.assertEqual(self.fetched, [(260.04, 57.9, 0.05, "CDS/P/2MASS/color")])
        self.assertIn("2MASS near-infrared", result.content)
        self.assertEqual(result.artifact["image"], "data:image/jpeg;base64,/9hqcGVn")
        with self.assertRaises(ValueError):  # not an allowed survey
            await self.call("sky_image", {"ra": 1.0, "dec": 1.0, "fov_arcmin": 3, "survey": "http://x"})
        none = await self.call("sky_image", {"fov_arcmin": 3})
        self.assertEqual(none.status, "error")

    async def test_run_field_starts_a_background_job(self):
        started = await self.call("run_field", {"key": "leo_1", "n_rhalf": 3})
        self.assertEqual(started.update["artifacts"]["call-1"], {"kind": "field_job", "job": "job42", "key": "leo_1"})
        self.assertIn("not loaded yet", started.update["messages"][0].content)
        self.assertEqual(self.started, [("leo_1", 3.0)])
        for args, expected in (({"key": "mini_1"}, "already exists"), ({"key": "leo_9"}, "Close: ['leo_1']")):
            result = await self.call("run_field", args)
            self.assertEqual(result.status, "error")
            self.assertIn(expected, result.content)
        found = json.loads((await self.call("find_lvdb", {"name": "Leo"})).content)
        self.assertEqual(found["matches"][0]["key"], "leo_1")

    async def test_a_drawn_box_filter_works_in_every_tool(self):
        # Regression: the user drew a box; its `inside` filter was rejected by the
        # tool argument model, and the agent retried the same call until the step limit.
        box = {"column": "position", "op": "inside",
               "value": {"polygon": [[260.005, 57.895], [260.035, 57.895], [260.035, 57.905], [260.005, 57.905]]}}
        described = json.loads((await self.call("describe_field", {"table": "gaia", "filter": [box]})).content)
        self.assertEqual({r["id"] for r in described["rows"]}, {"1001", "1002", "1003"})
        stats = json.loads((await self.call("summarize", {"table": "gaia", "columns": ["G"], "filter": [box]})).content)
        self.assertEqual(stats["rows"], 3)
        layer = await self.call("update_map", {"actions": [
            {"type": "layer.add", "id": "agent:box", "table": "gaia", "label": "In the box", "filter": [box]}]})
        self.assertIn("3 rows match", layer.update["messages"][0].content)
        # A malformed box is rejected by the typed schema (the graph turns this into a
        # correctable error message for the model).
        with self.assertRaises(ValueError):
            await self.call("describe_field", {"table": "gaia", "filter": [
                {"column": "position", "op": "inside", "value": {"polygon": [[1.0, 2.0]]}}]})
        # The circle shape the model kept getting wrong is now in the schema, and works.
        near = json.loads((await self.call("describe_field", {"table": "gaia", "filter": [
            {"column": "position", "op": "within", "value": {"ra": 260.02, "dec": 57.9, "radius": 0.006}}]})).content)
        self.assertEqual(near["matching"], 3)

    async def test_without_a_field_the_agent_is_told_what_can_be_loaded(self):
        result = await self.call("describe_field", {}, field=False)
        self.assertIn("mini_1", result.content)
        loaded = await self.call("load_field", {"key": "mini_1"}, field=False)
        self.assertEqual(loaded.update["artifacts"]["call-1"], {"kind": "field_load", "key": "mini_1"})
        missing = await self.call("load_field", {"key": "draco_9"}, field=False)
        self.assertEqual(missing.status, "error")


if __name__ == "__main__":
    unittest.main()
