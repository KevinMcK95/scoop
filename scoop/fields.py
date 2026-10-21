"""Field results and layers: what the map shows, as JSON shared with the browser.

A field result holds tables of rows (``id``, ``ra``, ``dec``, optional
``s_region`` footprint or ``polygon``, plus flat properties). A layer is a view
of one table: a filter, a style and a label. Tables, their metadata and the
default layers come from the data sources in ``scoop/sources/``.

The filter evaluator here and ``web/src/lib/field/filter.ts`` must agree; both
are tested against ``tests/fixtures/filter_cases.json``.
"""
from __future__ import annotations

import json
import math
import re
from pathlib import Path
from typing import Any

from .spatial import polygon_contains, project, separation_deg

OPS = ("==", "!=", "<", "<=", ">", ">=", "in", "exists", "within", "inside", "lasso")
MAX_LASSO_VERTICES = 200
SHAPES = ("circle", "square", "plus", "cross", "rhomb", "triangle")
FIELD_KEY = re.compile(r"[A-Za-z0-9_.-]{1,64}")
def _within(value: Any) -> bool:
    return (isinstance(value, dict) and set(value) == {"ra", "dec", "radius"}
            and all(isinstance(value[k], (int, float)) and not isinstance(value[k], bool)
                    for k in value) and 0 <= value["ra"] < 360 and -90 <= value["dec"] <= 90
            and 0 < value["radius"] <= 180)


def _inside(value: Any) -> bool:
    points = value.get("polygon") if isinstance(value, dict) and set(value) == {"polygon"} else None
    return (isinstance(points, list) and 3 <= len(points) <= 64 and all(
        isinstance(p, list) and len(p) == 2 and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in p)
        and 0 <= p[0] < 360 and -90 <= p[1] <= 90 for p in points))


