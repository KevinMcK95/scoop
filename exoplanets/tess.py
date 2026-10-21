"""TESS coverage, TIC stars and their light curves, as plain functions.

- tess_sectors: which TESS sectors imaged a field (full-frame images), and when.
- tic_search: TESS Input Catalog stars in a field brighter than a magnitude limit.
- extractable_light_curves: for those stars, the light curves that already exist
  (SPOC 2-min/20-s; light curves made from the full-frame images by QLP, TESS-SPOC,
  TGLC, ...) and whether one could be extracted from the images.
- short_cadence_candidates: stars that score well for a future short-cadence request,
  by fixed, adjustable criteria (past coverage only: future pointings are not checked).

Sources: MAST (CAOM) for TESS observations and light curves; VizieR (IV/39/tic82,
TIC v8.2) for the TIC. HTTP goes through archive.http_get, so responses can be
recorded and replayed like the archive's.
"""

from __future__ import annotations

import json
from datetime import date, timedelta

from .archive import ArchiveError, http_get, now, separation_arcsec

MAST_URL = "https://mast.stsci.edu/api/v0/invoke"
VIZIER_TAP = "https://tapvizier.cds.unistra.fr/TAPVizieR/tap/sync"
TIC_TABLE = '"IV/39/tic82"'
MJD_EPOCH = date(1858, 11, 17)
MAX_TIC_ROWS = 5000
DEFAULT_TMAG_MAX = 16.0
TIC_REASON = ("Tmag < 16 is about the faintest stars with ready-made light curves from TESS's "
              "full-frame images (TGLC and GSFC-ELEANOR-LITE reach Tmag ~16; QLP and TESS-SPOC "
              "~13.5). Fainter stars are mostly too faint for a useful transit search with TESS's "
              "21-arcsec pixels. Raise the limit to see more stars, lower it for the best ones.")
# Light curves made from full-frame images, as MAST names their pipelines (HLSPs).
FFI_PIPELINES = ("QLP", "TESS-SPOC", "TGLC", "GSFC-ELEANOR-LITE", "TASOC", "CDIPS", "PATHOS",
                 "DIAMANTE", "T16", "TARS", "TEQUILA")
# Feasibility of extracting a light curve from the full-frame images.
GOOD_TMAG, POSSIBLE_TMAG = 13.5, 16.0    # QLP/TESS-SPOC reach; TGLC/ELEANOR-LITE reach
GOOD_RCONT, POSSIBLE_RCONT = 0.2, 1.0    # Rcont: flux from neighbours / the star's own flux
# Short-cadence defaults: small, bright, uncontaminated dwarfs (the TESS mission's own priorities).
SHORT_CADENCE_DEFAULTS = {"tmag_max": 13.0, "logg_min": 4.0, "radius_max_rsun": 1.5,
                          "contamination_max": 0.1}


def mjd_date(mjd: float | None) -> str | None:
    return (MJD_EPOCH + timedelta(days=mjd)).isoformat() if mjd else None


def ffi_cadence(sector: int) -> str:
    """Full-frame image cadence by mission year: 30 min (sectors 1-26), 10 min (27-55), 200 s."""
    return "30 min" if sector <= 26 else "10 min" if sector <= 55 else "200 s"


def mast_query(service: str, params: dict) -> list[dict]:
    request = {"service": service, "format": "json", "pagesize": 50000, "params": params}
    body = json.loads(http_get(MAST_URL, {"request": json.dumps(request, sort_keys=True)}, post=True))
    if body.get("status") != "COMPLETE":
        raise ArchiveError(f"MAST query did not complete ({body.get('msg') or body.get('status')}). "
                           "Report that MAST is unavailable.")
    return body["data"]


def vizier_query(adql: str) -> list[dict]:
    body = json.loads(http_get(VIZIER_TAP, {"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "json",
                                            "QUERY": adql}, post=True))
    names = [m["name"] for m in body["metadata"]]
    return [dict(zip(names, row)) for row in body["data"]]


# ------------------------------------------------------------------ sectors


