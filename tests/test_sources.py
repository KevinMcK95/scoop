"""Data sources plug in by adding a module; nothing else changes."""
import json
import unittest

from scoop.fields import field_from_viewer_data
from scoop.sources import SOURCES, Column, Source, eq, layer, register
from scoop.sources.exoplanets import overview_link, planet
from scoop.fields import matches
from exoplanets.archive import radec_box
from scoop.sources.mast_tso import mjd_date, parse
from tests.field_fixture import viewer_data


def variable_stars(data):
    return [{"id": h["hostname"], "ra": h["ra"], "dec": h["dec"], "n_planets": h["n"]}
            for h in data["variable_stars"]]  # KeyError when the pipeline has none


class SourceRegistryTests(unittest.TestCase):
    def test_built_in_sources_are_discovered(self):
        self.assertEqual(set(SOURCES), {"target", "observations", "planned", "gaia", "spectra",
                                        "milliquas", "simbad_galaxies", "exoplanet_hosts", "tess_tois",
                                        "hst_jwst_spectra"})
        self.assertIsNotNone(SOURCES["exoplanet_hosts"].query)  # queried on demand
        self.assertIsNotNone(SOURCES["tess_tois"].query)

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


class ExoplanetDetailsTests(unittest.TestCase):
    PLANET = {"native_id": "KOI-351 b", "orbital_period_days": 7.008151, "radius_earth": 1.31,
              "mass_earth": 2.27, "mass_type": "M-R relationship", "equilibrium_temp_k": 1062.0,
              "discovery_method": "Transit", "discovery_year": 2013, "discovery_facility": "Kepler",
              "reference": "Cabrera et al. 2014", "bibcode": "2014ApJ...781...18C"}

    def test_planet_detail_says_how_the_mass_was_found_and_links_the_paper(self):
        d = planet(self.PLANET)
        self.assertEqual(d["title"], "KOI-351 b")
        self.assertEqual(d["fields"]["mass type"], "estimated from radius")
        self.assertEqual(d["fields"]["discovered"], "2013 · Kepler")
        self.assertEqual(d["links"], [["https://ui.adsabs.harvard.edu/abs/2014ApJ...781...18C",
                                       "discovery paper (ADS)"]])

    def test_missing_values_are_left_out_not_shown_empty(self):
        d = planet({**self.PLANET, "mass_earth": None, "bibcode": None, "discovery_facility": None})
        self.assertNotIn("mass (M⊕)", d["fields"])
        self.assertNotIn("mass type", d["fields"])  # no mass, so no mass type either
        self.assertEqual(d["fields"]["discovered"], "2013")
        self.assertEqual(d["links"], [])

    def test_host_links_to_its_archive_overview_page(self):
        self.assertEqual(overview_link({"id": "Proxima Cen"}),
                         [("https://exoplanetarchive.ipac.caltech.edu/overview/Proxima%20Cen",
                           "Exoplanet Archive overview")])


class TessToiTests(unittest.TestCase):
    def test_dispositions_split_into_layers_with_only_open_candidates_shown(self):
        source = SOURCES["tess_tois"]
        rows = [{"id": f"TOI-{i}.01", "disposition": code} for i, code in
                enumerate(["PC", "APC", "CP", "KP", "FP", "FA"])]
        layers = {l["id"]: l for l in source.layers(rows, {})}
        counts = {i: sum(matches(r, l["filter"]) for r in rows) for i, l in layers.items()}
        self.assertEqual(counts, {"tois:candidates": 2, "tois:planets": 2, "tois:false": 2})
        self.assertEqual([i for i, l in layers.items() if l["visible"]], ["tois:candidates"])

    def test_toi_links_to_its_exofop_page(self):
        self.assertEqual(SOURCES["tess_tois"].links({"tic_id": 150428135}),
                         [("https://exofop.ipac.caltech.edu/tess/target.php?id=150428135",
                           "ExoFOP TIC 150428135")])


