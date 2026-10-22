"""Map actions the agent can request, plus tools to load and inspect fields.

Actions are structured requests to the browser renderer, never JavaScript. They
are validated here against the loaded field result and the browser's current
layers, published as artifacts, and only count as applied once the browser
acknowledges them on a later turn. See docs/map_interaction.md.
"""
from __future__ import annotations

import json
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from ..science.fields import OPS, SHAPES, column_stats, layer_count, matches, table_summary, validate_filter
from ..archives.images import SURVEYS, cutout_params, data_url
from ..archives.lookup import lookup_position
from ..science.spatial import Footprint, footprint_contains, footprint_overlaps_circle, polygon_contains_circle, separation_deg
from .tools import publish_result, tool_error

Ra = Annotated[float, Field(ge=0, lt=360)]
Dec = Annotated[float, Field(ge=-90, le=90)]
Angle = Annotated[float, Field(gt=0, le=180)]
Text = Annotated[str, Field(min_length=1, max_length=300, pattern=r"\S")]
Id = Annotated[str, Field(min_length=1, max_length=80)]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class CircleValue(Strict):
    """Value of a `within` clause: a circle on the sky, degrees."""
    ra: Ra = Field(description="center right ascension, degrees")
    dec: Dec = Field(description="center declination, degrees")
    radius: Angle = Field(description="radius in degrees (1 arcmin = 0.01667)")


class PolygonValue(Strict):
    """Value of an `inside` clause: a sky polygon, degrees."""
    polygon: list[Annotated[list[float], Field(min_length=2, max_length=2)]] = Field(
        min_length=3, max_length=64, description="vertices [[ra, dec], ...] in degrees")


class LassoValue(Strict):
    """Value of a `lasso` clause: polygons drawn on a plot of `column` (x) against `y`."""
    y: str = Field(description="the plot's y column")
    polygons: list[list[Annotated[list[float], Field(min_length=2, max_length=2)]]] = Field(
        min_length=1, max_length=10,
        description="one or more polygons [[[x, y], ...], ...] in the plotted columns' units; inside any counts")
    missing: Literal["keep", "reject"] = Field(
        default="reject", description='"keep": rows without x or y pass (as in the user\'s Members selection)')


class Clause(Strict):
    """One condition; all clauses of a filter must hold."""
    column: str = Field(description='a column name ("lasso": the x column), or "position" for within/inside')
    op: Literal[OPS]
    # Explicit models (not a bare dict) so the schema shows the model the keys it needs.
    value: (str | float | int | bool | list[str | float | int | bool] | CircleValue | PolygonValue
            | LassoValue | None) = Field(default=None, description=(
                'compared value; a list for "in"; {"ra", "dec", "radius"} for "within"; '
                '{"polygon": [[ra, dec], ...]} for "inside"; {"y", "polygons", "missing"} for '
                '"lasso"; omit for "exists"'))


# Names models reach for that Aladin draws under another name.
SHAPE_SYNONYMS = {"diamond": "rhomb", "rhombus": "rhomb", "x": "cross", "+": "plus", "box": "square",
                  "dot": "circle", "point": "circle", "star": "plus"}


def _shape(value):
    return SHAPE_SYNONYMS.get(value.strip().lower(), value.strip().lower()) if isinstance(value, str) else value


class Style(Strict):
    color: str = Field(default="#ffd84d", pattern=r"^#[0-9a-fA-F]{6}$", description="#rrggbb")
    shape: Annotated[Literal[SHAPES], BeforeValidator(_shape)] = Field(
        default="circle", description="marker: circle, square, plus, cross, rhomb (diamond) or triangle")
    size: int = Field(default=12, ge=2, le=40)
    dash: bool = False
    hollow: bool = False


class LayerAdd(Strict):
    type: Literal["layer.add"]
    id: Id
    table: str
    filter: list[Clause] = []
    label: Text
    group: Text = "SCOOP layers"
    style: Style = Style()
    note: Text | None = None


class LayerUpdate(Strict):
    type: Literal["layer.update"]
    layerId: Id
    visible: bool | None = None
    added: bool | None = None
    filter: list[Clause] | None = None
    style: Style | None = None
    label: Text | None = None
    note: Text | None = None


class LayerRemove(Strict):
    type: Literal["layer.remove"]
    layerId: Id


class Focus(Strict):
    type: Literal["focus"]
    ra: Ra
    dec: Dec
    fov: Angle


class FocusRows(Strict):
    type: Literal["focus.rows"]
    table: str
    ids: list[Id] = Field(min_length=1, max_length=200)


class Highlight(Strict):
    type: Literal["highlight"]
    table: str
    ids: list[Id] = Field(max_length=200)
    reason: Text | None = None


class Annotate(Strict):
    type: Literal["annotate"]
    shape: Literal["circle", "label", "polygon"]
    ra: Ra
    dec: Dec
    text: Text
    radius: Angle | None = None
    points: list[Annotated[list[float], Field(min_length=2, max_length=2)]] | None = Field(
        default=None, min_length=3, max_length=64)
    table: str | None = None
    rowId: Id | None = None


class ClearAnnotations(Strict):
    type: Literal["annotations.clear"]


class Select(Strict):
    type: Literal["select"]
    table: str
    id: Id


