import re
import unittest
from unittest import mock

import numpy as np

try:
    from astropy.table import Table
except ModuleNotFoundError:
    Table = None

from scoop.archives import discovery
from scoop.science.fields import field_from_viewer_data
from tests.field_fixture import viewer_data

try:
    from langchain_core.messages import AIMessage
    from scoop.agents.data_agent import DATA_AGENT_PROMPT, build_data_tools, build_handoff_tool
    from scoop.agents.prompt import WORKSPACE_PROMPT
except ModuleNotFoundError as exc:
    if exc.name.split(".")[0] not in {"langgraph", "langchain_core", "pydantic"}:
        raise
    HAS_CHAT = False
else:
    HAS_CHAT = True

REGION = {"ra": 260.0684, "dec": 57.9185, "radius_deg": 0.33}


class SafetyTests(unittest.TestCase):
    def test_private_and_local_urls_are_refused(self):
        for url in ("http://127.0.0.1:8787/tap", "http://localhost/tap", "http://10.0.0.5/tap",
                    "file:///etc/passwd", "ftp://example.org/x"):
            with self.subTest(url=url), self.assertRaises(discovery.DiscoveryError):
                discovery.check_public_url(url)

    def test_extra_conditions_are_single_expressions(self):
        self.assertEqual(discovery.check_where(" calib_level = 2 "), "calib_level = 2")
        for bad in ("1=1; DROP TABLE x", "x = 1 -- c", "x IN (SELECT y FROM t)", "a /* b */ = 1"):
            with self.subTest(bad=bad), self.assertRaises(discovery.DiscoveryError):
                discovery.check_where(bad)

    def test_identifiers_are_plain_names(self):
        self.assertEqual(discovery.check_ident("ivoa.ObsCore", "table"), "ivoa.ObsCore")
        for bad in ("ivoa.obscore; x", "a b", "'t'", ""):
            with self.subTest(bad=bad), self.assertRaises(discovery.DiscoveryError):
                discovery.check_ident(bad, "table")


class AdqlTests(unittest.TestCase):
    def test_points_inside_the_region(self):
        q = discovery.build_adql("cat.stars", REGION, columns=["ra", "dec", "g"], ra_col="ra", dec_col="dec",
                                 region_col=None, where="g < 20", limit=100)
        self.assertEqual(q, "SELECT TOP 100 ra, dec, g FROM cat.stars WHERE CONTAINS(POINT('ICRS', ra, dec), "
                            "CIRCLE('ICRS', 260.068400, 57.918500, 0.330000)) = 1 AND (g < 20)")

    def test_footprints_that_intersect_the_region(self):
        q = discovery.build_adql("ivoa.ObsCore", REGION, columns=None, ra_col=None, dec_col=None,
                                 region_col="s_region", where=None, limit=10, count_only=True)
        self.assertEqual(q, "SELECT COUNT(*) AS n FROM ivoa.ObsCore WHERE INTERSECTS(s_region, "
                            "CIRCLE('ICRS', 260.068400, 57.918500, 0.330000)) = 1")

    def test_region_radius_is_capped(self):
        self.assertEqual(discovery.check_region({**REGION, "radius_deg": 5})["radius_deg"], discovery.MAX_RADIUS_DEG)


@unittest.skipIf(Table is None, "astropy absent")
class RowTests(unittest.TestCase):
    def test_rows_keep_ids_exact_and_footprints(self):
        big = 1433143832631934848  # a Gaia DR3 source_id: more digits than a float holds
        t = Table({"obs_id": ["a", "a", "b"], "s_ra": [260.0, 260.1, 260.2], "s_dec": [57.9, 57.8, 57.7],
                   "s_region": ["Polygon ICRS 1 2 3 4 5 6"] * 3,
                   "source": np.array([big, 7, 8], dtype=np.int64), "mag": [20.5, np.nan, 21.0]})
        rows, columns = discovery.table_rows(t, prefix="x", id_col="obs_id", region_col="s_region")
        self.assertEqual([r["id"] for r in rows], ["a", "a#2", "b"])
        self.assertEqual(rows[0]["source"], str(big))      # exact, as a string
        self.assertEqual(rows[1]["source"], 7)
        self.assertIsNone(rows[1]["mag"])                   # NaN -> null
        self.assertEqual(rows[0]["s_region"], "Polygon ICRS 1 2 3 4 5 6")
        self.assertEqual((rows[0]["ra"], rows[0]["dec"]), (260.0, 57.9))
        self.assertNotIn("s_ra", columns)

    def test_positions_by_ucd(self):
        t = Table({"RAdeg": [1.0], "DEdeg": [2.0]})
        t["RAdeg"].meta["ucd"] = "pos.eq.ra;meta.main"
        t["DEdeg"].meta["ucd"] = "pos.eq.dec;meta.main"
        self.assertEqual(discovery.position_columns(t), ("RAdeg", "DEdeg"))


