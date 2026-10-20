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

import functools

from agentbook import Tool, ToolError

from .archive import ArchiveError, exoplanet_cone_search, get_planets, resolve_target  # noqa: F401
from .archive import *  # noqa: F401,F403  (constants and helpers, for existing imports)


def _as_tool_error(fn):
    """The archive functions raise ArchiveError; agentbook expects ToolError."""
    @functools.wraps(fn)
    def wrapped(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ArchiveError as exc:
            raise ToolError(str(exc)) from exc
    return wrapped


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
    fn=_as_tool_error(resolve_target),
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
    fn=_as_tool_error(exoplanet_cone_search),
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
    fn=_as_tool_error(get_planets),
)


def exoplanet_tools() -> list[Tool]:
    return [RESOLVE_TARGET, EXOPLANET_CONE_SEARCH, GET_PLANETS]
