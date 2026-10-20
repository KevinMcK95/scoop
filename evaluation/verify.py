"""One-call verification of SCOOP outputs, for the MCP server, the CLI and other code.

    from evaluation.verify import verify_field, verify_result_file
    summary = verify_field("draco_1", root="out")      # a pipeline field
    summary = verify_result_file("evaluation/runs/M87/result.json")

Also small lookups an agent can use while it works (``lookup_ids``,
``check_papers``, ``check_positions``, ``check_gw``). Every function returns
plain JSON-friendly dicts.
"""
from __future__ import annotations

import json
import tempfile
from collections import Counter, defaultdict
from pathlib import Path

from .checks import FAIL, INFO, SKIP, WARN, _number, run_all
from .field_adapter import load_field, result_from_field
from .geometry import Region, separation_deg
from .run_checks import score_fakes, write_report
from .timeouts import set_socket_timeout

BENCHMARK = Path(__file__).parent / "benchmark.json"


def _benchmark():
    return json.loads(BENCHMARK.read_text()) if BENCHMARK.is_file() else None


def result_from_ads_payload(payload: dict, target: str | None = None) -> dict:
    """A result from scoop.literature's ADS payload (``result_payload``): one paper record per paper."""
    records = []
    for paper in payload.get("papers", []):
        prov, ids = paper.get("provenance") or {}, paper.get("identifiers") or {}
        records.append({"kind": "paper", "archive": prov.get("archive") or "ADS",
                        "native_id": prov.get("native_id") or ids.get("bibcode"), "title": paper.get("title"),
                        "first_author": (paper.get("authors") or [None])[0], "year": paper.get("year"),
                        "arxiv_id": ids.get("arxiv"), "abstract": paper.get("abstract"), "query": prov.get("query"),
                        "access_url": prov.get("access_url"), "retrieved_at": prov.get("retrieved_at")})
    status = {"archive": "ADS", "n": len(records),
              "status": "failed" if payload.get("error") else ("returned" if records else "empty")}
    if payload.get("error"):
        status["error"] = str(payload["error"])
    return {"query": {"target": target or "", "ra": None, "dec": None, "text": payload.get("query")},
            "generated_by": "scoop.literature ADS payload", "archive_status": [status], "records": records}


def normalize_result(result: dict, target: str | None = None) -> dict:
    """Accept the shapes teammates' code produces: nested ``provenance`` dicts (scoop.models),
    kind "publication", and ADS payloads from scoop.literature."""
    if "records" not in result and "papers" in result:
        result = result_from_ads_payload(result, target)
    records = []
    for rec in result.get("records", []):
        rec = dict(rec)
        prov = rec.pop("provenance", None)
        if isinstance(prov, dict):
            for k, v in prov.items():
                rec.setdefault(k, v)
        if rec.get("kind") == "publication":
            rec["kind"] = "paper"
        records.append(rec)
    query = dict(result.get("query") or {})
    if target and not query.get("target"):
        query["target"] = target
    return {**result, "query": query, "records": records}


def summarize(result, results, report_path, max_items=25) -> dict:
    """Totals per check and status, plus the first failures and warnings."""
    by_check = defaultdict(Counter)
    for r in results:
        if r.status != INFO:
            by_check[r.check][r.status] += 1
    fails = [r.to_dict() for r in results if r.status == FAIL]
    warns = [r.to_dict() for r in results if r.status == WARN]
    skips = [r.to_dict() for r in results if r.status == SKIP]
    summary = {
        "target": result["query"]["target"],
        "region": Region.from_query(result["query"]).describe() if result["query"].get("ra") is not None else None,
        "mode": "sampled" if any(r.status == INFO for r in results) else "full",
        "records": len(result["records"]),
        "verdict": "fail" if fails else ("incomplete" if skips else "pass"),
        "totals": dict(Counter(r.status for r in results if r.status != INFO)),
        "sampling": [f"{r.record}: {r.detail}" for r in results if r.status == INFO],
        "by_check": {k: dict(v) for k, v in by_check.items()},
        "failures": fails[:max_items], "n_failures": len(fails),
        "warnings": warns[:max_items], "n_warnings": len(warns),
        "skipped": skips,
        "report": str(report_path),
    }
    fakes, false_alarms = score_fakes(result, results)
    if fakes:
        summary["injected"] = {"planted": len(fakes), "caught": sum(1 for _, _, by in fakes if by),
                               "real_records_failing": len(false_alarms)}
    return summary


