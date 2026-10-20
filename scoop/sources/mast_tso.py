"""HST and JWST spectra, time series and IFU cubes (MAST), executed and planned, on demand.

The field pipeline fetches HST/JWST *images* only (``observations``), which misses
most exoplanet work: transit and eclipse spectroscopy is stored as time series
(JWST NIRSpec BOTS, NIRISS SOSS, MIRI) or spectra (HST WFC3 grisms, STIS, COS), and
spectra of directly imaged planets as IFU cubes (NIRSpec IFU, MIRI MRS).
This source adds those, one row per observation, with a summary per program.
"""
from __future__ import annotations

import json
from datetime import date, timedelta

from . import Column, Source, eq, layer, register
from ._palette import COLORS
from .mast import program_link

MAST_URL = "https://mast.stsci.edu/api/v0/invoke"
MAX_RADIUS_DEG = 0.5  # spectra and time series sit on their targets; a field rarely needs more
# Scheduled JWST observations are only ever "image", "spectrum" or "cube": planned transit
# spectroscopy shows up as "spectrum" until it is taken (checked 2026-10-20).
TYPES = {"timeseries": "time series", "spectrum": "spectrum", "cube": "IFU cube"}
MJD_EPOCH = date(1858, 11, 17)


def rows(data: dict) -> list[dict]:
    return data["extra"]["hst_jwst_spectra"]["rows"]  # KeyError until queried for this field


def mjd_date(mjd: float | None) -> str | None:
    return (MJD_EPOCH + timedelta(days=mjd)).isoformat() if mjd else None


def mast_request(ra: float, dec: float, radius_deg: float) -> dict:
    """A MAST CAOM position query for HST/JWST spectra and time series (incl. planned)."""
    return {"service": "Mast.Caom.Filtered.Position", "format": "json", "pagesize": 10000,
            "params": {"columns": "*", "position": f"{ra}, {dec}, {radius_deg}", "filters": [
                {"paramName": "obs_collection", "values": ["HST", "JWST"]},
                {"paramName": "dataproduct_type", "values": list(TYPES)}]}}


def to_row(o: dict) -> dict:
    planned = o["calib_level"] == -1  # MAST lists scheduled JWST observations at calib level -1
    return {"id": o["obs_id"], "ra": o["s_ra"], "dec": o["s_dec"], "s_region": o["s_region"],
            "tel": o["obs_collection"], "inst": o["instrument_name"], "filters": o["filters"],
            "type": TYPES.get(o["dataproduct_type"], o["dataproduct_type"]),
            "status": "planned" if planned else "executed", "target": o["target_name"],
            "date": mjd_date(o["t_min"]), "exptime": o["t_exptime"], "program": o["proposal_id"],
            "pi": o["proposal_pi"], "title": o["obs_title"], "rights": o["dataRights"]}


def parse(response: dict) -> list[dict]:
    """MAST answers one record per observation and product level; keep one per observation."""
    if response.get("status") != "COMPLETE":
        raise RuntimeError(f"MAST query did not complete: {response.get('msg') or response.get('status')}")
    seen: dict[str, dict] = {}
    for o in response["data"]:
        if o["obs_id"] not in seen or o["calib_level"] > seen[o["obs_id"]]["calib_level"]:
            seen[o["obs_id"]] = o
    return sorted((to_row(o) for o in seen.values()), key=lambda r: (r["date"] or "9999", r["id"]))


async def query(region: dict) -> list[dict]:
    import httpx

    request = mast_request(region["ra"], region["dec"], min(region["radius_deg"], MAX_RADIUS_DEG))
    async with httpx.AsyncClient(timeout=120.0) as client:
        response = await client.post(MAST_URL, data={"request": json.dumps(request)})
        response.raise_for_status()
    return parse(response.json())


def layers(rows: list[dict], data: dict) -> list[dict]:
    kinds = sorted({(r["tel"], r["type"]) for r in rows if r["status"] == "executed"})
    out = [layer(f"tso:{tel}:{kind}", "hst_jwst_spectra",
                 [eq("tel", tel), eq("type", kind), eq("status", "executed")],
                 "HST/JWST spectra & time series", f"{tel} {kind}", COLORS.get(tel, COLORS["HST"]),
                 order=12, dash=kind == "spectrum") for tel, kind in kinds]
    if any(r["status"] == "planned" for r in rows):
        out.append(layer("tso:planned", "hst_jwst_spectra", [eq("status", "planned")],
                         "HST/JWST spectra & time series", "Planned (not yet observed)", COLORS["JWST"],
                         order=13, dash=True, hollow=True))
    return out


def summaries(rows: list[dict], data: dict) -> list[dict]:
    """One line per program: who observed what here, and why (the program title)."""
    programs: dict[tuple, dict] = {}
    for r in rows:
        p = programs.setdefault((r["tel"], r["program"]), {
            "insts": set(), "n": 0, "first": None, "planned": 0, "pi": r["pi"], "title": r["title"]})
        p["insts"].add(r["inst"])
        p["n"] += 1
        p["planned"] += r["status"] == "planned"
        if r["date"] and (p["first"] is None or r["date"] < p["first"]):
            p["first"] = r["date"]
    if not programs:
        return []
    return [{"title": "HST/JWST spectroscopy and time-series programs",
             "columns": ["program", "PI", "title", "instruments", "observations", "first", "planned"],
             "rows": [[f"{tel} {program}", p["pi"], p["title"], ", ".join(sorted(p["insts"])), p["n"],
                       p["first"], p["planned"]]
                      for (tel, program), p in sorted(programs.items(), key=lambda kv: kv[1]["first"] or "9999")],
             "note": "Transit and eclipse spectroscopy is listed as time series (JWST) or spectra (HST)."}]


register(Source(
    name="hst_jwst_spectra", label="HST/JWST spectra and time series", provenance="MAST",
    kind="footprints", order=12, rows=rows, layers=layers, query=query, summaries=summaries,
    links=program_link, browse=("tel", "inst", "type", "status", "target", "date", "program", "pi", "title"),
    columns={"exptime": Column("s"), "type": Column(None, "MAST data product type"),
             "status": Column(None, "executed, or planned (scheduled JWST observation)"),
             "target": Column(None, "target name given by the observer"),
             "title": Column(None, "program title")}))