class DiscoveredTableTests(unittest.TestCase):
    def test_a_table_the_data_agent_stored_becomes_a_table_and_layer(self):
        data = viewer_data()
        data["extra"] = {"cfht_megacam": {
            "rows": [{"id": "1", "ra": 260.0, "dec": 57.9, "s_region": "Polygon ICRS 1 2 3 4 5 6",
                      "instrument_name": "MegaPrime"}],
            "meta": {"label": "CFHT MegaCam images (CADC)", "provenance": "CADC (ivoa.ObsCore)",
                     "kind": "footprints", "columns": {"instrument_name": {"unit": None, "description": "inst"}},
                     "query": {"adql": "SELECT ...", "rows": 1, "truncated": False}, "style": {"color": "#e66767"}}}}
        field = field_from_viewer_data(data)
        table = field["tables"]["cfht_megacam"]
        self.assertEqual((table["kind"], table["origin"], table["label"]),
                         ("footprints", "agent", "CFHT MegaCam images (CADC)"))
        self.assertEqual(table["query"]["adql"], "SELECT ...")
        layer = next(l for l in field["layers"] if l["table"] == "cfht_megacam")
        self.assertEqual((layer["origin"], layer["count"], layer["style"]["color"]), ("agent", 1, "#e66767"))


@unittest.skipIf(Table is None, "astropy absent")
class LinkTests(unittest.TestCase):
    def test_only_web_urls_become_links(self):
        t = Table({"ra": [1.0, 2.0], "dec": [1.0, 2.0],
                   "access_url": ["https://ws.example.org/datalink?ID=1", "javascript:alert(1)"]})
        rows, _ = discovery.table_rows(t, prefix="x")
        self.assertEqual(rows[0]["_links"], [["https://ws.example.org/datalink?ID=1", "access_url (ws.example.org)"]])
        self.assertNotIn("_links", rows[1])


