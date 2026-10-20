"""Checks on a SCOOP result: is it real, in the requested field, complete and correct?

Each check returns a list of ``CheckResult``. The checks go back to the archives
themselves (Sesame, Gaia, MAST) rather than trusting anything in the result
beyond the IDs, and never read the ``_injected`` fields used to score them.
"""
from __future__ import annotations

import math
import random
import re
import warnings
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy import log
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning

from .geometry import Region, separation_deg

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"
INFO = "info"  # notes such as what was sampled; not counted as pass or fail

# Checks that query an archive or a website per record; on big results they run on a sample.
PER_RECORD_REMOTE = {"id_recheck", "file", "file_matches_record", "file_content", "gw_localization",
                     "paper_exists", "paper_about_target", "arxiv", "links"}

# Missing required fields fail; missing recommended fields only warn, since
# archives themselves leave some empty (e.g. MAST often has no dataURL, and
# planned observations have no date yet).
REQUIRED_FIELDS = {
    "all": ["kind", "archive", "native_id", "query", "retrieved_at"],
    "target": ["ra", "dec"],
    "catalog_row": ["ra", "dec", "access_url"],
    "observation": ["ra", "dec", "s_region", "instrument_name"],
    "file": ["path", "parent", "product_type", "access_url"],
    "paper": ["title", "access_url"],
    "localization": ["access_url", "credible_level"],
}
RECOMMENDED_FIELDS = {
    "observation": ["t_min", "access_url"],
}

# First and last possible observation dates (None = still operating).
MISSION_DATES = {
    "HST": ("1990-04-24", None),
    "JWST": ("2021-12-25", None),
    "GALEX": ("2003-04-28", "2013-06-28"),
    "IUE": ("1978-01-26", "1996-09-30"),
    "SWIFT": ("2004-11-20", None),
    "TESS": ("2018-04-18", None),
}

INSTRUMENTS = {
    "HST": {"WFPC", "WFPC2", "FOC", "FOS", "GHRS", "HSP", "NICMOS", "STIS", "ACS", "WFC3", "COS", "FGS"},
    "JWST": {"NIRCAM", "NIRSPEC", "MIRI", "NIRISS", "FGS"},
}

# Approximate wavelength coverage in Angstrom, with generous margins.
SPECTRAL_RANGES = {
    ("STIS", "CCD"): (1600, 10500),
    ("STIS", "NUV-MAMA"): (1550, 3250),
    ("STIS", "FUV-MAMA"): (1100, 1800),
    ("COS", "FUV"): (800, 2250),
    ("COS", "NUV"): (1600, 3250),
}

# Gaia: the Heidelberg ARI mirror is faster and steadier than ESA; ESA is the fallback.
# Heidelberg silently caps jobs at 100,000 rows unless maxrec is passed (skills/surveys/gaia.md).
HEIDELBERG_TAP = "https://gaia.ari.uni-heidelberg.de/tap"

# Row counts that usually mean a service's default row limit was hit.
ROW_LIMITS = {50, 100, 500, 1000, 2000, 3000, 5000, 10000, 50000, 100000}


@dataclass
class CheckResult:
    check: str
    status: str
    record: str
    detail: str = ""

    def to_dict(self):
        return asdict(self)


def record_key(rec) -> str:
    return f"{rec.get('archive', '?')}:{rec.get('native_id', '?')}"


def instrument_base(name) -> str:
    return re.split(r"[/-]", str(name))[0].upper()


def _mjd(iso):
    return Time(iso).mjd


def _tol(result, key, default):
    """A tolerance from the result (e.g. when the source rounds its values), else the default."""
    return (result.get("tolerances") or {}).get(key, default)


def _chunks(seq, n):
    seq = list(seq)
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


def gaia_query(adql: str):
    """Run ADQL on the Heidelberg Gaia mirror, falling back to ESA. Returns (table, service name)."""
    from astroquery.utils.tap.core import TapPlus

    try:
        job = TapPlus(url=HEIDELBERG_TAP, verbose=False).launch_job_async(adql, maxrec=10_000_000, verbose=False)
        return job.get_results(), "Heidelberg ARI mirror"
    except Exception as exc:
        from astroquery.gaia import Gaia
        return Gaia.launch_job_async(adql).get_results(), f"ESA archive (Heidelberg failed: {type(exc).__name__})"


