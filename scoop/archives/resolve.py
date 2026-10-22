"""Object names to ICRS positions with CDS Sesame (SIMBAD, then NED, then VizieR)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/SNV?{name}"
# When Sesame is unreachable (seen 2026-10-21 while SIMBAD itself answered), SIMBAD's
# identifier table resolves the name; ADQL string literal, quotes doubled.
SIMBAD_TAP = "https://simbad.cds.unistra.fr/simbad/sim-tap/sync"
SIMBAD_BY_ID = ("SELECT TOP 1 b.main_id, b.ra, b.dec, b.otype FROM basic AS b JOIN ident AS i "
                "ON i.oidref = b.oid WHERE i.id = '{name}'")


def parse_sesame(xml: str) -> dict | None:
    """The first resolver answer with a position, or None if nothing resolved."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return None
    for resolver in root.iter("Resolver"):
        ra, dec = resolver.findtext("jradeg"), resolver.findtext("jdedeg")
        if ra and dec:
            return {"ra": float(ra), "dec": float(dec),
                    "name": (resolver.findtext("oname") or "").strip() or None,
                    "otype": resolver.findtext("otype"),
                    "resolver": resolver.get("name", "").split(",")[0].split("(")[0].split("=", 1)[-1].strip()}
    return None


def sesame_resolver(client):
    """An async ``name -> position | None`` using an active ``httpx.AsyncClient``."""

    async def resolve(name: str) -> dict | None:
        try:
            response = await client.get(SESAME.format(name=quote(name)), timeout=15.0)
            response.raise_for_status()
        except Exception as sesame_error:  # Sesame down or unreachable: ask SIMBAD directly
            try:
                return await simbad_by_id(client, name)
            except Exception:
                raise sesame_error from None
        return parse_sesame(response.text)

    return resolve


async def simbad_by_id(client, name: str) -> dict | None:
    """SIMBAD's position for an identifier (it normalises e.g. "ngc300"), or None."""
    import csv
    import io
    query = SIMBAD_BY_ID.format(name=" ".join(name.split()).replace("'", "''"))
    response = await client.post(SIMBAD_TAP, data={"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                                   "QUERY": query}, timeout=15.0)
    response.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(response.text)))
    if not rows or not rows[0].get("ra"):
        return None
    r = rows[0]
    return {"ra": float(r["ra"]), "dec": float(r["dec"]), "name": " ".join(r["main_id"].split()) or None,
            "otype": r.get("otype"), "resolver": "SIMBAD TAP (Sesame unreachable)"}


__all__ = ["parse_sesame", "sesame_resolver", "simbad_by_id"]