class GaiaLinkTests(unittest.TestCase):
    def tables(self, rows, gaia_id_values=None):
        from scoop.science.fields import _columns
        gaia = [{"id": "1433143832631934848", "ra": 260.0, "dec": 57.9},
                {"id": "1433143832631934976", "ra": 260.01, "dec": 57.9}]
        new = {"label": "New catalog", "kind": "catalog", "rows": rows, "columns": _columns(rows),
               "descriptions": {}, "units": {}}
        return {"gaia": {"rows": gaia, "columns": _columns(gaia), "descriptions": {}}, "new": new}

    def test_gaia_id_column_wins_over_position(self):
        from scoop.science.fields import link_to_gaia
        # Placed on the second star, but its Gaia ID names the first one.
        tables = self.tables([{"id": "a", "ra": 260.01, "dec": 57.9, "GaiaDR3": "1433143832631934848", "mag": 17}])
        link_to_gaia(tables, "new")
        row = tables["new"]["rows"][0]
        self.assertEqual((row["gaia_id"], row["gaia_match"]), ("1433143832631934848", "Gaia ID (GaiaDR3)"))
        first, second = tables["gaia"]["rows"]
        self.assertTrue(first["in_new"])
        self.assertFalse(second["in_new"])
        self.assertEqual(first["_details"]["New catalog"][0]["title"], "New catalog · a")
        self.assertIn("in_new", tables["gaia"]["columns"])

    def test_position_match_within_one_arcsec(self):
        from scoop.science.fields import link_to_gaia
        tables = self.tables([{"id": "near", "ra": 260.0 + 0.5 / 3600 / 0.5329, "dec": 57.9},  # ~0.5″ east
                              {"id": "far", "ra": 260.005, "dec": 57.9}])
        link_to_gaia(tables, "new")
        near, far = tables["new"]["rows"]
        self.assertEqual(near["gaia_id"], "1433143832631934848")
        self.assertTrue(near["gaia_match"].startswith("position, 0.5"))
        self.assertIsNone(far["gaia_id"])

    def test_velocities_reach_the_standard_columns_and_the_gaia_stars(self):
        from scoop.science.fields import add_measurements, link_to_gaia, measurement_roles
        rows = [{"id": "a1", "ra": 260.0, "dec": 57.9, "RVel": 78.0, "e_RVel": 1.0, "[Fe/H]": -2.0, "e_[Fe/H]": 0.1},
                {"id": "a2", "ra": 260.0, "dec": 57.9, "RVel": 80.0, "e_RVel": 1.0, "[Fe/H]": -2.2, "e_[Fe/H]": 0.1},
                {"id": "b", "ra": 260.01, "dec": 57.9, "RVel": 60.0, "e_RVel": 2.0}]
        tables = self.tables(rows)
        roles = measurement_roles(tables["new"]["columns"])
        self.assertEqual(roles, {"rv": "RVel", "rv_err": "e_RVel", "feh": "[Fe/H]", "feh_err": "e_[Fe/H]"})
        tables["gaia"]["rows"][1]["rv"] = 55.5  # already measured (e.g. DESI): kept
        add_measurements(tables["new"], roles, "new")
        self.assertEqual(tables["new"]["rows"][0]["rv"], 78.0)
        self.assertEqual([p["label"] for p in tables["new"]["presets"]], ["RV histogram · new", "RV vs [Fe/H] · new"])
        link_to_gaia(tables, "new")
        first, second = tables["gaia"]["rows"]
        self.assertAlmostEqual(first["rv"], 79.0)                # two measurements, equal weights
        self.assertAlmostEqual(first["rv_err"], 1 / 2 ** 0.5, places=3)
        self.assertEqual(first["rv_source"], "New catalog (mean of 2)")
        self.assertAlmostEqual(first["feh"], -2.1)
        self.assertEqual(second["rv"], 55.5)
        self.assertNotIn("rv_source", second)

    def test_declared_roles_win_and_errors_need_values(self):
        from scoop.science.fields import measurement_roles
        cols = ["Vel", "eVel", "rv", "e_FeH_x"]
        self.assertEqual(measurement_roles(cols, {"rv": "Vel", "rv_err": "eVel"}), {"rv": "Vel", "rv_err": "eVel"})
        self.assertEqual(measurement_roles(["e_rvel", "mag"]), {})  # an error without its value is dropped

    def test_float_gaia_ids_are_never_trusted(self):
        from scoop.science.fields import _gaia_id_column
        self.assertIsNone(_gaia_id_column([{"Gaia": 1.433143832631935e18}]))
        self.assertEqual(_gaia_id_column([{"Gaia": "1433143832631934848"}]), "Gaia")


