"""Object names to ICRS positions with CDS Sesame (SIMBAD, then NED, then VizieR)."""
from __future__ import annotations

import xml.etree.ElementTree as ET
from urllib.parse import quote

SESAME = "https://cds.unistra.fr/cgi-bin/nph-sesame/-ox/SNV?{name}"


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
        response = await client.get(SESAME.format(name=quote(name)), timeout=15.0)
        response.raise_for_status()
        return parse_sesame(response.text)

    return resolve


__all__ = ["parse_sesame", "sesame_resolver"]
