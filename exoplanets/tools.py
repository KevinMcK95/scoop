"""Exoplanet-host tools: resolve a name, find known hosts in a cone, list a host's planets.

Each tool is a plain Python function plus a `Tool` wrapper (name, description,
JSON schema). The functions return dicts; the ToolRegistry turns them into
JSON text for the model. Every planet or host comes back as a record in the
provenance format of evaluation/checks.py (kind, archive, native_id, ra, dec,
query, access_url, retrieved_at), so the team's checks can run on it.

Services (checked live on 2026-10-19, see skills/surveys/nasa_exoplanet_archive.md):
  - Sesame (CDS) name resolver, XML output
  - NASA Exoplanet Archive TAP, table pscomppars (one row per confirmed planet)

HTTP goes through `http_get`, which can record responses to exoplanets/fixtures/
and replay them, so tests and offline examples run without the network:

    SCOOP_HTTP=live    (default) query the services
    SCOOP_HTTP=record  query the services and save each response
    SCOOP_HTTP=replay  only read saved responses; fail if one is missing
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

import requests

from agentbook import Tool, ToolError

SESAME_URL = "https://cds.unistra.fr/cgi-bin/nph-sesame/-oxp/SNV"
TAP_URL = "https://exoplanetarchive.ipac.caltech.edu/TAP/sync"
ARCHIVE = "NASA Exoplanet Archive (pscomppars)"
FIXTURES = Path(__file__).resolve().parent / "fixtures"
TIMEOUT_S = 60
MAX_RADIUS_ARCMIN = 60  # larger cones are allowed by the archive, but rarely what the user wants
ARCHIVE_EPOCH = 2016.0  # archive positions match J2000 + proper motion x ~16 yr (TRAPPIST-1, Proxima Cen)

BIBCODE_RE = re.compile(r"ui\.adsabs\.harvard\.edu/abs/([^/\s\"'>]+)")
REFNAME_RE = re.compile(r">([^<]+)</a>")


# ------------------------------------------------------------------ HTTP


def http_get(url: str, params: dict, post: bool = False) -> str:
    """GET (or POST) `url` and return the body, live or from a saved fixture
    (see module docstring)."""
    mode = os.environ.get("SCOOP_HTTP", "live")
    key = hashlib.sha256(json.dumps([url, params], sort_keys=True).encode()).hexdigest()[:16]
    fixture = FIXTURES / f"{key}.json"
    if mode == "replay":
        if not fixture.exists():
            raise ToolError(f"No saved response for this query (fixture {key}). "
                            "Run once with SCOOP_HTTP=record to save it.")
        return json.loads(fixture.read_text())["body"]
    try:
        if post:
            response = requests.post(url, data=params, timeout=TIMEOUT_S)
        else:
            response = requests.get(url, params=params, timeout=TIMEOUT_S)
        response.raise_for_status()
    except requests.Timeout:
        raise ToolError(f"{url} did not answer within {TIMEOUT_S} s. Try again once; if it "
                        "fails again, report that this service is unavailable.")
    except requests.RequestException as e:
        status = getattr(e.response, "status_code", None)
        raise ToolError(f"Request to {url} failed ({type(e).__name__}"
                        f"{f', HTTP {status}' if status else ''}). Report that this "
                        "service is unavailable rather than answering from memory.")
    if mode == "record":
        FIXTURES.mkdir(exist_ok=True)
        fixture.write_text(json.dumps({"url": url, "params": params, "body": response.text},
                                      indent=1))
    return response.text


def tap_query(adql: str) -> list[dict]:
    """Run an ADQL query on the Exoplanet Archive and return rows as dicts.

    Sent as POST: the archive's firewall answers 403 to cone queries sent as GET
    (seen 2026-10-19), and TAP services accept both."""
    return json.loads(http_get(TAP_URL, {"query": adql, "format": "json"}, post=True))


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def adql_string(value: str) -> str:
    """Quote a string for ADQL. Doubling single quotes stops a name like
    "x' or 1=1 --" from changing the query."""
    return "'" + value.replace("'", "''") + "'"


def separation_arcsec(ra1, dec1, ra2, dec2) -> float:
    """Angular separation (haversine), in arcseconds."""
    ra1, dec1, ra2, dec2 = map(math.radians, (ra1, dec1, ra2, dec2))
    h = (math.sin((dec2 - dec1) / 2) ** 2
         + math.cos(dec1) * math.cos(dec2) * math.sin((ra2 - ra1) / 2) ** 2)
    return math.degrees(2 * math.asin(math.sqrt(min(1.0, h)))) * 3600


def parse_reference(refname: str | None) -> dict:
    """disc_refname is an HTML link; pull out the readable name and the ADS bibcode."""
    if not refname:
        return {"reference": None, "bibcode": None}
    name, bibcode = REFNAME_RE.search(refname), BIBCODE_RE.search(refname)
    return {"reference": name.group(1).strip() if name else refname,
            "bibcode": bibcode.group(1) if bibcode else None}


# ------------------------------------------------------------------ tools


def resolve_target(name: str) -> dict:
    """Name -> J2000 position, proper motion and object type, via Sesame."""
    url = f"{SESAME_URL}?{quote(name)}"  # Sesame takes the name as the bare query string
    xml = http_get(url, {})
    resolver = ET.fromstring(xml).find("./Target/Resolver")
    if resolver is None or resolver.findtext("jradeg") is None:
        raise ToolError(f"Sesame (SIMBAD, NED, VizieR) found no object named '{name}'. Check "
                        "the spelling, try another identifier (HD, HIP, 2MASS, TOI, Kepler, "
                        "Gaia DR3), or ask the user for coordinates.")
    pm_ra, pm_dec = resolver.findtext("pm/pmRA"), resolver.findtext("pm/pmDE")
    pm_ra, pm_dec = (float(pm_ra) if pm_ra else None), (float(pm_dec) if pm_dec else None)
    # How far the star has moved between J2000 and the archive's epoch, so the
    # model can pick a cone radius without doing the arithmetic itself.
    offset = math.hypot(pm_ra or 0, pm_dec or 0) / 1000 * (ARCHIVE_EPOCH - 2000)
    return {
        "name": name,
        "resolved_by": resolver.get("name"),
        "main_id": resolver.findtext("oname"),
        "object_type": resolver.findtext("otype"),
        "ra_deg": float(resolver.findtext("jradeg")),
        "dec_deg": float(resolver.findtext("jdedeg")),
        "position_epoch": "J2000",
        "pm_ra_mas_yr": pm_ra,
        "pm_dec_mas_yr": pm_dec,
        "offset_at_archive_epoch_arcsec": round(offset, 1),
        "suggested_min_radius_arcmin": max(0.5, math.ceil(offset * 1.5 / 30) / 2),
        "provenance": {"archive": "Sesame (CDS)", "query": name,
                       "access_url": url, "retrieved_at": now()},
    }


def exoplanet_cone_search(ra: float, dec: float, radius_arcmin: float) -> dict:
    """Known planet hosts within `radius_arcmin` of (ra, dec), nearest first."""
    adql = ("SELECT hostname, pl_name, ra, dec, sy_dist, sy_pnum FROM pscomppars "
            f"WHERE CONTAINS(POINT('ICRS', ra, dec), "
            f"CIRCLE('ICRS', {ra!r}, {dec!r}, {radius_arcmin / 60!r})) = 1")
    rows = tap_query(adql)
    retrieved = now()
    hosts: dict[str, dict] = {}
    for r in rows:
        host = hosts.setdefault(r["hostname"], {
            "kind": "catalog_row", "archive": ARCHIVE, "native_id": r["hostname"],
            "ra": r["ra"], "dec": r["dec"],
            "separation_arcsec": round(separation_arcsec(ra, dec, r["ra"], r["dec"]), 1),
            "distance_pc": r["sy_dist"], "n_planets": r["sy_pnum"], "planets": [],
            "query": adql, "access_url": TAP_URL, "retrieved_at": retrieved})
        host["planets"].append(r["pl_name"])
    result = {"center": {"ra": ra, "dec": dec}, "radius_arcmin": radius_arcmin,
              "n_hosts": len(hosts),
              "hosts": sorted(hosts.values(), key=lambda h: h["separation_arcsec"])}
    if not hosts:
        result["note"] = ("No confirmed planet hosts in this cone. Archive positions are near "
                          "epoch 2016, not J2000: if the target has high proper motion, retry once "
                          "with resolve_target's suggested_min_radius_arcmin before concluding "
                          "there are none.")
    return result


PLANET_COLUMNS = ("pl_name, hostname, ra, dec, sy_dist, st_spectype, st_teff, pl_orbper, "
                  "pl_rade, pl_bmasse, pl_bmassprov, pl_eqt, discoverymethod, disc_year, "
                  "disc_facility, disc_refname")


def get_planets(hostname: str) -> dict:
    """Every confirmed planet of one host, with its parameters and discovery paper."""
    adql = (f"SELECT {PLANET_COLUMNS} FROM pscomppars "
            f"WHERE hostname = {adql_string(hostname)} ORDER BY pl_orbper")
    rows = tap_query(adql)
    if not rows:
        raise ToolError(f"No confirmed planets with hostname '{hostname}'. Host names must match "
                        "the archive exactly (e.g. 'TRAPPIST-1', 'HD 209458'); use the "
                        "hostname returned by exoplanet_cone_search.")
    retrieved = now()
    first = rows[0]
    planets = [{
        "kind": "catalog_row", "archive": ARCHIVE, "native_id": r["pl_name"],
        "ra": r["ra"], "dec": r["dec"],
        "orbital_period_days": r["pl_orbper"], "radius_earth": r["pl_rade"],
        "mass_earth": r["pl_bmasse"], "mass_type": r["pl_bmassprov"],
        "equilibrium_temp_k": r["pl_eqt"], "discovery_method": r["discoverymethod"],
        "discovery_year": r["disc_year"], "discovery_facility": r["disc_facility"],
        **parse_reference(r["disc_refname"]),
        "query": adql, "access_url": TAP_URL, "retrieved_at": retrieved,
    } for r in rows]
    return {"hostname": hostname, "distance_pc": first["sy_dist"],
            "spectral_type": first["st_spectype"], "teff_k": first["st_teff"],
            "n_planets": len(planets), "planets": planets}


# ------------------------------------------------------------------ Tool wrappers
# The descriptions are prompts: they tell the model what each tool returns,
# when to use it, and what to do when it fails.

RESOLVE_TARGET = Tool(
    name="resolve_target",
    description=(
        "Resolve an astronomical object name (e.g. 'TRAPPIST-1', 'HD 209458', 'M87') to its "
        "J2000 position in degrees, proper motion (mas/yr), object type, and the smallest "
        "cone radius that will still find it in the exoplanet archive "
        "(suggested_min_radius_arcmin), using Sesame (SIMBAD, NED, VizieR). Use this first whenever the user gives a name instead of "
        "coordinates. If the name is not found, try another identifier or ask the user."),
    input_schema={
        "type": "object",
        "properties": {"name": {"type": "string", "minLength": 1,
                                "description": "Object name or identifier"}},
        "required": ["name"], "additionalProperties": False},
    fn=resolve_target,
)

EXOPLANET_CONE_SEARCH = Tool(
    name="exoplanet_cone_search",
    description=(
        "List the confirmed exoplanet host stars within a radius of a sky position, from the "
        "NASA Exoplanet Archive, nearest first: host name, position, separation, distance and "
        "planet names. Use this to answer 'are there known planet hosts here?'. It returns "
        "only confirmed planets, not candidates. An empty result is a real answer: say there "
        "are none rather than widening the radius repeatedly. Archive positions are near "
        "epoch 2016, so stars with large proper motion can sit 10+ arcsec from their J2000 "
        "position; use at least the suggested_min_radius_arcmin from resolve_target."),
    input_schema={
        "type": "object",
        "properties": {
            "ra": {"type": "number", "minimum": 0, "maximum": 360,
                   "description": "Right ascension, degrees (ICRS)"},
            "dec": {"type": "number", "minimum": -90, "maximum": 90,
                    "description": "Declination, degrees (ICRS)"},
            "radius_arcmin": {"type": "number", "exclusiveMinimum": 0,
                              "maximum": MAX_RADIUS_ARCMIN,
                              "description": "Search radius in arcminutes"}},
        "required": ["ra", "dec", "radius_arcmin"], "additionalProperties": False},
    fn=exoplanet_cone_search,
)

GET_PLANETS = Tool(
    name="get_planets",
    description=(
        "Get every confirmed planet of one host star from the NASA Exoplanet Archive: orbital "
        "period (days), radius and mass (Earth units; mass_type says if it is a true mass or "
        "M sin i), equilibrium temperature, discovery method, year, facility, and the "
        "discovery paper with its ADS bibcode. The hostname must match the archive exactly, "
        "so use a name returned by exoplanet_cone_search. Null means the archive has no value."),
    input_schema={
        "type": "object",
        "properties": {"hostname": {"type": "string", "minLength": 1,
                                    "description": "Host name exactly as in the archive"}},
        "required": ["hostname"], "additionalProperties": False},
    fn=get_planets,
)


def exoplanet_tools() -> list[Tool]:
    return [RESOLVE_TARGET, EXOPLANET_CONE_SEARCH, GET_PLANETS]