class ObservationColumnTests(unittest.TestCase):
    def test_obscore_columns_become_the_standard_ones(self):
        from scoop.science.observations import add_observation_columns
        row = add_observation_columns({"facility_name": "CFHT 3.6m", "instrument_name": "MegaPrime",
                                       "t_min": 51544.5, "t_max": 51544.5, "t_exptime": 600.0,
                                       "em_min": 6.66e-07, "em_max": 6.782e-07, "proposal_id": "05AF01"})
        self.assertEqual((row["tel"], row["inst"], row["year"], row["date"], row["exptime"], row["wavelength_nm"],
                          row["program"]), ("CFHT 3.6m", "MegaPrime", 2000.0, "2000-01-01", 600.0, 672.1, "05AF01"))

    def stored_cfht_field(self):
        data = viewer_data()
        data["extra"] = {"cfht": {
            # Overlaps both fixture footprints; observed at MJD 60000 = 2000 + 8455.5/365.25 = 2023.150.
            "rows": [{"id": "c1", "ra": 260.01, "dec": 57.91, "obs_collection": "CFHT",
                      "instrument_name": "MegaPrime", "t_min": 60000.0, "t_max": 60000.0,
                      "s_region": "POLYGON 260.0 57.9 260.02 57.9 260.02 57.92"}],
            "meta": {"label": "CFHT MegaCam", "provenance": "CADC", "kind": "footprints", "columns": {},
                     "query": {"rows": 1, "truncated": False}}}}
        return field_from_viewer_data(data)

    def test_stored_footprint_tables_get_them_too(self):
        self.assertEqual(self.stored_cfht_field()["tables"]["cfht"]["rows"][0]["year"], 2023.15)

    @unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
    def test_overlaps_across_two_footprint_tables(self):
        from scoop.agents.visual import overlap_report
        field = self.stored_cfht_field()
        rep = overlap_report(field, "observations", [{"column": "tel", "op": "==", "value": "HST"}], None,
                             0, 5, table_b="cfht")
        self.assertEqual(rep["pairs"], 1)
        pair = rep["longest"][0]
        self.assertEqual((pair["a"], pair["a_table"], pair["b"], pair["b_table"]),
                         ("j8x101", "observations", "c1", "cfht"))
        self.assertEqual(pair["years"], round(2023.15 - 2004.6, 2))
        self.assertEqual(rep["layer_filters"]["cfht"][0]["value"], ["c1"])


class ScriptedModel:
    def __init__(self, *responses):
        self.responses = iter(responses)

    def bind_tools(self, tools):
        self.tools = [t.name for t in tools]
        self.tools_full = list(tools)
        return self

    async def ainvoke(self, messages, config=None):
        self.last = messages
        return next(self.responses)


def call(name, args, call_id):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": call_id, "type": "tool_call"}])