def _is_number(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _lasso(value: Any) -> bool:
    if not isinstance(value, dict) or not {"y", "polygons"} <= set(value) <= {"y", "polygons", "missing"}:
        return False
    polygons = value["polygons"]
    return (isinstance(value["y"], str) and value.get("missing", "reject") in ("keep", "reject")
            and isinstance(polygons, list) and 1 <= len(polygons) <= 10
            and sum(len(p) if isinstance(p, list) else 0 for p in polygons) <= MAX_LASSO_VERTICES
            and all(isinstance(poly, list) and len(poly) >= 3 and all(
                isinstance(p, list) and len(p) == 2 and all(_is_number(v) for v in p) for p in poly)
                for poly in polygons))


def plane_polygon_contains(points: list, x: float, y: float) -> bool:
    """Point in a polygon drawn on a plot (ray casting in the plot's own units)."""
    inside = False
    for i in range(len(points)):
        x1, y1 = points[i]
        x2, y2 = points[(i + 1) % len(points)]
        if (y1 > y) != (y2 > y) and x < x1 + (y - y1) * (x2 - x1) / (y2 - y1):
            inside = not inside
    return inside


def validate_filter(filters: Any, columns: set[str]) -> list[dict]:
    """Return the filter if every clause is well formed and names a known column.

    ``{"column": "position", "op": "within", "value": {"ra", "dec", "radius"}}``
    keeps rows within ``radius`` degrees of a point;
    ``{"column": "position", "op": "inside", "value": {"polygon": [[ra, dec], ...]}}``
    keeps rows inside a polygon (3–64 vertices, e.g. a box drawn on the map);
    ``{"column": x, "op": "lasso", "value": {"y": y, "polygons": [[[x, y], ...], ...],
    "missing": "keep" | "reject"}}`` keeps rows whose (x, y) values lie inside any of the
    polygons drawn on a plot of those columns; rows missing x or y pass with
    ``missing: "keep"`` (a panel only constrains stars it can plot) and fail otherwise.
    """
    if not isinstance(filters, list):
        raise ValueError("filter must be a list of clauses")
    for clause in filters:
        if not isinstance(clause, dict) or set(clause) - {"column", "op", "value"}:
            raise ValueError(f"bad clause {clause!r}")
        if clause.get("op") == "within":
            if clause.get("column") != "position" or not _within(clause.get("value")):
                raise ValueError("'within' needs column 'position' and value {ra, dec, radius} in degrees")
            continue
        if clause.get("op") == "inside":
            if clause.get("column") != "position" or not _inside(clause.get("value")):
                raise ValueError("'inside' needs column 'position' and value {polygon: [[ra, dec], ...]} "
                                 "with 3-64 vertices in degrees")
            continue
        if clause.get("op") == "lasso":
            if not _lasso(clause.get("value")):
                raise ValueError("'lasso' needs value {y: column, polygons: [[[x, y], ...], ...], "
                                 "missing?: 'keep' | 'reject'}: 1-10 polygons of 3+ numeric vertices, "
                                 f"at most {MAX_LASSO_VERTICES} in all")
            for name in (clause.get("column"), clause["value"]["y"]):
                if name not in columns:
                    raise ValueError(f"unknown column {name!r}; known: {sorted(columns)}")
            continue
        if clause.get("column") not in columns:
            raise ValueError(f"unknown column {clause.get('column')!r}; known: {sorted(columns)}")
        if clause.get("op") not in OPS:
            raise ValueError(f"unknown op {clause.get('op')!r}; use one of {OPS}")
        if clause["op"] == "in" and not isinstance(clause.get("value"), list):
            raise ValueError("'in' needs a list value")
        if clause["op"] != "exists" and "value" not in clause:
            raise ValueError(f"clause on {clause['column']!r} needs a value")
    return filters


def _equal(a: Any, b: Any) -> bool:
    # JSON semantics: true is not 1 (Python says it is).
    return a == b and isinstance(a, bool) == isinstance(b, bool)


def _clause(row: dict, clause: dict) -> bool:
    value, op, target = row.get(clause["column"]), clause["op"], clause.get("value")
    if op == "inside":
        ra, dec = row.get("ra"), row.get("dec")
        return (isinstance(ra, (int, float)) and isinstance(dec, (int, float))
                and polygon_contains(target["polygon"], ra, dec))
    if op == "lasso":
        x, y = value, row.get(target["y"])
        if not (_is_number(x) and _is_number(y)):
            return target.get("missing", "reject") == "keep"
        return any(plane_polygon_contains(poly, x, y) for poly in target["polygons"])
    if op == "within":
        ra, dec = row.get("ra"), row.get("dec")
        return (isinstance(ra, (int, float)) and isinstance(dec, (int, float))
                and separation_deg(ra, dec, target["ra"], target["dec"]) <= target["radius"])
    if op == "exists":
        return (value is not None and value != "") == bool(target if "value" in clause else True)
    if op == "==":
        return _equal(value, target)
    if op == "!=":
        return not _equal(value, target)
    if op == "in":
        return any(_equal(value, item) for item in target)
    # Ordered comparisons only between two numbers or two strings; missing never matches.
    numbers = all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in (value, target))
    if not numbers and not (isinstance(value, str) and isinstance(target, str)):
        return False
    return {"<": value < target, "<=": value <= target,
            ">": value > target, ">=": value >= target}[op]


def matches(row: dict, filters: list[dict]) -> bool:
    return all(_clause(row, clause) for clause in filters)


# ── Field result from the smoke-test pipeline ────────────────────────────────

def _columns(rows: list[dict]) -> list[str]:
    names: dict[str, None] = {}
    for row in rows:
        names.update(dict.fromkeys(row))
    # "_"-prefixed keys (links, details) are for display, not filtering.
    return [n for n in names if n not in ("id", "polygon") and not n.startswith("_")]


