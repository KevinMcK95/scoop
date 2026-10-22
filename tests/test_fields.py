import json
import tempfile
import unittest
from pathlib import Path

from scoop.science.fields import FieldStore, column_stats, default_layers, field_from_viewer_data, matches, table_summary, validate_filter
from tests.field_fixture import viewer_data

CASES = json.loads((Path(__file__).parent / "fixtures" / "filter_cases.json").read_text())


class FilterTests(unittest.TestCase):
    def test_shared_cases(self):
        # web/src/lib/field/filter.test.ts runs the same cases.
        for case in CASES["cases"]:
            with self.subTest(filter=case["filter"]):
                ids = [row["id"] for row in CASES["rows"] if matches(row, case["filter"])]
                self.assertEqual(ids, case["ids"])

    def test_validation_names_the_problem(self):
        columns = {"G", "tel"}
        with self.assertRaisesRegex(ValueError, "unknown column 'mag'"):
            validate_filter([{"column": "mag", "op": "<", "value": 1}], columns)
        with self.assertRaisesRegex(ValueError, "unknown op"):
            validate_filter([{"column": "G", "op": "~", "value": 1}], columns)
        with self.assertRaisesRegex(ValueError, "needs a list"):
            validate_filter([{"column": "tel", "op": "in", "value": "HST"}], columns)


class StatsTests(unittest.TestCase):
    def test_numbers_are_robust_and_missing_values_counted(self):
        rows = [{"x": v} for v in (1, 2, 3, 4, 100)] + [{"x": None}, {}]
        stats = column_stats(rows, "x", bins=4)
        self.assertEqual((stats["n"], stats["missing"], stats["type"]), (5, 2, "number"))
        self.assertEqual((stats["median"], stats["mad"], stats["mean"]), (3, 1, 22))
        self.assertAlmostEqual(stats["sigma_mad"], 1.4826)
        self.assertAlmostEqual(stats["percentiles"]["16"], 1.64)
        self.assertEqual(stats["histogram"]["counts"], [4, 0, 0, 1])
        self.assertEqual(stats["histogram"]["edges"][-1], 100)

    def test_weighted_mean_uses_errors(self):
        rows = [{"rv": -290.0, "e": 1.0}, {"rv": -300.0, "e": 2.0}, {"rv": -295.0, "e": None}]
        weighted = column_stats(rows, "rv", error_column="e")["weighted_mean"]
        self.assertAlmostEqual(weighted["value"], -292.0)
        self.assertAlmostEqual(weighted["error"], (1 / 1.25) ** 0.5)
        self.assertEqual(weighted["n"], 2)
        self.assertNotIn("warning", weighted)
        contaminated = [{"rv": -290.0 + i % 3, "e": 2.0} for i in range(20)] + [{"rv": -20.0, "e": 0.01}]
        self.assertIn("members", column_stats(contaminated, "rv", error_column="e")["weighted_mean"]["warning"])

    def test_booleans_and_text_are_counted(self):
        self.assertEqual(column_stats([{"b": True}, {"b": False}, {"b": True}], "b"),
                         {"column": "b", "n": 3, "missing": 0, "type": "bool", "true": 2, "false": 1})
        text = column_stats([{"t": "HST"}, {"t": "JWST"}, {"t": "HST"}], "t")
        self.assertEqual((text["top"], text["distinct"]), ({"HST": 2, "JWST": 1}, 2))
        single = column_stats([{"x": 5}], "x")
        self.assertIsNone(single["std"])
        self.assertNotIn("histogram", single)