def _number(value):
    """Float of a table value, or None if masked / NaN."""
    if value is None or np.ma.is_masked(value):
        return None
    value = float(value)
    return value if np.isfinite(value) else None


# ---------------------------------------------------------------- provenance

def check_provenance(records):
    out = []
    table_level = Counter()
    for rec in records:
        if rec.get("provenance_level") == "table":
            table_level[(rec.get("archive"), rec.get("table"), rec.get("table_provenance"))] += 1
            continue
        kind = rec.get("kind")
        need = REQUIRED_FIELDS["all"] + REQUIRED_FIELDS.get(kind, [])
        missing = [k for k in need if rec.get(k) in (None, "")]
        if missing:
            out.append(CheckResult("provenance", FAIL, record_key(rec), f"missing {', '.join(missing)}"))
            continue
        absent = [k for k in RECOMMENDED_FIELDS.get(kind, []) if rec.get(k) in (None, "")]
        detail = f"no {', '.join(absent)}" if absent else ""
        if rec.get("calib_level") == -1:
            detail = "planned observation (calib_level -1), no data yet" + (f"; {detail}" if detail else "")
        out.append(CheckResult("provenance", WARN if absent else PASS, record_key(rec), detail))
    for (archive, table, label), n in table_level.items():
        out.append(CheckResult("provenance", WARN, f"{archive} (table {table!r})",
                               f"{n} records carry only table-level provenance ({label!r}): "
                               "no per-row query, access URL or retrieval time"))
    dupes = [k for k, n in Counter(record_key(r) for r in records).items() if n > 1]
    for key in dupes:
        out.append(CheckResult("provenance", WARN, key, "record appears more than once"))
    return out


def check_archive_status(result):
    out = []
    for s in result.get("archive_status", []):
        if s.get("status") == "failed":
            out.append(CheckResult("archive_status", WARN, s["archive"],
                                   f"query failed and was reported: {s.get('error', '')[:200]}"))
        else:
            out.append(CheckResult("archive_status", PASS, s["archive"], f"{s.get('status')} ({s.get('n')} records)"))
    if not result.get("archive_status"):
        out.append(CheckResult("archive_status", FAIL, "result", "no per-archive status reported"))
    return out


# ---------------------------------------------------------------- position

def check_target(result, tol_arcsec=2.0):
    q = result["query"]
    if q.get("ra") is None or not q.get("target"):
        return []
    tol_arcsec = _tol(result, "target_arcsec", tol_arcsec)
    try:
        c = SkyCoord.from_name(q["target"])
    except Exception as exc:
        return [CheckResult("target", WARN, f"Sesame:{q['target']}", f"could not resolve independently: {exc}")]
    sep = separation_deg(q["ra"], q["dec"], c.ra.deg, c.dec.deg) * 3600
    status = PASS if sep <= tol_arcsec else FAIL
    return [CheckResult("target", status, f"Sesame:{q['target']}",
                        f"query position is {sep:.2f} arcsec from Sesame's position (tolerance {tol_arcsec:.0f})")]


def check_in_field(result, tol_arcsec=1.0):
    if result["query"].get("ra") is None:
        return []
    region = Region.from_query(result["query"])
    out = []
    for rec in result["records"]:
        kind, key = rec.get("kind"), record_key(rec)
        if kind == "catalog_row":
            if rec.get("ra") is None or rec.get("dec") is None:
                out.append(CheckResult("in_field", WARN, key, "no position"))
                continue
            ok = region.contains(rec["ra"], rec["dec"], tol_arcsec / 3600)
            sep = separation_deg(region.ra, region.dec, rec["ra"], rec["dec"]) * 60
            out.append(CheckResult("in_field", PASS if ok else FAIL, key,
                                   f"{sep:.3f} arcmin from center, {'inside' if ok else 'outside'} "
                                   f"the {region.describe()}"))
        elif kind == "observation":
            if not rec.get("s_region"):
                if rec.get("ra") is None:
                    out.append(CheckResult("in_field", WARN, key, "no footprint and no position"))
                    continue
                sep = separation_deg(region.ra, region.dec, rec["ra"], rec["dec"]) * 60
                out.append(CheckResult("in_field", WARN, key,
                                       f"no footprint; pointing is {sep:.3f} arcmin from center"))
                continue
            try:
                overlaps, contains = region.s_region_overlap(rec["s_region"])
            except ValueError as exc:
                out.append(CheckResult("in_field", FAIL, key, str(exc)))
                continue
            detail = "footprint contains the target" if contains else (
                "footprint overlaps the region" if overlaps else "footprint does not overlap the region")
            out.append(CheckResult("in_field", PASS if overlaps else FAIL, key, detail))
    return out