def tess_sectors(ra: float, dec: float, radius_arcmin: float) -> dict:
    """The TESS sectors whose full-frame images cover any part of the cone."""
    rows = mast_query("Mast.Caom.Filtered.Position", {
        "columns": "obs_id,sequence_number,t_min,t_max,s_region",
        "position": f"{ra}, {dec}, {radius_arcmin / 60}",
        "filters": [{"paramName": "obs_collection", "values": ["TESS"]},
                    {"paramName": "dataproduct_type", "values": ["image"]}]})
    ccds = {}
    for r in rows:  # obs_id is tess-s0098-4-1: sector 98, camera 4, CCD 1
        _, s, camera, ccd = r["obs_id"].split("-")
        ccds[r["obs_id"]] = {"sector": int(s[1:]), "camera": int(camera), "ccd": int(ccd),
                             "start": mjd_date(r["t_min"]), "end": mjd_date(r["t_max"]),
                             "ffi_cadence": ffi_cadence(int(s[1:])), "s_region": r["s_region"]}
    ccds = sorted(ccds.values(), key=lambda c: (c["sector"], c["camera"], c["ccd"]))
    sectors = sorted({c["sector"] for c in ccds})
    return {"center": {"ra": ra, "dec": dec}, "radius_arcmin": radius_arcmin, "n_sectors": len(sectors),
            "sectors": sectors, "ccds": ccds, "archive": "MAST", "retrieved_at": now(),
            "note": "A sector counts if its full-frame images cover any part of the cone; stars near "
                    "a CCD edge or gap may be missing from some of them."}


# ------------------------------------------------------------------ TIC


def tic_search(ra: float, dec: float, radius_arcmin: float, tmag_max: float = DEFAULT_TMAG_MAX) -> dict:
    """TIC v8.2 stars within the cone brighter than `tmag_max`, brightest first."""
    adql = (f"SELECT TOP {MAX_TIC_ROWS + 1} TIC, RAJ2000, DEJ2000, Tmag, Teff, logg, Rad, Mass, Dist, Rcont, "
            f'LClass, GAIA, "S/G", Disp FROM {TIC_TABLE} WHERE Tmag < {float(tmag_max)!r} '
            f"AND 1=CONTAINS(POINT('ICRS', RAJ2000, DEJ2000), CIRCLE('ICRS', {ra!r}, {dec!r}, "
            f"{radius_arcmin / 60!r})) ORDER BY Tmag")
    rows = vizier_query(adql)
    truncated = len(rows) > MAX_TIC_ROWS
    stars = [{"tic_id": r["TIC"], "ra": r["RAJ2000"], "dec": r["DEJ2000"], "tmag": r["Tmag"],
              "teff_k": r["Teff"], "logg": r["logg"], "radius_rsun": r["Rad"], "mass_msun": r["Mass"],
              "distance_pc": r["Dist"], "contamination": r["Rcont"],
              "lum_class": (r["LClass"] or "").strip() or None, "gaia_dr2_id": r["GAIA"],
              "separation_arcsec": round(separation_arcsec(ra, dec, r["RAJ2000"], r["DEJ2000"]), 1)}
             for r in rows[:MAX_TIC_ROWS]
             if not r["Disp"] and (r["S/G"] or "").strip() in ("STAR", "")]  # skip duplicates, artefacts
    return {"center": {"ra": ra, "dec": dec}, "radius_arcmin": radius_arcmin, "tmag_max": tmag_max,
            "reason": TIC_REASON if tmag_max == DEFAULT_TMAG_MAX else None,
            "n_stars": len(stars), "truncated": truncated, "stars": stars, "archive": "TIC (VizieR IV/39/tic82)",
            "query": adql, "retrieved_at": now(),
            **({"note": f"More than {MAX_TIC_ROWS} stars: only the brightest {MAX_TIC_ROWS} are listed. "
                        "Lower the magnitude limit or the radius."} if truncated else {})}


# ------------------------------------------------------------------ light curves


def light_curves(tic_ids: list[int]) -> dict[int, dict]:
    """Existing TESS light curves per TIC: SPOC short cadence (2-min, 20-s) and light curves
    made from the full-frame images, by pipeline, with their sectors."""
    found: dict[int, dict] = {t: {"short_cadence": {}, "ffi": {}} for t in tic_ids}
    for i in range(0, len(tic_ids), 500):
        batch = [str(t) for t in tic_ids[i:i + 500]]
        rows = mast_query("Mast.Caom.Filtered", {
            "columns": "obs_collection,target_name,provenance_name,sequence_number,t_exptime",
            "filters": [{"paramName": "obs_collection", "values": ["TESS", "HLSP"]},
                        {"paramName": "dataproduct_type", "values": ["timeseries"]},
                        {"paramName": "target_name", "values": batch}]})
        for r in rows:
            try:
                entry = found[int(r["target_name"])]
            except (KeyError, ValueError, TypeError):
                continue
            sector = r["sequence_number"]
            if r["obs_collection"] == "TESS":  # SPOC, from pixels downloaded for this star
                kind = "20 s" if (r["t_exptime"] or 0) < 60 else "2 min"
                entry["short_cadence"].setdefault(kind, set()).add(sector)
            elif r["provenance_name"] in FFI_PIPELINES:
                entry["ffi"].setdefault(r["provenance_name"], set()).add(sector)
    return {t: {k: {name: sorted(s for s in sectors if s is not None) for name, sectors in v.items()}
                for k, v in e.items()} for t, e in found.items()}


