"""Checks on a SCOOP result: is it real, in the requested field, complete and correct?

Each check returns a list of ``CheckResult``. The checks go back to the archives
themselves (Sesame, Gaia, MAST) rather than trusting anything in the result
beyond the IDs, and never read the ``_injected`` fields used to score them.
"""
from __future__ import annotations

import re
import warnings
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path

import astropy.units as u
import numpy as np
from astropy import log
from astropy.coordinates import SkyCoord
from astropy.io import fits
from astropy.time import Time
from astropy.wcs import WCS, FITSFixedWarning

from .geometry import polygon_overlap, region_overlap, separation_deg

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"

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


# ---------------------------------------------------------------- provenance

def check_provenance(records):
    out = []
    for rec in records:
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
    try:
        c = SkyCoord.from_name(q["target"])
    except Exception as exc:
        return [CheckResult("target", WARN, f"Sesame:{q['target']}", f"could not resolve independently: {exc}")]
    sep = separation_deg(q["ra"], q["dec"], c.ra.deg, c.dec.deg) * 3600
    status = PASS if sep <= tol_arcsec else FAIL
    return [CheckResult("target", status, f"Sesame:{q['target']}",
                        f"query position is {sep:.2f} arcsec from Sesame's position")]


def check_in_field(result, tol_arcsec=1.0):
    q = result["query"]
    ra0, dec0, r = q["ra"], q["dec"], q["radius_arcmin"] / 60
    out = []
    for rec in result["records"]:
        kind = rec.get("kind")
        if kind == "catalog_row":
            sep = separation_deg(ra0, dec0, rec["ra"], rec["dec"]) * 60
            ok = sep <= q["radius_arcmin"] + tol_arcsec / 60
            out.append(CheckResult("in_field", PASS if ok else FAIL, record_key(rec),
                                   f"{sep:.3f} arcmin from center (radius {q['radius_arcmin']})"))
        elif kind == "observation":
            if not rec.get("s_region"):
                sep = separation_deg(ra0, dec0, rec["ra"], rec["dec"]) * 60
                out.append(CheckResult("in_field", WARN, record_key(rec),
                                       f"no footprint; pointing is {sep:.3f} arcmin from center"))
                continue
            try:
                overlaps, contains = region_overlap(rec["s_region"], ra0, dec0, r)
            except ValueError as exc:
                out.append(CheckResult("in_field", FAIL, record_key(rec), str(exc)))
                continue
            detail = "footprint contains the target" if contains else (
                "footprint overlaps the cone" if overlaps else "footprint does not overlap the cone")
            out.append(CheckResult("in_field", PASS if overlaps else FAIL, record_key(rec), detail))
    return out


# ---------------------------------------------------------------- re-check IDs

def check_gaia_ids(records, pos_tol_mas=1.0, mag_tol=0.001):
    from astroquery.gaia import Gaia

    rows = [r for r in records if r.get("kind") == "catalog_row" and r.get("archive") == "Gaia DR3"]
    if not rows:
        return []
    ids = sorted({int(r["native_id"]) for r in rows})
    adql = ("SELECT source_id, ra, dec, phot_g_mean_mag FROM gaiadr3.gaia_source "
            f"WHERE source_id IN ({', '.join(map(str, ids))})")
    truth = {str(t["source_id"]): t for t in Gaia.launch_job(adql).get_results()}
    out = []
    for rec in rows:
        t = truth.get(rec["native_id"])
        if t is None:
            out.append(CheckResult("id_recheck", FAIL, record_key(rec), "source_id not found in Gaia DR3"))
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
    table = Observations.query_criteria(obs_id=sorted({r["native_id"] for r in rows}))
    truth = {(str(t["obs_collection"]), str(t["obs_id"])): t for t in table}
    by_id = {str(t["obs_id"]): t for t in table}
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
            if str(t[field]) != str(rec.get(field)):
                problems.append(f"{field} = {rec.get(field)!r} but MAST has {str(t[field])!r}")
        sep = separation_deg(rec["ra"], rec["dec"], float(t["s_ra"]), float(t["s_dec"])) * 3600
        if sep > pos_tol_arcsec:
            problems.append(f"pointing differs from MAST by {sep:.1f} arcsec")
        for field in ("t_min", "t_max"):
            if rec.get(field) is not None and abs(rec[field] - float(t[field])) > time_tol_days:
                problems.append(f"{field} = {rec[field]:.5f} but MAST has {float(t[field]):.5f}")
        if " ".join(str(rec.get("s_region")).split()) != " ".join(str(t["s_region"]).split()):
            problems.append("s_region differs from MAST")
        out.append(CheckResult("id_recheck", FAIL if problems else PASS, record_key(rec), "; ".join(problems)))
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

def check_gaia_completeness(result):
    from astroquery.gaia import Gaia

    q = result["query"]
    adql = ("SELECT source_id FROM gaiadr3.gaia_source WHERE 1 = CONTAINS(POINT(ra, dec), "
            f"CIRCLE({q['ra']!r}, {q['dec']!r}, {q['radius_arcmin'] / 60!r}))")
    job = Gaia.launch_job_async(adql)  # async: no 2000-row limit
    truth = {str(s) for s in job.get_results()["source_id"]}
    got = {r["native_id"] for r in result["records"]
           if r.get("kind") == "catalog_row" and r.get("archive") == "Gaia DR3"}
    missing = sorted(truth - got)
    found = len(truth & got)
    detail = f"{found} of {len(truth)} Gaia DR3 sources in the cone are in the result"
    if len(got) in ROW_LIMITS and missing:
        detail += f"; {len(got)} rows returned looks like a row limit was hit"
    out = [CheckResult("completeness", FAIL if missing else PASS, "Gaia DR3", detail)]
    out += [CheckResult("completeness", FAIL, f"Gaia DR3:{sid}", "in Gaia DR3 inside the cone but missing from the result")
            for sid in missing]
    return out