# ---------------------------------------------------------------- re-check IDs

def check_gaia_ids(records, pos_tol_mas=1.0, mag_tol=0.001):
    rows = [r for r in records if r.get("kind") == "catalog_row" and r.get("archive") == "Gaia DR3"]
    if not rows:
        return []
    truth, services = {}, set()
    for chunk in _chunks(sorted({int(r["native_id"]) for r in rows}), 500):
        adql = ("SELECT source_id, ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source "
                f"WHERE source_id IN ({', '.join(map(str, chunk))})")
        table, service = gaia_query(adql)
        services.add(service)
        truth.update({str(t["source_id"]): t for t in table})
    out = []
    for rec in rows:
        t = truth.get(rec["native_id"])
        if t is None:
            out.append(CheckResult("id_recheck", FAIL, record_key(rec),
                                   f"source_id not found in Gaia DR3 ({', '.join(sorted(services))})"))
            continue
        problems = []
        sep_mas = separation_deg(rec["ra"], rec["dec"], float(t["ra"]), float(t["dec"])) * 3.6e6
        if sep_mas > pos_tol_mas:
            problems.append(f"position differs from Gaia by {sep_mas / 1000:.3f} arcsec")
        g_true = None if np.ma.is_masked(t["phot_g_mean_mag"]) else float(t["phot_g_mean_mag"])
        g = rec.get("phot_g_mean_mag")
        if (g is None) != (g_true is None) or (g is not None and abs(g - g_true) > mag_tol):
            problems.append(f"G = {g} but Gaia has {g_true}")
        out.append(CheckResult("id_recheck", FAIL if problems else PASS, record_key(rec), "; ".join(problems)))
    return out


def check_mast_ids(records, pos_tol_arcsec=1.0, time_tol_days=1e-5):
    from astroquery.mast import Observations

    rows = [r for r in records if r.get("kind") == "observation" and r.get("archive") == "MAST"]
    if not rows:
        return []
    truth, by_id = {}, {}
    for chunk in _chunks(sorted({r["native_id"] for r in rows}), 200):
        for t in Observations.query_criteria(obs_id=chunk):
            truth[(str(t["obs_collection"]), str(t["obs_id"]))] = t
            by_id[str(t["obs_id"])] = t
    out = []
    for rec in rows:
        t = truth.get((rec.get("obs_collection"), rec["native_id"]))
        if t is None:
            other = by_id.get(rec["native_id"])
            detail = (f"obs_id exists, but in {other['obs_collection']}, not {rec.get('obs_collection')}"
                      if other is not None else "obs_id not found in MAST")
            out.append(CheckResult("id_recheck", FAIL, record_key(rec), detail))
            continue
        problems = []
        for field in ("instrument_name", "dataproduct_type"):
            if rec.get(field) is not None and str(t[field]) != str(rec[field]):
                problems.append(f"{field} = {rec.get(field)!r} but MAST has {str(t[field])!r}")
        s_ra, s_dec = _number(t["s_ra"]), _number(t["s_dec"])
        if rec.get("ra") is not None and s_ra is not None:
            sep = separation_deg(rec["ra"], rec["dec"], s_ra, s_dec) * 3600
            if sep > pos_tol_arcsec:
                problems.append(f"pointing differs from MAST by {sep:.1f} arcsec")
        for field in ("t_min", "t_max"):
            true = _number(t[field])
            if rec.get(field) is not None and true is not None and abs(rec[field] - true) > time_tol_days:
                problems.append(f"{field} = {rec[field]:.5f} but MAST has {true:.5f}")
        if rec.get("s_region") and " ".join(str(rec["s_region"]).split()) != " ".join(str(t["s_region"]).split()):
            problems.append("s_region differs from MAST")
        out.append(CheckResult("id_recheck", FAIL if problems else PASS, record_key(rec), "; ".join(problems)))
    return out