def field_from_viewer_data(data: dict) -> dict:
    """Build a field from pipeline output with every registered data source.

    Each data source (``scoop/sources/<name>.py``) supplies its table's rows,
    column metadata, default layers and presets; enrichments then add
    cross-source columns and details. Nothing here names a data source.
    """
    from .sources import ENRICHMENTS, SOURCES, Column

    present, rows = [], {}
    for source in sorted(SOURCES.values(), key=lambda s: (s.order, s.name)):
        try:
            rows[source.name] = source.rows(data)
        except KeyError:  # this pipeline output has nothing from that data source
            continue
        present.append(source)
    for source in present:
        if source.links:
            for row in rows[source.name]:
                links = source.links(row)
                if links:
                    row["_links"] = [list(link) for link in links]
    for enrichment in ENRICHMENTS:
        if all(name in rows for name in enrichment.needs):
            enrichment.apply(rows, data)

    # Positions are part of the field model, the same for every data source.
    position = {"ra": Column("deg", "ICRS right ascension"), "dec": Column("deg", "ICRS declination")}
    tables = {}
    for source in present:
        columns = _columns(rows[source.name])
        known = {**position, **source.columns}
        info = {c: known[c] for c in columns if c in known}
        tables[source.name] = {
            "label": source.label, "provenance": source.provenance, "kind": source.kind,
            "columns": columns, "units": {c: i.unit for c, i in info.items() if i.unit},
            "descriptions": {c: i.description for c, i in info.items() if i.description},
            "presets": [dict(p) for p in source.presets], "browse": list(source.browse),
            "rows": rows[source.name]}
    field = {"key": data.get("key", data["target"]["name"]), "target": data["target"],
             "n_rhalf": data["n_rhalf"], "fov": data["fov"], "region": data["ellipses"]["n"],
             "tables": tables,
             # Data-source summaries (e.g. MAST imaging coverage), shown in the Summary tab.
             "summaries": [{"source": s.name, **summary} for s in present for summary in s.summaries(rows[s.name], data)],
             "decisions": data["decisions"], "surveys": data["surveys"]}
    layers = [layer for source in present for layer in source.layers(rows[source.name], data)]
    for i, (name, stored) in enumerate(sorted((data.get("extra") or {}).items())):
        meta = stored.get("meta") if isinstance(stored, dict) else None
        if not meta or name in tables:
            continue
        tables[name], table_layer = discovered_table(name, stored["rows"], meta, i)
        layers.append(table_layer)
        link_to_gaia(tables, name)
    for table in tables.values():
        add_tangent_plane(table, data["target"])
    if data.get("query_region"):
        field["query_region"] = data["query_region"]
        if data["query_region"].get("kind") == "band":
            from .regions import Region
            band = Region.from_spec({k: v for k, v in data["query_region"].items() if k != "cuts"})
            for name, table in tables.items():
                add_stream_coordinates(table, band, presets=name == "gaia")
    layers.sort(key=lambda layer: layer.pop("order"))
    for layer in layers:
        layer["count"] = layer_count(field, layer)
        # On the user's list from the start if shown by default; others wait in "Add data".
        layer["added"] = layer["visible"]
    field["layers"] = layers
    return field


STREAM_PRESETS = (
    {"label": "Stream (φ1, φ2)", "x": "phi1", "y": "phi2", "title": "Along the stream: φ1 vs φ2"},
    {"label": "φ1 vs Δφ2", "x": "phi1", "y": "dphi2", "title": "Offset from the track across the stream"},
    {"label": "φ1 vs μφ1", "x": "phi1", "y": "pm_phi1", "title": "μφ1 cos φ2 along the stream"},
    {"label": "φ1 vs μφ2", "x": "phi1", "y": "pm_phi2", "title": "μφ2 along the stream"},
    {"label": "Δμ (stream frame)", "x": "dpm_phi1", "y": "dpm_phi2",
     "title": "Proper motion relative to the track, in the stream frame"},
)
STREAM_COLUMNS = {
    "phi1": ("deg", "stream longitude (along the track)"), "phi2": ("deg", "stream latitude (across)"),
    "dphi2": ("deg", "phi2 minus the track's phi2 at this phi1"),
    "pm_phi1": ("mas/yr", "proper motion along the stream, mu_phi1 cos phi2"),
    "pm_phi2": ("mas/yr", "proper motion across the stream, mu_phi2"),
    "dpm_phi1": ("mas/yr", "mu_phi1 cos phi2 minus the track's at this phi1"),
    "dpm_phi2": ("mas/yr", "mu_phi2 minus the track's at this phi1"),
}


