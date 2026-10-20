"""MILLIQUAS QSOs (VizieR VII/294), confirmed and radio/X-ray candidates."""
from . import Column, Source, eq, layer, register
from ._palette import COLORS


def rows(data: dict) -> list[dict]:
    return [{"id": m["data"]["MILLIQUAS"], "ra": m["ra"], "dec": m["dec"], "type": m["data"]["type"],
             "z": m["data"]["z"], "Rmag": m["data"]["Rmag"], "confirmed": m["confirmed"]}
            for m in data["milliquas"]]


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [
        layer("qso:milliquas", "milliquas", [eq("confirmed", True)], "Trusted QSOs", "MILLIQUAS confirmed",
              COLORS["qso"], order=52, shape="triangle", size=14),
        layer("qso:milliquas-candidates", "milliquas", [eq("confirmed", False)], "Trusted QSOs",
              "MILLIQUAS radio/X-ray candidates", COLORS["qso"], order=53, shape="triangle", size=10,
              hollow=True),
    ]


register(Source(name="milliquas", label="MILLIQUAS QSOs", provenance="VizieR VII/294", order=40,
                rows=rows, layers=layers, browse=("type", "z", "Rmag", "confirmed"),
                columns={"z": Column(None, "redshift"), "Rmag": Column("mag"),
                         "confirmed": Column(None, "spectroscopic redshift (class Q/A/B/K/N)")}))
