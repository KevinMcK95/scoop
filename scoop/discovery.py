"""Finding and querying data the field pipeline does not cover.

Deterministic, read-only building blocks for the data agent
(``scoop/chat/data_agent.py``): what surveys cover a region (CDS MOC server),
catalogs in VizieR, services in the IVOA registry, a TAP service's tables and
columns, and bounded queries over a field's region. Nothing here runs code from
a model: queries are assembled from validated identifiers.

Every query result comes back as ``(rows, meta)``: rows in the field model
(``id``, ``ra``, ``dec``, optional ``s_region``, flat JSON values; 64-bit IDs as
strings) and meta with the provenance the evaluation checks need.
"""
from __future__ import annotations

import ipaddress
import math
import re
import socket
from collections import Counter
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urlparse

import numpy as np

from .observations import OBS_COLUMNS, add_observation_columns

MOCSERVER = "https://alasky.cds.unistra.fr/MocServer/query"
DEFAULT_ROWS = 2000
MAX_ROWS = 5000
MAX_COLUMNS = 40
MAX_RADIUS_DEG = 1.0
TIMEOUT_S = 120
# schema.table or column; letters, digits, underscores (TAP names are case-insensitive).
IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,63}(\.[A-Za-z_][A-Za-z0-9_]{0,63}){0,2}")
# VizieR table ids: V/162, J/ApJ/964/69, J/A+A/712/A207/tablee1, II/246/out ...
VIZIER_ID = re.compile(r"[A-Za-z0-9+_.]{1,32}(/[A-Za-z0-9+_.-]{1,32}){1,5}")
MAX_SAFE_INT = 2**53  # larger integers lose digits in JavaScript/JSON
# Only plain web links become clickable (never javascript:, data: or file: URLs).
URL = re.compile(r"https?://[^\s<>\"']+$")


class DiscoveryError(Exception):
    """A clear, actionable failure to report to the model (not a crash)."""


# ── Safety ───────────────────────────────────────────────────────────────────

def check_public_url(url: str) -> str:
    """Allow only http(s) URLs whose host resolves to public addresses.

    Service URLs come from the registry or the model; refuse anything that points
    at this machine or a private network (the server runs on a user's computer).
    """
    parts = urlparse(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise DiscoveryError(f"Not an http(s) URL: {url!r}")
    try:
        infos = socket.getaddrinfo(parts.hostname, parts.port or (443 if parts.scheme == "https" else 80))
    except socket.gaierror as exc:
        raise DiscoveryError(f"Cannot resolve {parts.hostname!r}: {exc}") from exc
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if (ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved
                or ip.is_multicast or ip.is_unspecified):
            raise DiscoveryError(f"Refusing {parts.hostname!r}: it resolves to a non-public address")
    return url


def check_ident(name: str, what: str) -> str:
    if not isinstance(name, str) or not IDENT.fullmatch(name):
        raise DiscoveryError(f"Invalid {what} {name!r}: use a plain name like schema.table or column")
    return name


def check_where(where: str | None) -> str | None:
    """An extra ADQL condition from the model: one expression, no statements or comments."""
    if where is None or not where.strip():
        return None
    if len(where) > 500 or any(s in where for s in (";", "--", "/*", "*/")):
        raise DiscoveryError("The extra condition must be one short ADQL expression "
                             "(no ';' or comments), e.g. calib_level = 2 AND instrument_name LIKE 'MegaPrime%'")
    if re.search(r"\b(select|union|into|insert|update|delete|drop|create)\b", where, re.I):
        raise DiscoveryError("The extra condition can't contain a sub-query or statement")
    return where.strip()


def check_region(region: dict) -> dict:
    ra, dec, r = float(region["ra"]), float(region["dec"]), float(region["radius_deg"])
    if not (0 <= ra < 360 and -90 <= dec <= 90 and 0 < r):
        raise DiscoveryError(f"Bad region {region!r}")
    return {"ra": ra, "dec": dec, "radius_deg": min(r, MAX_RADIUS_DEG)}


# ── Rows ─────────────────────────────────────────────────────────────────────

def json_value(value: Any) -> Any:
    """A JSON-safe cell: None for masked/NaN, exact strings for big integers."""
    if value is None or value is np.ma.masked:
        return None
    if isinstance(value, (bytes, np.bytes_)):
        return value.decode(errors="replace").strip()
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (int, np.integer)):
        v = int(value)
        return str(v) if abs(v) > MAX_SAFE_INT else v
    if isinstance(value, (float, np.floating)):
        v = float(value)
        return None if not math.isfinite(v) else v
    if isinstance(value, np.ndarray):
        return None  # array-valued cells don't fit a flat table
    text = str(value).strip()
    return text or None


