"""Exoplanet-host tools: resolve a name, find known hosts in a cone, list a host's planets.

Each tool is a plain Python function plus a `Tool` wrapper (name, description,
JSON schema). The functions return dicts; the ToolRegistry turns them into
JSON text for the model. Every planet or host comes back as a record in the
provenance format of evaluation/checks.py (kind, archive, native_id, ra, dec,
query, access_url, retrieved_at), so the team's checks can run on it.

Services (checked live on 2026-10-19, see scoop/skills/surveys/nasa_exoplanet_archive.md):
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
from .archive import toi_cone_search
from .mast import hst_jwst_programs
from .papers import find_papers, get_abstract
from .tess import SHORT_CADENCE_DEFAULTS, extractable_light_curves, short_cadence_candidates
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
        "M sin i), equilibrium temperature, insolation (Earth = 1), discovery method, year, facility, and the "
        "discovery paper with its ADS bibcode. The hostname must match the archive exactly, "
        "so use a name returned by exoplanet_cone_search. Null means the archive has no value."),
    input_schema={
        "type": "object",
        "properties": {"hostname": {"type": "string", "minLength": 1,
                                    "description": "Host name exactly as in the archive"}},
        "required": ["hostname"], "additionalProperties": False},
    fn=_as_tool_error(get_planets),
)


FIND_PAPERS = Tool(
    name="find_papers",
    description=(
        "Search ADS (the astronomy literature) for papers, most relevant first: bibcode, "
        "title, first author, year, journal, citation count. Use it to find what has been "
        "published on a planet beyond its discovery paper, e.g. '\"TOI-561 b\" atmosphere'. "
        "Put names in double quotes. It returns no abstracts: call get_abstract for each "
        "paper you want to say anything about. Each paper has a type: prefer research papers "
        "('article'); news items ('newsitem'), proposals ('proposal') and abstracts are not "
        "results. The same paper can appear twice, as an arXiv preprint ('eprint') and as the "
        "journal version; prefer the journal version. Results are ordered by relevance, not "
        "date: check the years and include recent papers."),
    input_schema={
        "type": "object",
        "properties": {
            "query": {"type": "string", "minLength": 1, "description": "ADS query"},
            "rows": {"type": "integer", "minimum": 1, "maximum": 20,
                     "description": "Number of papers (default 10)"},
            "refereed_only": {"type": "boolean", "description": "Only refereed papers"}},
        "required": ["query"], "additionalProperties": False},
    fn=_as_tool_error(find_papers),
)

GET_ABSTRACT = Tool(
    name="get_abstract",
    description=(
        "Get one paper's title, authors and full abstract from ADS, by bibcode. Any claim "
        "about what a paper found must quote this abstract word for word, so fetch it before "
        "citing a paper. Use bibcodes returned by find_papers or get_planets."),
    input_schema={
        "type": "object",
        "properties": {"bibcode": {"type": "string", "minLength": 19, "maxLength": 19,
                                   "description": "ADS bibcode, e.g. 2017Natur.542..456G"}},
        "required": ["bibcode"], "additionalProperties": False},
    fn=_as_tool_error(get_abstract),
)


def _cone_schema(**extra) -> dict:
    """ra, dec, radius_arcmin, plus any extra properties (all optional)."""
    return {"type": "object",
            "properties": {
                "ra": {"type": "number", "minimum": 0, "maximum": 360,
                       "description": "Right ascension, degrees (ICRS)"},
                "dec": {"type": "number", "minimum": -90, "maximum": 90,
                        "description": "Declination, degrees (ICRS)"},
                "radius_arcmin": {"type": "number", "exclusiveMinimum": 0, "maximum": MAX_RADIUS_ARCMIN,
                                  "description": "Search radius in arcminutes"},
                **extra},
            "required": ["ra", "dec", "radius_arcmin"], "additionalProperties": False}


TOI_CONE_SEARCH = Tool(
    name="toi_cone_search",
    description=(
        "List the TESS Objects of Interest (TOIs) within a radius of a sky position, from the "
        "NASA Exoplanet Archive, nearest first: TOI number, TIC ID, disposition (planet "
        "candidate, confirmed, false positive, ...), period, radius, transit depth, TESS "
        "magnitude. Use it for TESS candidates, which exoplanet_cone_search (confirmed planets "
        "only) does not list. Positions are J2000. An empty result is a real answer."),
    input_schema=_cone_schema(),
    fn=_as_tool_error(toi_cone_search),
)

HST_JWST_PROGRAMS = Tool(
    name="hst_jwst_programs",
    description=(
        "List the HST and JWST programs that took spectra, time series (transit and eclipse "
        "observations) or IFU cubes within a radius of a sky position, from MAST, earliest "
        "first: program, PI, title, instruments, targets, number of observations, first and "
        "last dates, and how many are planned (scheduled, not yet taken). Use it for 'has "
        "JWST/HST observed this planet's atmosphere?'. A program title says why it was "
        "proposed, not what it found: for findings, find the papers."),
    input_schema=_cone_schema(),
    fn=_as_tool_error(hst_jwst_programs),
)

EXTRACTABLE_LIGHT_CURVES = Tool(
    name="extractable_light_curves",
    description=(
        "For the TESS Input Catalog stars in a field brighter than tmag_max (default 16), say "
        "which already have TESS light curves (SPOC 2-min/20-s, and light curves made from the "
        "full-frame images by QLP, TESS-SPOC, TGLC, ...) with their sectors, and rate whether "
        "a light curve could be extracted from the full-frame images: good, possible, poor or "
        "not observed, with the reason (brightness, contamination by neighbours). Also gives "
        "the sectors that imaged the field. Lists the brightest max_stars (default 50)."),
    input_schema=_cone_schema(
        tmag_max={"type": "number", "minimum": 4, "maximum": 20,
                  "description": "Faintest TESS magnitude to include (default 16)"},
        max_stars={"type": "integer", "minimum": 1, "maximum": 200,
                   "description": "Most stars to list, brightest first (default 50)"}),
    fn=_as_tool_error(extractable_light_curves),
)

SHORT_CADENCE_CANDIDATES = Tool(
    name="short_cadence_candidates",
    description=(
        "Rank a field's stars for a future TESS short-cadence (2-min/20-s) request by fixed "
        "criteria, with each star's score, its parts and reasons, and how many stars each "
        "criterion rejected. Defaults: "
        + ", ".join(f"{k}={v}" for k, v in SHORT_CADENCE_DEFAULTS.items())
        + ", stars that already have 2-min/20-s data excluded. Change a criterion only when the "
        "user asks, and say which you changed. The score rewards bright, small, uncontaminated "
        "dwarfs and TOI or planet hosts. It does not check future TESS pointings: say so."),
    input_schema=_cone_schema(
        tmag_max={"type": "number", "minimum": 4, "maximum": 20, "description": "Faintest Tmag (default 13)"},
        logg_min={"type": "number", "minimum": 0, "maximum": 6, "description": "Smallest log g (default 4.0)"},
        radius_max_rsun={"type": "number", "exclusiveMinimum": 0, "description": "Largest radius, R_sun (default 1.5)"},
        contamination_max={"type": "number", "minimum": 0, "description": "Largest TIC Rcont (default 0.1)"},
        exclude_existing_short_cadence={"type": "boolean", "description": "Drop stars with 2-min/20-s data (default true)"},
        max_results={"type": "integer", "minimum": 1, "maximum": 50, "description": "Most candidates to list (default 10)"}),
    fn=_as_tool_error(short_cadence_candidates),
)


def exoplanet_tools() -> list[Tool]:
    return [RESOLVE_TARGET, EXOPLANET_CONE_SEARCH, GET_PLANETS]


def agent_tools() -> list[Tool]:
    """Everything the fact-checked exoplanet agent uses: the archive, and the papers."""
    return exoplanet_tools() + [TOI_CONE_SEARCH, HST_JWST_PROGRAMS, FIND_PAPERS, GET_ABSTRACT,
                                EXTRACTABLE_LIGHT_CURVES, SHORT_CADENCE_CANDIDATES]
