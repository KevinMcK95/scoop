"""HST and JWST spectra, time series and IFU cubes (MAST), executed and planned, on demand.

The field pipeline fetches HST/JWST *images* only (``observations``), which misses
most exoplanet work: transit and eclipse spectroscopy is stored as time series
(JWST NIRSpec BOTS, NIRISS SOSS, MIRI) or spectra (HST WFC3 grisms, STIS, COS), and
spectra of directly imaged planets as IFU cubes (NIRSpec IFU, MIRI MRS).
This source adds those, one row per observation, with a summary per program. The
MAST query itself is in scoop/exoplanets/mast.py, shared with the exoplanet agent.
"""
from __future__ import annotations

import asyncio

from . import Column, Source, eq, layer, register
from ._palette import COLORS
from .mast import program_link



def rows(data: dict) -> list[dict]:
    return data["extra"]["hst_jwst_spectra"]["rows"]  # KeyError until queried for this field


def _mast():
    from ..exoplanets import mast

    return mast


async def query(region: dict) -> list[dict]:
    return await asyncio.to_thread(_mast().hst_jwst_spectra, region["ra"], region["dec"],
                                   region["radius_deg"] * 60)


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
    programs = _mast().programs(rows)
    if not programs:
        return []
    return [{"title": "HST/JWST spectroscopy and time-series programs",
             "columns": ["program", "PI", "title", "instruments", "observations", "first", "planned"],
             "rows": [[p["program"], p["pi"], p["title"], ", ".join(p["instruments"]), p["observations"],
                       p["first"], p["planned"]] for p in programs],
             "note": "Transit and eclipse spectroscopy is listed as time series (JWST) or spectra (HST)."}]


register(Source(
    name="hst_jwst_spectra", label="HST/JWST spectra and time series", provenance="MAST",
    kind="footprints", order=12, rows=rows, layers=layers, query=query, summaries=summaries,
    links=program_link, browse=("tel", "inst", "type", "status", "target", "date", "program", "pi", "title"),
    columns={"exptime": Column("s"), "type": Column(None, "MAST data product type"),
             "status": Column(None, "executed, or planned (scheduled JWST observation)"),
             "target": Column(None, "target name given by the observer"),
             "title": Column(None, "program title")}))