UCD_RA = ("pos.eq.ra;meta.main", "pos.eq.ra")
UCD_DEC = ("pos.eq.dec;meta.main", "pos.eq.dec")
ID_UCDS = ("meta.id;meta.main", "meta.id")
ID_NAMES = ("obs_id", "obsid", "source_id", "objid", "obj_id", "id", "designation", "name")
RA_NAMES = ("ra", "s_ra", "raj2000", "_raj2000", "ra_icrs", "radeg", "ra_deg")
DEC_NAMES = ("dec", "s_dec", "dej2000", "_dej2000", "de_icrs", "dedeg", "dec_deg")


def position_columns(table) -> tuple[str | None, str | None]:
    """RA/Dec columns of an astropy table, by UCD first, then by common names."""
    def by_ucd(ucds):
        for ucd in ucds:
            for c in table.colnames:
                if str((table[c].meta or {}).get("ucd") or "").lower() == ucd:
                    return c
        return None

    def by_name(names):
        lower = {c.lower(): c for c in table.colnames}
        return next((lower[n] for n in names if n in lower), None)

    return by_ucd(UCD_RA) or by_name(RA_NAMES), by_ucd(UCD_DEC) or by_name(DEC_NAMES)


def id_column(table) -> str | None:
    """The table's own identifier column, by UCD first, then by common names."""
    for ucd in ID_UCDS:
        for c in table.colnames:
            if str((table[c].meta or {}).get("ucd") or "").lower() == ucd:
                return c
    lower = {c.lower(): c for c in table.colnames}
    return next((lower[n] for n in ID_NAMES if n in lower), None)


def table_rows(table, *, prefix: str, ra_col: str | None = None, dec_col: str | None = None,
               id_col: str | None = None, region_col: str | None = None,
               max_columns: int = MAX_COLUMNS) -> tuple[list[dict], dict]:
    """Rows in the field model, and per-column units/descriptions."""
    guess_ra, guess_dec = position_columns(table)
    ra_col, dec_col = ra_col or guess_ra, dec_col or guess_dec
    id_col = id_col or id_column(table)
    if ra_col not in table.colnames or dec_col not in table.colnames:
        raise DiscoveryError(f"No RA/Dec columns found (columns: {table.colnames[:20]}); name them explicitly")
    skip = {ra_col, dec_col, id_col, region_col}
    keep = [c for c in table.colnames if c not in skip and table[c].ndim == 1][:max_columns]
    columns = {c: {"unit": str(table[c].unit) if table[c].unit is not None else None,
                   "description": (getattr(table[c], "description", None) or None)} for c in keep}
    rows, seen = [], Counter()
    for i, rec in enumerate(table):
        rid = json_value(rec[id_col]) if id_col else None
        rid = str(rid) if rid not in (None, "") else f"{prefix}-{i}"
        seen[rid] += 1
        if seen[rid] > 1:  # e.g. one observation with several data products
            rid = f"{rid}#{seen[rid]}"
        row = {"id": rid, "ra": json_value(rec[ra_col]), "dec": json_value(rec[dec_col])}
        if region_col:
            row["s_region"] = json_value(rec[region_col])
        row.update({c: json_value(rec[c]) for c in keep})
        links = [[v, f"{c} ({urlparse(v).hostname})"] for c, v in row.items()
                 if c in columns and isinstance(v, str) and URL.match(v)]
        if links:
            row["_links"] = links
        rows.append(row)
    return rows, columns