def feasibility(star: dict, n_sectors: int) -> tuple[str, str]:
    """Could a light curve be extracted from the full-frame images? (rating, reason)"""
    tmag, rcont = star["tmag"], star["contamination"]
    if n_sectors == 0:
        return "not observed", "no TESS full-frame images cover this field"
    contamination = ("contamination unknown (no Rcont in the TIC)" if rcont is None
                     else f"contamination {rcont:.2f} of the star's flux")
    if tmag <= GOOD_TMAG and (rcont is None or rcont < GOOD_RCONT):
        return "good", f"Tmag {tmag:.1f} (within QLP/TESS-SPOC reach), {contamination}"
    if tmag <= POSSIBLE_TMAG and (rcont is None or rcont < POSSIBLE_RCONT):
        return "possible", (f"Tmag {tmag:.1f}" + (" (fainter than QLP/TESS-SPOC; TGLC/ELEANOR-LITE reach)"
                                                  if tmag > GOOD_TMAG else "") + f", {contamination}")
    return "poor", (f"Tmag {tmag:.1f} too faint" if tmag > POSSIBLE_TMAG
                    else f"neighbours contribute {rcont:.2f} of the star's flux")


def extractable_light_curves(ra: float, dec: float, radius_arcmin: float,
                             tmag_max: float = DEFAULT_TMAG_MAX, max_stars: int = 50) -> dict:
    """For the field's TIC stars: light curves that exist, and whether one could be extracted
    from the full-frame images (good / possible / poor / not observed), with reasons."""
    sectors = tess_sectors(ra, dec, radius_arcmin)
    tic = tic_search(ra, dec, radius_arcmin, tmag_max)
    stars = tic["stars"][:max_stars]
    curves = light_curves([s["tic_id"] for s in stars])
    out, counts = [], {}
    for s in stars:
        rating, reason = feasibility(s, sectors["n_sectors"])
        existing = curves[s["tic_id"]]
        counts[rating] = counts.get(rating, 0) + 1
        out.append({"tic_id": s["tic_id"], "tmag": s["tmag"], "contamination": s["contamination"],
                    "existing_short_cadence": existing["short_cadence"], "existing_ffi_light_curves": existing["ffi"],
                    "has_light_curve": bool(existing["short_cadence"] or existing["ffi"]),
                    "extraction": rating, "reason": reason})
    return {"center": {"ra": ra, "dec": dec}, "radius_arcmin": radius_arcmin, "tmag_max": tmag_max,
            "n_sectors": sectors["n_sectors"], "sectors": sectors["sectors"], "n_stars_in_field": tic["n_stars"],
            "n_listed": len(out), "n_with_light_curve": sum(s["has_light_curve"] for s in out),
            "extraction_counts": counts, "stars": out, "archive": "MAST", "retrieved_at": now(),
            "rules": (f"good: Tmag <= {GOOD_TMAG} and contamination < {GOOD_RCONT}; possible: Tmag <= "
                      f"{POSSIBLE_TMAG} and contamination < {POSSIBLE_RCONT}; poor: otherwise. Sector "
                      "coverage is the field's, not checked star by star."),
            **({"note": f"Only the brightest {max_stars} of {tic['n_stars']} stars are listed."}
               if tic["n_stars"] > max_stars else {})}


# ------------------------------------------------------------------ short cadence