def add_stream_coordinates(table: dict, band, presets: bool = False) -> None:
    """Stream-frame columns for a field queried along a band: phi1, phi2, the offset
    from the track, and (with proper motions) the motion along/across the stream and
    relative to the track's."""
    import numpy as np

    rows = [r for r in table["rows"] if _is_number(r.get("ra")) and _is_number(r.get("dec"))]
    if not rows or table.get("kind") in ("footprints", "annotation"):
        return
    ra, dec = np.array([r["ra"] for r in rows]), np.array([r["dec"] for r in rows])
    phi1, phi2 = band.frame.to_frame(ra, dec)
    dphi2 = phi2 - band.phi2_track(phi1)
    has_pm = np.array([_is_number(r.get("pmra")) and _is_number(r.get("pmdec")) for r in rows])
    pm = np.array([[r["pmra"], r["pmdec"]] if ok else [np.nan, np.nan] for r, ok in zip(rows, has_pm)])
    p1, p2 = band.frame.pm_to_frame(ra, dec, pm[:, 0], pm[:, 1])
    track = band.pm_track_frame(phi1)
    added = ["phi1", "phi2", "dphi2"]
    for k, r in enumerate(rows):
        r["phi1"], r["phi2"], r["dphi2"] = (round(float(v), 5) for v in (phi1[k], phi2[k], dphi2[k]))
        if has_pm[k]:
            r["pm_phi1"], r["pm_phi2"] = round(float(p1[k]), 4), round(float(p2[k]), 4)
            if track is not None:
                r["dpm_phi1"], r["dpm_phi2"] = round(float(p1[k] - track[0][k]), 4), round(float(p2[k] - track[1][k]), 4)
    if has_pm.any():
        added += ["pm_phi1", "pm_phi2"] + (["dpm_phi1", "dpm_phi2"] if track is not None else [])
    table["columns"] += [c for c in added if c not in table["columns"]]
    table.setdefault("units", {}).update({c: STREAM_COLUMNS[c][0] for c in added})
    table.setdefault("descriptions", {}).update({c: STREAM_COLUMNS[c][1] for c in added})
    if presets:
        table["presets"] = [*table.get("presets", []), *[dict(p) for p in STREAM_PRESETS if {p["x"], p["y"]} <= set(added)]]


def add_tangent_plane(table: dict, target: dict) -> None:
    """Give a point table ``xi`` / ``eta``: gnomonic coordinates about the target, in
    arcmin (xi east, eta north). Great circles are straight lines in this projection,
    so a lasso drawn on xi/eta selects the same stars as that polygon on the sky, with
    no RA wrap at 0/360 and no cos(dec) stretch."""
    if table.get("kind") in ("footprints", "annotation"):
        return
    added = False
    for row in table["rows"]:
        ra, dec = row.get("ra"), row.get("dec")
        if not (_is_number(ra) and _is_number(dec)):
            continue
        xy = project(ra, dec, target["ra"], target["dec"])
        if xy is not None:
            row["xi"], row["eta"] = round(xy[0] * 60, 5), round(xy[1] * 60, 5)
            added = True
    if not added:
        return
    table["columns"] += [c for c in ("xi", "eta") if c not in table["columns"]]
    table.setdefault("units", {}).update(xi="arcmin", eta="arcmin")
    table.setdefault("descriptions", {}).update(
        xi="tangent-plane offset east of the target", eta="tangent-plane offset north of the target")


# Colours for tables the data agent adds (dark-surface palette steps not used by sources).
DISCOVERED_COLORS = ("#e66767", "#c98500", "#9085e9", "#008300", "#d55181", "#3987e5")


# Columns that hold a velocity or metallicity, by name (case-insensitive), when the data
# agent didn't say which ("roles" in the table's meta).
MEASUREMENT_NAMES = {
    "rv": ("rv", "rvel", "hrv", "vhel", "vhelio", "vlos", "v_los", "vrad", "radial_velocity", "rv_adop",
           "helio_rv", "vr"),
    "rv_err": ("rv_err", "e_rv", "e_rvel", "e_hrv", "e_vhel", "e_vhelio", "vlos_err", "e_vlos", "e_vrad",
               "radial_velocity_error", "rv_adop_err", "rv_error", "e_vr", "verr"),
    "feh": ("feh", "[fe/h]", "fe_h", "feh_adop"),
    "feh_err": ("feh_err", "e_[fe/h]", "e_feh", "fe_h_err", "e_fe_h"),
}
MEASUREMENT_UNITS = {"rv": "km/s", "rv_err": "km/s", "feh": "dex", "feh_err": "dex"}