@unittest.skipUnless(HAS_CHAT, "optional chat dependencies absent")
class HandoffTests(unittest.IsolatedAsyncioTestCase):
    async def test_the_data_agent_queries_stores_and_reports(self):
        field = field_from_viewer_data(viewer_data())
        stored = {}

        async def store_rows(key, name, rows, meta):
            stored[name] = (key, rows, meta)

        rows = [{"id": "J1", "ra": 260.05, "dec": 57.91, "Teff": 4500.0}]
        meta = {"label": "LAMOST stars", "provenance": "VizieR V/162/x", "kind": "catalog",
                "columns": {"Teff": {"unit": "K", "description": None}},
                "query": {"service": "VizieR", "table": "V/162/x", "rows": 1, "truncated": False}}
        model = ScriptedModel(
            call("read_skill", {"path": "index"}, "c1"),
            call("query_catalog", {"table": "V/162/x", "name": "lamost", "label": "LAMOST stars"}, "c2"),
            AIMessage(content="Added table lamost: 1 LAMOST star (VizieR V/162/x)."))
        handoff = build_handoff_tool(model=model, field=field, store_rows=store_rows)
        with mock.patch.object(discovery, "query_catalog", return_value=(rows, meta)) as q:
            result = await handoff.ainvoke({"type": "tool_call", "name": "ask_data_agent", "id": "h1",
                                            "args": {"request": "LAMOST spectra here"}})
        self.assertEqual(q.call_args.args[0], "V/162/x")
        self.assertEqual(stored["lamost"][0], field["key"])
        self.assertEqual(stored["lamost"][2]["style"]["shape"], "circle")
        update = result.update
        self.assertEqual(update["artifacts"]["h1"], {"kind": "field_refresh", "key": field["key"], "added": ["lamost"]})
        self.assertIn("Added table lamost", update["messages"][0].content)
        self.assertIn('"field": {"key": "mini_1"', model.last[1].content)  # the request carried the field context
        self.assertIn("survey_inventory", model.tools)

    async def test_existing_pipeline_tables_are_not_overwritten(self):
        field = field_from_viewer_data(viewer_data())
        tools = {t.name: t for t in build_data_tools(field=field, store_rows=None, added=[])}
        result = await tools["query_catalog"].ainvoke({"type": "tool_call", "name": "query_catalog", "id": "c",
                                                      "args": {"table": "V/1/x", "name": "gaia", "label": "x"}})
        self.assertEqual(result.status, "error")
        self.assertIn("already has a table", result.content)

    async def test_skills_cannot_be_read_outside_the_skills_folder(self):
        field = field_from_viewer_data(viewer_data())
        tools = {t.name: t for t in build_data_tools(field=field, store_rows=None, added=[])}
        bad = await tools["read_skill"].ainvoke({"type": "tool_call", "name": "read_skill", "id": "s",
                                                "args": {"path": "../README"}})
        self.assertEqual(bad.status, "error")
        good = await tools["read_skill"].ainvoke({"type": "tool_call", "name": "read_skill", "id": "t",
                                                 "args": {"path": "index"}})
        self.assertIn("SCOOP skills index", good.content)

    async def test_skill_drafts_follow_the_scout_convention_and_never_overwrite(self):
        import tempfile
        from pathlib import Path
        with tempfile.TemporaryDirectory() as tmp:
            skills = Path(tmp)
            (skills / "surveys").mkdir()
            (skills / "index.md").write_text("# SCOOP skills index\n\n## Surveys and archives (`surveys/`)\n\n"
                                             "| Skill | Use it for |\n|---|---|\n| [lamost](surveys/lamost.md) | x |\n\n"
                                             "## Literature\n")
            (skills / "surveys" / "lamost.md").write_text("reviewed")
            field = field_from_viewer_data(viewer_data())
            tools = {t.name: t for t in build_data_tools(field=field, store_rows=None, added=[], skills_dir=skills)}

            async def run(name, args):
                return await tools[name].ainvoke({"type": "tool_call", "name": name, "id": "x", "args": args})

            draft = {"name": "nvss", "survey": "NVSS", "description": "NVSS 1.4 GHz sources via VizieR",
                     "body": "## What's here\n- VIII/65/nvss", "sources_checked": ["VizieR VIII/65"]}
            saved = await run("save_skill_draft", draft)
            self.assertIn("Saved draft skill surveys/nvss", saved.content)
            text = (skills / "surveys" / "nvss.md").read_text()
            self.assertIn("\nstatus: draft\n", text)  # exactly what the scout's coverage reader expects
            self.assertIn("proposed_by: data agent", text)
            index = (skills / "index.md").read_text()
            self.assertIn("| [nvss](surveys/nvss.md) | NVSS 1.4 GHz sources via VizieR *(draft, written by the data agent)* |",
                          index)
            self.assertLess(index.index("surveys/nvss.md"), index.index("## Literature"))
            again = await run("save_skill_draft", draft)
            self.assertEqual(again.status, "error")
            refused = await run("save_skill_draft", {**draft, "name": "lamost"})
            self.assertEqual(refused.status, "error")
            self.assertEqual((skills / "surveys" / "lamost.md").read_text(), "reviewed")

    async def test_new_fields_are_sized_and_need_the_users_go_ahead(self):
        from langchain_core.tools import tool
        from typing import Annotated
        from langchain_core.tools import InjectedToolCallId
        from scoop.agents.data_agent import build_estimate_tool, size_gated
        from scoop.agents.tools import publish_result
        started = []

        @tool
        async def run_cone(radius_arcmin: float, tool_call_id: Annotated[str, InjectedToolCallId],
                           ra: float | None = None, dec: float | None = None, name: str | None = None,
                           label: str | None = None):
            """Start a cone."""
            started.append({"ra": ra, "dec": dec, "name": name, "label": label})
            return publish_result(tool_call_id, tool_call_id, {"kind": "field_job", "job": "j1", "key": "ngc_300"},
                                  "Started field 'ngc_300'.")

        sizes = {"sources": 2_000_000}

        def estimate(region, gmax=None):
            return {"sources": sizes["sources"], "low": 1, "high": 3, "area_deg2": region.area_deg2}

        def factory(confirmed):
            return [*size_gated([run_cone], confirmed=confirmed, estimate=estimate),
                    build_estimate_tool(estimate=estimate)]

        def handoff_with(*responses):
            model = ScriptedModel(*responses)
            return model, build_handoff_tool(model=model, field=None, store_rows=None, field_tools=factory)

        cone = {"radius_arcmin": 60, "ra": 13.72, "dec": -37.68, "name": "NGC 300: a cone"}
        model, handoff = handoff_with(call("run_cone", cone, "c1"), AIMessage(content="Needs a go-ahead."))
        result = await handoff.ainvoke({"type": "tool_call", "name": "ask_data_agent", "id": "h1",
                                        "args": {"request": "a degree around NGC 300"}})
        self.assertEqual(started, [])  # 2 million sources: refused without the user's go-ahead
        refusal = next(m for m in model.last if getattr(m, "tool_call_id", None) == "c1")
        self.assertIn("needs_confirmation", refusal.content)
        self.assertIn('"field": null', model.last[1].content)
        self.assertNotIn("query_catalog", model.tools)  # no field: nothing to query over yet
        self.assertIn("estimate_gaia", model.tools)

        model, handoff = handoff_with(call("run_cone", cone, "c2"), AIMessage(content="Started ngc_300."))
        result = await handoff.ainvoke({"type": "tool_call", "name": "ask_data_agent", "id": "h2",
                                        "args": {"request": "go ahead", "user_confirmed": True}})
        # Coordinates and a name: the name becomes the label rather than a second centre.
        self.assertEqual(started, [{"ra": 13.72, "dec": -37.68, "name": None, "label": "NGC 300: a cone"}])
        # The field job the data agent started reaches the browser through the hand-off.
        self.assertEqual(result.update["artifacts"]["c2"]["kind"], "field_job")
        self.assertIn("Fields started: ['ngc_300']", result.update["messages"][0].content)
        sized = next(m for m in model.last if getattr(m, "tool_call_id", None) == "c2")
        self.assertIn("about 2,000,000 Gaia sources", sized.content)

    async def test_the_data_agent_clears_added_tables_on_request(self):
        field = field_from_viewer_data(viewer_data())
        calls = []

        def remove(key, names):
            calls.append((key, names))
            return ["ps1_dr2", "twomass_psc"]

        model = ScriptedModel(call("remove_tables", {}, "r1"), AIMessage(content="Removed 2 added tables."))
        handoff = build_handoff_tool(model=model, field=field, store_rows=None, remove_tables=remove,
                                     added_tables=lambda key: ["ps1_dr2", "twomass_psc"])
        result = await handoff.ainvoke({"type": "tool_call", "name": "ask_data_agent", "id": "h1",
                                        "args": {"request": "clear the extra catalogs from this field"}})
        self.assertEqual(calls, [(field["key"], None)])
        self.assertEqual(result.update["artifacts"]["h1"]["kind"], "field_refresh")  # the browser reloads it
        self.assertEqual(result.update["artifacts"]["h1"]["removed"], ["ps1_dr2", "twomass_psc"])
        self.assertIn("ps1_dr2, twomass_psc", next(t for t in model.tools_full if t.name == "remove_tables").description)

    def test_the_prompt_names_every_add_data_source(self):
        from scoop.sources import SOURCES
        for name, source in SOURCES.items():
            if source.query:
                self.assertRegex(DATA_AGENT_PROMPT, rf"\b{name} \(", name)

    def test_prompts_describe_every_tool(self):
        self.assertRegex(WORKSPACE_PROMPT, r"\bask_data_agent\b")
        field = field_from_viewer_data(viewer_data())
        for t in build_data_tools(field=field, store_rows=None, added=[]):
            self.assertTrue(re.search(rf"\b{t.name}\b", DATA_AGENT_PROMPT), t.name)
        from scoop.agents.data_agent import FIELD_TOOLS
        from scoop.agents.region_tools import build_region_tools
        for name in [*FIELD_TOOLS, "estimate_gaia", *(t.name for t in build_region_tools())]:
            self.assertTrue(re.search(rf"\b{name}\b", DATA_AGENT_PROMPT), name)


if __name__ == "__main__":
    unittest.main()