class RaDecBoxTests(unittest.TestCase):
    """The toi table has no spatial index, so cones become RA/Dec boxes."""

    def test_ra_range_widens_with_declination(self):
        self.assertRegex(radec_box(100.0, 0.0, 1.0),
                         r"^dec BETWEEN -1\.0 AND 1\.0 AND ra BETWEEN 98\.9998\d* AND 101\.0001\d*$")
        # At dec 59-61, 1 deg of sky spans 1 / cos(61 deg) = 2.06 deg of RA.
        self.assertRegex(radec_box(100.0, 60.0, 1.0), r"ra BETWEEN 97\.937\d* AND 102\.062\d*$")

    def test_box_across_ra_zero_uses_or(self):
        self.assertRegex(radec_box(359.9, 0.0, 0.5), r"\(ra >= 359\.39\d+ OR ra <= 0\.40\d+\)$")

    def test_box_containing_a_pole_takes_every_ra(self):
        self.assertEqual(radec_box(10.0, 89.8, 0.5), "dec BETWEEN 89.3 AND 90.0")


def mast_record(obs_id, calib_level, **kw):
    return {"obs_id": obs_id, "calib_level": calib_level, "s_ra": 346.62, "s_dec": -5.04, "s_region": "",
            "obs_collection": "JWST", "instrument_name": "NIRSPEC/SLIT", "filters": "CLEAR;PRISM",
            "dataproduct_type": "timeseries", "target_name": "TRAPPIST-1", "t_min": 60272.0,
            "t_exptime": 21225.1, "proposal_id": "1331", "proposal_pi": "Lewis, Nikole",
            "obs_title": "Transit Spectroscopy of TRAPPIST-1e", "dataRights": "PUBLIC", **kw}


class HstJwstSpectraTests(unittest.TestCase):
    RESPONSE = {"status": "COMPLETE", "data": [
        mast_record("jw01331-o001", 2), mast_record("jw01331-o001", 3),  # one observation, two product levels
        mast_record("jw01331-o002", 3, t_min=60300.0),
        mast_record("jw11253-o001", -1, dataproduct_type="spectrum", instrument_name="NIRISS/SOSS",
                    t_min=None, proposal_id="11253", proposal_pi="Masuda", obs_title="Fluffy or Flashy?")]}

    def test_one_row_per_observation_with_planned_ones_marked(self):
        rows = parse(self.RESPONSE)
        self.assertEqual([(r["id"], r["status"], r["type"]) for r in rows],
                         [("jw01331-o001", "executed", "time series"), ("jw01331-o002", "executed", "time series"),
                          ("jw11253-o001", "planned", "spectrum")])
        self.assertEqual(rows[0]["date"], "2023-11-24")

    def test_planned_observations_get_their_own_layer(self):
        rows = parse(self.RESPONSE)
        layers = {l["id"]: sum(matches(r, l["filter"]) for r in rows)
                  for l in SOURCES["hst_jwst_spectra"].layers(rows, {})}
        self.assertEqual(layers, {"tso:JWST:time series": 2, "tso:planned": 1})

    def test_summary_lists_each_program_with_its_title(self):
        summary, = SOURCES["hst_jwst_spectra"].summaries(parse(self.RESPONSE), {})
        self.assertEqual(summary["rows"], [
            ["JWST 1331", "Lewis, Nikole", "Transit Spectroscopy of TRAPPIST-1e", "NIRSPEC/SLIT", 2, "2023-11-24", 0],
            ["JWST 11253", "Masuda", "Fluffy or Flashy?", "NIRISS/SOSS", 1, None, 1]])

    def test_an_incomplete_mast_answer_is_an_error_not_an_empty_field(self):
        with self.assertRaisesRegex(RuntimeError, "did not complete"):
            parse({"status": "ERROR", "msg": "timeout", "data": []})

    def test_mjd_to_date(self):
        self.assertEqual(mjd_date(51544.0), "2000-01-01")
        self.assertIsNone(mjd_date(None))


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