class PlotShow(Strict):
    type: Literal["plot.show"]
    table: str
    x: str
    y: str | None = None
    invert_x: bool = False
    invert_y: bool = False
    equal: bool = False  # same scale on both axes (sky positions)
    title: Text | None = None


class PlotHide(Strict):
    type: Literal["plot.hide"]


MapAction = Annotated[
    LayerAdd | LayerUpdate | LayerRemove | Focus | FocusRows | Highlight | Annotate
    | ClearAnnotations | Select | PlotShow | PlotHide,
    Field(discriminator="type"),
]


def _row_ids(field: dict, table: str) -> set[str]:
    return {row["id"] for row in field["tables"][table]["rows"]}


def check_action(action: BaseModel, field: dict | None, layers: dict[str, dict]) -> str | None:
    """Return why the action cannot apply to this field and layer set, or None."""
    tables = field["tables"] if field else {}
    table = getattr(action, "table", None)
    if table is not None and table not in tables:
        return f"Unknown table {table!r}; tables: {sorted(tables) or 'none (no field loaded)'}."
    if isinstance(action, (LayerAdd, LayerUpdate)) and action.filter is not None:
        name = action.table if isinstance(action, LayerAdd) else layers.get(action.layerId, {}).get("table")
        if name in tables:
            try:
                validate_filter([c.model_dump(exclude_unset=True) for c in action.filter],
                                {"id", *tables[name]["columns"]})
            except ValueError as exc:
                return f"Invalid filter: {exc}"
    if isinstance(action, LayerAdd) and action.id in layers:
        return f"Layer {action.id!r} already exists; use layer.update."
    if isinstance(action, (LayerUpdate, LayerRemove)):
        layer = layers.get(action.layerId)
        if layer is None:
            return f"Unknown layer {action.layerId!r}; inspect the current layers first."
        if isinstance(action, LayerRemove) and layer.get("origin") == "pipeline":
            return "Pipeline layers can be hidden with layer.update but not removed."
    if isinstance(action, PlotShow):
        rows = tables[action.table]["rows"]
        for column in filter(None, (action.x, action.y)):
            if not any(isinstance(r.get(column), (int, float)) and not isinstance(r.get(column), bool)
                       for r in rows):
                return f"{column!r} is not a numeric column of {action.table}."
    ids = getattr(action, "ids", None) or ([action.id] if isinstance(action, Select) else [])
    if isinstance(action, Annotate):
        if action.shape == "circle" and action.radius is None:
            return "A circle needs a radius."
        if action.shape == "polygon" and not action.points:
            return "A polygon needs points [[ra, dec], ...]."
        if action.points and not all(0 <= ra < 360 and -90 <= dec <= 90 for ra, dec in action.points):
            return "Polygon points must be [ra, dec] in degrees."
    if isinstance(action, Annotate) and action.rowId is not None:
        if action.table is None:
            return "An anchored annotation needs both table and rowId."
        ids = [action.rowId]
    if ids:
        unknown = [i for i in ids if i not in _row_ids(field, table)]
        if unknown:
            return f"Unknown {table} ids: {unknown[:5]}. Use ids from describe_field or a result."
    return None


def _summary(action: BaseModel, field: dict | None, layers: dict[str, dict]) -> str:
    if isinstance(action, LayerAdd):
        layer = {"table": action.table, "filter": [c.model_dump(exclude_unset=True) for c in action.filter]}
        return f"layer.add {action.id!r}: {layer_count(field, layer)} rows match."
    if isinstance(action, LayerUpdate) and action.filter is not None:
        layer = {"table": layers[action.layerId]["table"],
                 "filter": [c.model_dump(exclude_unset=True) for c in action.filter]}
        return f"layer.update {action.layerId!r}: {layer_count(field, layer)} rows match the new filter."
    return action.type


Resolver = Callable[[str], Awaitable[dict | None]]


def _coverage(field: dict, ra: float, dec: float, radius: float) -> tuple[str, str]:
    """Whether a circle lies inside the region the field's archives were queried for."""
    if field.get("query_region"):  # a box, polygon or band: test against the region itself
        import math

        from ..science.regions import Region
        shape = Region.from_spec({k: v for k, v in field["query_region"].items() if k != "cuts"})
        d = [k * math.pi / 8 for k in range(16)]
        dec_ = [dec + radius * math.cos(t) for t in d]
        ra_ = [ra + radius * math.sin(t) / max(math.cos(math.radians(dec)), 1e-6) for t in d]
        rim = shape.contains(ra_, dec_)
        if shape.contains([ra], [dec])[0] and rim.all():
            return "inside", "The circle is inside the field's query region."
        if rim.any() or shape.contains([ra], [dec])[0]:
            return "partial", ("Part of the circle is outside the field's query region; archives were "
                               "not queried there, so counts are lower limits.")
        return "outside", "The circle is outside the field's query region; no archive data was retrieved there."
    region = field.get("region")
    if not region:
        return "unknown", "The field has no query region recorded."
    stcs = "POLYGON " + " ".join(f"{a} {d}" for a, d in region)
    if polygon_contains_circle(region, ra, dec, radius):
        return "inside", "The circle is inside the field's query region."
    if footprint_overlaps_circle(stcs, ra, dec, radius):
        return "partial", ("Part of the circle is outside the field's query region; archives were "
                           "not queried there, so counts are lower limits.")
    return "outside", ("The circle is outside the field's query region; no archive data was "
                       "retrieved there. Say so rather than reporting zero.")