class FieldTests(unittest.TestCase):
    def setUp(self):
        self.field = field_from_viewer_data(viewer_data())

    def test_gaia_rows_merge_list_membership_into_flags(self):
        gaia = {row["id"]: row for row in self.field["tables"]["gaia"]["rows"]}
        self.assertEqual(len(gaia), 5)
        self.assertTrue(gaia["1004"]["gaia_crf"] and gaia["1004"]["quaia"])
        self.assertFalse(gaia["1004"]["star"])
        self.assertTrue(gaia["1001"]["has_spectra"])

    def test_a_cone_target_is_labelled_as_a_cone(self):
        data = viewer_data()
        data["target"] = {**data["target"], "kind": "cone", "rhalf_arcmin": 5.0, "distance_kpc": None}
        field = field_from_viewer_data(data)
        self.assertEqual([r["id"] for r in field["tables"]["target"]["rows"]], ["center", "region"])
        labels = {l["id"]: l["label"] for l in field["layers"] if l["table"] == "target"}
        self.assertEqual(labels, {"target:center": "Cone center", "target:region": "5′ cone (query region)"})

    def test_default_layers_follow_the_viewer_presentation(self):
        layers = {layer["id"]: layer for layer in default_layers(self.field)}
        self.assertEqual(layers["fp:HST:ACS/WFC"]["count"], 1)
        self.assertEqual(layers["gaia:stars"]["count"], 2)
        self.assertEqual(layers["qso:crf"]["count"], 1)
        self.assertEqual(layers["spectra:desi"]["count"], 1)
        self.assertFalse(layers["gaia:rrlyrae"]["visible"])
        # The map starts with the target, footprints and trusted QSOs; the rest waits in "Add data".
        self.assertEqual({i for i, l in layers.items() if l["added"]},
                         {"fp:HST:ACS/WFC", "fp:JWST:NIRCAM/IMAGE", "qso:crf", "qso:quaia", "qso:milliquas",
                          "qso:milliquas-candidates", "target:center", "target:region"})
        self.assertTrue(all(l["added"] == l["visible"] for l in layers.values()))
        self.assertNotIn("fp:planned", layers)
        self.assertTrue(all(layer["origin"] == "pipeline" for layer in layers.values()))

    def test_columns_carry_units_and_spectral_values(self):
        gaia = self.field["tables"]["gaia"]
        self.assertEqual(gaia["units"]["pmra"], "mas/yr")
        self.assertIn("RUWE", " ".join(gaia["descriptions"].values()).upper())
        self.assertEqual(gaia["rows"][0]["pmra_error"], 0.3)
        spectra = {row["id"]: row for row in self.field["tables"]["spectra"]["rows"]}
        self.assertEqual((spectra["1001"]["rv"], spectra["1001"]["feh"]), (-291.5, -1.9))
        self.assertIsNone(spectra["sdss:42"]["rv"])
        self.assertEqual(self.field["target"]["pmdec"], -0.19)
        summary = table_summary(gaia)
        self.assertEqual(summary["columns"]["parallax"]["unit"], "mas")

    def test_within_validation(self):
        good = [{"column": "position", "op": "within", "value": {"ra": 260.0, "dec": 57.9, "radius": 0.1}}]
        self.assertEqual(validate_filter(good, set()), good)
        for bad in ({"column": "ra", "op": "within", "value": {"ra": 1, "dec": 1, "radius": 1}},
                    {"column": "position", "op": "within", "value": {"ra": 1, "dec": 1}},
                    {"column": "position", "op": "within", "value": {"ra": 400, "dec": 1, "radius": 1}}):
            with self.assertRaisesRegex(ValueError, "within"):
                validate_filter([bad], set())

    def test_data_sources_provide_summaries(self):
        coverage, baselines = self.field["summaries"]
        self.assertEqual((coverage["source"], coverage["title"]), ("observations", "Imaging coverage"))
        self.assertEqual(coverage["rows"], [["HST ACS/WFC", 1, 0.2]])
        self.assertEqual(baselines["rows"], [["HST–JWST", 18.7, "j8x101", "jw01234"]])
        self.assertNotIn("coverage", self.field)  # no data-source-specific field keys

    def test_summary_gives_ranges_and_examples_not_rows(self):
        summary = table_summary(self.field["tables"]["observations"])
        self.assertEqual(summary["rows"], 2)
        self.assertEqual(summary["columns"]["year"], {"type": "number", "min": 2004.6, "max": 2023.3, "present": 2,
                                                      "unit": "yr", "description": "mid-exposure decimal year"})
        self.assertEqual(summary["columns"]["tel"]["examples"], ["HST", "JWST"])
        self.assertNotIn("s_region", summary["columns"])

    def test_point_tables_get_tangent_plane_offsets(self):
        from scoop.science.spatial import project
        gaia = self.field["tables"]["gaia"]
        self.assertEqual(gaia["units"]["xi"], "arcmin")
        target = self.field["target"]
        for row in gaia["rows"]:
            xi, eta = project(row["ra"], row["dec"], target["ra"], target["dec"])
            self.assertAlmostEqual(row["xi"], xi * 60, places=4)
            self.assertAlmostEqual(row["eta"], eta * 60, places=4)
        self.assertNotIn("xi", self.field["tables"]["observations"]["columns"])  # footprints have no point position

    def test_frontend_fixture_is_current(self):
        # web/ tests render this file; regenerate it if the normalization changes.
        fixture = json.loads((Path(__file__).parent / "fixtures" / "mini_field.json").read_text())
        expected = {**self.field, "layers": default_layers(self.field)}
        self.assertEqual(fixture, json.loads(json.dumps(expected)))

    def test_store_reads_smoke_test_output_and_rejects_odd_keys(self):
        with tempfile.TemporaryDirectory() as tmp:
            (Path(tmp) / "mini").mkdir()
            (Path(tmp) / "mini" / "mini_1_field.json").write_text(json.dumps(viewer_data()))
            store = FieldStore(Path(tmp))
            self.assertEqual(store.keys(), [{"key": "mini_1", "path": "mini/mini_1_field.json"}])
            self.assertEqual(store.get("mini_1")["target"]["name"], "Mini")
            self.assertIsNone(store.get("../mini_1"))
            self.assertIsNone(store.get("other"))


if __name__ == "__main__":
    unittest.main()
