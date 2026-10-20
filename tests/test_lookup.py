import asyncio
import unittest

from scoop.lookup import cone_adql, lookup_position, parse_tap_json, references_adql

# Shape of SIMBAD TAP FORMAT=json output (metadata + data rows).
CONE = {"metadata": [{"name": n} for n in ("oid", "main_id", "ra", "dec", "otype", "nbref", "dist")],
        "data": [[3274139, "NAME Draco Dwarf Galaxy", 260.0517, 57.9153, "G", 1100, 0.0001],
                 [3274200, "Gaia DR3 1433", 260.06, 57.92, "*", 2, 0.001]]}
REFS = {"metadata": [{"name": n} for n in ("bibcode", "ref_year", "title")],
        "data": [["2025ApJ...980..123A", 2025, "Draco proper motions"], ["2024MNRAS.530.1X", 2024, None]]}


class AdqlTests(unittest.TestCase):
    def test_cone_query_is_built_from_numbers_only(self):
        adql = cone_adql(260.05, 57.92, 5 / 3600, 10)
        self.assertIn("CIRCLE('ICRS', 260.05, 57.92, 0.001388", adql)
        self.assertTrue(adql.startswith("SELECT TOP 10 oid, main_id"))
        self.assertIn("ORDER BY dist", adql)
        for bad in (("260", 57.9, 0.01), (float("nan"), 0, 0.01), (10.0, 95.0, 0.01), (10.0, 0.0, 2.0), (True, 0, 0.01)):
            with self.assertRaises(ValueError):
                cone_adql(*bad)
        with self.assertRaises(ValueError):
            cone_adql(10.0, 0.0, 0.01, 1000)

    def test_reference_query_needs_an_integer_oid(self):
        adql = references_adql(3274139, 5)
        self.assertIn("WHERE h.oidref = 3274139", adql)
        # Verified against SIMBAD TAP (2026-10-20): "year" must be quoted, ORDER BY uses the alias.
        self.assertIn('r."year" AS ref_year', adql)
        self.assertTrue(adql.endswith("ORDER BY ref_year DESC"))
        for bad in ("3274139; DROP", -1, 1.5, True):
            with self.assertRaises(ValueError):
                references_adql(bad)

    def test_parse_rows_or_refuse_odd_shapes(self):
        self.assertEqual(parse_tap_json(REFS)[0], {"bibcode": "2025ApJ...980..123A", "ref_year": 2025,
                                                   "title": "Draco proper motions"})
        for bad in (None, [], {"data": []}, {"metadata": [], "data": "x"}):
            with self.assertRaises(ValueError):
                parse_tap_json(bad)


class LookupTests(unittest.TestCase):
    def test_nearest_object_and_its_references(self):
        queries = []

        async def query(adql):
            queries.append(adql)
            return parse_tap_json(REFS if "has_ref" in adql else CONE)

        result = asyncio.run(lookup_position(query, 260.0517, 57.9153, 5.0, 2))
        self.assertEqual(result["objects"][0]["main_id"], "NAME Draco Dwarf Galaxy")
        self.assertEqual(result["objects"][0]["sep_arcsec"], 0.36)
        self.assertEqual(result["references_of"], "NAME Draco Dwarf Galaxy")
        self.assertEqual([r["bibcode"] for r in result["references"]], ["2025ApJ...980..123A", "2024MNRAS.530.1X"])
        self.assertIn("oidref = 3274139", queries[1])

    def test_nothing_nearby_skips_the_reference_query(self):
        async def query(adql):
            self.assertNotIn("has_ref", adql)
            return []

        self.assertEqual(asyncio.run(lookup_position(query, 10.0, 0.0, 5.0, 5)),
                         {"service": "SIMBAD TAP", "objects": []})


if __name__ == "__main__":
    unittest.main()