def region_report(field: dict, ra: float, dec: float, radius: float, tables: list[str] | None,
                  limit: int) -> dict:
    """Per-table contents of a circle: point rows nearest first, footprints covering it."""
    coverage, note = _coverage(field, ra, dec, radius)
    report: dict = {"coverage": coverage, "coverage_note": note, "tables": {}, "footprints": {}}
    for name, table in field["tables"].items():
        if table.get("kind") == "annotation" or (tables and name not in tables):
            continue
        rows = table["rows"]
        if any(row.get("s_region") for row in rows):
            containing = [r["id"] for r in rows if footprint_contains(r.get("s_region", ""), ra, dec)]
            overlapping = [r for r in rows if footprint_overlaps_circle(r.get("s_region", ""), ra, dec, radius)]
            by = Counter(" ".join(str(r.get(k, "")) for k in ("tel", "inst")).strip() for r in overlapping)
            report["footprints"][name] = {
                "containing_center": containing, "overlapping": len(overlapping),
                "overlapping_by": dict(by), "overlapping_ids": [r["id"] for r in overlapping][:200]}
            continue
        near = []
        for row in rows:
            if isinstance(row.get("ra"), (int, float)) and isinstance(row.get("dec"), (int, float)):
                sep = separation_deg(row["ra"], row["dec"], ra, dec)
                if sep <= radius:
                    near.append((sep, row))
        near.sort(key=lambda item: item[0])
        report["tables"][name] = {"count": len(near), "nearest": [
            {"sep_arcsec": round(sep * 3600, 2),
             **{k: v for k, v in row.items() if k not in ("polygon", "s_region") and not k.startswith("_")}}
            for sep, row in near[:limit]]}
    report["layer_filter"] = [{"column": "position", "op": "within",
                               "value": {"ra": ra, "dec": dec, "radius": radius}}]
    return report


def match_report(field: dict, points: str, point_filter: list[dict], footprints: str,
                 footprint_filter: list[dict], limit: int) -> dict:
    """Which filtered point rows fall inside which filtered footprint rows."""
    shapes = [(row, Footprint(row["s_region"])) for row in field["tables"][footprints]["rows"]
              if row.get("s_region") and matches(row, footprint_filter)]
    candidates = [row for row in field["tables"][points]["rows"]
                  if isinstance(row.get("ra"), (int, float)) and isinstance(row.get("dec"), (int, float))
                  and matches(row, point_filter)]
    matched, by_group = [], Counter()
    for row in candidates:
        covering = [fp_row for fp_row, fp in shapes if fp.contains(row["ra"], row["dec"])]
        if covering:
            groups = sorted({_label(fp_row) for fp_row in covering})
            by_group.update(groups)
            years = [fp_row["year"] for fp_row in covering if isinstance(fp_row.get("year"), (int, float))]
            matched.append({
                "id": row["id"], "n_covering": len(covering), "instruments": groups,
                # Epoch span of the covering images: what an astrometric baseline needs.
                **({"first_year": min(years), "last_year": max(years),
                    "baseline_yr": round(max(years) - min(years), 2)} if years else {}),
                "covering": [fp_row["id"] for fp_row in covering][:20]})
    ids = [m["id"] for m in matched]
    return {"points": points, "footprints": footprints, "points_considered": len(candidates),
            "footprints_considered": len(shapes), "matched": len(matched),
            "points_by_instrument": dict(by_group), "rows": matched[:limit],
            "layer_filter": [{"column": "id", "op": "in", "value": ids[:1000]}],
            **({"note": "Only the first 1000 ids are in layer_filter."} if len(ids) > 1000 else {})}


def _label(row: dict) -> str:
    return " ".join(str(row.get(k, "")) for k in ("tel", "inst")).strip() or row["id"]


def overlap_report(field: dict, table: str, filter_a: list[dict], filter_b: list[dict] | None,
                   min_years: float, limit: int, table_b: str | None = None) -> dict:
    """Overlapping footprint pairs with their time baselines, longest first. With
    ``table_b``, pairs between two footprint tables (e.g. HST/JWST vs CFHT)."""
    rows = [r for r in field["tables"][table]["rows"] if r.get("s_region")]
    rows_b = rows if table_b is None else [r for r in field["tables"][table_b]["rows"] if r.get("s_region")]
    side_a = [(r, Footprint(r["s_region"])) for r in rows if matches(r, filter_a)]
    if table_b is None and filter_b is None:
        side_b = side_a
    else:
        side_b = [(r, Footprint(r["s_region"])) for r in rows_b if matches(r, filter_b or [])]
    year = lambda r: r["year"] if isinstance(r.get("year"), (int, float)) else None  # noqa: E731
    pairs, seen = [], set()
    in_b = table if table_b is None else table_b
    for ra, fa in side_a:
        for rb, fb in side_b:
            key = tuple(sorted(((table, ra["id"]), (in_b, rb["id"]))))
            if (table, ra["id"]) == (in_b, rb["id"]) or key in seen:
                continue
            seen.add(key)
            if not fa.overlaps(fb):
                continue
            ya, yb = year(ra), year(rb)
            years = round(abs(yb - ya), 2) if ya is not None and yb is not None else None
            if min_years and (years is None or years < min_years):
                continue
            first, second = sorted(((ra, ya, table), (rb, yb, in_b)), key=lambda item: (item[1] is None, item[1]))
            pairs.append({"a": first[0]["id"], "a_table": first[2], "a_label": _label(first[0]), "a_year": first[1],
                          "b": second[0]["id"], "b_table": second[2], "b_label": _label(second[0]),
                          "b_year": second[1], "years": years})
    pairs.sort(key=lambda p: -(p["years"] or 0))
    groups: dict[str, dict] = {}
    for p in pairs:
        name = " ↔ ".join(sorted({p["a_label"], p["b_label"]})) if p["a_label"] != p["b_label"] else p["a_label"]
        g = groups.setdefault(name, {"pairs": 0, "max_years": None, "longest": None})
        g["pairs"] += 1
        if p["years"] is not None and (g["max_years"] is None or p["years"] > g["max_years"]):
            g["max_years"], g["longest"] = p["years"], [p["a"], p["b"]]
    shown = pairs[:limit]
    by_table: dict[str, set] = {}
    for p in shown:
        for side in ("a", "b"):
            by_table.setdefault(p[f"{side}_table"], set()).add(p[side])
    filters = {t: [{"column": "id", "op": "in", "value": sorted(ids)}] for t, ids in by_table.items()}
    out = {"table": table, "footprints_a": len(side_a), "footprints_b": len(side_b), "pairs": len(pairs),
           "by_instruments": groups, "longest": shown,
           "layer_filter": filters.get(table, [{"column": "id", "op": "in", "value": []}])}
    if table_b is not None:
        out["table_b"], out["layer_filters"] = table_b, filters
    return out


