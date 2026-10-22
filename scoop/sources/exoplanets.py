"""Confirmed planet hosts (NASA Exoplanet Archive), queried on demand.

Uses Xuan Ji's ``scoop/exoplanets/archive.py`` cone search (plain functions, no agent
harness; synchronous, with its record/replay HTTP layer), run in a worker thread.
Each host carries its planets (parameters and discovery paper) for the Selected panel.
"""
from __future__ import annotations

import asyncio
from urllib.parse import quote

from . import Column, Source, detail, layer, register

MAX_RADIUS_ARCMIN = 60  # the cone search's own limit
OVERVIEW_URL = "https://exoplanetarchive.ipac.caltech.edu/overview/"
ADS_URL = "https://ui.adsabs.harvard.edu/abs/"
# pscomppars' pl_bmassprov: over a third of masses are not measured but estimated from the radius.
MASS_TYPE = {"Mass": "measured", "Msini": "minimum (M sin i)", "Msin(i)/sin(i)": "measured (M sin i / sin i)",
             "M-R relationship": "estimated from radius"}


def rows(data: dict) -> list[dict]:
    return data["extra"]["exoplanet_hosts"]["rows"]  # KeyError until queried for this field


def _cone(ra: float, dec: float, radius_arcmin: float) -> tuple[dict, dict[str, list[dict]]]:
    from ..exoplanets.archive import exoplanet_cone_search, planets_in_cone

    return exoplanet_cone_search(ra, dec, radius_arcmin), planets_in_cone(ra, dec, radius_arcmin)


def planet(p: dict) -> dict:
    """One planet for the Selected panel; its discovery paper links to ADS."""
    fields = {"period (d)": p["orbital_period_days"], "radius (R⊕)": p["radius_earth"],
              "mass (M⊕)": p["mass_earth"], "mass type": MASS_TYPE.get(p["mass_type"], p["mass_type"]) if p["mass_earth"] is not None else None,
              "T_eq (K)": p["equilibrium_temp_k"], "method": p["discovery_method"],
              "discovered": " · ".join(str(v) for v in (p["discovery_year"], p["discovery_facility"]) if v),
              "discovery paper": p["reference"]}
    links = [(ADS_URL + p["bibcode"], "discovery paper (ADS)")] if p["bibcode"] else []
    return detail(p["native_id"], fields, links)


async def query(region: dict) -> list[dict]:
    radius = min(region["radius_deg"] * 60, MAX_RADIUS_ARCMIN)
    result, planets = await asyncio.to_thread(_cone, region["ra"], region["dec"], radius)
    return [{"id": h["native_id"], "ra": h["ra"], "dec": h["dec"], "n_planets": h["n_planets"],
             "distance_pc": h["distance_pc"], "planets": ", ".join(h["planets"]),
             "retrieved_at": h["retrieved_at"],
             "_details": {"Planets": [planet(p) for p in planets.get(h["native_id"], [])]}}
            for h in result["hosts"]]


def overview_link(row: dict) -> list[tuple[str, str]]:
    return [(OVERVIEW_URL + quote(row["id"]), "Exoplanet Archive overview")]


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [layer("exoplanets:hosts", "exoplanet_hosts", [], "Exoplanet hosts", "Confirmed planet hosts",
                  "#f0d264", order=55, shape="rhomb", size=14, hollow=True)]


register(Source(
    name="exoplanet_hosts", label="Confirmed planet hosts", provenance="NASA Exoplanet Archive (pscomppars)",
    order=45, rows=rows, layers=layers, query=query, links=overview_link, browse=("n_planets", "distance_pc", "planets"),
    columns={"n_planets": Column(None, "confirmed planets"), "distance_pc": Column("pc", "system distance"),
             "planets": Column(None, "planet names")}))