def verify_result(result: dict, base_dir: Path, out_dir: Path, name="report", source=None,
                  timeout=300, only=None, benchmark=None, log_progress=print, sample=100, seed=0) -> dict:
    """Run the checks. ``sample``: per archive, how many records (or what fraction) to check against
    the archive and how many links to fetch; None checks everything."""
    set_socket_timeout()
    result = normalize_result(result)
    results = run_all(result, Path(base_dir), timeout=timeout, benchmark=benchmark or _benchmark(),
                      only=only, log_progress=log_progress, sample=sample, seed=seed)
    md_path, *_ = write_report(result, results, Path(out_dir), name=name, source=source)
    return summarize(result, results, md_path)


def verify_result_file(path, target=None, **kw) -> dict:
    """Check a ``result.json`` (records in the provenance format, or an ADS payload from
    scoop.literature, for which ``target`` names the object); report goes next to it."""
    path = Path(path)
    result = normalize_result(json.loads(path.read_text()), target)
    return verify_result(result, path.parent, path.parent, name=f"{path.stem}_report" if path.stem != "result"
                         else "report", source=path, **kw)


def verify_field(field, root="out", **kw) -> dict:
    """Check a SCOOP field (by key under ``root``, or a ``*_field.json`` path); report goes next to it."""
    fld, path = load_field(field, root)
    result = result_from_field(fld, path)
    key = fld.get("key") or path.name.removesuffix("_field.json")
    (path.parent / f"{key}_verify_result.json").write_text(json.dumps(result, indent=1))
    return verify_result(result, path.parent, path.parent, name=f"{key}_verify_report", source=path, **kw)


# ---------------------------------------------------------------- quick lookups

def lookup_ids(archive: str, ids: list[str]) -> dict:
    """Do these IDs exist? ``archive`` is gaia, mast, simbad or ads. Returns found (with values) and missing."""
    set_socket_timeout()
    archive = archive.lower()
    ids = [str(i).strip() for i in ids]
    found = {}
    if archive == "gaia":
        from .checks import gaia_query
        for i in range(0, len(ids), 500):
            chunk = [str(int(x)) for x in ids[i:i + 500]]
            t, _ = gaia_query("SELECT source_id, ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source "
                              f"WHERE source_id IN ({', '.join(chunk)})")
            found.update({str(r["source_id"]): {"ra": float(r["ra"]), "dec": float(r["dec"]),
                                                "phot_g_mean_mag": _number(r["phot_g_mean_mag"])}
                          for r in t})
    elif archive == "mast":
        from astroquery.mast import Observations
        for i in range(0, len(ids), 200):
            for r in Observations.query_criteria(obs_id=ids[i:i + 200]):
                found[str(r["obs_id"])] = {k: str(r[k]) for k in
                                           ("obs_collection", "instrument_name", "dataproduct_type",
                                            "s_ra", "s_dec", "t_min", "calib_level")}
    elif archive == "simbad":
        from astroquery.simbad import Simbad
        quoted = ", ".join("'" + x.replace("'", "''") + "'" for x in ids)
        t = Simbad.query_tap("SELECT ident.id, basic.main_id, basic.ra, basic.dec, basic.otype "
                             f"FROM ident JOIN basic ON ident.oidref = basic.oid WHERE ident.id IN ({quoted})")
        by_norm = {" ".join(x.split()): x for x in ids}
        for r in t:
            orig = by_norm.get(" ".join(str(r["id"]).split()), str(r["id"]))
            found[orig] = {"main_id": str(r["main_id"]), "ra": float(r["ra"]), "dec": float(r["dec"]),
                           "otype": str(r["otype"])}
    elif archive == "ads":
        from .literature import _bibcode_query, ads_search
        for d in ads_search(_bibcode_query(ids), ["bibcode", "title", "author", "year"], rows=len(ids)):
            found[d["bibcode"]] = {"title": (d.get("title") or [""])[0],
                                   "first_author": (d.get("author") or [None])[0], "year": d.get("year")}
    else:
        raise ValueError(f"unknown archive {archive!r}; use gaia, mast, simbad or ads")
    return {"archive": archive, "found": found, "missing": [i for i in ids if i not in found]}