def build_map_tools(
    *, field: dict | None = None, layers: list[dict] = (), field_keys: list[str] = (),
    resolver: Resolver | None = None,
    simbad: Callable[[str], Awaitable[list[dict]]] | None = None,
    jobs=None, lvdb=None,
    fetch_image: Callable[[float, float, float, str], Awaitable[bytes]] | None = None,
    add_source: Callable[..., Awaitable[int]] | None = None,  # (key, source, options=None) -> rows
    read_field: Callable[[str], dict | None] | None = None,
    known_layers: dict | None = None,
) -> list[BaseTool]:
    """Tools bound to the field and layers the browser reports for this turn.

    ``known_layers`` (optional) is a dict shared with other tools that add layers in
    the same turn (e.g. auto_members), so update_map can update those layers too."""
    current = known_layers if known_layers is not None else {}
    current.update({layer["id"]: layer for layer in layers if isinstance(layer, dict) and "id" in layer})

    def no_field() -> str:
        return f"No field is loaded. Available: {list(field_keys) or 'none'}."

    @tool
    async def update_map(
        actions: Annotated[list[MapAction], Field(min_length=1, max_length=20)],
        tool_call_id: Annotated[str, InjectedToolCallId],
    ):
        """Change the shared sky map with structured actions, applied in order.

        Layers with added=false are available but not on the user's list; showing
        one (visible true) adds it. layer.add / layer.update / layer.remove: show
        a table's rows filtered by
        AND-ed clauses {column, op, value} (ops: == != < <= > >= in exists inside lasso; and
        {column: "position", op: "within", value: {ra, dec, radius}} in degrees), with
        a style and label; pipeline layers can be hidden but not removed.
        focus (ra, dec, fov) or focus.rows (frame rows of a table); highlight
        rows with a short reason shown on the map (empty ids clears); annotate
        with a circle (radius), polygon (points) or text label, optionally
        anchored to a row; select opens
        a row in the Selected panel. plot.show draws a scatter (x, y) or
        histogram (x only) of a table next to the map, using the same layers and
        colors (e.g. CMD: table gaia, x bp_rp, y G, invert_y true; PM: pmra vs
        pmdec); the user can drag a box there to make a layer. Angles are ICRS degrees. Use describe_field
        for tables, columns and ids. Nothing is displayed until the browser
        acknowledges it on a later turn.
        """
        problems = []
        pending = dict(current)  # layers this call adds; kept only if every action is valid
        for i, action in enumerate(actions):
            problem = check_action(action, field, pending)
            if problem:
                problems.append(f"action {i} ({action.type}): {problem}")
            elif isinstance(action, LayerAdd):
                pending[action.id] = {"id": action.id, "table": action.table, "origin": "agent"}
        if problems:
            return ToolMessage(content="No actions sent. " + " ".join(problems),
                               tool_call_id=tool_call_id, status="error")
        current.update(pending)
        payload = {"kind": "map_actions", "status": "requested",
                   "actions": [a.model_dump(mode="json", exclude_none=True) for a in actions]}
        lines = [_summary(a, field, current) for a in actions]
        return publish_result(tool_call_id, tool_call_id, payload,
                              "Requested map actions:\n" + "\n".join(lines) +
                              "\nNot displayed until the browser acknowledges them.")

    @tool
    async def describe_field(
        tool_call_id: Annotated[str, InjectedToolCallId],
        table: str | None = None,
        filter: list[Clause] | None = None,
        limit: Annotated[int, Field(ge=0, le=50)] = 10,
    ):
        """Describe the loaded field: target, tables (columns with ranges or
        example values), layers with counts, and the pipeline's decisions. With
        a table, also return up to ``limit`` rows matching the optional filter,
        so you can find ids to highlight, select or annotate.
        """
        if field is None:
            return tool_error(no_field() + " Use load_field.", tool_call_id)
        if table is not None and table not in field["tables"]:
            return ToolMessage(content=f"Unknown table {table!r}; tables: {sorted(field['tables'])}.",
                               tool_call_id=tool_call_id, status="error")
        info: dict = {"target": field["target"], "n_rhalf": field["n_rhalf"],
                      "layers": [{k: layer.get(k) for k in ("id", "table", "label", "visible", "origin", "count")}
                                 for layer in current.values()]}
        if table is None:
            info["tables"] = {name: table_summary(t) for name, t in field["tables"].items()}
            info["summaries"] = field.get("summaries", [])
            info["decisions"] = field["decisions"]
        else:
            clauses = [c.model_dump(exclude_unset=True) for c in filter or []]
            try:
                validate_filter(clauses, {"id", *field["tables"][table]["columns"]})
            except ValueError as exc:
                return ToolMessage(content=f"Invalid filter: {exc}", tool_call_id=tool_call_id,
                                   status="error")
            rows = [r for r in field["tables"][table]["rows"] if matches(r, clauses)]
            info["table"] = table_summary(field["tables"][table])
            info["matching"] = len(rows)
            info["rows"] = [{k: v for k, v in r.items() if k not in ("polygon", "s_region") and not k.startswith("_")}
                            for r in rows[:limit]]
        return ToolMessage(content=json.dumps(info, default=str), tool_call_id=tool_call_id)

    @tool
    async def load_field(key: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """Load a field result (archive data around a target) onto the map, replacing
        the current field. Its default layers follow the pipeline's presentation. The
        other tools use the new field straight away, in this same turn: describe it,
        summarize, highlight and adjust layers without waiting.
        """
        nonlocal field
        if key not in field_keys:
            return ToolMessage(content=f"No field {key!r}. Available: {list(field_keys) or 'none'}.",
                               tool_call_id=tool_call_id, status="error")
        loaded = read_field(key) if read_field else None
        if loaded is None:
            return publish_result(tool_call_id, tool_call_id, {"kind": "field_load", "key": key},
                                  f"Requested field {key!r}. Call describe_field on the next turn, "
                                  "after the browser confirms it loaded.")
        # The browser shows the field's default layers; map actions this turn apply after it loads.
        field = loaded
        current.clear()
        current.update({layer["id"]: layer for layer in loaded.get("layers", [])})
        return publish_result(tool_call_id, tool_call_id, {"kind": "field_load", "key": key},
                              f"Loaded field {key!r} ({loaded['target']['name']}); the other tools use it now. "
                              "Map actions you request next apply once the browser has loaded it.")

    async def locate(ra, dec, table, row_id, name) -> tuple[float, float, str] | str:
        """A center from ra+dec, a field row, or a name; or why not."""
        given = [ra is not None or dec is not None, table is not None or row_id is not None, name is not None]
        if sum(given) != 1:
            return "Give exactly one center: ra+dec, table+row_id, or name."
        if given[0]:
            return (ra, dec, "coordinates") if ra is not None and dec is not None else "Give both ra and dec."
        if given[1]:
            if field is None or table not in field["tables"] or row_id is None:
                return f"Unknown table {table!r}, or row_id missing."
            row = next((r for r in field["tables"][table]["rows"] if r["id"] == row_id), None)
            if row is None or not isinstance(row.get("ra"), (int, float)):
                return f"No positioned row {row_id!r} in {table}."
            return row["ra"], row["dec"], f"{table} {row_id}"
        if resolver is None:
            return "Name resolution is not available; ask for coordinates."
        try:
            found = await resolver(name)
        except Exception as exc:  # network failure is an outcome, not a crash
            return f"Name resolver failed ({type(exc).__name__}); try again or give coordinates."
        if found is None:
            return f"Sesame could not resolve {name!r}. Ask the user for coordinates."
        return found["ra"], found["dec"], (
            f"{found.get('resolver') or 'Sesame'}: {found.get('name') or name} ({found.get('otype') or '?'})")

    @tool
    async def region(
        radius_arcmin: Annotated[float, Field(gt=0, le=600)],
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: Ra | None = None,
        dec: Dec | None = None,
        table: str | None = None,
        row_id: str | None = None,
        name: str | None = None,
        tables: list[str] | None = None,
        limit: Annotated[int, Field(ge=0, le=25)] = 5,
    ):
        """What lies within a radius of a point in the loaded field.

        Give the center as ra+dec (ICRS degrees), as a row (table + row_id, e.g.
        the user's selection), or as an object name (resolved with CDS Sesame).
        Returns per-table counts with the nearest rows and their separations,
        footprints containing the center or overlapping the circle, whether the
        circle lies inside the region the archives were queried for, and a
        layer filter to show the result with update_map.
        """
        def error(text: str) -> ToolMessage:
            return tool_error(text, tool_call_id)

        if field is None:
            return error(no_field())
        center = await locate(ra, dec, table, row_id, name)
        if isinstance(center, str):
            return error(center)
        ra, dec, source = center
        bad = [t for t in tables or [] if t not in field["tables"]]
        if bad:
            return error(f"Unknown tables {bad}; tables: {sorted(field['tables'])}.")
        report = region_report(field, ra, dec, radius_arcmin / 60, tables, limit)
        report = {"center": {"ra": ra, "dec": dec, "from": source}, "radius_arcmin": radius_arcmin, **report}
        return ToolMessage(content=json.dumps(report, default=str), tool_call_id=tool_call_id)

    @tool
    async def match(
        points: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        footprints: str | None = None,
        points_filter: list[Clause] | None = None,
        footprints_filter: list[Clause] | None = None,
        limit: Annotated[int, Field(ge=0, le=50)] = 10,
    ):
        """Find rows of one table that fall inside footprints of another, e.g.
        trusted QSOs (points="gaia", points_filter quaia/gaia_crf, or "milliquas")
        inside HST/JWST imaging (footprints="observations", optionally filtered by
        tel, inst or year). Returns counts, which footprints cover each matched
        row with the epoch span (baseline_yr) of those images, counts per
        instrument, and an id filter for update_map's layer.add.
        """
        def error(text: str) -> ToolMessage:
            return tool_error(text, tool_call_id)

        if field is None:
            return error(no_field())
        if footprints is None:  # the first footprint table the field's data sources provide
            footprints = next((n for n, t in field["tables"].items() if t.get("kind") == "footprints"
                               and t["rows"]), None)
            if footprints is None:
                return error("This field has no footprint tables.")
        for name in (points, footprints):
            if name not in field["tables"]:
                return error(f"Unknown table {name!r}; tables: {sorted(field['tables'])}.")
        if not any(row.get("s_region") for row in field["tables"][footprints]["rows"]):
            return error(f"Table {footprints!r} has no footprints (s_region); use observations or planned.")
        clauses = []
        for name, given in ((points, points_filter), (footprints, footprints_filter)):
            clause = [c.model_dump(exclude_unset=True) for c in given or []]
            try:
                validate_filter(clause, {"id", *field["tables"][name]["columns"]})
            except ValueError as exc:
                return error(f"Invalid filter on {name}: {exc}")
            clauses.append(clause)
        report = match_report(field, points, clauses[0], footprints, clauses[1], limit)
        return ToolMessage(content=json.dumps(report), tool_call_id=tool_call_id)

    @tool
    async def summarize(
        table: str,
        columns: Annotated[list[str], Field(min_length=1, max_length=8)],
        tool_call_id: Annotated[str, InjectedToolCallId],
        filter: list[Clause] | None = None,
        bins: Annotated[int, Field(ge=0, le=50)] = 15,
        error_column: str | None = None,
    ):
        """Distributions of columns over a table's rows matching a filter: for
        numbers n, missing, min/max, mean, std, median, MAD and sigma_MAD,
        5/16/50/84/95th percentiles and a histogram; counts for booleans and
        text. With error_column (e.g. rv_err for rv), also the inverse-variance
        weighted mean of the first column and its error. Quote these numbers
        rather than estimating from examples.
        """
        def error(text: str) -> ToolMessage:
            return tool_error(text, tool_call_id)

        if field is None:
            return error(no_field())
        if table not in field["tables"]:
            return error(f"Unknown table {table!r}; tables: {sorted(field['tables'])}.")
        known = {"id", *field["tables"][table]["columns"]}
        unknown = [c for c in [*columns, *([error_column] if error_column else [])] if c not in known]
        if unknown:
            return error(f"Unknown columns {unknown}; columns: {sorted(known)}.")
        clauses = [c.model_dump(exclude_unset=True) for c in filter or []]
        try:
            validate_filter(clauses, known)
        except ValueError as exc:
            return error(f"Invalid filter: {exc}")
        rows = [r for r in field["tables"][table]["rows"] if matches(r, clauses)]
        units = field["tables"][table].get("units", {})
        stats = []
        for i, column in enumerate(columns):
            result = column_stats(rows, column, bins=bins, error_column=error_column if i == 0 else None)
            if column in units:
                result["unit"] = units[column]
            stats.append(result)
        return ToolMessage(content=json.dumps({"table": table, "rows": len(rows), "filter": clauses,
                                               "columns": stats}), tool_call_id=tool_call_id)

    @tool
    async def lookup(
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: Ra | None = None,
        dec: Dec | None = None,
        table: str | None = None,
        row_id: str | None = None,
        name: str | None = None,
        radius_arcsec: Annotated[float, Field(gt=0, le=600)] = 5.0,
        papers: Annotated[int, Field(ge=0, le=25)] = 10,
    ):
        """What SIMBAD knows at a position: catalogued objects within
        radius_arcsec (nearest first: main_id, object type, reference count,
        separation) and the most recent references (bibcodes, titles) of the
        nearest one. Center from ra+dec, a field row (e.g. the user's selection),
        or a name. Use ads_paper on the bibcodes for abstracts. No match is not
        proof that nothing is known; say what radius was searched.
        """
        def error(text: str) -> ToolMessage:
            return tool_error(text, tool_call_id)

        if simbad is None:
            return error("SIMBAD lookup is not available on this server.")
        center = await locate(ra, dec, table, row_id, name)
        if isinstance(center, str):
            return error(center)
        ra, dec, source = center
        try:
            result = await lookup_position(simbad, ra, dec, radius_arcsec, papers)
        except Exception as exc:  # archive failure is an outcome, not a crash
            return error(f"SIMBAD query failed ({type(exc).__name__}: {exc}); say so, don't guess.")
        result = {"center": {"ra": ra, "dec": dec, "from": source}, "radius_arcsec": radius_arcsec, **result}
        return ToolMessage(content=json.dumps(result, default=str), tool_call_id=tool_call_id)

    @tool
    async def overlaps(
        tool_call_id: Annotated[str, InjectedToolCallId],
        table: str | None = None,
        filter_a: list[Clause] | None = None,
        filter_b: list[Clause] | None = None,
        min_years: Annotated[float, Field(ge=0, le=100)] = 0.0,
        limit: Annotated[int, Field(ge=1, le=50)] = 15,
        table_b: str | None = None,
    ):
        """Footprints that overlap each other, with time baselines (years between
        their epochs), longest first, for astrometric proper motions or
        variability. Compare one set with another by filtering each side (e.g.
        filter_a tel == HST, filter_b tel == JWST); without filter_b, pairs
        within filter_a. To compare two footprint tables (e.g. observations vs
        cfht_megacam), give table and table_b; filter_a applies to table and
        filter_b to table_b. Returns per-instrument-pair counts and longest
        baselines, the longest pairs, and id filters for update_map.
        """
        def error(text: str) -> ToolMessage:
            return tool_error(text, tool_call_id)

        if field is None:
            return error(no_field())
        if table is None:
            table = next((n for n, t in field["tables"].items() if t.get("kind") == "footprints" and t["rows"]), None)
            if table is None:
                return error("This field has no footprint tables.")
        for name in (table, table_b):
            if name is not None and (name not in field["tables"] or
                                     not any(r.get("s_region") for r in field["tables"][name]["rows"])):
                footprint_tables = [n for n, t in field["tables"].items() if any(r.get("s_region") for r in t["rows"])]
                return error(f"No footprints in table {name!r}. Footprint tables: {footprint_tables}.")
        clauses = []
        for given, name in ((filter_a, table), (filter_b, table_b or table)):
            clause = None if given is None else [c.model_dump(exclude_unset=True) for c in given]
            try:
                validate_filter(clause or [], {"id", *field["tables"][name]["columns"]})
            except ValueError as exc:
                return error(f"Invalid filter on {name!r}: {exc}")
            clauses.append(clause)
        report = overlap_report(field, table, clauses[0] or [], clauses[1], min_years, limit, table_b=table_b)
        return ToolMessage(content=json.dumps(report), tool_call_id=tool_call_id)

    @tool
    async def find_lvdb(name: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """Find Local Volume Database objects (dwarf galaxies, globular clusters)
        by name, e.g. "Ursa Minor" or "Leo I"; returns keys for run_field."""
        found = lvdb.search(name) if lvdb else None
        if found is None:
            return ToolMessage(content="The LVDB index is not available yet (it is cached after the first "
                               "field run).", tool_call_id=tool_call_id, status="error")
        return ToolMessage(content=json.dumps({"matches": found, "already_run": list(field_keys)}),
                           tool_call_id=tool_call_id)

    @tool
    async def run_field(
        key: str,
        tool_call_id: Annotated[str, InjectedToolCallId],
        n_rhalf: Annotated[float, Field(ge=0.5, le=10)] = 2.0,
        refresh: bool = False,
    ):
        """Query the archives (MAST HST/JWST, Gaia DR3, QSO catalogs, SIMBAD,
        SDSS/DESI spectra) around a Local Volume Database object out to n_rhalf
        half-light radii, in the background. Use find_lvdb for the key. Takes 30 s
        to a few minutes; the browser shows progress and loads the field when it
        is ready, and confirms on the next turn. Tell the user it is running.
        """
        if jobs is None:
            return ToolMessage(content="Running new fields is not available on this server.",
                               tool_call_id=tool_call_id, status="error")
        if key in field_keys and not refresh:
            return ToolMessage(content=f"Field {key!r} already exists; use load_field, or refresh=true to "
                               "query the archives again.", tool_call_id=tool_call_id, status="error")
        if lvdb is not None and lvdb.has(key) is False:
            close = [m["key"] for m in lvdb.search(key.replace("_", " "), 5) or []]
            return ToolMessage(content=f"No LVDB object {key!r}. Close: {close or 'none'}; try find_lvdb.",
                               tool_call_id=tool_call_id, status="error")
        try:
            job = jobs.start(key, n_rhalf)
        except ValueError as exc:
            return ToolMessage(content=str(exc), tool_call_id=tool_call_id, status="error")
        return publish_result(tool_call_id, tool_call_id, {"kind": "field_job", "job": job.id, "key": key},
                              f"Started the archive queries for {key!r} (job {job.id}). The browser "
                              "will load the field when it is ready; not loaded yet.")

    @tool
    async def run_cone(
        radius_arcmin: Annotated[float, Field(gt=0, le=60)],
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: Ra | None = None,
        dec: Dec | None = None,
        name: str | None = None,
        label: str | None = None,
        include: list[Literal["hst_jwst", "qso", "spectra"]] = [],
    ):
        """Query the archives in a circular cone around any position (not just
        LVDB objects), in the background: Gaia DR3 (with its QSO and galaxy
        candidates) and SIMBAD galaxies; include adds HST/JWST observations
        (hst_jwst), MILLIQUAS/Quaia QSOs (qso) or SDSS/DESI spectra (spectra), only
        when the user asks. Center from ra+dec or an object name (Sesame); radius up
        to 60′ (larger cones exceed the Gaia row limit). For boxes, polygons and
        stream tracks use plan_region instead. The browser loads the field when ready.
        """
        if jobs is None:
            return ToolMessage(content="Running new fields is not available on this server.",
                               tool_call_id=tool_call_id, status="error")
        center = await locate(ra, dec, None, None, name)
        if isinstance(center, str):
            return ToolMessage(content=center, tool_call_id=tool_call_id, status="error")
        ra, dec, source = center
        title = label or (f"{name} {radius_arcmin:g} arcmin" if name else f"cone {ra:.4f} {dec:+.4f} {radius_arcmin:g} arcmin")
        try:
            job = jobs.start_cone(ra, dec, radius_arcmin, title, include=include)
        except ValueError as exc:
            return ToolMessage(content=str(exc), tool_call_id=tool_call_id, status="error")
        return publish_result(tool_call_id, tool_call_id, {"kind": "field_job", "job": job.id, "key": job.key},
                              f"Started a {radius_arcmin:g}′ cone around {source} ({ra:.5f}, {dec:+.5f}) as "
                              f"field {job.key!r} (job {job.id}); not loaded yet.")

    @tool
    async def sky_image(
        fov_arcmin: Annotated[float, Field(gt=0, le=600)],
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: Ra | None = None,
        dec: Dec | None = None,
        table: str | None = None,
        row_id: str | None = None,
        name: str | None = None,
        survey: Literal[tuple(SURVEYS)] = "CDS/P/DSS2/color",
    ):
        """Look at the sky: a 512-pixel image of a survey (DSS2, Pan-STARRS, DESI
        Legacy, SDSS, 2MASS infrared, unWISE mid-infrared) centered on ra+dec, a
        row, or a name, fov_arcmin wide (north up, east left). You will see the
        image after this call. Use it to describe what is there (morphology,
        artefacts, crowding); identify objects with lookup and the field's
        catalogs. The image has no map overlays.
        """
        if fetch_image is None:
            return ToolMessage(content="Sky images are not available on this server.",
                               tool_call_id=tool_call_id, status="error")
        center = await locate(ra, dec, table, row_id, name)
        if isinstance(center, str):
            return ToolMessage(content=center, tool_call_id=tool_call_id, status="error")
        ra, dec, source = center
        try:
            cutout_params(ra, dec, fov_arcmin / 60, survey)
            jpeg = await fetch_image(ra, dec, fov_arcmin / 60, survey)
        except Exception as exc:  # the image service failing is an outcome
            return ToolMessage(content=f"No image ({type(exc).__name__}: {exc}).", tool_call_id=tool_call_id,
                               status="error")
        return ToolMessage(
            content=(f"{SURVEYS[survey]} image, {fov_arcmin:g}′ wide, centered on {source} "
                     f"({ra:.5f}, {dec:+.5f}); north up, east left. The image follows."),
            tool_call_id=tool_call_id, name="sky_image",
            artifact={"image": data_url(jpeg), "survey": survey, "ra": ra, "dec": dec, "fov_arcmin": fov_arcmin})

    @tool
    async def add_data(source: str, tool_call_id: Annotated[str, InjectedToolCallId],
                       options: dict[str, float] | None = None):
        """Query an archive the field does not include yet, over the field's region,
        and add its table (e.g. source="exoplanet_hosts": confirmed planet hosts from
        the NASA Exoplanet Archive). The browser adds its layers to the map.
        Some sources take options, e.g. source="tic" with options={"tmag_max": 13} for TIC
        stars brighter than Tmag 13 (default 16). With options, a source already in the
        field is queried again with the new values.
        """
        from ..sources import SOURCES

        if field is None or add_source is None:
            return ToolMessage(content="No field is loaded.", tool_call_id=tool_call_id, status="error")
        queryable = {n: s for n, s in SOURCES.items() if s.query}
        offered = sorted(n for n in queryable if n not in field["tables"] or (options and queryable[n].options))
        if source not in offered:
            return ToolMessage(content=f"Not available to add: {source!r}. Can add: {offered or 'nothing'}.",
                               tool_call_id=tool_call_id, status="error")
        accepted = {o.name: f"{o.label} (default {o.default:g}, {o.min:g}-{o.max:g})" for o in queryable[source].options}
        if options and not accepted:
            return ToolMessage(content=f"{source!r} takes no options; add it without them.",
                               tool_call_id=tool_call_id, status="error")
        try:
            count = await (add_source(field["key"], source, options) if options else add_source(field["key"], source))
        except ValueError as exc:  # an unknown or out-of-range option
            return ToolMessage(content=f"Invalid options for {source!r}: {exc}. It takes: {accepted}.",
                               tool_call_id=tool_call_id, status="error")
        except Exception as exc:  # archive failure is an outcome
            return ToolMessage(content=f"The {source} query failed ({type(exc).__name__}: {exc}); say so.",
                               tool_call_id=tool_call_id, status="error")
        used = f" with {options}" if options else ""
        return publish_result(tool_call_id, tool_call_id, {"kind": "field_refresh", "key": field["key"]},
                              f"Added {count} {SOURCES[source].label.lower()} ({SOURCES[source].provenance}){used} to "
                              f"field {field['key']!r} as table {source!r}. Use describe_field on it next turn.")

    return [update_map, describe_field, load_field, region, match, summarize, lookup, overlaps,
            find_lvdb, run_field, run_cone, sky_image, add_data]


__all__ = ["MapAction", "build_map_tools", "check_action"]
