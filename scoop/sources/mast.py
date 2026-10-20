"""HST and JWST observations (MAST), executed and planned, as footprints."""
from . import Column, Source, eq, layer, register
from ._palette import COLORS

COLUMNS = {"exptime": Column("s"), "year": Column("yr", "mid-exposure decimal year"),
           "frac_in_region": Column(None, "fraction of the footprint inside the query region")}


def program_link(row: dict) -> list[tuple[str, str]]:
    site = "jwst" if row.get("tel") == "JWST" else "hst"
    return [(f"https://www.stsci.edu/{site}-program-info/program/?program={row['program']}",
             f"STScI program {row['program']}")] if row.get("program") else []


def observations(data: dict) -> list[dict]:
    return [{"id": o["obs_id"], "ra": o["ra"], "dec": o["dec"], "s_region": o["s_region"],
             "tel": o["tel"], "inst": o["inst"], "filters": o["filters"], "date": o["date"],
             "year": o["year"], "exptime": o["exptime"], "program": o["program"], "pi": o["pi"],
             "rights": o["rights"], "frac_in_region": o["frac"]} for o in data["observations"]]


def planned(data: dict) -> list[dict]:
    return [{"id": f"planned-{i}", "ra": None, "dec": None, "s_region": p["s_region"], "tel": p["tel"],
             "inst": p["inst"], "filters": p["filters"], "program": p["program"], "pi": p["pi"]}
            for i, p in enumerate(data["planned"])]


def observation_layers(rows: list[dict], data: dict) -> list[dict]:
    return [layer(f"fp:{tel}:{inst}", "observations", [eq("tel", tel), eq("inst", inst)],
                  "HST/JWST footprints", f"{tel} {inst}", COLORS.get(tel, COLORS["HST"]), order=10)
            for tel, inst in sorted({(r["tel"], r["inst"]) for r in rows})]


def planned_layers(rows: list[dict], data: dict) -> list[dict]:
    return [layer("fp:planned", "planned", [], "HST/JWST footprints", "JWST planned (not yet observed)",
                  COLORS["JWST"], order=11, dash=True, hollow=True)] if rows else []


def observation_summaries(rows: list[dict], data: dict) -> list[dict]:
    """Coverage of the query region per instrument, and the longest overlap baselines
    (both computed by the pipeline from footprint MOCs)."""
    counts: dict[tuple, int] = {}
    for row in rows:
        counts[(row["tel"], row["inst"])] = counts.get((row["tel"], row["inst"]), 0) + 1
    coverage = {"title": "Imaging coverage", "columns": ["instrument", "observations", "region covered"],
                "rows": [[f"{c['tel']} {c['inst']}", counts.get((c["tel"], c["inst"]), 0), round(c["frac"], 4)]
                         for c in data.get("coverage", [])],
                "note": "Fraction of the query region covered by each instrument's footprints."}
    baselines = {"title": "Longest baselines (overlapping images)", "columns": ["pair", "years", "first", "second"],
                 "rows": [[b["pair"], b["years"], b["a"], b["b"]] for b in data.get("baselines", [])],
                 "note": "Per telescope pair; the overlaps tool lists every overlapping pair."}
    return [summary for summary in (coverage, baselines) if summary["rows"]]


register(Source(name="observations", label="HST/JWST observations", provenance="MAST",
                kind="footprints", order=10, rows=observations, layers=observation_layers,
                summaries=observation_summaries,
                columns=COLUMNS, links=program_link,
                browse=("tel", "inst", "filters", "date", "exptime", "program", "pi", "frac_in_region")))
register(Source(name="planned", label="Planned JWST observations", provenance="MAST",
                kind="footprints", order=11, rows=planned, layers=planned_layers, links=program_link,
                browse=("tel", "inst", "filters", "program", "pi")))
