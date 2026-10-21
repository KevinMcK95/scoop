"""Data sources (archives and catalogues: MAST, Gaia, MILLIQUAS, ...): each module
in this package declares one data source, which becomes one table of a field.

Adding a data source means adding a module here that calls ``register(Source(...))``;
modules are discovered automatically, and nothing else needs editing. A source
declares how its rows look (columns with units and descriptions), how they are
shown by default (layers, plot presets, table-browser columns, per-row links),
and where its rows come from. Cross-source steps (e.g. matching spectra to Gaia
sources) are ``Enrichment``s that run after every source has produced rows.

Rows come from the smoke-test pipeline's output (``rows(data)`` reads
``sky_viewer.viewer_data``), or, for data sources with a ``query``, from an
on-demand query of the field's region stored beside it (``rows`` then reads
``data["extra"][name]``).
"""
from __future__ import annotations

import importlib
import pkgutil
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Literal

Row = dict
Link = tuple[str, str]  # (url, label)


@dataclass(frozen=True)
class Column:
    unit: str | None = None
    description: str | None = None


@dataclass(frozen=True)
class Option:
    """A number the user can set before a data source's query, e.g. a magnitude limit."""
    name: str  # the query's keyword argument
    label: str  # shown beside the input, e.g. "Tmag <"
    default: float
    min: float
    max: float
    step: float = 1
    help: str = ""  # why the default, shown with the input


@dataclass(frozen=True)
class Source:
    name: str  # the table name in a field, e.g. "gaia"
    label: str
    provenance: str
    rows: Callable[[dict], list[Row]]
    kind: Literal["catalog", "footprints", "annotation"] = "catalog"
    order: int = 50
    columns: Mapping[str, Column] = field(default_factory=dict)
    layers: Callable[[list[Row], dict], list[dict]] = lambda rows, data: []
    presets: tuple[dict, ...] = ()  # plot presets: {label, x, y?, invert_x?, invert_y?, equal?, title?}
    browse: tuple[str, ...] = ()  # columns for the table browser; empty = not listed there
    links: Callable[[Row], list[Link]] | None = None
    # Fetch rows for a field's region on demand ({"ra", "dec", "radius_deg"}), for data
    # sources the field pipeline does not query. Rows are kept next to the field.
    query: Callable[[dict], Awaitable[list[Row]]] | None = None
    # Numbers the user can set for the query; it is then called as query(region, **values).
    options: tuple[Option, ...] = ()
    # Small titled tables about the data source's rows, e.g. imaging coverage:
    # [{"title", "columns": [...], "rows": [[...], ...], "note"?}]
    summaries: Callable[[list[Row], dict], list[dict]] = lambda rows, data: []


@dataclass(frozen=True)
class Enrichment:
    """Runs after all sources, on tables it ``needs``; adds columns or details."""
    name: str
    needs: tuple[str, ...]
    apply: Callable[[dict[str, list[Row]], dict], None]


SOURCES: dict[str, Source] = {}
ENRICHMENTS: list[Enrichment] = []


def register(source: Source) -> Source:
    if source.name in SOURCES:
        raise ValueError(f"source {source.name!r} is registered twice")
    SOURCES[source.name] = source
    return source


def register_enrichment(enrichment: Enrichment) -> Enrichment:
    ENRICHMENTS.append(enrichment)
    return enrichment


def layer(id: str, table: str, filters: list[dict], group: str, label: str, color: str, *,
          order: int, shape: str = "circle", size: int = 8, dash: bool = False,
          hollow: bool = False, visible: bool = True) -> dict:
    """A default (pipeline) layer; ``order`` places it among all sources' layers."""
    return {"id": id, "table": table, "filter": filters, "group": group, "label": label,
            "style": {"color": color, "shape": shape, "size": size, "dash": dash, "hollow": hollow},
            "visible": visible, "origin": "pipeline", "note": None, "order": order}


def eq(column: str, value) -> dict:
    return {"column": column, "op": "==", "value": value}


def detail(title: str, fields: Mapping | None = None, links: list[Link] | None = None) -> dict:
    """A display-ready record for the Selected panel, e.g. one spectrum."""
    return {"title": title, "fields": {k: v for k, v in (fields or {}).items() if v not in (None, "")},
            "links": [list(link) for link in links or []]}


def _discover() -> None:
    for module in pkgutil.iter_modules(__path__):
        if not module.name.startswith("_"):
            importlib.import_module(f"{__name__}.{module.name}")


_discover()

def option_values(source: Source, given: Mapping | None) -> dict:
    """The source's option values: defaults, overridden by `given`, each checked in range."""
    values = {}
    for option in source.options:
        value = (given or {}).get(option.name, option.default)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{option.name} must be a number")
        if not option.min <= value <= option.max:
            raise ValueError(f"{option.name} must be between {option.min:g} and {option.max:g}")
        values[option.name] = value
    unknown = set(given or {}) - set(values)
    if unknown:
        raise ValueError(f"{source.name} has no option {', '.join(sorted(unknown))}")
    return values


__all__ = ["Column", "ENRICHMENTS", "Enrichment", "Option", "SOURCES", "Source", "detail", "eq", "layer",
           "option_values",
           "register", "register_enrichment"]