def short_cadence_candidates(ra: float, dec: float, radius_arcmin: float, tmag_max: float = 13.0,
                             logg_min: float = 4.0, radius_max_rsun: float = 1.5,
                             contamination_max: float = 0.1, exclude_existing_short_cadence: bool = True,
                             max_results: int = 10) -> dict:
    """Rank the field's stars for a future short-cadence request by fixed criteria.

    Kept: dwarfs (log g >= logg_min; or no log g and radius <= radius_max_rsun), radius <=
    radius_max_rsun, contamination <= contamination_max, in a field TESS has imaged, and
    (by default) with no SPOC 2-min/20-s light curve yet. Score = brightness (tmag_max - Tmag)
    + small star (1 / radius in R_sun) + cleanliness (1 - contamination / contamination_max)
    + 2 for a TOI host + 1 for a confirmed planet host. Future TESS pointings are not checked.
    """
    criteria = {"tmag_max": tmag_max, "logg_min": logg_min, "radius_max_rsun": radius_max_rsun,
                "contamination_max": contamination_max,
                "exclude_existing_short_cadence": exclude_existing_short_cadence}
    sectors = tess_sectors(ra, dec, radius_arcmin)
    tic = tic_search(ra, dec, radius_arcmin, tmag_max)
    rejected: dict[str, int] = {}

    def reject(why: str) -> None:
        rejected[why] = rejected.get(why, 0) + 1

    kept = []
    for s in tic["stars"]:
        dwarf = (s["logg"] >= logg_min) if s["logg"] is not None else (
            s["radius_rsun"] is not None and s["radius_rsun"] <= radius_max_rsun)
        if not dwarf:
            reject(f"not a dwarf (log g < {logg_min}, or no log g and no small radius)")
        elif s["radius_rsun"] is None or s["radius_rsun"] > radius_max_rsun:
            reject(f"radius unknown or > {radius_max_rsun} R_sun")
        elif s["contamination"] is None or s["contamination"] > contamination_max:
            reject(f"contamination unknown or > {contamination_max}")
        else:
            kept.append(s)
    if sectors["n_sectors"] == 0:
        rejected["field never imaged by TESS"] = len(kept)
        kept = []
    curves = light_curves([s["tic_id"] for s in kept]) if kept else {}

    notes = ["Future TESS pointings were not checked: a short-cadence request only helps if TESS "
             "observes the star again."]
    toi_hosts, planet_hosts = set(), set()
    try:
        from .archive import toi_cone_search
        toi_hosts = {t["tic_id"] for t in toi_cone_search(ra, dec, radius_arcmin)["tois"]}
        planet_hosts = {int(r["tic_id"].split()[-1]) for r in _hosts_with_tic(ra, dec, radius_arcmin)}
    except ArchiveError:
        notes.append("TOI and planet-host bonuses not applied: the NASA Exoplanet Archive is unavailable.")

    ranked = []
    for s in kept:
        existing = curves.get(s["tic_id"], {"short_cadence": {}, "ffi": {}})
        if exclude_existing_short_cadence and existing["short_cadence"]:
            reject("already has SPOC 2-min/20-s data")
            continue
        parts = {"brightness": round(tmag_max - s["tmag"], 2), "small star": round(1 / s["radius_rsun"], 2),
                 "low contamination": round(1 - s["contamination"] / contamination_max, 2)}
        reasons = [f"Tmag {s['tmag']:.2f}", f"radius {s['radius_rsun']:.2f} R_sun",
                   f"contamination {s['contamination']:.3f}"]
        if s["tic_id"] in toi_hosts:
            parts["TOI host"] = 2
            reasons.append("hosts a TESS Object of Interest")
        if s["tic_id"] in planet_hosts:
            parts["confirmed planet host"] = 1
            reasons.append("hosts a confirmed planet")
        reasons.append("FFI light curves exist (" + ", ".join(existing["ffi"]) + ")" if existing["ffi"]
                       else "no light curve made from the full-frame images yet")
        ranked.append({"tic_id": s["tic_id"], "ra": s["ra"], "dec": s["dec"], "tmag": s["tmag"],
                       "teff_k": s["teff_k"], "radius_rsun": s["radius_rsun"], "logg": s["logg"],
                       "contamination": s["contamination"], "distance_pc": s["distance_pc"],
                       "score": round(sum(parts.values()), 2), "score_parts": parts, "reasons": reasons,
                       "existing_short_cadence": existing["short_cadence"],
                       "existing_ffi_light_curves": existing["ffi"]})
    ranked.sort(key=lambda c: -c["score"])
    return {"center": {"ra": ra, "dec": dec}, "radius_arcmin": radius_arcmin, "criteria": criteria,
            "defaults": SHORT_CADENCE_DEFAULTS, "n_sectors": sectors["n_sectors"], "sectors": sectors["sectors"],
            "n_stars_considered": tic["n_stars"], "n_candidates": len(ranked), "rejected": rejected,
            "candidates": ranked[:max_results], "notes": notes, "archive": "SCOOP short-cadence criteria",
            "scoring": "brightness (tmag_max - Tmag) + small star (1 / radius) + low contamination "
                       "(1 - contamination / contamination_max) + 2 for a TOI host + 1 for a confirmed host",
            "retrieved_at": now()}


def _hosts_with_tic(ra: float, dec: float, radius_arcmin: float) -> list[dict]:
    """Confirmed planet hosts in the cone with their TIC IDs ("TIC 150428135")."""
    from .archive import tap_query

    return [r for r in tap_query(
        "SELECT DISTINCT hostname, tic_id FROM pscomppars WHERE tic_id IS NOT NULL AND "
        f"CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra!r}, {dec!r}, {radius_arcmin / 60!r})) = 1")
        if r.get("tic_id")]