def measurement_roles(columns: list[str], explicit: dict | None = None) -> dict[str, str]:
    """Which columns are the line-of-sight velocity, [Fe/H] and their errors: as the data
    agent declared them, else by common names. An error only counts with its value."""
    explicit = {k: v for k, v in (explicit or {}).items() if k in MEASUREMENT_NAMES and v in columns}
    lower = {c.lower(): c for c in columns}
    roles = dict(explicit)
    for role, names in MEASUREMENT_NAMES.items():
        if role not in roles:
            found = next((lower[n] for n in names if n in lower), None)
            if found:
                roles[role] = found
    for value, err in (("rv", "rv_err"), ("feh", "feh_err")):
        if value not in roles:
            roles.pop(err, None)
    return roles


def add_measurements(table: dict, roles: dict[str, str], name: str) -> None:
    """Copy a table's velocity / metallicity columns into the standard rv, rv_err, feh,
    feh_err (so the RV plots, summaries and member fits read them), with RV presets."""
    for row in table["rows"]:
        for role, column in roles.items():
            if role != column and _is_number(row.get(column)):
                row[role] = row[column]
    added = [r for r in roles if r not in table["columns"]]
    table["columns"] += added
    for role, column in roles.items():
        table.setdefault("units", {}).setdefault(role, MEASUREMENT_UNITS[role])
        if role != column:
            table.setdefault("descriptions", {})[role] = f"{column}, as the standard {role} column"
    table["measurement_roles"] = dict(roles)
    if "rv" in roles:
        table["presets"] = [*table.get("presets", []), {"label": f"RV histogram · {name}", "x": "rv",
                                                 "title": f"Line-of-sight velocities: {table['label']}"}]
        if "feh" in roles:
            table["presets"].append({"label": f"RV vs [Fe/H] · {name}", "x": "feh", "y": "rv",
                                     "title": f"RV vs [Fe/H]: {table['label']}"})


def _combine(values: list[tuple[float, float | None]]) -> tuple[float, float | None]:
    """Inverse-variance mean (and its error) of repeated measurements; a plain mean when
    any error is missing."""
    if all(e is not None and e > 0 for _, e in values):
        w = [1 / e ** 2 for _, e in values]
        return sum(v * wi for (v, _), wi in zip(values, w)) / sum(w), 1 / math.sqrt(sum(w))
    return sum(v for v, _ in values) / len(values), None


def discovered_table(name: str, rows: list[dict], meta: dict, index: int = 0) -> tuple[dict, dict]:
    """A table and its default layer for data found at run time (no source plug-in)."""
    from .sources import layer

    if meta.get("kind") == "footprints":  # also tables stored before these columns existed
        from .observations import OBS_COLUMNS, add_observation_columns
        rows = [add_observation_columns(r) for r in rows]
        meta = {**meta, "columns": {**OBS_COLUMNS, **(meta.get("columns") or {})}}
    columns = _columns(rows)
    info = meta.get("columns") or {}
    style = meta.get("style") or {}
    table = {"label": meta["label"], "provenance": meta["provenance"], "kind": meta.get("kind", "catalog"),
             "columns": columns,
             "units": {c: info[c]["unit"] for c in columns if (info.get(c) or {}).get("unit")},
             "descriptions": {c: info[c]["description"] for c in columns if (info.get(c) or {}).get("description")},
             "presets": [], "browse": columns[:8], "rows": rows, "query": meta.get("query"),
             "origin": "agent"}
    if table["kind"] == "catalog":
        roles = measurement_roles(columns, meta.get("roles"))
        if roles:
            add_measurements(table, roles, name)
    lay = layer(f"agent-data:{name}", name, [], "Added by the data agent", meta["label"],
                style.get("color") or DISCOVERED_COLORS[index % len(DISCOVERED_COLORS)], order=90 + index,
                shape=style.get("shape", "circle"), size=int(style.get("size", 10)),
                hollow=bool(style.get("hollow", meta.get("kind") == "footprints")))
    lay["origin"] = "agent"
    return table, lay


