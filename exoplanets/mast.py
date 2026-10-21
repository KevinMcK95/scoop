"""HST and JWST spectra, time series and IFU cubes from MAST, as plain functions.

Used by the workspace's data source (scoop/sources/mast_tso.py, one row per
observation on the map) and by the exoplanet agent (hst_jwst_programs: one line per
program, which is what a question like "has JWST looked at this planet?" needs).
"""

from __future__ import annotations

import json

from .archive import ArchiveError, http_get, now
from .tess import MAST_URL, mjd_date

# Scheduled JWST observations are only ever "image", "spectrum" or "cube": planned transit
# spectroscopy shows up as "spectrum" until it is taken (checked 2026-10-20).
TYPES = {"timeseries": "time series", "spectrum": "spectrum", "cube": "IFU cube"}
MAX_RADIUS_ARCMIN = 30  # spectra and time series sit on their targets; a field rarely needs more


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
        raise ArchiveError(f"MAST query did not complete: {response.get('msg') or response.get('status')}. "
                           "Report that MAST is unavailable.")
    seen: dict[str, dict] = {}
    for o in response["data"]:
        if o["obs_id"] not in seen or o["calib_level"] > seen[o["obs_id"]]["calib_level"]:
            seen[o["obs_id"]] = o
    return sorted((to_row(o) for o in seen.values()), key=lambda r: (r["date"] or "9999", r["id"]))


def hst_jwst_spectra(ra: float, dec: float, radius_arcmin: float) -> list[dict]:
    """One row per HST/JWST spectrum, time series or IFU cube in the cone, executed or planned."""
    request = mast_request(ra, dec, min(radius_arcmin, MAX_RADIUS_ARCMIN) / 60)
    return parse(json.loads(http_get(MAST_URL, {"request": json.dumps(request, sort_keys=True)}, post=True)))


def programs(rows: list[dict]) -> list[dict]:
    """One entry per program, earliest first: who observed here, with what, and why (title)."""
    grouped: dict[tuple, dict] = {}
    for r in rows:
        p = grouped.setdefault((r["tel"], r["program"]), {
            "program": f"{r['tel']} {r['program']}", "pi": r["pi"], "title": r["title"], "instruments": set(),
            "targets": set(), "observations": 0, "planned": 0, "first": None, "last": None})
        p["instruments"].add(r["inst"])
        if r["target"]:
            p["targets"].add(r["target"])
        p["observations"] += 1
        p["planned"] += r["status"] == "planned"
        if r["date"]:
            p["first"] = min(filter(None, (p["first"], r["date"])))
            p["last"] = max(filter(None, (p["last"], r["date"])))
    out = sorted(grouped.values(), key=lambda p: p["first"] or "9999")
    for p in out:
        p["instruments"], p["targets"] = sorted(p["instruments"]), sorted(p["targets"])
    return out


def hst_jwst_programs(ra: float, dec: float, radius_arcmin: float) -> dict:
    """HST/JWST spectroscopy, time-series and IFU programs in the cone, one line per program."""
    rows = hst_jwst_spectra(ra, dec, radius_arcmin)
    return {"center": {"ra": ra, "dec": dec}, "radius_arcmin": min(radius_arcmin, MAX_RADIUS_ARCMIN),
            "n_observations": len(rows), "n_programs": len({(r["tel"], r["program"]) for r in rows}),
            "programs": programs(rows), "archive": "MAST", "retrieved_at": now(),
            "note": "Transit and eclipse spectroscopy is listed as time series (JWST) or spectra (HST); "
                    "planned JWST observations appear as spectra until taken."}