def check_simbad_ids(records, pos_tol_arcsec=1.0):
    """Look up SIMBAD identifiers (any alias, not just main identifiers) and compare positions."""
    from astroquery.simbad import Simbad

    rows = [r for r in records if r.get("kind") == "catalog_row" and r.get("archive") == "SIMBAD"]
    if not rows:
        return []
    truth = {}
    for chunk in _chunks(sorted({r["native_id"] for r in rows}), 200):
        quoted = ", ".join("'" + i.replace("'", "''") + "'" for i in chunk)
        table = Simbad.query_tap("SELECT ident.id, basic.main_id, basic.ra, basic.dec "
                                 f"FROM ident JOIN basic ON ident.oidref = basic.oid WHERE ident.id IN ({quoted})")
        truth.update({" ".join(str(t["id"]).split()): t for t in table})
    out = []
    for rec in rows:
        t = truth.get(" ".join(rec["native_id"].split()))
        if t is None:
            out.append(CheckResult("id_recheck", FAIL, record_key(rec), "identifier not found in SIMBAD"))
            continue
        ra, dec = _number(t["ra"]), _number(t["dec"])
        if ra is None or rec.get("ra") is None:
            out.append(CheckResult("id_recheck", PASS, record_key(rec), "found (no position to compare)"))
            continue
        sep = separation_deg(rec["ra"], rec["dec"], ra, dec) * 3600
        status = PASS if sep <= pos_tol_arcsec else FAIL
        detail = "" if status == PASS else f"position differs from SIMBAD ({t['main_id']}) by {sep:.1f} arcsec"
        out.append(CheckResult("id_recheck", status, record_key(rec), detail))
    return out


# ---------------------------------------------------------------- sanity rules

def check_sanity(records):
    today = Time.now().mjd
    out = []
    for rec in records:
        if rec.get("kind") != "observation":
            continue
        problems = []
        coll = rec.get("obs_collection")
        t_min, t_max, t_exp = rec.get("t_min"), rec.get("t_max"), rec.get("t_exptime")
        if coll in MISSION_DATES and t_min is not None:
            start, end = MISSION_DATES[coll]
            if t_min < _mjd(start):
                problems.append(f"observed {Time(t_min, format='mjd').iso[:10]}, before {coll} launched ({start})")
            if end and t_min > _mjd(end) + 1:
                problems.append(f"observed {Time(t_min, format='mjd').iso[:10]}, after {coll} ended ({end})")
        if t_min is not None and t_max is not None and t_min > t_max:
            problems.append("t_min is after t_max")
        if t_min is not None and t_min > today:
            problems.append("observation date is in the future")
        if t_exp is not None and t_exp < 0:
            problems.append(f"negative exposure time {t_exp}")
        if coll in INSTRUMENTS and instrument_base(rec.get("instrument_name")) not in INSTRUMENTS[coll]:
            problems.append(f"{rec.get('instrument_name')!r} is not a {coll} instrument")
        out.append(CheckResult("sanity", FAIL if problems else PASS, record_key(rec), "; ".join(problems)))
    return out


# ---------------------------------------------------------------- completeness

