"""SIMBAD galaxies (not AGN), with Gaia's verdict on each."""
from . import Column, Source, eq, layer, register
from ._palette import COLORS


def rows(data: dict) -> list[dict]:
    return [{"id": g["data"]["SIMBAD"], "ra": g["ra"], "dec": g["dec"], "type": g["data"]["type"],
             "major_axis_arcmin": g["data"]["major axis (′)"], "gaia_verdict": g["data"]["Gaia"],
             "gaia_id": g["data"]["Gaia DR3"] or None, "flagged": g["flagged"], "unflagged": g["unflagged"]}
            for g in data["simbad_galaxies"]]


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [
        layer("simbad:flagged", "simbad_galaxies", [eq("flagged", True)], "SIMBAD galaxies",
              "Flagged by Gaia as galaxy candidates", COLORS["simbad"], order=40, size=20, visible=False),
        layer("simbad:unflagged", "simbad_galaxies", [eq("unflagged", True)], "SIMBAD galaxies",
              "Gaia source, not flagged", COLORS["simbad"], order=41, shape="cross", size=20, visible=False),
        layer("simbad:other", "simbad_galaxies", [eq("flagged", False), eq("unflagged", False)],
              "SIMBAD galaxies", "Other (no Gaia source, or QSO-flagged)", COLORS["simbad"], order=42,
              size=14, hollow=True, visible=False),
    ]


register(Source(name="simbad_galaxies", label="SIMBAD galaxies", provenance="SIMBAD TAP", order=50,
                rows=rows, layers=layers, browse=("type", "major_axis_arcmin", "gaia_verdict"),
                columns={"major_axis_arcmin": Column("arcmin")}))