GAIA_ID = re.compile(r"\d{15,19}")
LINK_RADIUS_ARCSEC = 1.0


def _gaia_id_column(rows: list[dict]) -> str | None:
    """A column holding Gaia source IDs as exact integers or digit strings (never floats,
    which would already have lost digits)."""
    names = {c for r in rows[:50] for c in r if not c.startswith("_")}
    for c in sorted(names):
        if "gaia" not in c.lower() and c.lower() != "source_id":
            continue
        values = [r.get(c) for r in rows[:50] if r.get(c) not in (None, "")]
        if values and all(isinstance(v, (int, str)) and not isinstance(v, bool)
                          and GAIA_ID.fullmatch(str(v)) for v in values):
            return c
    return None


def link_to_gaia(tables: dict, name: str, radius_arcsec: float = LINK_RADIUS_ARCSEC) -> None:
    """Match a discovered catalog to the field's Gaia sources, by a Gaia ID column if it has
    one, else by position. Matched rows get gaia_id / gaia_match; Gaia rows get in_<name>
    and a details group, so clicking a star shows its entries in the new catalog."""
    import bisect

    gaia, table = tables.get("gaia"), tables[name]
    if not gaia or table["kind"] != "catalog":
        return
    by_id = {str(r["id"]): r for r in gaia["rows"]}
    ordered = sorted((r for r in gaia["rows"] if r.get("ra") is not None and r.get("dec") is not None),
                     key=lambda r: r["dec"])
    decs = [r["dec"] for r in ordered]
    radius = radius_arcsec / 3600
    id_col = _gaia_id_column(table["rows"])

    def nearby(ra, dec):
        lo, hi = bisect.bisect_left(decs, dec - radius), bisect.bisect_right(decs, dec + radius)
        found = []
        for g in ordered[lo:hi]:
            dx = (g["ra"] - ra + 540) % 360 - 180
            sep = math.hypot(dx * math.cos(math.radians(dec)), g["dec"] - dec) * 3600
            if sep <= radius_arcsec:
                found.append((sep, g))
        return sorted(found, key=lambda x: x[0])

    shown = [c for c in table["columns"] if c not in ("gaia_id", "gaia_match")][:6]
    for row in table["rows"]:
        star, how = None, None
        if id_col and str(row.get(id_col)) in by_id:
            star, how = by_id[str(row[id_col])], f"Gaia ID ({id_col})"
        elif row.get("ra") is not None and row.get("dec") is not None:
            found = nearby(row["ra"], row["dec"])
            if found:
                star = found[0][1]
                how = f"position, {found[0][0]:.2f}″" + (" (ambiguous)" if len(found) > 1 else "")
        row["gaia_id"], row["gaia_match"] = (str(star["id"]), how) if star else (None, None)
        if star:
            star[f"in_{name}"] = True
            item = {"title": f"{table['label']} · {row['id']}",
                    "fields": {c: row[c] for c in shown if row.get(c) not in (None, "")} | {"matched by": how},
                    "links": [list(link) for link in row.get("_links", [])]}
            star["_details"] = {**star.get("_details", {}),
                                table["label"]: [*star.get("_details", {}).get(table["label"], []), item]}
    for g in gaia["rows"]:
        g.setdefault(f"in_{name}", False)
    # Velocities and metallicities reach the matched Gaia stars (repeats combined), where
    # the RV plots and the member fits read them; a star's existing value is kept.
    roles = table.get("measurement_roles") or {}
    for value, err in (("rv", "rv_err"), ("feh", "feh_err")):
        if value not in roles:
            continue
        per_star: dict[str, list] = {}
        for row in table["rows"]:
            if row.get("gaia_id") and _is_number(row.get(value)):
                e = row.get(err)
                per_star.setdefault(row["gaia_id"], []).append((float(row[value]), float(e) if _is_number(e) else None))
        for gid, measured in per_star.items():
            star = by_id[gid]
            if _is_number(star.get(value)):
                continue
            star[value], star[err] = _combine(measured)
            star[value] = round(star[value], 3)
            star[err] = None if star[err] is None else round(star[err], 3)
            star[f"{value}_source"] = table["label"] + (f" (mean of {len(measured)})" if len(measured) > 1 else "")
        gaia.setdefault("units", {}).setdefault(value, MEASUREMENT_UNITS[value])
        gaia["units"].setdefault(err, MEASUREMENT_UNITS[err])
        gaia["descriptions"][f"{value}_source"] = f"where {value} comes from"
    table["columns"] = _columns(table["rows"])
    gaia["columns"] = _columns(gaia["rows"])
    gaia["descriptions"][f"in_{name}"] = f"has a match in {table['label']}"
    table["descriptions"].update({"gaia_id": "matched Gaia DR3 source", "gaia_match": "how it was matched"})