def check_gaia_completeness(result, edge_tol_arcsec=1.0):
    if result["query"].get("ra") is None:
        return []
    region = Region.from_query(result["query"])
    # Query the enclosing circle and cut to the region here: the archive can't run
    # CONTAINS on polygons with many vertices (e.g. a 181-vertex ellipse).
    circle = f"CIRCLE('ICRS', {region.ra!r}, {region.dec!r}, {region.radius_deg!r})"
    adql = f"SELECT source_id, ra, dec FROM gaiadr3.gaia_source WHERE 1 = CONTAINS(POINT('ICRS', ra, dec), {circle})"
    table, service = gaia_query(adql)
    ra, dec = np.asarray(table["ra"], float), np.asarray(table["dec"], float)
    inside = region.contains_many(ra, dec)
    edge = region.contains_many(ra, dec, tol_deg=edge_tol_arcsec / 3600) & ~inside
    truth = {str(s) for s in table["source_id"][inside]}
    near_edge = {str(s) for s in table["source_id"][edge]}
    got = {r["native_id"] for r in result["records"]
           if r.get("kind") == "catalog_row" and r.get("archive") == "Gaia DR3"}
    missing = sorted(truth - got)
    found = len(truth & got)
    detail = f"{found} of {len(truth)} Gaia DR3 sources in the region are in the result ({service})"
    if len(got) in ROW_LIMITS and missing:
        detail += f"; {len(got)} rows returned looks like a row limit was hit"
    out = [CheckResult("completeness", FAIL if missing else PASS, "Gaia DR3", detail)]
    out += [CheckResult("completeness", FAIL, f"Gaia DR3:{sid}", "in Gaia DR3 inside the region but missing from the result")
            for sid in missing]
    extra = sorted(got & near_edge)
    if extra:
        out.append(CheckResult("completeness", WARN, "Gaia DR3",
                               f"{len(extra)} sources in the result lie within {edge_tol_arcsec:g}\" outside the region's "
                               "edge (the source and this check trace the edge slightly differently)"))
    return out


def check_mast_completeness(result):
    """Compare with MAST queried independently.

    If the result states the selection it applied (``result["selection"]["MAST"]``),
    every MAST observation matching that selection and overlapping the region must
    be present, and each missing one fails. Otherwise only per-collection counts are
    compared, as warnings, since an agent may sample on purpose.
    """
    from astroquery.mast import Observations

    if result["query"].get("ra") is None:
        return []
    region = Region.from_query(result["query"])
    coord = SkyCoord(region.ra, region.dec, unit="deg")
    got = [r for r in result["records"] if r.get("kind") == "observation" and r.get("archive") == "MAST"]
    sel = (result.get("selection") or {}).get("MAST")
    if sel:
        table = Observations.query_criteria(coordinates=coord, radius=region.radius_deg * u.deg,
                                            obs_collection=sel["obs_collection"],
                                            dataproduct_type=sel["dataproduct_type"])
    else:
        table = Observations.query_region(coord, radius=region.radius_deg * u.deg)
    keep = np.asarray(table["intentType"], str) == "science"
    if sel:
        keep &= ~np.isin(np.asarray(table["project"], str), sel.get("exclude_project", []))
        keep &= np.ma.filled(table["calib_level"], -9) >= sel.get("min_calib_level", -9)
    table = table[keep]
    if region.polygon:
        def overlaps(s_region):
            try:
                return region.s_region_overlap(s_region)[0]
            except ValueError:
                return False
        table = table[[overlaps(str(s)) for s in table["s_region"]]] if len(table) else table

    if not sel:
        truth = Counter(str(c) for c in table["obs_collection"])
        n_got = Counter(r.get("obs_collection") for r in got)
        out = [CheckResult("completeness", WARN if len(got) < len(table) else PASS, "MAST",
                           f"{len(got)} of {len(table)} MAST science observations in the region are in the result")]
        for coll, n in truth.most_common():
            k = n_got.get(coll, 0)
            if k == 0:
                out.append(CheckResult("completeness", WARN, f"MAST/{coll}", f"no records, but MAST has {n} observations"))
            elif k < n:
                out.append(CheckResult("completeness", WARN, f"MAST/{coll}", f"{k} of {n} observations in the result"))
        return out

    truth = {(str(t["obs_collection"]), str(t["obs_id"])) for t in table}
    have = {(r.get("obs_collection"), r["native_id"]) for r in got}
    missing = sorted(truth - have)
    rules = (f"{'/'.join(sel['obs_collection'])} {'/'.join(sel['dataproduct_type'])}, science"
             + (f", not {'/'.join(sel['exclude_project'])}" if sel.get("exclude_project") else "")
             + (f", calib_level >= {sel['min_calib_level']}" if "min_calib_level" in sel else ""))
    out = [CheckResult("completeness", FAIL if missing else PASS, "MAST",
                       f"{len(truth & have)} of {len(truth)} MAST observations matching the stated selection "
                       f"({rules}) and overlapping the region are in the result")]
    out += [CheckResult("completeness", FAIL, f"MAST:{obs_id}",
                        f"{coll} observation matches the stated selection and overlaps the region, but is missing")
            for coll, obs_id in missing]
    return out