def check_papers(bibcodes: list[str], target: str | None = None, titles: list[str] | None = None) -> dict:
    """Are these papers real (ADS), do titles match, and (with ``target``) are they about it?"""
    from .literature import check_papers_exist, check_papers_linked

    set_socket_timeout()
    titles = titles or [None] * len(bibcodes)
    records = [{"kind": "paper", "archive": "ADS", "native_id": b, "title": t} for b, t in zip(bibcodes, titles)]
    result = {"query": {"target": target or ""}, "records": records}
    out = [r.to_dict() for r in check_papers_exist(result)]
    if target:
        out += [r.to_dict() for r in check_papers_linked(result)]
    return {"results": out, "n_failures": sum(r["status"] == FAIL for r in out)}


def check_positions(ra: float, dec: float, radius_arcmin: float | None = None, polygon=None,
                    positions=None, s_regions=None, tolerance_arcsec: float = 1.0) -> dict:
    """Which positions ([ra, dec] pairs) and footprints (s_region strings) fall in a cone or polygon?"""
    region = Region(ra, dec, None if radius_arcmin is None else radius_arcmin / 60, polygon)
    points = [{"ra": a, "dec": b, "inside": region.contains(a, b, tolerance_arcsec / 3600),
               "sep_arcmin": round(float(separation_deg(ra, dec, a, b)) * 60, 4)} for a, b in (positions or [])]
    shapes = []
    for s in s_regions or []:
        try:
            overlaps, contains = region.s_region_overlap(s)
            shapes.append({"s_region": s[:80], "overlaps": overlaps, "contains_center": contains})
        except ValueError as exc:
            shapes.append({"s_region": s[:80], "error": str(exc)})
    return {"region": region.describe(), "positions": points, "footprints": shapes,
            "n_outside": sum(not p["inside"] for p in points) + sum(not f.get("overlaps") for f in shapes)}


def check_links(urls: list[str]) -> dict:
    """Do these links work and point at what they claim? (See evaluation/links.py.)"""
    from .links import check_links as _check

    set_socket_timeout()
    records = [{"archive": "link", "native_id": u, "access_url": u} for u in urls]
    out = [r.to_dict() for r in _check(records)]
    return {"results": out, "n_failures": sum(r["status"] == FAIL for r in out),
            "n_warnings": sum(r["status"] == WARN for r in out)}


def check_gw(event: str, ra: float, dec: float, skymap_url: str | None = None) -> dict:
    """Where does (ra, dec) fall in a GW event's sky map? Defaults to the public GWTC-1 map."""
    from .gw import GWTC1_SKYMAP, credible_level, distance_at, fetch, read_skymap

    set_socket_timeout()
    url = skymap_url or GWTC1_SKYMAP.format(event=event)
    with tempfile.TemporaryDirectory() as tmp:
        sm = read_skymap(fetch(url, Path(tmp) / "skymap.fits.gz"))
    level = credible_level(sm, ra, dec)
    dist = distance_at(sm, ra, dec)
    obj = str(sm["header"].get("OBJECT", "")).strip()
    return {"event": event, "skymap_url": url, "skymap_object": obj or None,
            "object_matches_event": (obj == event) if obj else None,
            "credible_level": round(level, 4), "in_90": level <= 0.9,
            "distance_mpc": None if dist is None else {"mean": round(dist[0], 1), "std": round(dist[1], 1)}}
