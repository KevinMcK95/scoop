"""TESS Input Catalog stars (TIC v8.2, VizieR) brighter than a magnitude limit, on demand.

The limit is an option the user sets on the page (default Tmag < 16; the reason is
shown with it). The query is exoplanets/tess.py's tic_search, shared with the agent.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path

from . import Column, Option, Source, layer, register

ROOT = Path(__file__).resolve().parents[2]
MAX_RADIUS_ARCMIN = 60
EXOFOP_URL = "https://exofop.ipac.caltech.edu/tess/target.php?id="
COLOR = "#c3c2b7"


def _tess():
    if str(ROOT) not in sys.path:  # exoplanets/ is a top-level package beside scoop/
        sys.path.insert(0, str(ROOT))
    from exoplanets import tess

    return tess


def rows(data: dict) -> list[dict]:
    return data["extra"]["tic"]["rows"]  # KeyError until queried for this field


async def query(region: dict, tmag_max: float) -> list[dict]:
    result = await asyncio.to_thread(_tess().tic_search, region["ra"], region["dec"],
                                     min(region["radius_deg"] * 60, MAX_RADIUS_ARCMIN), tmag_max)
    return [{"id": f"TIC {s['tic_id']}", "tic_id": s["tic_id"], "ra": s["ra"], "dec": s["dec"],
             "tmag": s["tmag"], "teff": s["teff_k"], "logg": s["logg"], "radius": s["radius_rsun"],
             "mass": s["mass_msun"], "distance_pc": s["distance_pc"], "contamination": s["contamination"],
             "lum_class": s["lum_class"], "gaia_dr2": s["gaia_dr2_id"]} for s in result["stars"]]


def exofop_link(row: dict) -> list[tuple[str, str]]:
    return [(EXOFOP_URL + str(row["tic_id"]), f"ExoFOP TIC {row['tic_id']}")]


def limit(data: dict) -> float:
    return data["extra"]["tic"].get("options", {}).get("tmag_max", _tess().DEFAULT_TMAG_MAX)


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [layer("tic:stars", "tic", [], "TIC stars", f"TIC stars, Tmag < {limit(data):g}", COLOR,
                  order=47, shape="circle", size=6, hollow=True)]


def summaries(rows: list[dict], data: dict) -> list[dict]:
    tess, tmag_max = _tess(), limit(data)
    note = tess.TIC_REASON if tmag_max == tess.DEFAULT_TMAG_MAX else (
        f"Limit set to Tmag < {tmag_max:g} (default {tess.DEFAULT_TMAG_MAX:g}: {tess.TIC_REASON})")
    if len(rows) >= tess.MAX_TIC_ROWS:
        note += f" Only the brightest {tess.MAX_TIC_ROWS} stars are listed: lower the limit to see all."
    return [{"title": f"TIC stars: {len(rows)} with Tmag < {tmag_max:g}", "columns": ["limit", "stars"],
             "rows": [[f"Tmag < {tmag_max:g}", len(rows)]], "note": note}]


register(Source(
    name="tic", label="TIC stars (TESS Input Catalog)", provenance="TIC v8.2 (VizieR IV/39/tic82)",
    order=47, rows=rows, layers=layers, query=query, summaries=summaries, links=exofop_link,
    options=(Option("tmag_max", "Tmag <", 16, 4, 20, 0.5,
                    "Tmag < 16 is about the faintest stars with ready-made TESS light curves from the "
                    "full-frame images; raise it for more stars, lower it for the best ones."),),
    browse=("tmag", "teff", "logg", "radius", "distance_pc", "contamination", "lum_class"),
    presets=({"label": "Tmag histogram", "x": "tmag", "title": "TIC stars by TESS magnitude"},
             {"label": "Teff vs radius", "x": "teff", "y": "radius", "invert_x": True,
              "title": "TIC stars: Teff vs radius"}),
    columns={"tic_id": Column(None, "TESS Input Catalog ID"), "tmag": Column("mag", "TESS magnitude"),
             "teff": Column("K", "effective temperature"), "logg": Column("dex", "surface gravity"),
             "radius": Column("R_sun", "stellar radius"), "mass": Column("M_sun", "stellar mass"),
             "distance_pc": Column("pc", "distance"),
             "contamination": Column(None, "flux from neighbours / the star's own flux (TIC Rcont)"),
             "lum_class": Column(None, "DWARF, SUBGIANT or GIANT"), "gaia_dr2": Column(None, "Gaia DR2 source ID")}))