# ---------------------------------------------------------------- files

def _header_value(hdul, key):
    for hdu in hdul:
        if key in hdu.header:
            return hdu.header[key]
    return None


def _check_image(hdul, region):
    level = log.level
    log.setLevel("WARNING")  # silence astropy's SIP-without-"-SIP" notice on drizzled images
    try:
        return _check_image_wcs(hdul, region)
    finally:
        log.setLevel(level)


def _check_image_wcs(hdul, region):
    ra0, dec0 = region.ra, region.dec
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        for hdu in hdul:
            if hdu.data is None or getattr(hdu.data, "ndim", 0) != 2:
                continue
            wcs = WCS(hdu.header, hdul)
            if not wcs.has_celestial:
                continue
            corners = wcs.celestial.calc_footprint(axes=hdu.data.shape[::-1])
            overlaps, contains = region.polygon_overlap(corners)
            if not overlaps:
                return FAIL, "image footprint (from its WCS) does not overlap the region"
            x, y = wcs.celestial.world_to_pixel_values(ra0, dec0)
            ny, nx = hdu.data.shape
            if contains and 0 <= x < nx and 0 <= y < ny:
                value = hdu.data[int(y), int(x)]
                if not np.isfinite(value):
                    return WARN, "target is in the image but its pixel is not finite"
                return PASS, f"target at pixel ({x:.0f}, {y:.0f}) of {nx}x{ny}"
            return PASS, "image overlaps the region (target itself is outside the image)"
    return WARN, "no 2D image with a celestial WCS found"


def _check_spectrum(hdul, region):
    problems = []
    ra_t, dec_t = _header_value(hdul, "RA_TARG"), _header_value(hdul, "DEC_TARG")
    if ra_t is not None and dec_t is not None and not region.contains(ra_t, dec_t):
        sep = separation_deg(region.ra, region.dec, ra_t, dec_t) * 60
        problems.append(f"spectrum target is {sep:.2f} arcmin from center, outside the region")
    table = next((h for h in hdul if isinstance(h, fits.BinTableHDU) and "WAVELENGTH" in h.columns.names), None)
    if table is None:
        return WARN, "no WAVELENGTH column found"
    wave = np.asarray(table.data["WAVELENGTH"], float)
    flux = np.asarray(table.data["FLUX"], float) if "FLUX" in table.columns.names else None
    w_lo, w_hi = np.nanmin(wave), np.nanmax(wave)
    key = (str(_header_value(hdul, "INSTRUME")).upper(), str(_header_value(hdul, "DETECTOR")).upper())
    if key in SPECTRAL_RANGES:
        lo, hi = SPECTRAL_RANGES[key]
        if w_lo < lo or w_hi > hi:
            problems.append(f"wavelengths {w_lo:.0f}-{w_hi:.0f} A outside {key[0]}/{key[1]} range {lo}-{hi} A")
    if flux is not None and not np.any(np.isfinite(flux) & (flux != 0)):
        problems.append("flux is all zero or non-finite")
    detail = "; ".join(problems) or f"{w_lo:.0f}-{w_hi:.0f} A"
    return (FAIL if problems else PASS), detail