def check_mast_completeness(result):
    from astroquery.mast import Observations

    q = result["query"]
    coord = SkyCoord(q["ra"], q["dec"], unit="deg")
    table = Observations.query_region(coord, radius=q["radius_arcmin"] * u.arcmin)
    table = table[table["intentType"] == "science"]
    truth = Counter(str(c) for c in table["obs_collection"])
    got = Counter(r.get("obs_collection") for r in result["records"]
                  if r.get("kind") == "observation" and r.get("archive") == "MAST")
    out = [CheckResult("completeness", WARN if sum(got.values()) < sum(truth.values()) else PASS, "MAST",
                       f"{sum(got.values())} of {sum(truth.values())} MAST science observations in the cone are in the result")]
    for coll, n in truth.most_common():
        k = got.get(coll, 0)
        if k == 0:
            out.append(CheckResult("completeness", WARN, f"MAST/{coll}", f"no records, but MAST has {n} observations"))
        elif k < n:
            out.append(CheckResult("completeness", WARN, f"MAST/{coll}", f"{k} of {n} observations in the result"))
    return out


# ---------------------------------------------------------------- files

def _header_value(hdul, key):
    for hdu in hdul:
        if key in hdu.header:
            return hdu.header[key]
    return None


def _check_image(hdul, ra0, dec0, r):
    level = log.level
    log.setLevel("WARNING")  # silence astropy's SIP-without-"-SIP" notice on drizzled images
    try:
        return _check_image_wcs(hdul, ra0, dec0, r)
    finally:
        log.setLevel(level)


def _check_image_wcs(hdul, ra0, dec0, r):
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", FITSFixedWarning)
        for hdu in hdul:
            if hdu.data is None or getattr(hdu.data, "ndim", 0) != 2:
                continue
            wcs = WCS(hdu.header, hdul)
            if not wcs.has_celestial:
                continue
            corners = wcs.celestial.calc_footprint(axes=hdu.data.shape[::-1])
            overlaps, contains = polygon_overlap(corners, ra0, dec0, r)
            if not overlaps:
                return FAIL, "image footprint (from its WCS) does not overlap the cone"
            x, y = wcs.celestial.world_to_pixel_values(ra0, dec0)
            ny, nx = hdu.data.shape
            if contains and 0 <= x < nx and 0 <= y < ny:
                value = hdu.data[int(y), int(x)]
                if not np.isfinite(value):
                    return WARN, "target is in the image but its pixel is not finite"
                return PASS, f"target at pixel ({x:.0f}, {y:.0f}) of {nx}x{ny}"
            return PASS, "image overlaps the cone (target itself is outside the image)"
    return WARN, "no 2D image with a celestial WCS found"


def _check_spectrum(hdul, ra0, dec0, r_arcmin):
    problems = []
    ra_t, dec_t = _header_value(hdul, "RA_TARG"), _header_value(hdul, "DEC_TARG")
    if ra_t is not None and dec_t is not None:
        sep = separation_deg(ra0, dec0, ra_t, dec_t) * 60
        if sep > r_arcmin:
            problems.append(f"spectrum target is {sep:.2f} arcmin from center, outside the cone")
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
    q = result["query"]
    ra0, dec0, r = q["ra"], q["dec"], q["radius_arcmin"] / 60
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
                status, detail = _check_image(hdul, ra0, dec0, r)
            elif rec.get("product_type") == "spectrum":
                status, detail = _check_spectrum(hdul, ra0, dec0, q["radius_arcmin"])
            else:
                status, detail = SKIP, f"no content check for product type {rec.get('product_type')!r}"
            out.append(CheckResult("file_content", status, key, detail))
    return out


# ---------------------------------------------------------------- all

def run_all(result, base_dir: Path, timeout: float = 300, benchmark=None):
    """Run every check, each with a time limit.

    A check that cannot run (network error, or no answer within ``timeout``
    seconds) is reported as skipped rather than stopping the run.
    """
    from .gw import check_localizations
    from .literature import check_arxiv, check_must_include, check_papers_exist, check_papers_linked
    from .timeouts import TimeLimitExceeded, call_with_timeout

    records = result["records"]
    steps = [
        ("provenance", lambda: check_provenance(records)),
        ("archive_status", lambda: check_archive_status(result)),
        ("target", lambda: check_target(result)),
        ("in_field", lambda: check_in_field(result)),
        ("id_recheck (Gaia)", lambda: check_gaia_ids(records)),
        ("id_recheck (MAST)", lambda: check_mast_ids(records)),
        ("sanity", lambda: check_sanity(records)),
        ("completeness (Gaia)", lambda: check_gaia_completeness(result)),
        ("completeness (MAST)", lambda: check_mast_completeness(result)),
        ("files", lambda: check_files(result, base_dir)),
        ("gw_localization", lambda: check_localizations(result, base_dir)),
        ("paper_exists", lambda: check_papers_exist(result)),
        ("paper_about_target", lambda: check_papers_linked(result)),
        ("arxiv", lambda: check_arxiv(result)),
        ("must_include", lambda: check_must_include(result, benchmark)),
    ]
    out = []
    for name, step in steps:
        try:
            got = call_with_timeout(step, timeout)
        except TimeLimitExceeded as exc:
            got = [CheckResult(name, SKIP, "-", f"timed out: {exc}")]
        except Exception as exc:
            got = [CheckResult(name, SKIP, "-", f"check could not run: {exc!r}"[:300])]
        print(f"  {name}: {Counter(r.status for r in got).most_common()}")
        out += got
    return out
