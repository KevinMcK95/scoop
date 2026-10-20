"""Spectra (SDSS DR20 index, DESI DR1) grouped by object, matched to Gaia."""
from . import Column, Enrichment, Source, detail, eq, layer, register, register_enrichment
from ._palette import COLORS

VALUES = ("spectype", "z", "zwarn", "rv", "rv_err", "feh", "teff", "logg")
COLUMNS = {"rv": Column("km/s", "DESI radial velocity"), "rv_err": Column("km/s"),
           "feh": Column("dex", "DESI [Fe/H]"), "teff": Column("K"), "logg": Column("dex"),
           "z": Column(None, "redshift"), "gaia_id": Column(None, "matched Gaia DR3 source")}


def rows(data: dict) -> list[dict]:
    out = []
    for key, obj in data["spectra"].items():
        kinds = sorted({s["kind"] for s in obj["spectra"]})
        # DESI's measured values become columns; other surveys only link to viewers.
        desi = next((s for s in obj["spectra"] if s["kind"] == "DESI"), {})
        out.append({"id": key, "ra": obj["ra"], "dec": obj["dec"], "n_spectra": len(obj["spectra"]),
                    "kinds": ", ".join(kinds), "has_desi": "DESI" in kinds,
                    "has_sdss": any(k != "DESI" for k in kinds),
                    "gaia_id": key if ":" not in key else None, **{c: desi.get(c) for c in VALUES}})
    return out


def spectrum(s: dict) -> dict:
    if s["kind"] == "DESI":
        title = f"DESI {s.get('survey', '')}/{s.get('program', '')} · {s.get('spectype', '')}"
        if s.get("spectype") != "STAR" and s.get("z") is not None:
            title += f" z = {s['z']}"
    else:
        title = f"{s['kind']} {s.get('survey', '')}"
        if s.get("program") not in (None, "", "None"):
            title += f" / {s['program']}"
        if s.get("apogee_id"):
            title += f" · {s['apogee_id']}"
    fields = {"MJD": s.get("mjd"), "RV (km/s)": s.get("rv"), "RV error (km/s)": s.get("rv_err"),
              "[Fe/H]": s.get("feh"), "Teff (K)": s.get("teff"), "log g": s.get("logg"),
              "ZWARN": s.get("zwarn") or None, "matched by": s.get("match")}
    return detail(title, fields, [tuple(link) for link in s.get("links", [])])


def attach(tables: dict[str, list[dict]], data: dict) -> None:
    """Spectra details on spectra rows, and on any row naming the same Gaia source."""
    details = {key: [spectrum(s) for s in obj["spectra"]] for key, obj in data["spectra"].items()}
    for row in tables["spectra"]:
        row["_details"] = {"Spectra": details.get(row["id"], [])}
    for name, rows in tables.items():
        if name == "spectra":
            continue
        for row in rows:
            key = row["id"] if name == "gaia" else row.get("gaia_id")
            if name == "gaia":
                row["has_spectra"] = key in details
                # DESI's line-of-sight velocity on the star itself, for velocity-vs-G and
                # membership selection in the gaia table (one table for every panel).
                desi = next((s for s in data["spectra"].get(key, {}).get("spectra", [])
                             if s.get("kind") == "DESI" and s.get("rv") is not None), None)
                if desi:
                    row.update({"rv": desi["rv"], "rv_err": desi.get("rv_err"), "feh": desi.get("feh")})
            if key in details:
                row["_details"] = {**row.get("_details", {}), "Spectra": details[key]}


def layers(rows: list[dict], data: dict) -> list[dict]:
    return [
        layer("spectra:sdss", "spectra", [eq("has_sdss", True)], "Spectra", "SDSS / APOGEE / MaNGA",
              COLORS["spectra"], order=30, shape="plus", size=14, visible=False),
        layer("spectra:desi", "spectra", [eq("has_desi", True)], "Spectra", "DESI DR1", COLORS["spectra"],
              order=31, shape="triangle", size=12, hollow=True, visible=False),
    ]


register(Source(
    name="spectra", label="Objects with spectra", provenance="SDSS DR20, DESI DR1", order=30,
    rows=rows, layers=layers, columns=COLUMNS,
    browse=("kinds", "n_spectra", "spectype", "rv", "rv_err", "feh", "teff", "logg"),
    presets=({"label": "RV histogram", "x": "rv", "title": "DESI radial velocities"},
             {"label": "RV vs [Fe/H]", "x": "feh", "y": "rv", "title": "DESI RV vs [Fe/H]"})))
register_enrichment(Enrichment("spectra details", needs=("spectra",), apply=attach))