def check_files(result, base_dir: Path):
    if not any(r.get("kind") == "file" for r in result["records"]):
        return []
    region = Region.from_query(result["query"])
    parents = {record_key(r_): r_ for r_ in result["records"] if r_.get("kind") == "observation"}
    out = []
    for rec in result["records"]:
        if rec.get("kind") != "file":
            continue
        key = record_key(rec)
        path = base_dir / rec.get("path", "")
        if not path.is_file():
            out.append(CheckResult("file", FAIL, key, f"file not found: {path}"))
            continue
        size = path.stat().st_size
        if size == 0:
            out.append(CheckResult("file", FAIL, key, "file is empty"))
            continue
        if rec.get("expected_size") and size != rec["expected_size"]:
            out.append(CheckResult("file", FAIL, key, f"size {size} bytes, archive lists {rec['expected_size']}"))
        with open(path, "rb") as fh:
            head = fh.read(30)
        if not head.startswith(b"SIMPLE  ="):
            out.append(CheckResult("file", FAIL, key, f"not a FITS file (starts with {head[:20]!r})"))
            continue
        try:
            hdul = fits.open(path)
        except Exception as exc:
            out.append(CheckResult("file", FAIL, key, f"FITS file does not open: {exc}"))
            continue
        with hdul:
            try:
                hdul.verify("exception")
                out.append(CheckResult("file", PASS, key, "valid FITS"))
            except Exception as exc:
                out.append(CheckResult("file", WARN, key, f"FITS opens but does not fully verify: {str(exc)[:150]}"))

            parent = parents.get(rec.get("parent"))
            if parent is None:
                out.append(CheckResult("file_matches_record", FAIL, key, f"parent {rec.get('parent')} is not in the result"))
            else:
                problems = []
                telescop = str(_header_value(hdul, "TELESCOP") or "").upper()
                if parent.get("obs_collection") in ("HST", "JWST") and telescop != parent["obs_collection"]:
                    problems.append(f"TELESCOP = {telescop!r} but the record says {parent['obs_collection']}")
                instrume = str(_header_value(hdul, "INSTRUME") or "").upper()
                expected = set(re.split(r"[/-]", str(parent.get("instrument_name")).upper()))
                if instrume and instrume not in expected:
                    problems.append(f"INSTRUME = {instrume!r} but the record says {parent.get('instrument_name')}")
                expstart = _header_value(hdul, "EXPSTART")
                if expstart is not None and parent.get("t_min") is not None:
                    t_lo, t_hi = parent["t_min"], parent.get("t_max") or parent["t_min"]
                    if not (t_lo - 1 <= float(expstart) <= t_hi + 1):
                        problems.append(f"EXPSTART {float(expstart):.3f} is outside the record's {t_lo:.3f}-{t_hi:.3f}")
                out.append(CheckResult("file_matches_record", FAIL if problems else PASS, key, "; ".join(problems)))

            if rec.get("product_type") == "image":
                status, detail = _check_image(hdul, region)
            elif rec.get("product_type") == "spectrum":
                status, detail = _check_spectrum(hdul, region)
            else:
                status, detail = SKIP, f"no content check for product type {rec.get('product_type')!r}"
            out.append(CheckResult("file_content", status, key, detail))
    return out


# ---------------------------------------------------------------- sampling

def sample_size(n: int, sample) -> int:
    """How many of n to check: all if sample is None/0, at most ``sample`` if >= 1, else that fraction."""
    if not sample:
        return n
    return min(n, int(sample)) if sample >= 1 else max(1, math.ceil(sample * n))


def sample_records(records, sample, seed=0):
    """A random subset of each (kind, archive) group, and which groups were cut down."""
    groups = defaultdict(list)
    for i, rec in enumerate(records):
        groups[(rec.get("kind"), rec.get("archive"))].append(i)
    rng = random.Random(seed)
    keep, cut = set(), {}
    for group, idx in groups.items():
        k = sample_size(len(idx), sample)
        chosen = idx if k >= len(idx) else sorted(rng.sample(idx, k))
        keep.update(chosen)
        if k < len(idx):
            cut[group] = {"checked": {record_key(records[i]) for i in chosen}, "total": len(idx)}
    return [r for i, r in enumerate(records) if i in keep], cut


def upper_limit(k: int, n: int, cl: float = 0.95) -> float:
    """One-sided upper limit on a failure rate after k failures in n random checks (Clopper-Pearson)."""
    if n == 0 or k >= n:
        return 1.0
    try:
        from scipy.stats import beta
        return float(beta.ppf(cl, k + 1, n - k))
    except ImportError:
        if k == 0:
            return 1 - (1 - cl) ** (1 / n)
        z, p = 1.645, k / n  # Wilson score bound
        return min(1.0, (p + z * z / (2 * n) + z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))) / (1 + z * z / n))


