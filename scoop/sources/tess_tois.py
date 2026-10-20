"""TESS Objects of Interest (NASA Exoplanet Archive toi table), queried on demand.

Uses ``exoplanets/archive.py``'s TOI cone search, like the confirmed hosts in
``exoplanets.py``. One row per TOI, split into layers by TFOPWG disposition:
open candidates are shown; planets already confirmed or known, and false
positives, are offered under "Add data".
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from . import Column, Source, layer, register

ROOT = Path(__file__).resolve().parents[2]
MAX_RADIUS_ARCMIN = 60  # as for the confirmed hosts
EXOFOP_URL = "https://exofop.ipac.caltech.edu/tess/target.php?id="
COLOR = "#f0d264"  # the confirmed hosts' gold: same planets, different stage


def rows(data: dict) -> list[dict]:
    return data["extra"]["tess_tois"]["rows"]  # KeyError until queried for this field


def _cone(ra: float, dec: float, radius_arcmin: float) -> dict:
    if str(ROOT) not in sys.path:  # exoplanets/ is a top-level package beside scoop/
        sys.path.insert(0, str(ROOT))
    from exoplanets.archive import toi_cone_search

    return toi_cone_search(ra, dec, radius_arcmin)


async def query(region: dict) -> list[dict]:
    radius = min(region["radius_deg"] * 60, MAX_RADIUS_ARCMIN)
    result = await asyncio.to_thread(_cone, region["ra"], region["dec"], radius)
    return [{"id": t["native_id"], "ra": t["ra"], "dec": t["dec"], "tic_id": t["tic_id"],
             "disposition": t["disposition_code"], "status": t["disposition"],
             "period_days": t["orbital_period_days"], "radius_earth": t["radius_earth"],
             "depth_ppm": t["transit_depth_ppm"], "duration_hours": t["transit_duration_hours"],
             "teq_k": t["equilibrium_temp_k"], "tess_mag": t["tess_mag"], "distance_pc": t["distance_pc"],
             "created": t["created"], "retrieved_at": t["retrieved_at"]} for t in result["tois"]]


def exofop_link(row: dict) -> list[tuple[str, str]]:
    return [(EXOFOP_URL + str(row["tic_id"]), f"ExoFOP TIC {row['tic_id']}")] if row.get("tic_id") else []


def is_one_of(*codes: str) -> dict:
    return {"column": "disposition", "op": "in", "value": list(codes)}


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [
        layer("tois:candidates", "tess_tois", [is_one_of("PC", "APC")], "TESS TOIs", "Planet candidates",
              COLOR, order=56, shape="circle", size=12, hollow=True),
        layer("tois:planets", "tess_tois", [is_one_of("CP", "KP")], "TESS TOIs", "Confirmed or known planets",
              COLOR, order=57, shape="circle", size=8, visible=False),
        layer("tois:false", "tess_tois", [is_one_of("FP", "FA")], "TESS TOIs", "False positives and alarms",
              "#c3c2b7", order=58, shape="cross", size=9, visible=False),
    ]


register(Source(
    name="tess_tois", label="TESS Objects of Interest", provenance="NASA Exoplanet Archive (toi)",
    order=46, rows=rows, layers=layers, query=query, links=exofop_link,
    browse=("status", "period_days", "radius_earth", "depth_ppm", "tess_mag"),
    presets=({"label": "Radius vs period", "x": "period_days", "y": "radius_earth",
              "title": "TOI radius vs orbital period"},),
    columns={"tic_id": Column(None, "TESS Input Catalog ID"),
             "disposition": Column(None, "TFOPWG disposition code (PC, APC, CP, KP, FP, FA)"),
             "status": Column(None, "TFOPWG disposition"), "period_days": Column("d", "orbital period"),
             "radius_earth": Column("R⊕", "planet radius"), "depth_ppm": Column("ppm", "transit depth"),
             "duration_hours": Column("h", "transit duration"), "teq_k": Column("K", "equilibrium temperature"),
             "tess_mag": Column("mag", "TESS magnitude"), "distance_pc": Column("pc", "stellar distance"),
             "created": Column(None, "date the TOI was announced")}))