def default_layers(field: dict) -> list[dict]:
    """The data sources' default presentation of a field (as built)."""
    return field["layers"]


def layer_count(field: dict, layer: dict) -> int:
    return sum(matches(row, layer["filter"]) for row in field["tables"][layer["table"]]["rows"])


def table_summary(table: dict, max_values: int = 8) -> dict:
    """Columns with numeric ranges or a few distinct values: what the agent sees."""
    columns = {}
    for name in table["columns"]:
        values = [row.get(name) for row in table["rows"] if row.get(name) not in (None, "")]
        numbers = [v for v in values if isinstance(v, (int, float)) and not isinstance(v, bool)
                   and math.isfinite(v)]
        if numbers and len(numbers) == len(values):
            columns[name] = {"type": "number", "min": min(numbers), "max": max(numbers),
                             "present": len(values)}
        elif values and all(isinstance(v, bool) for v in values):
            columns[name] = {"type": "bool", "true": sum(values), "present": len(values)}
        elif name not in ("s_region",):
            distinct = sorted({str(v) for v in values})
            columns[name] = {"type": "text", "distinct": len(distinct),
                             "examples": distinct[:max_values], "present": len(values)}
    for name, info in columns.items():
        if name in table.get("units", {}):
            info["unit"] = table["units"][name]
        if name in table.get("descriptions", {}):
            info["description"] = table["descriptions"][name]
    return {"label": table["label"], "provenance": table["provenance"],
            "rows": len(table["rows"]), "columns": columns}


def _quantile(sorted_values: list[float], q: float) -> float:
    """Linear interpolation between order statistics (numpy's default)."""
    pos = (len(sorted_values) - 1) * q
    lo, hi = math.floor(pos), math.ceil(pos)
    return sorted_values[lo] + (sorted_values[hi] - sorted_values[lo]) * (pos - lo)


