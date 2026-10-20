"""Gaia DR3 sources with quality flags and QSO/galaxy-candidate memberships."""
from . import Column, Source, eq, layer, register
from ._palette import COLORS

COLUMNS = {
    "G": Column("mag", "Gaia G"), "bp_rp": Column("mag", "Gaia BP-RP colour"),
    "parallax": Column("mas"), "parallax_error": Column("mas"),
    "pmra": Column("mas/yr", "proper motion in RA (×cos dec)"), "pmra_error": Column("mas/yr"),
    "pmdec": Column("mas/yr"), "pmdec_error": Column("mas/yr"),
    "pmra_pmdec_corr": Column(None, "correlation of the pmra and pmdec errors"),
    "ruwe": Column(None, "renormalised unit weight error; >1.4 suggests a poor astrometric fit"),
    "clean": Column(None, "passes RUWE, IPD, visibility-period and excess-noise cuts"),
    "r_ell": Column("r_half", "elliptical radius from the target center, in half-light radii"),
    "rv": Column("km/s", "DESI DR1 line-of-sight velocity (Milky Way Survey), where matched"),
    "rv_err": Column("km/s"), "feh": Column("dex", "DESI [Fe/H], where matched"),
    "gaia_crf": Column(None, "Gaia-CRF3 source (trusted QSO)"), "quaia": Column(None, "in Quaia (G < 20)"),
}
# The viewer data lists each Gaia source under the classes it belongs to.
FLAGS = {"stars": "star", "galaxies": "galaxy_candidate", "crf": "gaia_crf", "quaia": "quaia",
         "candidates": "qso_candidate", "rrlyrae": "rrlyrae_candidate"}


def rows(data: dict) -> list[dict]:
    gaia: dict[str, dict] = {}
    for group, flag in FLAGS.items():
        for r in data["gaia"][group]:
            d = r["data"]
            row = gaia.setdefault(d["Gaia DR3"], {
                "id": d["Gaia DR3"], "ra": r["ra"], "dec": r["dec"], "G": d["G"],
                "bp_rp": d["BP-RP"], "parallax": d["parallax (mas)"], "pmra": d["pmRA (mas/yr)"],
                "pmdec": d["pmDec (mas/yr)"], "parallax_error": d.get("parallax error (mas)"),
                "pmra_error": d.get("pmRA error (mas/yr)"), "pmdec_error": d.get("pmDec error (mas/yr)"),
                **({"pmra_pmdec_corr": d["pmRA-pmDec corr"]} if "pmRA-pmDec corr" in d else {}),
                "r_ell": d.get("r_ell (r_half)"), "ruwe": d["RUWE"], "clean": d["clean"],
                **{f: False for f in FLAGS.values()}})
            row[flag] = True
    return list(gaia.values())


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [
        layer("gaia:stars", "gaia", [eq("star", True)], "Gaia DR3", "Gaia sources", COLORS["star"],
              order=20, size=5, visible=False),
        layer("gaia:galaxies", "gaia", [eq("galaxy_candidate", True)], "Gaia DR3", "Gaia galaxy candidates",
              COLORS["galaxy"], order=21, shape="square", size=10, hollow=True, visible=False),
        layer("qso:crf", "gaia", [eq("gaia_crf", True)], "Trusted QSOs", "Gaia-CRF3", COLORS["qso"],
              order=50, shape="rhomb", size=16),
        layer("qso:quaia", "gaia", [eq("quaia", True)], "Trusted QSOs", "Quaia (G < 20)", COLORS["qso"],
              order=51, size=14),
        layer("gaia:qso-candidates", "gaia", [eq("qso_candidate", True)], "Gaia QSO candidates (impure)",
              "Other Gaia QSO candidates", COLORS["candidate"], order=60, size=9, visible=False),
        layer("gaia:rrlyrae", "gaia", [eq("rrlyrae_candidate", True)], "Gaia QSO candidates (impure)",
              "Candidates that are Gaia RR Lyrae", COLORS["candidate"], order=61, shape="cross", size=9,
              visible=False),
    ]


register(Source(
    name="gaia", label="Gaia DR3 sources", provenance="Gaia DR3 (ARI Heidelberg TAP)", order=20,
    rows=rows, layers=layers, columns=COLUMNS,
    browse=("G", "bp_rp", "parallax", "pmra", "pmdec", "ruwe", "r_ell", "clean"),
    presets=({"label": "CMD", "x": "bp_rp", "y": "G", "invert_y": True, "title": "Gaia colour–magnitude"},
             {"label": "Proper motion", "x": "pmra", "y": "pmdec", "title": "Gaia proper motions"},
             {"label": "Parallax / G", "x": "G", "y": "parallax", "title": "Parallax vs G"},
             {"label": "RV / G", "x": "G", "y": "rv", "title": "DESI line-of-sight velocity vs G"},
             {"label": "Sky (ξ, η)", "x": "xi", "y": "eta", "invert_x": True, "equal": True,
              "title": "Sky position about the target (tangent plane, east to the left)"})))
