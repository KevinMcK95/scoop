"""Confirmed planet hosts (NASA Exoplanet Archive), queried on demand.

Uses Xuan Ji's ``exoplanets/archive.py`` cone search (plain functions, no agent
harness; synchronous, with its record/replay HTTP layer), run in a worker thread.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from . import Column, Source, layer, register

ROOT = Path(__file__).resolve().parents[2]
MAX_RADIUS_ARCMIN = 60  # the cone search's own limit


def rows(data: dict) -> list[dict]:
    return data["extra"]["exoplanet_hosts"]["rows"]  # KeyError until queried for this field


def _cone(ra: float, dec: float, radius_arcmin: float) -> dict:
    if str(ROOT) not in sys.path:  # exoplanets/ is a top-level package beside scoop/
        sys.path.insert(0, str(ROOT))
    from exoplanets.archive import exoplanet_cone_search

    return exoplanet_cone_search(ra, dec, radius_arcmin)


async def query(region: dict) -> list[dict]:
    radius = min(region["radius_deg"] * 60, MAX_RADIUS_ARCMIN)
    result = await asyncio.to_thread(_cone, region["ra"], region["dec"], radius)
    return [{"id": h["native_id"], "ra": h["ra"], "dec": h["dec"], "n_planets": h["n_planets"],
             "distance_pc": h["distance_pc"], "planets": ", ".join(h["planets"]),
             "retrieved_at": h["retrieved_at"]} for h in result["hosts"]]


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [layer("exoplanets:hosts", "exoplanet_hosts", [], "Exoplanet hosts", "Confirmed planet hosts",
                  "#f0d264", order=55, shape="rhomb", size=14, hollow=True)]


register(Source(
    name="exoplanet_hosts", label="Confirmed planet hosts", provenance="NASA Exoplanet Archive (pscomppars)",
    order=45, rows=rows, layers=layers, query=query, browse=("n_planets", "distance_pc", "planets"),
    columns={"n_planets": Column(None, "confirmed planets"), "distance_pc": Column("pc", "system distance"),
             "planets": Column(None, "planet names")}))