def sampling_notes(results, cut, seed):
    out = []
    for (kind, archive), info in cut.items():
        remote = [r for r in results if r.check in PER_RECORD_REMOTE and r.record in info["checked"]]
        if not remote:  # nothing was checked per record for this group, so sampling didn't matter
            continue
        failed = {r.record for r in remote if r.status == FAIL}
        n, k, total = len(info["checked"]), len(failed), info["total"]
        out.append(CheckResult("sampling", INFO, f"{archive} ({kind})",
                               f"checked {n} of {total} at random (seed {seed}): {k} failed; at 95% confidence "
                               f"at most {upper_limit(k, n):.1%} of all {total} would fail. "
                               "Run with sample=None to check every one."))
    return out


# ---------------------------------------------------------------- all

def run_all(result, base_dir: Path, timeout: float = 300, benchmark=None, only=None, log_progress=print,
            sample=None, seed=0):
    """Run every check (or those whose names start with one of ``only``), each with a time limit.

    A check that cannot run (network error, or no answer within ``timeout``
    seconds) is reported as skipped rather than stopping the run. With
    ``sample`` (a count per archive, or a fraction), checks that query an
    archive or website per record run on a reproducible random sample of
    each big group, and the report states how many were checked.
    """
    from .gw import check_localizations
    from .links import check_links, distinct_links
    from .literature import check_arxiv, check_must_include, check_papers_exist, check_papers_linked
    from .timeouts import TimeLimitExceeded, call_with_timeout, with_retries

    records = result["records"]
    sampled, cut = sample_records(records, sample, seed)
    some = {**result, "records": sampled}
    urls = sorted(distinct_links(records))
    k = sample_size(len(urls), sample)
    url_sample = urls if k >= len(urls) else sorted(random.Random(seed).sample(urls, k))
    if k < len(urls):
        cut[("link", "distinct URLs")] = {"checked": set(url_sample), "total": len(urls)}
    steps = [
        ("provenance", lambda: check_provenance(records)),
        ("archive_status", lambda: check_archive_status(result)),
        ("target", lambda: check_target(result)),
        ("in_field", lambda: check_in_field(result)),
        ("id_recheck (Gaia)", lambda: check_gaia_ids(sampled, pos_tol_mas=_tol(result, "position_mas", 1.0),
                                                     mag_tol=_tol(result, "gaia_g_mag", 0.001))),
        ("id_recheck (MAST)", lambda: check_mast_ids(sampled, time_tol_days=_tol(result, "time_days", 1e-5))),
        ("id_recheck (SIMBAD)", lambda: check_simbad_ids(sampled)),
        ("sanity", lambda: check_sanity(records)),
        ("completeness (Gaia)", lambda: check_gaia_completeness(result)),
        ("completeness (MAST)", lambda: check_mast_completeness(result)),
        ("files", lambda: check_files(some, base_dir)),
        ("gw_localization", lambda: check_localizations(some, base_dir)),
        ("paper_exists", lambda: check_papers_exist(some)),
        ("paper_about_target", lambda: check_papers_linked(some)),
        ("arxiv", lambda: check_arxiv(some)),
        ("links", lambda: check_links(records, urls=url_sample)),  # many slow websites: 3x the time limit
        ("must_include", lambda: check_must_include(result, benchmark)),
    ]
    if only:
        steps = [(name, step) for name, step in steps if any(name.startswith(o) for o in only)]
    out = []
    for name, step in steps:
        limit = 3 * timeout if name == "links" else timeout
        try:
            got = call_with_timeout(lambda: with_retries(step), limit)
        except TimeLimitExceeded as exc:
            got = [CheckResult(name, SKIP, "-", f"timed out: {exc}")]
        except Exception as exc:
            got = [CheckResult(name, SKIP, "-", f"check could not run: {exc!r}"[:300])]
        log_progress(f"  {name}: {Counter(r.status for r in got).most_common()}")
        out += got
    return out + sampling_notes(out, cut, seed)
