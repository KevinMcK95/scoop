"""TESS sectors that imaged the field (full-frame images, MAST), queried on demand.

One row per sector, camera and CCD whose full-frame images cover part of the field,
drawn as that CCD's footprint; the summary says how many sectors, which, and when.
The query is exoplanets/tess.py's tess_sectors, shared with the exoplanet agent.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from . import Column, Source, layer, register

ROOT = Path(__file__).resolve().parents[2]
MAX_RADIUS_ARCMIN = 60
COLOR = "#7fb8e6"


def rows(data: dict) -> list[dict]:
    return data["extra"]["tess_sectors"]["rows"]  # KeyError until queried for this field


def _sectors(ra: float, dec: float, radius_arcmin: float) -> dict:
    if str(ROOT) not in sys.path:  # exoplanets/ is a top-level package beside scoop/
        sys.path.insert(0, str(ROOT))
    from exoplanets.tess import tess_sectors

    return tess_sectors(ra, dec, radius_arcmin)


async def query(region: dict) -> list[dict]:
    result = await asyncio.to_thread(_sectors, region["ra"], region["dec"],
                                     min(region["radius_deg"] * 60, MAX_RADIUS_ARCMIN))
    return [{"id": f"s{c['sector']:04d}-{c['camera']}-{c['ccd']}", "ra": None, "dec": None,
             "s_region": c["s_region"], "sector": c["sector"], "camera": c["camera"], "ccd": c["ccd"],
             "start": c["start"], "end": c["end"], "ffi_cadence": c["ffi_cadence"]} for c in result["ccds"]]


def layers(rows: list[dict], data: dict) -> list[dict]:
    n = len({r["sector"] for r in rows})
    return [layer("tess:sectors", "tess_sectors", [], "TESS", f"TESS sectors ({n})", COLOR,
                  order=14, dash=True, hollow=True)] if rows else []


def summaries(rows: list[dict], data: dict) -> list[dict]:
    sectors: dict[int, dict] = {}
    for r in rows:
        s = sectors.setdefault(r["sector"], {"ccds": [], "start": r["start"], "end": r["end"],
                                             "cadence": r["ffi_cadence"]})
        s["ccds"].append(f"{r['camera']}.{r['ccd']}")
    return [{"title": f"TESS coverage: {len(sectors)} sector{'s' if len(sectors) != 1 else ''}",
             "columns": ["sector", "camera.CCD", "start", "end", "FFI cadence"],
             "rows": [[n, ", ".join(s["ccds"]), s["start"], s["end"], s["cadence"]]
                      for n, s in sorted(sectors.items())],
             "note": "Sectors whose full-frame images cover any part of the field; stars near a CCD "
                     "edge or gap may be missing from some."}]


register(Source(
    name="tess_sectors", label="TESS sectors", provenance="MAST (TESS full-frame images)",
    kind="footprints", order=14, rows=rows, layers=layers, query=query, summaries=summaries,
    browse=("sector", "camera", "ccd", "start", "end", "ffi_cadence"),
    columns={"sector": Column(None, "TESS sector"), "camera": Column(None, "TESS camera (1-4)"),
             "ccd": Column(None, "CCD on the camera (1-4)"), "start": Column(None, "first image"),
             "end": Column(None, "last image"), "ffi_cadence": Column(None, "full-frame image cadence")}))