def _meta(*, label: str, provenance: str, kind: str, columns: dict, query: dict, rows: list,
          limit: int) -> dict:
    return {"label": label, "provenance": provenance, "kind": kind, "columns": columns,
            "query": {**query, "retrieved_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                      "rows": len(rows), "truncated": len(rows) >= limit, "row_limit": limit}}


# ── What covers this region ──────────────────────────────────────────────────

REGIMES = {"radio": "Radio", "millimeter": "Millimeter", "infrared": "Infrared", "optical": "Optical",
           "uv": "UV", "ultraviolet": "UV", "x-ray": "X-ray", "gamma-ray": "Gamma-ray"}


def _regime(value: Any) -> str:
    values = value if isinstance(value, list) else [value]
    for v in values:
        text = str(v or "").lower()
        for key, name in REGIMES.items():
            if key in text:
                return name
        if "hi " in text or "ghz" in text or "mhz" in text:
            return "Radio"
    return "Unspecified"


def survey_inventory(region: dict, per_regime: int = 8, http=None) -> dict:
    """Datasets (catalogs, images, cubes) whose sky coverage overlaps the region,
    from the CDS MOC server, grouped by wavelength regime and ranked by how much
    of the sky they cover (all-sky surveys first, single-paper tables last)."""
    import requests
    region = check_region(region)
    get = http or requests.get
    resp = get(MOCSERVER, params={"RA": region["ra"], "DEC": region["dec"], "SR": region["radius_deg"],
                                  "fmt": "json", "get": "record",
                                  "fields": "ID,obs_title,obs_regime,dataproduct_type,moc_sky_fraction"},
               timeout=TIMEOUT_S)
    resp.raise_for_status()
    records = resp.json()
    groups: dict[str, list[dict]] = {}
    for r in records:
        frac = r.get("moc_sky_fraction")
        try:
            frac = float(frac[0] if isinstance(frac, list) else frac)
        except (TypeError, ValueError):
            frac = None
        groups.setdefault(_regime(r.get("obs_regime")), []).append({
            "id": r.get("ID"), "title": str(r.get("obs_title") or "")[:100],
            "type": str(r.get("dataproduct_type") or "unknown"), "sky_fraction": frac})
    out = {}
    for regime, items in sorted(groups.items(), key=lambda kv: -len(kv[1])):
        items.sort(key=lambda d: -(d["sky_fraction"] or 0))
        widest, families = [], set()
        for d in items:  # one entry per survey: HiPS variants and a catalog's tables share a family
            family = "/".join(str(d["id"]).split("/")[:3])
            if family not in families:
                families.add(family)
                widest.append(d)
            if len(widest) == per_regime:
                break
        out[regime] = {"datasets": len(items),
                       "by_type": dict(Counter(d["type"] for d in items).most_common()),
                       "widest": widest}
    return {"region": region, "total": len(records), "by_regime": out,
            "source": "CDS MOC server (catalogs and HiPS whose coverage overlaps the region)",
            "not_covered": ["gravitational-wave localizations (GraceDB; not in the MOC server)",
                            "pointed-observation archives are only partly listed: use their own "
                            "services (e.g. MAST, CADC, HEASARC) for complete inventories"]}


# ── VizieR ───────────────────────────────────────────────────────────────────

def find_catalogs(keywords: str, limit: int = 15) -> list[dict]:
    from astroquery.vizier import Vizier
    found = Vizier.find_catalogs(keywords)
    return [{"catalog": k, "title": str(v.description)[:120]} for k, v in list(found.items())[:limit]]


def describe_catalog(catalog: str, max_tables: int = 10, max_columns: int = 30) -> list[dict]:
    from astroquery.vizier import Vizier
    if not VIZIER_ID.fullmatch(catalog):
        raise DiscoveryError(f"Not a VizieR catalog id: {catalog!r} (e.g. V/162 or J/ApJ/964/69)")
    tables = Vizier(row_limit=1, columns=["*"]).get_catalogs(catalog)
    out = []
    for name in list(tables.keys())[:max_tables]:
        t = tables[name]
        out.append({"table": name, "description": str(t.meta.get("description", ""))[:160],
                    "columns": [{"name": c, "unit": str(t[c].unit) if t[c].unit is not None else None,
                                 "description": str(getattr(t[c], "description", "") or "")[:80]}
                                for c in t.colnames[:max_columns]]})
    if not out:
        raise DiscoveryError(f"VizieR has no tables for {catalog!r}")
    return out


def query_catalog(table: str, region: dict, *, label: str, columns: list[str] | None = None,
                  id_col: str | None = None, limit: int = DEFAULT_ROWS) -> tuple[list[dict], dict]:
    """A VizieR table over the region (cone). Positions are VizieR's computed J2000 ones."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astroquery.vizier import Vizier
    if not VIZIER_ID.fullmatch(table):
        raise DiscoveryError(f"Not a VizieR table id: {table!r} (e.g. V/162/lrs_stellar)")
    region, limit = check_region(region), max(1, min(int(limit), MAX_ROWS))
    cols = [check_ident(c, "column") for c in columns] if columns else ["*"]
    result = Vizier(columns=[*cols, "_RAJ2000", "_DEJ2000"], row_limit=limit).query_region(
        SkyCoord(region["ra"], region["dec"], unit="deg"), radius=region["radius_deg"] * u.deg, catalog=table)
    if not len(result):
        return [], _meta(label=label, provenance=f"VizieR {table}", kind="catalog", columns={},
                         query={"service": "VizieR", "table": table, "region": region}, rows=[], limit=limit)
    t = result[0]
    rows, colmeta = table_rows(t, prefix=table.replace("/", "_"), ra_col="_RAJ2000", dec_col="_DEJ2000",
                               id_col=check_ident(id_col, "id column") if id_col else None)
    return rows, _meta(label=label, provenance=f"VizieR {table}", kind="catalog", columns=colmeta,
                       query={"service": "VizieR", "table": table, "columns": cols, "region": region},
                       rows=rows, limit=limit)


# ── IVOA registry and TAP ────────────────────────────────────────────────────

SERVICE_TYPES = {"tap": "tap", "conesearch": "conesearch", "sia": "sia", "ssa": "ssa"}


def find_services(keywords: list[str], servicetype: str | None = "tap", limit: int = 15) -> list[dict]:
    from pyvo import registry
    if servicetype is not None and servicetype not in SERVICE_TYPES:
        raise DiscoveryError(f"servicetype must be one of {sorted(SERVICE_TYPES)} or null")
    kwargs = {"keywords": keywords}
    if servicetype:
        kwargs["servicetype"] = SERVICE_TYPES[servicetype]
    out = []
    for r in list(registry.search(**kwargs))[:limit]:
        urls = {}
        for kind in ("tap", "conesearch", "sia", "ssa"):
            try:
                urls[kind] = r.get_service(kind).baseurl
            except Exception:  # the resource has no such interface
                pass
        out.append({"ivoid": r.ivoid, "title": (r.res_title or "")[:100],
                    "description": (r.res_description or "")[:200], "waveband": r.waveband,
                    "services": urls})
    return out


def _tap(url: str):
    import pyvo
    return pyvo.dal.TAPService(check_public_url(url))


def describe_service(url: str, table: str | None = None, search: str | None = None,
                     max_items: int = 60) -> dict:
    """A TAP service's tables (optionally filtered by a word), or one table's columns."""
    tap = _tap(url)
    if table:
        check_ident(table, "table")
        cols = tap.search("SELECT column_name, unit, ucd, datatype, description FROM tap_schema.columns "
                          f"WHERE table_name = '{table}'", maxrec=500).to_table()
        if not len(cols):
            raise DiscoveryError(f"No table {table!r} in this service's TAP_SCHEMA; list tables first")
        return {"service": url, "table": table, "columns": [
            {k: json_value(c[k]) for k in ("column_name", "unit", "ucd", "datatype", "description")}
            for c in cols[:max_items]], "total_columns": len(cols)}
    where = ""
    if search:
        if not re.fullmatch(r"[\w .+-]{1,40}", search):
            raise DiscoveryError("search must be a plain word or phrase")
        s = search.lower().replace("'", "")
        where = (f" WHERE LOWER(table_name) LIKE '%{s}%' OR LOWER(description) LIKE '%{s}%'")
    tabs = tap.search(f"SELECT table_name, description FROM tap_schema.tables{where}", maxrec=500).to_table()
    names = [str(n) for n in tabs["table_name"]]
    return {"service": url, "tables": [{"table": json_value(t["table_name"]),
                                        "description": (json_value(t["description"]) or "")[:160]}
                                       for t in tabs[:max_items]],
            "total_tables": len(tabs), "has_obscore": any(n.lower() == "ivoa.obscore" for n in names)}


def build_adql(table: str, region: dict, *, columns: list[str] | None, ra_col: str | None,
               dec_col: str | None, region_col: str | None, where: str | None, limit: int,
               count_only: bool = False) -> str:
    """SELECT over the region: footprints that intersect it, or points inside it."""
    check_ident(table, "table")
    region = check_region(region)
    circle = f"CIRCLE('ICRS', {region['ra']:.6f}, {region['dec']:.6f}, {region['radius_deg']:.6f})"
    if region_col:
        spatial = f"INTERSECTS({check_ident(region_col, 'region column')}, {circle}) = 1"
    elif ra_col and dec_col:
        spatial = (f"CONTAINS(POINT('ICRS', {check_ident(ra_col, 'RA column')}, "
                   f"{check_ident(dec_col, 'Dec column')}), {circle}) = 1")
    else:
        raise DiscoveryError("Give ra_col and dec_col (points) or region_col (footprints, e.g. s_region)")
    extra = check_where(where)
    cond = spatial + (f" AND ({extra})" if extra else "")
    if count_only:
        return f"SELECT COUNT(*) AS n FROM {table} WHERE {cond}"
    cols = ", ".join(check_ident(c, "column") for c in columns) if columns else "*"
    return f"SELECT TOP {int(limit)} {cols} FROM {table} WHERE {cond}"


def query_service(url: str, table: str, region: dict, *, label: str, columns: list[str] | None = None,
                  ra_col: str | None = None, dec_col: str | None = None, region_col: str | None = None,
                  id_col: str | None = None, where: str | None = None, limit: int = DEFAULT_ROWS,
                  count_only: bool = False):
    """Run a bounded TAP query; ``count_only`` returns just the number of matches."""
    limit = max(1, min(int(limit), MAX_ROWS))
    tap = _tap(url)
    if region_col and not (ra_col and dec_col):  # footprints still need a position for the map
        ra_col, dec_col = ra_col or "s_ra", dec_col or "s_dec"
    if columns:
        columns = list(dict.fromkeys([*columns, *(c for c in (ra_col, dec_col, region_col, id_col) if c)]))
    adql = build_adql(table, region, columns=columns, ra_col=ra_col, dec_col=dec_col,
                      region_col=region_col, where=where, limit=limit, count_only=count_only)
    result = tap.search(adql, maxrec=limit).to_table()
    if count_only:
        return int(result["n"][0]), adql
    rows, colmeta = table_rows(result, prefix=table.split(".")[-1], ra_col=ra_col, dec_col=dec_col,
                               id_col=id_col, region_col=region_col)
    if region_col:
        rows = [add_observation_columns(r) for r in rows]
        colmeta = {**colmeta, **{c: m for c, m in OBS_COLUMNS.items() if any(c in r for r in rows)}}
    return rows, _meta(label=label, provenance=f"{url} ({table})",
                       kind="footprints" if region_col else "catalog", columns=colmeta,
                       query={"service": url, "table": table, "adql": adql, "region": check_region(region)},
                       rows=rows, limit=limit)


__all__ = ["DiscoveryError", "build_adql", "check_public_url", "check_where", "describe_catalog",
           "describe_service", "find_catalogs", "find_services", "json_value", "position_columns",
           "query_catalog", "query_service", "survey_inventory", "table_rows"]
