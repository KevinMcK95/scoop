"""MCP server: SCOOP's evaluation checks as tools for any MCP client.

Works with Claude Code / Claude Desktop, LangGraph (via langchain-mcp-adapters)
and anything else that speaks MCP over stdio. Run from anywhere:

    python evaluation/mcp_server.py

Register with Claude Code (from the repository root):

    claude mcp add scoop-eval -- "$PWD/.venv/bin/python" "$PWD/evaluation/mcp_server.py"

Relative paths in tool arguments (e.g. ``root="out"``) resolve against the
repository root. Needs ``mcp`` (``pip install -e '.[eval]'``).
"""
from __future__ import annotations

import contextlib
import functools
import os
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
if __package__ in (None, ""):  # run as a script
    sys.path.insert(0, str(REPO))

# stdout carries the MCP protocol; everything the checks print goes to stderr.
with contextlib.redirect_stdout(sys.stderr):
    from evaluation import verify

try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # mcp < 2
    from mcp.server.fastmcp import FastMCP as MCPServer

INSTRUCTIONS = """Checks that SCOOP data is real, inside the requested region, complete and correct,
by querying the archives (Gaia, MAST, SIMBAD, ADS, arXiv, GW sky maps) independently.
Use verify_field for a pipeline output (e.g. "draco_1"), verify_result for a result.json, and the
small tools (lookup_ids, check_papers, check_links, check_positions, check_gw) to check individual items.
By default big results are checked on a random sample (100 records per archive, 100 links), with an
upper limit on the failure rate; pass sample=0 for a full check when the user asks for one.
Pass `only` to run a subset of checks."""

server = MCPServer("scoop-eval", instructions=INSTRUCTIONS)


def _repo_path(p: str) -> str:
    return p if os.path.isabs(p) else str(REPO / p)


def quiet(fn):
    """Send anything the tool prints to stderr (stdout is the protocol), and return errors as
    ``{"error": ...}`` so the caller sees what went wrong (e.g. an archive timing out)."""
    @functools.wraps(fn)
    def wrapper(*args, **kwargs):
        with contextlib.redirect_stdout(sys.stderr):
            try:
                return fn(*args, **kwargs)
            except Exception as exc:
                return {"error": f"{type(exc).__name__}: {exc}"[:1000]}
    return wrapper


@server.tool()
@quiet
def verify_field(field: str, root: str = "out", sample: float = 100, seed: int = 0,
                 only: list[str] | None = None, timeout: float = 300) -> dict:
    """Verify a SCOOP field: the output of scoop/pipeline/lvdb_field.py / the workspace server.

    field: a field key such as "draco_1" (searched under root) or a path to <key>_field.json.
    sample: per archive, how many records to re-check against the archive and how many links to
      fetch (a number, or a fraction if below 1); 0 checks everything (slower). Default 100.
    seed: random seed for the sample, so a run can be repeated.
    only: run just the checks whose names start with these, e.g. ["in_field", "id_recheck"].
    Check names: provenance, archive_status, target, in_field, id_recheck, sanity, completeness,
    files, gw_localization, paper_exists, paper_about_target, arxiv, links, must_include.
    Returns the verdict, totals per check, what was sampled, the first failures and warnings,
    and the report path.
    """
    target = field if not field.endswith(".json") else _repo_path(field)
    return verify.verify_field(target, root=_repo_path(root), only=only, timeout=timeout,
                               sample=sample or None, seed=seed, log_progress=lambda m: print(m, file=sys.stderr))


@server.tool()
@quiet
def verify_result(path: str, target: str | None = None, sample: float = 100, seed: int = 0,
                  only: list[str] | None = None, timeout: float = 300) -> dict:
    """Verify a result.json whose records follow the provenance format in evaluation/README.md, or a
    saved ADS payload from scoop.literature (then give target, the object the papers should be about).
    sample / seed / only work as in verify_field."""
    return verify.verify_result_file(_repo_path(path), target=target, only=only, timeout=timeout,
                                     sample=sample or None, seed=seed,
                                     log_progress=lambda m: print(m, file=sys.stderr))


@server.tool()
@quiet
def lookup_ids(archive: str, ids: list[str]) -> dict:
    """Check that IDs exist. archive: "gaia" (source_id), "mast" (obs_id), "simbad" (any identifier)
    or "ads" (bibcode). Returns the archive's values for each found ID, and the missing ones."""
    return verify.lookup_ids(archive, ids)


@server.tool()
@quiet
def check_papers(bibcodes: list[str], target: str | None = None, titles: list[str] | None = None) -> dict:
    """Check papers: the bibcode exists in ADS, the title (if given) matches, and (if target is given)
    the paper is linked to the target in SIMBAD or ADS."""
    return verify.check_papers(bibcodes, target=target, titles=titles)


@server.tool()
@quiet
def check_links(urls: list[str]) -> dict:
    """Check web links: each must answer without an HTTP error, not be a "not found" page, and
    name the ID in its own URL (catches generic "soft 404" pages). ADS abstract links are checked
    through the ADS API, STScI program pages by their title."""
    return verify.check_links(urls)


@server.tool()
@quiet
def check_positions(ra: float, dec: float, radius_arcmin: float | None = None,
                    polygon: list[list[float]] | None = None, positions: list[list[float]] | None = None,
                    s_regions: list[str] | None = None, tolerance_arcsec: float = 1.0) -> dict:
    """Which positions ([ra, dec] in degrees) and footprints (ObsCore s_region strings) fall inside
    a cone (ra, dec, radius_arcmin) or a polygon ([[ra, dec], ...] around ra, dec)?"""
    return verify.check_positions(ra, dec, radius_arcmin=radius_arcmin, polygon=polygon,
                                  positions=positions, s_regions=s_regions, tolerance_arcsec=tolerance_arcsec)


@server.tool()
@quiet
def check_gw(event: str, ra: float, dec: float, skymap_url: str | None = None) -> dict:
    """Credible level, 90%-region containment and line-of-sight distance of (ra, dec) in a GW event's
    sky map. Uses the public GWTC-1 map for the event unless skymap_url is given."""
    return verify.check_gw(event, ra, dec, skymap_url=skymap_url)


if __name__ == "__main__":
    server.run()