def column_stats(rows: list[dict], column: str, *, bins: int = 20,
                 error_column: str | None = None) -> dict:
    """Distribution of one column over rows: robust numbers for numbers,
    counts for booleans and text. Missing values are counted, never imputed."""
    values = [row.get(column) for row in rows]
    present = [v for v in values if v not in (None, "")]
    out: dict = {"column": column, "n": len(present), "missing": len(values) - len(present)}
    numbers = [float(v) for v in present if isinstance(v, (int, float)) and not isinstance(v, bool)
               and math.isfinite(v)]
    if present and all(isinstance(v, bool) for v in present):
        out.update(type="bool", true=sum(present), false=len(present) - sum(present))
        return out
    if not numbers or len(numbers) != len(present):
        counts: dict[str, int] = {}
        for v in present:
            counts[str(v)] = counts.get(str(v), 0) + 1
        out.update(type="text", top=dict(sorted(counts.items(), key=lambda kv: -kv[1])[:10]),
                   distinct=len(counts))
        return out
    xs = sorted(numbers)
    n, mean = len(xs), sum(xs) / len(xs)
    median = _quantile(xs, 0.5)
    mad = _quantile(sorted(abs(x - median) for x in xs), 0.5)
    out.update(type="number", min=xs[0], max=xs[-1], mean=mean,
               std=math.sqrt(sum((x - mean) ** 2 for x in xs) / (n - 1)) if n > 1 else None,
               median=median, mad=mad, sigma_mad=1.4826 * mad,
               percentiles={str(q): _quantile(xs, q / 100) for q in (5, 16, 50, 84, 95)})
    if bins and xs[-1] > xs[0]:
        width = (xs[-1] - xs[0]) / bins
        counts = [0] * bins
        for x in xs:
            counts[min(int((x - xs[0]) / width), bins - 1)] += 1
        out["histogram"] = {"edges": [xs[0] + i * width for i in range(bins + 1)], "counts": counts}
    if error_column:
        pairs = [(float(r[column]), float(r[error_column])) for r in rows
                 if isinstance(r.get(column), (int, float)) and isinstance(r.get(error_column), (int, float))
                 and not isinstance(r.get(column), bool) and r[error_column] > 0]
        if pairs:
            weights = [1 / e ** 2 for _, e in pairs]
            wmean = sum(w * x for w, (x, _) in zip(weights, pairs)) / sum(weights)
            out["weighted_mean"] = {"value": wmean, "error": 1 / math.sqrt(sum(weights)),
                                    "n": len(pairs), "error_column": error_column}
            # A few very precise outliers (e.g. foreground stars) can dominate.
            if out["sigma_mad"] and abs(wmean - median) > 3 * out["sigma_mad"]:
                out["weighted_mean"]["warning"] = (
                    "Far from the median: a few low-error outliers dominate. Select members "
                    "(or clip outliers) before quoting a weighted mean.")
    return out


class FieldStore:
    """Field results under ``root/<dir>/<key>_field.json`` (smoke-test output).

    Rows from on-demand data-source queries live beside each field in
    ``<key>_extra.json`` ({source: {"rows": [...]}}), so pipeline output is never edited.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)
        self._cache: dict[str, tuple[tuple, dict]] = {}

    def keys(self) -> list[dict]:
        found = []
        for path in sorted(self.root.glob("*/*_field.json")):
            key = path.name.removesuffix("_field.json")
            found.append({"key": key, "path": str(path.relative_to(self.root))})
        return found

    def _path(self, key: str) -> Path | None:
        if not FIELD_KEY.fullmatch(key):
            return None
        return next(iter(sorted(self.root.glob(f"*/{key}_field.json"))), None)

    def _extra(self, path: Path) -> Path:
        return path.with_name(path.name.replace("_field.json", "_extra.json"))

    def get(self, key: str) -> dict | None:
        path = self._path(key)
        if path is None:
            return None
        extra = self._extra(path)
        stamp = (path.stat().st_mtime, extra.stat().st_mtime if extra.exists() else None)
        cached = self._cache.get(key)
        if cached is None or cached[0] != stamp:
            data = json.loads(path.read_text())
            data["extra"] = json.loads(extra.read_text()) if extra.exists() else {}
            cached = (stamp, field_from_viewer_data(data))
            self._cache[key] = cached
        return cached[1]

    def add_rows(self, key: str, source: str, rows: list[dict], meta: dict | None = None) -> None:
        """Store an on-demand query's rows for a field (replacing earlier ones).

        ``meta`` describes a table found by the data agent (label, provenance,
        kind, columns, layer style, the query), for sources with no plug-in.
        """
        path = self._path(key)
        if path is None:
            raise KeyError(key)
        extra = self._extra(path)
        stored = json.loads(extra.read_text()) if extra.exists() else {}
        stored[source] = {"rows": rows, **({"meta": meta} if meta else {})}
        extra.write_text(json.dumps(stored))


def field_region(field: dict) -> dict:
    """The field's queried area as a circle: target center, out to the farthest region vertex."""
    target = field["target"]
    radius = max((separation_deg(target["ra"], target["dec"], ra, dec) for ra, dec in field.get("region") or []),
                 default=target.get("rhalf_arcmin", 1) / 60)
    return {"ra": target["ra"], "dec": target["dec"], "radius_deg": radius}


__all__ = ["FieldStore", "field_region", "OPS", "SHAPES", "column_stats", "default_layers", "field_from_viewer_data",
           "layer_count", "matches", "table_summary", "validate_filter"]
