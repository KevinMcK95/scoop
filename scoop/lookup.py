"""SIMBAD at a position: catalogued objects nearby and their bibliography.

Queries SIMBAD's TAP service with ADQL built only from validated numbers (names
are resolved to positions with Sesame first), so no user text reaches the query.
The JSON parser accepts TAP's ``{"metadata": [...], "data": [[...]]}`` output.
"""
from __future__ import annotations

import math
from typing import Any

SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"


def _number(value: float, name: str, low: float, high: float) -> str:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) \
            or not low <= value <= high:
        raise ValueError(f"{name} must be a number in [{low}, {high}]")
    return repr(float(value))


def _count(limit: int) -> int:
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100:
        raise ValueError("limit must be an integer in [1, 100]")
    return limit


def cone_adql(ra: float, dec: float, radius_deg: float, limit: int = 10) -> str:
    ra_s, dec_s = _number(ra, "ra", 0, 360), _number(dec, "dec", -90, 90)
    r_s = _number(radius_deg, "radius", 0, 1)
    top = _count(limit)
    point = f"POINT('ICRS', {ra_s}, {dec_s})"
    return (f"SELECT TOP {top} oid, main_id, ra, dec, otype, nbref, "
            f"DISTANCE(POINT('ICRS', ra, dec), {point}) AS dist FROM basic "
            f"WHERE CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra_s}, {dec_s}, {r_s})) = 1 "
            f"ORDER BY dist")


def references_adql(oid: int, limit: int = 10) -> str:
    if not isinstance(oid, int) or isinstance(oid, bool) or oid < 0:
        raise ValueError("oid must be a non-negative integer")
    top = _count(limit)
    # "year" is an ADQL reserved word: quote it, and sort by an alias (SIMBAD's
    # parser rejects a qualified quoted column in ORDER BY).
    return (f'SELECT TOP {top} r.bibcode, r."year" AS ref_year, r.title FROM has_ref AS h '
            f"JOIN ref AS r ON h.oidbibref = r.oidbib WHERE h.oidref = {oid} "
            f"ORDER BY ref_year DESC")


def parse_tap_json(payload: Any) -> list[dict]:
    """Rows as dicts from TAP JSON; raises ValueError on an unexpected shape."""
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list) \
            or not isinstance(payload.get("metadata"), list):
        raise ValueError("unexpected TAP response")
    names = [column.get("name") for column in payload["metadata"]]
    return [dict(zip(names, row)) for row in payload["data"] if isinstance(row, list)]


def simbad_query(client):
    """An async ``adql -> rows`` using an active ``httpx.AsyncClient``."""

    async def query(adql: str) -> list[dict]:
        response = await client.post(SIMBAD_TAP, data={
            "REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json", "QUERY": adql}, timeout=30.0)
        response.raise_for_status()
        return parse_tap_json(response.json())

    return query


async def lookup_position(query, ra: float, dec: float, radius_arcsec: float, papers: int) -> dict:
    """Objects within the radius (nearest first) and the nearest one's references."""
    rows = await query(cone_adql(ra, dec, radius_arcsec / 3600))
    objects = [{"main_id": str(r.get("main_id", "")).strip(), "otype": r.get("otype"),
                "ra": r.get("ra"), "dec": r.get("dec"), "n_refs": r.get("nbref"),
                "sep_arcsec": round(float(r["dist"]) * 3600, 2) if r.get("dist") is not None else None}
               for r in rows]
    result: dict = {"service": "SIMBAD TAP", "objects": objects}
    if rows and papers and isinstance(rows[0].get("oid"), int):
        refs = await query(references_adql(rows[0]["oid"], papers))
        result["references_of"] = objects[0]["main_id"]
        result["references"] = [{"bibcode": str(r.get("bibcode", "")).strip(), "year": r.get("ref_year"),
                                 "title": r.get("title")} for r in refs]
    return result


__all__ = ["cone_adql", "lookup_position", "parse_tap_json", "references_adql", "simbad_query"]
