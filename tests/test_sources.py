"""Data sources plug in by adding a module; nothing else changes."""
import json
import unittest

from scoop.fields import field_from_viewer_data
from scoop.sources import SOURCES, Column, Source, eq, layer, register
from tests.field_fixture import viewer_data


def variable_stars(data):
    return [{"id": h["hostname"], "ra": h["ra"], "dec": h["dec"], "n_planets": h["n"]}
            for h in data["variable_stars"]]  # KeyError when the pipeline has none


class SourceRegistryTests(unittest.TestCase):
    def test_built_in_sources_are_discovered(self):
        self.assertEqual(set(SOURCES), {"target", "observations", "planned", "gaia", "spectra",
                                        "milliquas", "simbad_galaxies", "exoplanet_hosts"})
        self.assertIsNotNone(SOURCES["exoplanet_hosts"].query)  # queried on demand

    def test_adding_a_source_only_adds_its_table_and_layers(self):
        before = json.loads(json.dumps(field_from_viewer_data(viewer_data())))
        register(Source(
            name="variable_stars", label="Confirmed planet hosts", provenance="NASA Exoplanet Archive",
            order=45, rows=variable_stars, columns={"n_planets": Column(None, "confirmed planets")},
            layers=lambda rows, data: [layer("var:stars", "variable_stars", [eq("n_planets", 1)],
                                             "Exoplanets", "Single-planet hosts", "#ffffff", order=55)],
            presets=({"label": "Planets", "x": "n_planets"},), browse=("n_planets",)))
        try:
            data = {**viewer_data(), "variable_stars": [{"hostname": "HD 1", "ra": 260.03, "dec": 57.91, "n": 1}]}
            after = json.loads(json.dumps(field_from_viewer_data(data)))
            without = json.loads(json.dumps(field_from_viewer_data(viewer_data())))
        finally:
            SOURCES.pop("variable_stars")
        table = after["tables"].pop("variable_stars")
        self.assertEqual(table["descriptions"], {"n_planets": "confirmed planets", "ra": "ICRS right ascension",
                                                 "dec": "ICRS declination",  # and every point table's sky offsets
                                                 "xi": "tangent-plane offset east of the target",
                                                 "eta": "tangent-plane offset north of the target"})
        self.assertEqual(table["presets"], [{"label": "Planets", "x": "n_planets"}])
        new_layer = next(layer for layer in after["layers"] if layer["id"] == "var:stars")
        self.assertEqual(new_layer["count"], 1)
        # Placed by its order key among the existing layers, which are unchanged.
        ids = [layer["id"] for layer in after["layers"]]
        self.assertLess(ids.index("qso:milliquas-candidates"), ids.index("var:stars"))
        self.assertLess(ids.index("var:stars"), ids.index("gaia:qso-candidates"))
        after["layers"].remove(new_layer)
        self.assertEqual(after, before)
        # Pipeline output without that data source simply has no such table.
        self.assertEqual(without, before)

    def test_duplicate_registration_is_refused(self):
        with self.assertRaisesRegex(ValueError, "registered twice"):
            register(SOURCES["gaia"])


if __name__ == "__main__":
    unittest.main()


try:
    from starlette.testclient import TestClient
    from scoop.server import create_app
    HAS_SERVER = True
except ModuleNotFoundError:
    HAS_SERVER = False


@unittest.skipUnless(HAS_SERVER, "optional server dependencies absent")
class QueryOnDemandTests(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        from scoop.fields import FieldStore
        self.regions = []

        async def query(region):
            self.regions.append(region)
            return [{"id": "V1", "ra": 260.05, "dec": 57.91, "period_d": 0.6}]

        register(Source(name="variables", label="Variable stars", provenance="Test archive", order=60,
                        rows=lambda data: data["extra"]["variables"]["rows"], query=query,
                        layers=lambda rows, data: [layer("var:all", "variables", [], "Variables", "Variable stars",
                                                         "#ffffff", order=56)]))
        self.addCleanup(SOURCES.pop, "variables")
        tmp = Path(self.enterContext(tempfile.TemporaryDirectory()))
        (tmp / "mini").mkdir()
        (tmp / "mini" / "mini_1_field.json").write_text(json.dumps(viewer_data()))
        self.store = FieldStore(tmp)
        self.client = TestClient(create_app(fields=self.store))

    def test_a_queryable_source_adds_its_table_beside_the_field(self):
        listed = {s["name"]: s["queryable"] for s in self.client.get("/api/sources").json()["sources"]}
        self.assertTrue(listed["variables"] and listed["exoplanet_hosts"] and not listed["gaia"])
        self.assertNotIn("variables", self.store.get("mini_1")["tables"])
        added = self.client.post("/api/fields/mini_1/sources/variables").json()
        self.assertEqual(added, {"field": "mini_1", "source": "variables", "rows": 1})
        field = self.store.get("mini_1")
        self.assertEqual(field["tables"]["variables"]["rows"][0]["id"], "V1")
        self.assertIn("var:all", [l["id"] for l in field["layers"]])
        # The region is the field's: its target center, out to the query region's edge.
        self.assertEqual((self.regions[0]["ra"], self.regions[0]["dec"]), (260.05, 57.92))
        self.assertGreater(self.regions[0]["radius_deg"], 0.1)
        self.assertEqual(self.client.post("/api/fields/mini_1/sources/gaia").status_code, 404)
        self.assertEqual(self.client.post("/api/fields/nope/sources/variables").status_code, 404)
