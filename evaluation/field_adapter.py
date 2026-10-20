"""Turn a SCOOP field (pipeline output, see ``scoop/fields.py``) into a result the checks read.

A field is what ``smoke_tests/lvdb_field.py`` writes as ``<key>_field.json``
and what the workspace server loads: one table per data source, the queried
region (an ellipse traced by vertices), and on-demand rows from
``<key>_extra.json``.

The pipeline records provenance once per table, not per row, so records made
here carry ``provenance_level = "table"``; the provenance check reports that
once per table instead of failing every row. Tolerances follow the pipeline's
rounding (G to 0.01 mag, dates to the day).

Each record keeps its row's links (``_links`` and the links inside
``_details``, e.g. spectrum viewers) for the link check, and the target's
literature references (LVDB keys such as ``Munoz2018ApJ...860...66M``) become
paper records for the paper checks.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from astropy.time import Time

# Archive names the ID re-checks recognize, by field table.
ARCHIVES = {"gaia": "Gaia DR3", "observations": "MAST", "simbad_galaxies": "SIMBAD"}

# What smoke_tests/lvdb_field.py keeps from MAST (query_mast and select_observations).
MAST_SELECTION = {"obs_collection": ["HST", "JWST"], "dataproduct_type": ["image"],
                  "exclude_project": ["HAP"], "min_calib_level": 2}

TOLERANCES = {
    "gaia_g_mag": 0.006,   # G is rounded to 0.01 mag in the field
    "position_mas": 1.0,   # positions keep 7 decimals (< 0.4 mas)
    "time_days": 1.0,      # observation dates are kept to the day
}


def row_links(row) -> list[list[str]]:
    """[url, label] pairs from a row's ``_links`` and from the ``links`` inside its ``_details``."""
    links = [list(link) for link in row.get("_links") or []]
    for items in (row.get("_details") or {}).values():
        for item in items if isinstance(items, list) else [items]:
            if isinstance(item, dict):
                links += [list(link) for link in item.get("links") or []]
    return links


def bibcode_of(ref: str) -> str | None:
    """The bibcode in an LVDB reference key: 'Munoz2018ApJ...860...66M' -> '2018ApJ...860...66M'."""
    tail = str(ref)[-19:]
    return tail if len(tail) == 19 and tail[:4].isdigit() else None


def load_field(field: str | Path, root: str | Path = "out"):
    """Load a field by key (e.g. ``draco_1``, searched under ``root``) or by path to ``*_field.json``.

    Returns ``(field, path)``.
    """
    from scoop.fields import FieldStore, field_from_viewer_data

    path = Path(field)
    if path.suffix == ".json" and path.is_file():
        data = json.loads(path.read_text())
        extra = path.with_name(path.name.replace("_field.json", "_extra.json"))
        data["extra"] = json.loads(extra.read_text()) if extra.exists() else {}
        return field_from_viewer_data(data), path
    store = FieldStore(Path(root))
    found = next((Path(root) / k["path"] for k in store.keys() if k["key"] == str(field)), None)
    if found is None:
        keys = ", ".join(k["key"] for k in store.keys()) or "none"
        raise FileNotFoundError(f"no field {field!r} under {root} (found: {keys})")
    return store.get(str(field)), found


def result_from_field(field: dict, path: Path) -> dict:
    """Records, region, tolerances and stated selection for one field."""
    target = field["target"]
    stamp = datetime.fromtimestamp(path.stat().st_mtime, timezone.utc).isoformat(timespec="seconds")
    records, status, notes = [], [], []

    for name, table in field["tables"].items():
        rows, label = table["rows"], table.get("provenance", name)
        archive = ARCHIVES.get(name, label)
        status.append({"archive": f"{label} (table {name!r})", "status": "returned" if rows else "empty",
                       "n": len(rows)})
        base = {"archive": archive, "query": f"{path.name}, table {name!r} ({label})", "retrieved_at": stamp,
                "access_url": None, "provenance_level": "table", "table": name, "table_provenance": label}
        if name == "planned":
            if rows:
                notes.append(f"Skipped {len(rows)} planned observations (no archive IDs yet).")
            continue
        for row in rows:
            links = row_links(row)
            if links:
                base = {**base, "links": links}
            else:
                base.pop("links", None)
            if name == "target":
                if row.get("id") == "center":
                    records.append({**base, "kind": "target", "native_id": target["name"],
                                    "ra": row["ra"], "dec": row["dec"]})
                continue
            if name == "observations":
                date = row.get("date")
                records.append({**base, "kind": "observation", "native_id": str(row["id"]),
                                "obs_collection": row.get("tel"), "instrument_name": row.get("inst"),
                                "filters": row.get("filters"), "dataproduct_type": "image",
                                "ra": row.get("ra"), "dec": row.get("dec"), "s_region": row.get("s_region"),
                                "t_min": Time(date).mjd if date else None, "t_exptime": row.get("exptime")})
                continue
            if row.get("ra") is None or row.get("dec") is None:
                continue
            rec = {**base, "kind": "catalog_row", "native_id": str(row["id"]), "ra": row["ra"], "dec": row["dec"]}
            if name == "gaia":
                rec["phot_g_mean_mag"] = row.get("G")
            records.append(rec)

    refs = {}
    for prop, ref in (target.get("references") or {}).items():
        bibcode = bibcode_of(ref)
        if bibcode:
            refs.setdefault(bibcode, []).append(prop)
    label = field["tables"].get("target", {}).get("provenance", "target")
    for bibcode, props in refs.items():
        records.append({"kind": "paper", "archive": "ADS", "native_id": bibcode, "about": target["name"],
                        "supports": props, "query": f"{path.name}, target references ({label})",
                        "retrieved_at": stamp, "access_url": f"https://ui.adsabs.harvard.edu/abs/{bibcode}",
                        "provenance_level": "table", "table": "target.references", "table_provenance": label})

    tolerances = dict(TOLERANCES)
    if target.get("rhalf_arcmin"):
        # Centers of extended objects differ between catalogs by a fraction of their size.
        tolerances["target_arcsec"] = max(2.0, 0.1 * target["rhalf_arcmin"] * 60)
    from .geometry import Region

    region = Region(target["ra"], target["dec"], polygon=field["region"])
    return {
        "query": {"target": target["name"], "ra": target["ra"], "dec": target["dec"],
                  "polygon": [list(v) for v in field["region"]], "radius_arcmin": region.radius_deg * 60,
                  "frame": "icrs", "field_key": field.get("key"), "n_rhalf": field.get("n_rhalf")},
        "generated_by": f"evaluation/field_adapter.py from {path}",
        "created_at": stamp,
        "archive_status": status,
        "selection": {"MAST": MAST_SELECTION} if "observations" in field["tables"] else {},
        "tolerances": tolerances,
        "notes": notes + list(field.get("decisions") or []),
        "records": records,
    }
