"""Smoke test: HST/JWST imaging and Gaia sources within 2 half-light radii of a
Local Volume Database (LVDB) object (default: the Draco dSph).

Follows the SCOOP skills (skills/surveys/gaia.md, skills/surveys/mast_hst_jwst.md):
structure from the LVDB, MAST footprints (s_region) intersected with the
half-light ellipse, Gaia from the Heidelberg mirror with an ESA fallback and a
row-limit truncation check, and a log of every default decision.

Outputs in --out:
    <key>_viewer.html         interactive Aladin Lite sky view (open in a browser)
    <key>_map.png             static version of the map, plus an epoch timeline
    <key>_images.csv          one row per HST/JWST observation overlapping the region
    <key>_gaia.csv            Gaia sources inside the region, with candidate flags
    <key>_summary.md          text summary and decision log
    cache/                    raw query results (re-used on later runs)

Usage:
    python smoke_tests/lvdb_field.py --out <dir> [--lvdb-key draco_1] [--n-rhalf 2]
        [--show-qso-candidates] [--serve]
"""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from itertools import combinations
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import Angle, SkyCoord
from astropy.table import Table, join
from astropy.time import Time

from ids import check_id_columns, to_int_id

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # the scoop package, for regions
from scoop.lvdb import CACHE_NAME as LVDB_CACHE_NAME, LVDB_BASE, LVDB_RELEASE  # noqa: E402
from scoop.regions import Region, adql_condition, read_spec  # noqa: E402

LVDB_URL = LVDB_BASE + "comb_all.ecsv"
HEIDELBERG_TAP = "https://gaia.ari.uni-heidelberg.de/tap"
TAP_ROW_LIMITS = (2_000, 100_000, 3_000_000, 10_000_000)  # a result this long is truncated
GAIA_DR3_EPOCH = 2016.0
MOC_DEPTH = 16  # ~3" HEALPix cells; footprints are arcmin-scale

# Reference palette (dataviz skill), light mode.
COLOR = {
    "HST": "#2a78d6",          # categorical slot 1
    "JWST": "#eb6834",         # slot 2
    "extragalactic": "#1baf7a",  # slot 3
    "simbad": "#eda100",       # slot 4 (distinct marker shape too)
    "star": "#a3a29b",         # neutral context
    "ink": "#0b0b0b",
    "ink2": "#52514e",
    "surface": "#fcfcfb",
}

DECISIONS: list[str] = []


def note(msg: str) -> None:
    """Record a decision the agent would report to the user."""
    DECISIONS.append(msg)
    print(f"  [decision] {msg}")


# ── Target and region ────────────────────────────────────────────────────────

def load_lvdb_target(key: str, cache: Path) -> dict:
    path = cache / LVDB_CACHE_NAME
    if not path.exists():
        import urllib.request
        urllib.request.urlretrieve(LVDB_URL, path)
    lvdb = Table.read(path)
    row = lvdb[lvdb["key"] == key]
    if len(row) != 1:
        raise ValueError(f"LVDB key {key!r} not found")
    row = row[0]
    return {
        "key": key, "name": str(row["name"]),
        "ra": float(row["ra"]), "dec": float(row["dec"]),
        "rhalf_arcmin": float(row["rhalf"]),            # semi-major axis
        "ellipticity": float(row["ellipticity"]),
        "pa_deg": float(row["position_angle"]),         # east of north
        "ref_structure": str(row["ref_structure"]),
        "distance_kpc": float(row["distance"]),
        "pmra": float(row["pmra"]), "pmdec": float(row["pmdec"]),
        "ref_proper_motion": str(row["ref_proper_motion"]),
        # LVDB's reference for each property ("AuthorYYYY" + ADS bibcode), for the literature.
        "references": {c[4:]: str(row[c]) for c in row.colnames
                       if c.startswith("ref_") and str(row[c]) not in ("--", "", "None")},
    }


def tangent_offsets(center: SkyCoord, ra, dec):
    """(xi, eta) in arcmin: xi toward east, eta toward north."""
    off = SkyCoord(ra, dec, unit="deg").transform_to(center.skyoffset_frame())
    return off.lon.to_value(u.arcmin), off.lat.to_value(u.arcmin)


def elliptical_radius(xi, eta, tgt) -> np.ndarray:
    """Elliptical radius in units of the half-light radius."""
    pa = np.deg2rad(tgt["pa_deg"])
    major = xi * np.sin(pa) + eta * np.cos(pa)
    minor = xi * np.cos(pa) - eta * np.sin(pa)
    return np.hypot(major, minor / (1 - tgt["ellipticity"])) / tgt["rhalf_arcmin"]


def ellipse_outline(tgt, n_rhalf: float, n: int = 361):
    a = n_rhalf * tgt["rhalf_arcmin"]
    b = a * (1 - tgt["ellipticity"])
    pa = np.deg2rad(tgt["pa_deg"])
    t = np.linspace(0, 2 * np.pi, n)
    major, minor = a * np.cos(t), b * np.sin(t)
    return major * np.sin(pa) + minor * np.cos(pa), major * np.cos(pa) - minor * np.sin(pa)


def ellipse_moc(tgt, n_rhalf: float):
    from mocpy import MOC
    a = n_rhalf * tgt["rhalf_arcmin"] * u.arcmin
    return MOC.from_elliptical_cone(
        lon=tgt["ra"] * u.deg, lat=tgt["dec"] * u.deg, a=Angle(a),
        b=Angle(a * (1 - tgt["ellipticity"])), pa=Angle(tgt["pa_deg"] * u.deg),
        max_depth=MOC_DEPTH)


# ── MAST ─────────────────────────────────────────────────────────────────────

def parse_s_region(s: str) -> list[np.ndarray]:
    """STC-S POLYGON/CIRCLE string -> list of (N, 2) arrays of (ra, dec) deg."""
    shapes, tokens = [], str(s).replace(",", " ").split()
    i = 0
    while i < len(tokens):
        kind = tokens[i].upper()
        if kind not in ("POLYGON", "CIRCLE"):
            i += 1
            continue
        i += 1
        nums = []
        while i < len(tokens) and tokens[i].upper() not in ("POLYGON", "CIRCLE"):
            try:
                nums.append(float(tokens[i]))
            except ValueError:
                pass                       # frame names such as ICRS
            i += 1
        if kind == "POLYGON" and len(nums) >= 6:
            shapes.append(np.array(nums[: len(nums) // 2 * 2]).reshape(-1, 2))
        elif kind == "CIRCLE" and len(nums) == 3:
            ra0, dec0, r = nums
            t = np.linspace(0, 2 * np.pi, 73)[:-1]
            shapes.append(np.column_stack([
                ra0 + r * np.cos(t) / np.cos(np.deg2rad(dec0)), dec0 + r * np.sin(t)]))
    return shapes


def footprint_moc(shapes):
    from mocpy import MOC
    moc = None
    for poly in shapes:
        ra = np.unwrap(np.deg2rad(poly[:, 0]))
        m = MOC.from_polygon_skycoord(
            SkyCoord(np.rad2deg(ra) % 360, poly[:, 1], unit="deg"), max_depth=MOC_DEPTH)
        moc = m if moc is None else moc.union(m)
    return moc


def with_retries(fn, what: str, tries: int = 5, delay: float = 10.0):
    for attempt in range(tries):
        try:
            return fn()
        except Exception as e:  # MAST and TAP servers fail transiently
            if attempt == tries - 1:
                raise
            print(f"  {what} failed ({e}); retrying in {delay:.0f} s")
            time.sleep(delay)
            delay *= 2


def query_mast(tgt, radius_deg: float, cache: Path):
    """HST/JWST image observations (executed) and planned JWST observations."""
    from astroquery.mast import Observations
    center = SkyCoord(tgt["ra"], tgt["dec"], unit="deg")
    path, path_planned = cache / "mast_images.ecsv", cache / "mast_planned.ecsv"
    if path.exists() and path_planned.exists():
        return Table.read(path), Table.read(path_planned)
    kw = dict(coordinates=center, radius=radius_deg * u.deg, dataproduct_type=["image"])
    n = with_retries(lambda: Observations.query_criteria_count(
        obs_collection=["HST", "JWST"], **kw), "MAST count")
    print(f"  MAST: {n} HST/JWST image records within {radius_deg:.3f} deg")
    obs = with_retries(lambda: Observations.query_criteria(
        obs_collection=["HST", "JWST"], **kw), "MAST query")
    planned = with_retries(lambda: Observations.query_criteria(
        obs_collection=["JWST"], calib_level=[-1], **kw), "MAST planned query")
    for t, p in ((obs, path), (planned, path_planned)):
        for c in t.colnames:              # masked/object columns don't round-trip
            if t[c].dtype == object:
                t[c] = t[c].astype(str)
        t.write(p, overwrite=True)
    return obs, planned


MAST_COLUMNS = {"intentType": str, "project": str, "calib_level": np.int64, "s_region": str, "obs_collection": str,
                "instrument_name": str, "filters": str, "obs_id": str, "target_name": str, "t_min": float,
                "t_max": float, "t_exptime": float, "proposal_id": str, "proposal_pi": str, "dataRights": str,
                "s_ra": float, "s_dec": float, "obsid": str}


def empty_mast() -> Table:
    """No HST/JWST records, with the columns the rest of the pipeline reads."""
    return Table({c: np.array([], dtype=d) for c, d in MAST_COLUMNS.items()})


def select_observations(obs: Table, region_moc, tgt):
    """Science observations whose footprint overlaps the region; one row per dataset."""
    keep = np.ones(len(obs), bool)
    intent = np.char.lower(np.asarray(obs["intentType"], str))
    n_cal = int((intent != "science").sum())
    if n_cal:
        note(f"Skipped {n_cal} non-science records (target acquisitions and "
             f"calibration exposures); say 'include calibration' to keep them.")
    keep &= intent == "science"
    is_hap = np.asarray(obs["project"], str) == "HAP"
    n_hap = int((keep & is_hap).sum())
    if n_hap:
        note(f"{n_hap} Hubble Advanced Products (HAP) mosaics also cover this area; "
             f"they duplicate the standard HST products, so they're counted once "
             f"(standard products) and not drawn.")
    keep &= ~is_hap
    low = keep & (np.asarray(obs["calib_level"]) < 2)
    if low.any():
        note(f"Skipped {int(low.sum())} records with only raw/uncalibrated data "
             f"(calib_level < 2).")
    keep &= ~low
    obs = obs[keep]

    rows, mocs = [], []
    for r in obs:
        shapes = parse_s_region(r["s_region"])
        if not shapes:
            continue
        fm = footprint_moc(shapes)
        inter = fm.intersection(region_moc)
        if inter.sky_fraction == 0:
            continue
        rows.append(r)
        mocs.append((fm, shapes, inter.sky_fraction / fm.sky_fraction))
    sel = Table(rows=rows, names=obs.colnames) if rows else obs[:0]
    sel["frac_in_region"] = [m[2] for m in mocs]
    where = "region" if tgt.get("kind") == "region" else f"{N_RHALF:g} r_half ellipse"
    if len(obs) or tgt.get("kind") != "region":
        note(f"Kept {len(sel)} HST/JWST observations whose footprints overlap the "
             f"{where} (footprint polygons, not pointing centers).")
    return sel, [m[0] for m in mocs], [m[1] for m in mocs]


def baselines(sel: Table, mocs) -> dict:
    """Longest time baseline between overlapping footprints, per telescope pair."""
    mid = 0.5 * (np.asarray(sel["t_min"], float) + np.asarray(sel["t_max"], float))
    tel = np.asarray(sel["obs_collection"], str)
    best: dict[str, tuple] = {}
    for i, j in combinations(range(len(sel)), 2):
        key = "–".join(sorted((tel[i], tel[j])))
        dt = abs(mid[i] - mid[j]) / 365.25
        if dt <= best.get(key, (0,))[0]:
            continue
        if mocs[i].intersection(mocs[j]).sky_fraction > 0:
            best[key] = (dt, sel["obs_id"][i], sel["obs_id"][j])
    return best


# ── Gaia ─────────────────────────────────────────────────────────────────────

GAIA_COLS = ("g.source_id, g.ra, g.dec, g.phot_g_mean_mag, g.bp_rp, g.parallax, "
             "g.parallax_error, g.pmra, g.pmra_error, g.pmdec, g.pmdec_error, g.pmra_pmdec_corr, g.ruwe, "
             "g.astrometric_params_solved, g.ipd_gof_harmonic_amplitude, "
             "g.visibility_periods_used, g.astrometric_excess_noise_sig, "
             "g.in_qso_candidates, g.in_galaxy_candidates, "
             "g.classprob_dsc_combmod_quasar, g.classprob_dsc_combmod_galaxy")


def run_tap(adql: str, what: str) -> Table:
    """Heidelberg first (faster), ESA on failure; refuse truncated results."""
    from astroquery.utils.tap.core import TapPlus
    try:
        tap = TapPlus(url=HEIDELBERG_TAP, verbose=False)
        res = with_retries(lambda: tap.launch_job_async(adql, maxrec=10_000_000)
                           .get_results(), f"Heidelberg {what}", tries=3)
        server = "Heidelberg ARI mirror"
    except Exception as e:
        from astroquery.gaia import Gaia
        print(f"  Heidelberg failed ({e}); falling back to ESA")
        Gaia.ROW_LIMIT = -1
        res = with_retries(lambda: Gaia.launch_job_async(adql).get_results(),
                           f"ESA {what}", tries=3)
        server = "ESA Gaia archive"
    if len(res) in TAP_ROW_LIMITS:
        raise RuntimeError(f"{what}: {len(res)} rows is a TAP output limit -- "
                           f"truncated; split the query")
    return res, server


def query_gaia(tgt, radius_deg: float, cache: Path) -> Table:
    path = cache / "gaia.ecsv"
    if path.exists():
        return Table.read(path)
    circle = (f"1=CONTAINS(POINT('ICRS', g.ra, g.dec), "
              f"CIRCLE('ICRS', {tgt['ra']}, {tgt['dec']}, {radius_deg}))")
    src, server = run_tap(f"SELECT {GAIA_COLS} FROM gaiadr3.gaia_source AS g WHERE {circle}",
                          "gaia_source")
    note(f"Queried Gaia DR3 from the {server}; results are identical to ESA's DR3.")
    qso, _ = run_tap("SELECT q.source_id, q.gaia_crf_source, q.classlabel_dsc_joint "
                     "AS qso_classlabel_dsc_joint, q.redshift_qsoc FROM gaiadr3.qso_candidates "
                     f"AS q JOIN gaiadr3.gaia_source AS g ON g.source_id = q.source_id "
                     f"WHERE {circle}", "qso_candidates")
    gal, _ = run_tap("SELECT q.source_id, q.classlabel_dsc_joint AS gal_classlabel_dsc_joint "
                     "FROM gaiadr3.galaxy_candidates AS q JOIN gaiadr3.gaia_source AS g "
                     f"ON g.source_id = q.source_id WHERE {circle}", "galaxy_candidates")
    out = join(src, qso, keys="source_id", join_type="left")
    out = join(out, gal, keys="source_id", join_type="left")
    # Known variables masquerading as QSO candidates (RR Lyrae in dSphs). Look up
    # by source_id: a spatial join against vari_rrlyrae took minutes, this < 1 s.
    cand = np.asarray(out["source_id"])[as_bool(out["in_qso_candidates"])]
    rr_ids = []
    for k in range(0, len(cand), 1000):
        ids = ",".join(str(i) for i in cand[k:k + 1000])
        res, _ = run_tap(f"SELECT source_id FROM gaiadr3.vari_rrlyrae WHERE source_id IN ({ids})",
                         "vari_rrlyrae")
        rr_ids += list(res["source_id"])
    out["is_rrlyrae"] = np.isin(np.asarray(out["source_id"]), rr_ids)
    for c in out.colnames:
        if out[c].dtype == object:
            out[c] = out[c].astype(str)
    out.write(path, overwrite=True)
    return out


# Columns read downstream, so an empty cone (VizieR returns no table) still has them.
QSO_EMPTY_COLUMNS = {
    "milliquas": {"RAJ2000": float, "DEJ2000": float, "Name": str, "Type": str, "z": float, "Rmag": float},
    "quaia": {"GaiaDR3": np.int64},
}


def _with_columns(t: Table, name: str) -> Table:
    if len(t) == 0 and not set(QSO_EMPTY_COLUMNS[name]) <= set(t.colnames):
        return Table({c: np.array([], dtype=d) for c, d in QSO_EMPTY_COLUMNS[name].items()})
    return t


def query_qso_catalogs(tgt, radius_deg: float, cache: Path):
    """MILLIQUAS v8 (VII/294) and Quaia G<20.0 (J/ApJ/964/69) cone searches on VizieR."""
    from astroquery.vizier import Vizier
    out = {}
    center = SkyCoord(tgt["ra"], tgt["dec"], unit="deg")
    for name, cat in (("milliquas", "VII/294"), ("quaia", "J/ApJ/964/69")):
        path = cache / f"{name}.ecsv"
        if path.exists():
            out[name] = _with_columns(Table.read(path), name)
            continue
        res = with_retries(lambda: Vizier(columns=["**"], row_limit=-1).query_region(
            center, radius=radius_deg * u.deg, catalog=cat), f"VizieR {cat}")
        t = _with_columns(res[0] if len(res) else Table(), name)
        for c in t.colnames:
            if t[c].dtype == object:
                t[c] = t[c].astype(str)
        t.write(path, overwrite=True)
        out[name] = t
    return out["milliquas"], out["quaia"]


SKYSERVER_SQL = "https://skyserver.sdss.org/dr20/SkyServerWS/SearchTools/SqlSearch"
DATALAB_TAP = "https://datalab.noirlab.edu/tap"
# The certifi bundle that requests uses lacks the root certificate of SkyServer's
# chain (seen 2026-10-19), but operating-system stores have it. Never disable
# verification; fall back to the OS store instead.
SYSTEM_CA_CANDIDATES = (
    "/etc/ssl/cert.pem",                    # macOS, Alpine
    "/etc/ssl/certs/ca-certificates.crt",   # Debian, Ubuntu
    "/etc/pki/tls/certs/ca-bundle.crt",     # RHEL, Fedora, CentOS
    "/etc/ssl/ca-bundle.pem",               # openSUSE
)


def system_ca_bundle() -> str | None:
    """Path to the OS certificate bundle ($SCOOP_CA_BUNDLE overrides), or None."""
    import os
    override = os.environ.get("SCOOP_CA_BUNDLE")
    if override:
        return override
    return next((c for c in SYSTEM_CA_CANDIDATES if os.path.exists(c)), None)


def sdss_sql(sql: str) -> Table:
    import requests
    params = {"cmd": sql, "format": "csv"}
    try:
        r = requests.get(SKYSERVER_SQL, params=params, timeout=300)
    except requests.exceptions.SSLError:
        ca = system_ca_bundle()
        if ca is None:
            raise RuntimeError("SkyServer's certificate failed verification and no system "
                               "certificate bundle was found; set SCOOP_CA_BUNDLE to one")
        r = requests.get(SKYSERVER_SQL, params=params, timeout=300, verify=ca)
    r.raise_for_status()
    text = r.text.split("\n", 1)[1] if r.text.startswith("#Table") else r.text
    return Table.read(text, format="ascii.csv")


def query_spectra(tgt, radius_deg: float, cache: Path, stars_only: bool = False):
    """Index rows (not the spectra themselves) for SDSS DR20 and DESI DR1 in the region.

    SDSS: allspec (SDSS-I to V, incl. APOGEE and MaNGA), plus the Gaia IDs SDSS-V
    records in spAll. DESI: zpix, plus Milky Way Survey parameters and Gaia IDs.
    Spectrum files are only fetched when a user follows a link.
    """
    p_sdss, p_desi = cache / "sdss_allspec.ecsv", cache / "desi_dr1.ecsv"
    if p_sdss.exists() and p_desi.exists():
        return Table.read(p_sdss), Table.read(p_desi)
    r_arcmin = radius_deg * 60
    cone = f"dbo.fGetNearbyAllspecEq({tgt['ra']}, {tgt['dec']}, {r_arcmin}) AS n"
    n = int(sdss_sql(f"SELECT COUNT(*) AS n FROM {cone}")["n"][0])
    if n >= 500_000:
        raise RuntimeError(f"{n} SDSS spectra: above the SkyServer row limit; split the region")
    sdss = sdss_sql(
        "SELECT n.distance, a.allspec_id, a.sdss_phase, a.observatory, a.instrument, a.survey, "
        "a.programname, a.ra, a.dec, a.mjd, a.sas_url, a.cas_url, a.apogee_id, a.sdss_id "
        f"FROM {cone} JOIN allspec AS a ON a.allspec_id = n.allspec_id")
    if len(sdss) and float(np.max(sdss["distance"])) > 1.01 * r_arcmin:
        note("Warning: SDSS cone distances exceed the requested radius; the radius units "
             "of fGetNearbyAllspecEq may not be arcminutes.")
    # SDSS-V records a Gaia ID per object (sdss_id); attach it to every spectrum of that object.
    spall = sdss_sql(
        "SELECT s.sdss_id, s.gaia_id FROM "
        f"dbo.fGetNearbySpAllEq({tgt['ra']}, {tgt['dec']}, {r_arcmin}) AS n "
        "JOIN spAll AS s ON s.specobjid = n.specobjid WHERE s.gaia_id > 0")
    gmap = {_int(a): _int(b) for a, b in zip(spall["sdss_id"], spall["gaia_id"])} if len(spall) else {}
    # Explicit int64: an empty list would otherwise become a float column (see ids.py).
    sdss["survey_gaia_id"] = np.array([gmap.get(_int(x), 0) for x in sdss["sdss_id"]], dtype=np.int64)
    sdss["survey_gaia_release"] = "unknown"   # not documented in the pages we checked

    try:
        desi = query_desi(tgt, radius_deg, stars_only)
    except Exception as e:  # one archive being down shouldn't lose the whole field
        note(f"DESI DR1 (NOIRLab Data Lab) failed ({str(e)[:120]}); this field has no DESI spectra. "
             "Re-run the field to try again.")
        desi = Table({c: np.array([], dtype=d) for c, d in DESI_EMPTY_COLUMNS.items()})
        sdss.write(p_sdss, overwrite=True)  # cache SDSS only, so a re-run retries DESI
        return sdss, desi
    for t, p in ((sdss, p_sdss), (desi, p_desi)):
        for c in t.colnames:
            if t[c].dtype == object:
                t[c] = t[c].astype(str)
        t.write(p, overwrite=True)
    return sdss, desi


OPTIONAL_ARCHIVES = {"hst_jwst": "HST/JWST observations", "qso": "QSO catalogs (MILLIQUAS, Quaia)",
                     "spectra": "SDSS/DESI spectra"}
MATCH_COLUMNS = {"gaia_source_id": np.int64, "match_method": str, "match_sep_arcsec": float}
SDSS_EMPTY_COLUMNS = {"allspec_id": np.int64, "sdss_phase": str, "observatory": str, "instrument": str, "survey": str,
                      "programname": str, "ra": float, "dec": float, "mjd": float, "sas_url": str, "cas_url": str,
                      "apogee_id": str, "sdss_id": np.int64, "survey_gaia_id": np.int64, "survey_gaia_release": str,
                      **MATCH_COLUMNS}
DESI_EMPTY_COLUMNS = {"targetid": np.int64, "mean_fiber_ra": float, "mean_fiber_dec": float, "z": float,
                      "zerr": float, "zwarn": np.int64, "spectype": str, "subtype": str, "survey": str,
                      "program": str, "healpix": np.int64, "zcat_primary": bool, "mean_mjd": float,
                      "survey_gaia_release": str, "survey_gaia_id": np.int64}


def query_desi(tgt, radius_deg: float, stars_only: bool = False) -> Table:
    """DESI DR1 redshift catalog rows (zpix) in a cone, with Milky Way Survey parameters."""
    import pyvo
    tap = pyvo.dal.TAPService(DATALAB_TAP)
    # Data Lab's TAP has no ADQL geometry; it takes the Q3C function in this form.
    desi = tap.search(
        "SELECT targetid, mean_fiber_ra, mean_fiber_dec, z, zerr, zwarn, spectype, subtype, "
        "survey, program, healpix, zcat_primary, mean_mjd FROM desi_dr1.zpix WHERE "
        f"'t' = q3c_radial_query(mean_fiber_ra, mean_fiber_dec, {tgt['ra']}, {tgt['dec']}, "
        f"{radius_deg})" + (" AND spectype = 'STAR'" if stars_only else "")).to_table()
    stars = np.unique(np.asarray(desi["targetid"])[np.asarray(desi["spectype"], str) == "STAR"])
    mws_rows = []
    for k in range(0, len(stars), 500):
        ids = ",".join(str(int(i)) for i in stars[k:k + 500])
        mws_rows.append(tap.search(
            "SELECT targetid, rv_adop, rv_err, feh, feh_err, teff, logg, source_id, ref_cat "
            f"FROM desi_dr1.mws WHERE targetid IN ({ids})").to_table())
    if mws_rows:
        from astropy.table import vstack, unique
        mws = unique(vstack(mws_rows), keys="targetid")
        desi = join(desi, mws, keys="targetid", join_type="left")
    # DESI's ref_cat 'G2' means the ID is a Gaia DR2 source_id.
    desi["survey_gaia_release"] = "DR2"
    desi["survey_gaia_id"] = np.array([
        _int(sid) if "ref_cat" in desi.colnames and str(rc) == "G2" else 0
        for sid, rc in zip(desi["source_id"] if "source_id" in desi.colnames else [0] * len(desi),
                           desi["ref_cat"] if "ref_cat" in desi.colnames else [""] * len(desi))],
        dtype=np.int64)
    return desi


def _int(x) -> int:
    """Exact integer ID (see ids.py); 0 if missing."""
    return to_int_id(x)


def match_spectra_to_gaia(gaia: Table, sdss: Table, desi: Table, cache: Path,
                          radius_arcsec: float = 1.0):
    """Gaia DR3 source_id for each spectrum, by ID where possible, else by position.

    Order: a Gaia ID the survey recorded (as DR3, or mapped from DR2 through
    gaiadr3.dr2_neighbourhood); for APOGEE, its 2MASS ID through
    gaiadr3.tmass_psc_xsc_best_neighbour; otherwise the nearest Gaia source
    within radius_arcsec, flagged if two are that close.
    """
    ids = np.asarray(gaia["source_id"])
    in_field = set(ids.tolist())

    # 2MASS designations of the field's Gaia sources.
    p_tm = cache / "gaia_tmass.ecsv"
    if p_tm.exists():
        tm = Table.read(p_tm)
    else:
        parts = []
        for k in range(0, len(ids), 1000):
            res, _ = run_tap("SELECT source_id, original_ext_source_id FROM "
                             "gaiadr3.tmass_psc_xsc_best_neighbour WHERE source_id IN ("
                             + ",".join(str(i) for i in ids[k:k + 1000]) + ")", "2MASS neighbours")
            parts.append(res)
        from astropy.table import vstack
        tm = vstack(parts) if parts else Table(names=("source_id", "original_ext_source_id"))
        tm.write(p_tm, overwrite=True)
    tmass_to_gaia = {str(d): int(g) for g, d in zip(tm["source_id"], tm["original_ext_source_id"])}

    # Survey Gaia IDs that aren't DR3 IDs in this field: try them as DR2 IDs.
    # DESI IDs are DR2 (always map them); SDSS-V's release is unknown (try DR3 first).
    dr2_ids = sorted(({_int(i) for i in desi["survey_gaia_id"]}
                      | ({_int(i) for i in sdss["survey_gaia_id"]} - in_field)) - {0})
    dr2_to_dr3 = {}
    for k in range(0, len(dr2_ids), 1000):
        res, _ = run_tap("SELECT dr2_source_id, dr3_source_id, angular_distance FROM "
                         "gaiadr3.dr2_neighbourhood WHERE dr2_source_id IN ("
                         + ",".join(str(i) for i in dr2_ids[k:k + 1000]) + ")", "DR2 neighbourhood")
        best = {}
        for d2, d3, ang in zip(res["dr2_source_id"], res["dr3_source_id"], res["angular_distance"]):
            if int(d3) in in_field and (int(d2) not in best or ang < best[int(d2)][1]):
                best[int(d2)] = (int(d3), float(ang))
        dr2_to_dr3.update({k2: v[0] for k2, v in best.items()})

    gc = SkyCoord(gaia["ra"], gaia["dec"], unit="deg")
    for t, ra, dec in ((sdss, "ra", "dec"), (desi, "mean_fiber_ra", "mean_fiber_dec")):
        gid = np.zeros(len(t), np.int64)
        method = np.full(len(t), "", dtype="U32")
        sep_out = np.full(len(t), np.nan)
        if len(t) and len(gaia):
            sc = SkyCoord(t[ra], t[dec], unit="deg")
            i1, s1, _ = sc.match_to_catalog_sky(gc)
            _, s2, _ = sc.match_to_catalog_sky(gc, nthneighbor=2)
            apogee = (np.asarray(t["apogee_id"], str) if "apogee_id" in t.colnames
                      else np.full(len(t), ""))
            release = str(t["survey_gaia_release"][0]) if len(t) else ""
            for k in range(len(t)):
                sid = _int(t["survey_gaia_id"][k])
                tmass = apogee[k][2:] if apogee[k].startswith("2M") else ""
                if sid and release != "DR2" and sid in in_field:
                    gid[k], method[k] = sid, "Gaia DR3 ID"
                elif sid and sid in dr2_to_dr3:
                    gid[k], method[k] = dr2_to_dr3[sid], "Gaia DR2 ID"
                elif tmass and tmass in tmass_to_gaia:
                    gid[k], method[k] = tmass_to_gaia[tmass], "2MASS ID"
                elif s1[k] < radius_arcsec * u.arcsec:
                    gid[k] = ids[i1[k]]
                    method[k] = ("position (ambiguous)" if s2[k] < radius_arcsec * u.arcsec
                                 else "position")
                if gid[k]:
                    j = np.flatnonzero(ids == gid[k])[0]
                    sep_out[k] = sc[k].separation(gc[j]).arcsec
                    # An ID match should agree with position, allowing for proper
                    # motion over up to ~25 yr between the survey epoch and Gaia's.
                    pm = np.hypot(float(np.ma.filled(gaia["pmra"][j], 0)),
                                  float(np.ma.filled(gaia["pmdec"][j], 0)))
                    if "ID" in method[k] and sep_out[k] > 2 + 0.025 * pm:
                        method[k] += " (position offset: check)"
        t["gaia_source_id"], t["match_method"], t["match_sep_arcsec"] = gid, method, sep_out
    gaia["has_sdss"] = np.isin(ids, np.asarray(sdss["gaia_source_id"]))
    gaia["has_desi"] = np.isin(ids, np.asarray(desi["gaia_source_id"]))


def query_simbad_galaxies(tgt, radius_deg: float, cache: Path) -> Table:
    """SIMBAD galaxies (object type G and descendants, minus the AGN family)."""
    path = cache / "simbad_galaxies.ecsv"
    if path.exists():
        return Table.read(path)
    from astroquery.simbad import Simbad
    circle = (f"CONTAINS(POINT('ICRS', ra, dec), "
              f"CIRCLE('ICRS', {tgt['ra']}, {tgt['dec']}, {radius_deg})) = 1")
    cols = "main_id, ra, dec, otype, galdim_majaxis, galdim_minaxis, galdim_angle"
    gal = with_retries(lambda: Simbad.query_tap(
        f"SELECT {cols} FROM basic WHERE {circle} AND otype = 'G..'"), "SIMBAD galaxies")
    agn = with_retries(lambda: Simbad.query_tap(
        f"SELECT main_id FROM basic WHERE {circle} AND otype = 'AGN..'"), "SIMBAD AGN")
    gal = gal[~np.isin(np.asarray(gal["main_id"], str), np.asarray(agn["main_id"], str))]
    for c in gal.colnames:
        if gal[c].dtype == object:
            gal[c] = gal[c].astype(str)
    gal.write(path, overwrite=True)
    return gal


def match_gaia_to_galaxies(sg: Table, gaia: Table, radius_arcsec: float = 2.0) -> Table:
    """For each SIMBAD galaxy, the nearest Gaia source and how Gaia classified it."""
    sg["gaia_source_id"] = np.zeros(len(sg), np.int64)
    for c in ("gaia_match", "gaia_gal_cand", "gaia_qso_cand", "gaia_2p"):
        sg[c] = np.zeros(len(sg), bool)
    if not len(sg) or not len(gaia):
        return sg
    idx, sep, _ = SkyCoord(sg["ra"], sg["dec"], unit="deg").match_to_catalog_sky(
        SkyCoord(gaia["ra"], gaia["dec"], unit="deg"))
    ok = sep < radius_arcsec * u.arcsec
    j = idx[ok]
    sg["gaia_match"][ok] = True
    sg["gaia_source_id"][ok] = np.asarray(gaia["source_id"])[j]
    sg["gaia_gal_cand"][ok] = np.asarray(gaia["is_gal_cand"])[j]
    sg["gaia_qso_cand"][ok] = np.asarray(gaia["is_qso_cand"])[j]
    sg["gaia_2p"][ok] = np.ma.filled(gaia["astrometric_params_solved"], 0)[j] == 3
    return sg


def as_bool(col) -> np.ndarray:
    """Booleans arrive as bool (ESA) or 0/1 ints (Heidelberg), possibly masked."""
    arr = np.ma.filled(np.ma.asarray(col), 0)
    if arr.dtype.kind in "US":
        return np.isin(np.char.lower(arr.astype(str)), ["true", "1", "t"])
    return arr.astype(bool)


# ── Plot ─────────────────────────────────────────────────────────────────────

def make_plot(tgt, sel, shapes_list, planned, gaia, mq, sg, out_png: Path,
              show_qso_candidates: bool = False):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    center = SkyCoord(tgt["ra"], tgt["dec"], unit="deg")
    plt.rcParams.update({"font.size": 10, "axes.edgecolor": COLOR["ink2"],
                         "axes.labelcolor": COLOR["ink"], "xtick.color": COLOR["ink2"],
                         "ytick.color": COLOR["ink2"]})
    fig, (ax, axt) = plt.subplots(
        1, 2, figsize=(14, 7.2), gridspec_kw={"width_ratios": [1.15, 1]},
        facecolor=COLOR["surface"])

    # Footprints
    tel = np.asarray(sel["obs_collection"], str)
    for shapes, t in zip(shapes_list, tel):
        for poly in shapes:
            x, y = tangent_offsets(center, poly[:, 0], poly[:, 1])
            ax.fill(x, y, facecolor=COLOR[t], alpha=0.06, edgecolor="none", zorder=1)
            ax.plot(np.append(x, x[0]), np.append(y, y[0]), color=COLOR[t],
                    lw=0.8, alpha=0.75, zorder=2)
    for r in planned:
        for poly in parse_s_region(r["s_region"]):
            x, y = tangent_offsets(center, poly[:, 0], poly[:, 1])
            ax.plot(np.append(x, x[0]), np.append(y, y[0]), color=COLOR["JWST"],
                    lw=1.2, ls=(0, (4, 3)), zorder=2)

    # Gaia
    xi, eta = gaia["xi"], gaia["eta"]
    qso, gal, crf = gaia["is_qso_cand"], gaia["is_gal_cand"], gaia["is_crf"]
    quaia = gaia["in_quaia"]
    rrl = qso & gaia["is_rrlyrae"]
    other_qso = qso & ~crf & ~quaia & ~rrl
    star = ~(gal | crf | quaia) & (~qso if show_qso_candidates else True)
    ax.scatter(xi[star], eta[star], s=3, color=COLOR["star"], lw=0, zorder=3)
    if show_qso_candidates:
        ax.scatter(xi[rrl], eta[rrl], s=16, marker="x", color=COLOR["ink2"], lw=0.8,
                   alpha=0.7, zorder=3)
        ax.scatter(xi[other_qso], eta[other_qso], s=34, marker="o", facecolor="none",
                   edgecolor=COLOR["extragalactic"], lw=1.0, zorder=4)
    ax.scatter(xi[gal], eta[gal], s=30, marker="s", facecolor="none",
               edgecolor=COLOR["extragalactic"], lw=1.0, zorder=4)
    # Trusted QSOs; a source in several catalogs shows stacked markers.
    conf = mq["confirmed"]
    ax.scatter(mq["xi"][conf], mq["eta"][conf], s=70, marker="^",
               facecolor=COLOR["extragalactic"], edgecolor=COLOR["surface"], lw=0.8, zorder=5)
    ax.scatter(mq["xi"][~conf], mq["eta"][~conf], s=70, marker="^", facecolor="none",
               edgecolor=COLOR["extragalactic"], lw=1.4, zorder=5)
    ax.scatter(xi[quaia], eta[quaia], s=55, marker="D", facecolor=COLOR["extragalactic"],
               edgecolor=COLOR["surface"], lw=0.8, alpha=0.85, zorder=6)
    ax.scatter(xi[crf], eta[crf], s=150, marker="*", facecolor=COLOR["extragalactic"],
               edgecolor=COLOR["surface"], lw=0.8, zorder=7)
    ax.scatter(sg["xi"], sg["eta"], s=150, marker="o", facecolor="none",
               edgecolor=COLOR["simbad"], lw=1.4, zorder=4)
    bad = gaia["trusted_qso"] & ~gaia["qso_astrometry_ok"]
    ax.scatter(xi[bad], eta[bad], s=260, marker="o", facecolor="none",
               edgecolor=COLOR["ink"], lw=1.2, zorder=8)

    # Target
    for n_r, ls in ((1, "-"), (N_RHALF, (0, (6, 3)))):
        ex, ey = ellipse_outline(tgt, n_r)
        ax.plot(ex, ey, color=COLOR["ink"], lw=2 if n_r == 1 else 1.4, ls=ls, zorder=6)
    ax.plot(0, 0, marker="+", ms=14, mew=2.2, color=COLOR["ink"], zorder=7)

    lim = 1.08 * N_RHALF * tgt["rhalf_arcmin"] * (1.3 if tgt.get("kind") == "region" else 1.0)
    ax.set_xlim(lim, -lim)                       # east to the left
    ax.set_ylim(-lim, lim)
    ax.set_aspect("equal")
    ax.set_xlabel("Δα cos δ (arcmin; east ←)")
    ax.set_ylabel("Δδ (arcmin; north ↑)")
    ax.set_title(f"{tgt['name']}: HST/JWST footprints and Gaia DR3 sources",
                 loc="left", color=COLOR["ink"])
    ax.grid(color="#e6e5e0", lw=0.6, zorder=0)
    ax.set_facecolor(COLOR["surface"])
    n_planned = len(planned)
    handles = [
        Patch(facecolor=COLOR["HST"], alpha=0.25, edgecolor=COLOR["HST"],
              label=f"HST footprints ({(tel == 'HST').sum()})"),
        Patch(facecolor=COLOR["JWST"], alpha=0.25, edgecolor=COLOR["JWST"],
              label=f"JWST footprints ({(tel == 'JWST').sum()})"),
        Line2D([], [], color=COLOR["JWST"], ls=(0, (4, 3)),
               label=f"JWST planned ({n_planned})"),
        Line2D([], [], ls="", marker=".", color=COLOR["star"], ms=6,
               label=f"Gaia sources ({int(np.sum(star))})"),
        Line2D([], [], ls="", marker="s", mfc="none", mec=COLOR["extragalactic"], ms=7,
               label=f"Gaia galaxy candidates ({int(gal.sum())})"),
        Line2D([], [], ls="", marker="*", color=COLOR["extragalactic"], ms=12,
               label=f"Gaia-CRF3 QSOs ({int(crf.sum())})"),
        Line2D([], [], ls="", marker="D", color=COLOR["extragalactic"], ms=7,
               label=f"Quaia QSOs, G<20 ({int(quaia.sum())})"),
        Line2D([], [], ls="", marker="^", color=COLOR["extragalactic"], ms=8,
               label=f"MILLIQUAS confirmed ({int(conf.sum())})"),
        Line2D([], [], ls="", marker="^", mfc="none", mec=COLOR["extragalactic"], ms=8,
               mew=1.4, label=f"MILLIQUAS radio/X-ray candidates ({int((~conf).sum())})"),
        Line2D([], [], ls="", marker="o", mfc="none", mec=COLOR["ink"], ms=11,
               label=f"QSO with non-zero PM/parallax ({int(bad.sum())})"),
    ]
    if show_qso_candidates:
        handles += [
            Line2D([], [], ls="", marker="o", mfc="none", mec=COLOR["extragalactic"], ms=7,
                   label=f"Other Gaia QSO candidates, unvetted ({int(other_qso.sum())})"),
            Line2D([], [], ls="", marker="x", color=COLOR["ink2"], ms=6,
                   label=f"QSO candidates that are Gaia RR Lyrae ({int(rrl.sum())})"),
        ]
    handles += [
        Line2D([], [], ls="", marker="o", mfc="none", mec=COLOR["simbad"], ms=11, mew=1.4,
               label=f"SIMBAD galaxies ({len(sg)}; {int(sg['gaia_gal_cand'].sum())} flagged by Gaia)"),
        Line2D([], [], color=COLOR["ink"], lw=2, label="r_half ellipse"),
        Line2D([], [], color=COLOR["ink"], lw=1.4, ls=(0, (6, 3)),
               label=f"{N_RHALF:g} r_half (query region)"),
    ]
    fig.legend(handles=handles, loc="lower center", ncol=5, frameon=False,
               fontsize=8.5, labelcolor=COLOR["ink"])

    # Epoch timeline: one row per instrument
    inst = np.asarray(sel["instrument_name"], str)
    labels = sorted(set(zip(tel, inst)), key=lambda k: (k[0], k[1]))
    years = Time(0.5 * (np.asarray(sel["t_min"], float) + np.asarray(sel["t_max"], float)),
                 format="mjd").decimalyear
    for k, (t, i) in enumerate(labels):
        m = (tel == t) & (inst == i)
        axt.scatter(years[m], np.full(m.sum(), k), s=40, color=COLOR[t],
                    edgecolor=COLOR["surface"], lw=1.0, zorder=3)
    axt.axvline(GAIA_DR3_EPOCH, color=COLOR["ink2"], lw=1, ls=(0, (2, 2)), zorder=1)
    axt.text(GAIA_DR3_EPOCH, len(labels) - 0.4, " Gaia DR3 epoch", color=COLOR["ink2"],
             fontsize=8.5, va="bottom")
    axt.set_yticks(range(len(labels)), [f"{t} {i}" for t, i in labels])
    axt.set_ylim(-0.6, len(labels) - 0.1)
    axt.set_xlabel("Observation date (year)")
    axt.set_title("When each instrument observed the region", loc="left",
                  color=COLOR["ink"])
    axt.grid(axis="x", color="#e6e5e0", lw=0.6, zorder=0)
    axt.set_facecolor(COLOR["surface"])
    for s in ("top", "right"):
        axt.spines[s].set_visible(False)
        ax.spines[s].set_visible(False)

    fig.tight_layout(rect=(0, 0.08, 1, 1))
    fig.savefig(out_png, dpi=150, facecolor=COLOR["surface"])
    plt.close(fig)


# ── Summary ──────────────────────────────────────────────────────────────────

def pct(f: float) -> str:
    return "<1%" if 0 < f < 0.005 else f"{f:.0%}"


def write_summary(tgt, sel, planned, gaia, mq, sg, sdss, desi, best, region_cov, out_md: Path):
    if tgt.get("kind") == "region":
        lines = [f"# {tgt['name']}: HST/JWST imaging and Gaia in the region", "",
                 f"- Region centre: RA {tgt['ra']:.4f}, Dec {tgt['dec']:+.4f}; shape and source: "
                 f"{tgt['ref_structure']} (see the decisions below)", ""]
    else:
        lines = [f"# {tgt['name']}: HST/JWST imaging and Gaia within "
                 f"{N_RHALF:g} half-light radii", ""]
        lines += [f"- Center (LVDB): RA {tgt['ra']:.4f}, Dec {tgt['dec']:+.4f}; "
                  f"r_half = {tgt['rhalf_arcmin']:.2f}′ (semi-major), e = {tgt['ellipticity']:.2f}, "
                  f"PA = {tgt['pa_deg']:.0f}° (ref: {tgt['ref_structure']})",
                  f"- Region: ellipse with semi-major axis {N_RHALF * tgt['rhalf_arcmin']:.2f}′",
                  ""]
    tel = np.asarray(sel["obs_collection"], str)
    lines += ["## Imaging", "",
              "| Telescope | Instrument | Obs. | Filters | Dates | Programs (PI) | Region covered |",
              "|---|---|---|---|---|---|---|"]
    for (t, i), n in sorted(Counter(zip(tel, np.asarray(sel["instrument_name"], str))).items()):
        m = (tel == t) & (np.asarray(sel["instrument_name"], str) == i)
        s = sel[m]
        filt = sorted(set(";".join(np.asarray(s["filters"], str)).split(";")) - {""})
        d0 = Time(np.min(s["t_min"]), format="mjd").iso[:10]
        d1 = Time(np.max(s["t_max"]), format="mjd").iso[:10]
        progs = sorted({(str(p), str(pi)) for p, pi in zip(s["proposal_id"], s["proposal_pi"])},
                       key=lambda x: x[0])
        prog_txt = ", ".join(f"{p} ({pi.split(',')[0]})" for p, pi in progs[:6])
        if len(progs) > 6:
            prog_txt += f", +{len(progs) - 6} more"
        lines.append(f"| {t} | {i} | {n} | {', '.join(filt[:8])}{' …' if len(filt) > 8 else ''} "
                     f"| {d0} – {d1} | {prog_txt} | {pct(region_cov.get((t, i), 0))} |")
    lines += ["", "Region covered = fraction of the region inside the union of that "
              "instrument's footprints.", ""]
    lines += ["### Longest baselines between overlapping images", ""]
    for k, (dt, a, b) in sorted(best.items()):
        lines.append(f"- {k}: {dt:.1f} yr ({a} ↔ {b})")
    if not best:
        lines.append("- none (no overlapping pairs)")
    lines += ["", f"### Planned JWST observations: {len(planned)}", ""]
    for r in planned:
        lines.append(f"- program {r['proposal_id']} ({r['proposal_pi']}): "
                     f"{r['instrument_name']} {r['filters']}")
    qso, gal, crf = gaia["is_qso_cand"], gaia["is_gal_cand"], gaia["is_crf"]
    rrl = qso & gaia["is_rrlyrae"]
    inside = gaia["r_ell"] <= 1
    frac_in = (lambda m: f"{inside[m].mean():.0%}" if m.any() else "n/a")
    # Background objects are spread evenly, so ~1/N^2 of them fall inside 1 r_half.
    rest = qso & ~crf & ~gaia["in_quaia"] & ~rrl
    expected = 1 / N_RHALF**2
    cand_txt = (f"- Not shown: the Gaia QSO candidate table ({int(qso.sum())} sources here"
                + (f", {int(rrl.sum())} of them known Gaia RR Lyrae, i.e. member stars of "
                   f"{tgt['name']}" if rrl.any() else "") + "). ")
    if rest.any() and tgt.get("kind") != "region":
        f_rest = inside[rest].mean()
        verdict = ("so most are probably member stars too" if f_rest > 2 * expected
                   else "consistent with background objects")
        cand_txt += (f"Background objects should put ~{expected:.0%} inside 1 r_half; "
                     f"{f_rest:.0%} of the {int(rest.sum())} remaining candidates are there, "
                     f"{verdict}. Gaia-CRF3: {frac_in(crf)}.")
    lines += ["", "## Gaia DR3", "",
              f"- {len(gaia)} sources inside the region; {int(gaia['clean'].sum())} pass the "
              "standard astrometric quality flags (RUWE ≤ 1.4, ipd_gof_harmonic_amplitude ≤ 0.2, "
              "visibility_periods_used ≥ 9, astrometric_excess_noise_sig ≤ 2).",
              f"- Galaxy candidates: {int(gal.sum())}.", "",
              "## Spectra", "",
              "- SDSS (DR20 index): " + (", ".join(f"{k} {v}" for k, v in sorted(Counter(np.asarray(sdss["instrument"], str)).items())) or "none")
              + f" spectra; {int(gaia['has_sdss'].sum())} Gaia sources have one (matched by ID, or within 1″).",
              "- DESI DR1: " + (", ".join(f"{k} {v}" for k, v in sorted(Counter(np.asarray(desi["spectype"], str)).items())) or "none")
              + f"; {int(gaia['has_desi'].sum())} Gaia sources have one"
              + (f"; {int(np.isfinite(np.ma.filled(desi['rv_adop'], np.nan).astype(float)).sum())} DESI stars have Milky Way Survey velocities." if "rv_adop" in desi.colnames else "."),
              "",
              "## Galaxies: does Gaia flag them?", "",
              f"- SIMBAD lists {len(sg)} galaxies (not AGN) in the region; "
              f"{int(sg['gaia_match'].sum())} have a Gaia source within 2″.",
              f"- Of those, Gaia flagged {int(sg['gaia_gal_cand'].sum())} as galaxy candidates "
              f"and {int(sg['gaia_qso_cand'].sum())} as QSO candidates; "
              f"{int((sg['gaia_match'] & ~sg['gaia_gal_cand'] & ~sg['gaia_qso_cand']).sum())} "
              "are unflagged and would pass as stars.",
              f"- {int(sg['gaia_2p'].sum())} of the matched Gaia sources have only a 2-parameter "
              "solution (no parallax or proper motion), typical of extended sources.", "",
              "## QSOs", "",
              f"- Gaia-CRF3: {int(crf.sum())}; Quaia (G < 20.0): {int(gaia['in_quaia'].sum())}; "
              f"MILLIQUAS v8: {len(mq)}, {int(mq['confirmed'].sum())} confirmed (types: "
              f"{', '.join(f'{k} {v}' for k, v in sorted(Counter(np.asarray(mq['Type'], str)).items()))}"
              f"); {int(gaia['trusted_qso'].sum())} distinct Gaia sources in at least one.",
              f"- Astrometry: {int((gaia['trusted_qso'] & ~gaia['qso_astrometry_ok']).sum())} of "
              "these have parallax or proper motion inconsistent with zero at 3σ (circled on the "
              "map); don't use them as reference sources without a closer look.",
              cand_txt,
              "", "## Decisions", ""]
    lines += [f"- {d}" for d in DECISIONS]
    out_md.write_text("\n".join(lines) + "\n")


# ── Regions (boxes, polygons, bands along stream tracks) ────────────────────

def region_target(region, key: str) -> dict:
    """A region shaped like a target so the rest of the pipeline runs: its centre, and an
    r_half chosen so the viewer's field of view (2.6 r_half) spans the region."""
    _, _, r = region.bounding_cone()
    return {"key": key, "name": region.name, "ra": region.ra, "dec": region.dec,
            "rhalf_arcmin": max(r, 0.01) * 60 / 1.3, "ellipticity": 1e-6, "pa_deg": 0.0,
            "ref_structure": region.spec.get("source") or f"{region.kind} region", "distance_kpc": None,
            "pmra": None, "pmdec": None, "ref_proper_motion": None, "kind": "region"}


def region_outline(region):
    """An ellipse_outline replacement: the region outline as sky offsets (arcmin) about
    the centre (spherical offsets, valid at any size)."""
    def outline(tgt, n_rhalf, n=361):
        ring = np.array(region.outline())
        return tangent_offsets(SkyCoord(tgt["ra"], tgt["dec"], unit="deg"), ring[:, 0], ring[:, 1])
    return outline


def region_moc(region):
    from mocpy import MOC
    if region.kind == "cone":
        return MOC.from_cone(lon=region.ra * u.deg, lat=region.dec * u.deg, radius=Angle(region.radius * u.deg),
                             max_depth=MOC_DEPTH)
    return footprint_moc([np.array(p["polygon"]) for p in region.parts(pad_deg=0.0)])


def _pm_prefilter(region, part, cuts) -> str:
    """ADQL proper-motion circle around the track's motion in this part, wide enough to
    hold the stream-frame window (applied exactly afterwards)."""
    window = cuts.get("pm_window")
    pm = region.track_values("pm_track") if region.kind == "band" else None
    if not window or pm is None or "phi1" not in part:
        return ""
    phi1, _ = region.frame.to_frame(*np.array(region.spec["track"])[region._order].T)
    lo, hi = part["phi1"]
    sel = (phi1 >= lo - 1) & (phi1 <= hi + 1)
    sel = sel if sel.any() else np.ones(len(phi1), bool)
    centre = pm[sel].mean(axis=0)
    spread = float(np.max(np.hypot(*(pm[sel] - centre).T)))
    radius = float(np.hypot(*window)) + spread
    return (f" AND POWER(g.pmra - ({centre[0]:.4f}), 2) + POWER(g.pmdec - ({centre[1]:.4f}), 2) "
            f"< {radius ** 2:.4f}")


def gaia_region_where(region, part, cuts) -> str:
    where = adql_condition(part, "g.ra", "g.dec")
    if cuts.get("gmax") is not None:
        where += f" AND g.phot_g_mean_mag <= {float(cuts['gmax']):.3f}"
    return where + _pm_prefilter(region, part, cuts)


def count_region(region, cuts) -> dict:
    """Gaia rows the region would return, per part: all, and with the cuts (an upper
    bound for a stream-frame PM window, which is applied exactly only after the query)."""
    from concurrent.futures import ThreadPoolExecutor
    parts = region.parts()

    def count(where):
        res, _ = run_tap(f"SELECT COUNT(*) AS n FROM gaiadr3.gaia_source AS g WHERE {where}", "Gaia count")
        return int(res["n"][0])

    jobs = []
    for part in parts:
        jobs.append(adql_condition(part, "g.ra", "g.dec"))
        jobs.append(gaia_region_where(region, part, cuts))
    with ThreadPoolExecutor(max_workers=4) as pool:
        n = list(pool.map(count, jobs))
    return {"region": region.describe(), "cuts": cuts, "gaia_all": sum(n[0::2]), "gaia_with_cuts": sum(n[1::2]),
            "per_part": [{"cone": p["cone"], "all": a, "with_cuts": b} for p, a, b in zip(parts, n[0::2], n[1::2])],
            "note": "with_cuts is an upper bound when a stream-frame PM window is given: the query "
                    "uses a circle that contains the window, and the exact window is applied afterwards."}


def stream_pm_mask(gaia: Table, region, cuts) -> np.ndarray:
    """Stars whose proper motion, in the stream frame, is within the window of the track's."""
    window = cuts.get("pm_window")
    if not window or region.kind != "band" or region.track_values("pm_track") is None:
        return np.ones(len(gaia), bool)
    ra, dec = np.asarray(gaia["ra"], float), np.asarray(gaia["dec"], float)
    phi1, _ = region.frame.to_frame(ra, dec)
    p1, p2 = region.frame.pm_to_frame(ra, dec, np.ma.filled(gaia["pmra"], np.nan).astype(float),
                                      np.ma.filled(gaia["pmdec"], np.nan).astype(float))
    t1, t2 = region.pm_track_frame(phi1)
    return (np.abs(p1 - t1) <= window[0]) & (np.abs(p2 - t2) <= window[1])


def query_gaia_region(region, cuts, cache: Path) -> Table:
    from astropy.table import unique, vstack
    path = cache / "gaia.ecsv"
    if path.exists():
        return Table.read(path)
    tables = []
    for i, part in enumerate(region.parts()):
        where = gaia_region_where(region, part, cuts)
        src, server = run_tap(f"SELECT {GAIA_COLS} FROM gaiadr3.gaia_source AS g WHERE {where}",
                              f"gaia_source part {i + 1}")
        qso, _ = run_tap("SELECT q.source_id, q.gaia_crf_source, q.classlabel_dsc_joint "
                         "AS qso_classlabel_dsc_joint, q.redshift_qsoc FROM gaiadr3.qso_candidates "
                         f"AS q JOIN gaiadr3.gaia_source AS g ON g.source_id = q.source_id WHERE {where}",
                         "qso_candidates")
        gal, _ = run_tap("SELECT q.source_id, q.classlabel_dsc_joint AS gal_classlabel_dsc_joint "
                         "FROM gaiadr3.galaxy_candidates AS q JOIN gaiadr3.gaia_source AS g "
                         f"ON g.source_id = q.source_id WHERE {where}", "galaxy_candidates")
        part_t = join(join(src, qso, keys="source_id", join_type="left"), gal, keys="source_id", join_type="left")
        tables.append(part_t)
        if cuts.get("pm_window"):
            # A stream proper-motion cut removes every QSO (they don't move), so also take this
            # piece's Gaia-CRF3 sources, with the G limit but no motion cut, as references.
            g_cut = f" AND g.phot_g_mean_mag <= {float(cuts['gmax']):.3f}" if cuts.get("gmax") is not None else ""
            ref, _ = run_tap(f"SELECT {GAIA_COLS}, q.gaia_crf_source, q.classlabel_dsc_joint AS qso_classlabel_dsc_joint, "
                             "q.redshift_qsoc FROM gaiadr3.qso_candidates AS q JOIN gaiadr3.gaia_source AS g "
                             f"ON g.source_id = q.source_id WHERE {adql_condition(part, 'g.ra', 'g.dec')}{g_cut}",
                             f"Gaia-CRF3 part {i + 1}")
            tables.append(ref[as_bool(ref["gaia_crf_source"])])
    for t in tables:
        for c in t.colnames:
            if t[c].dtype == object:
                t[c] = t[c].astype(str)
    note(f"Queried Gaia DR3 from the {server} in {len(tables)} piece(s) of the region; results are "
         "identical to ESA's DR3.")
    out = unique(vstack(tables, metadata_conflicts="silent"), keys="source_id")  # pieces overlap at their edges
    cand = np.asarray(out["source_id"])[as_bool(out["in_qso_candidates"])]
    rr_ids = []
    for k in range(0, len(cand), 1000):
        ids = ",".join(str(i) for i in cand[k:k + 1000])
        res, _ = run_tap(f"SELECT source_id FROM gaiadr3.vari_rrlyrae WHERE source_id IN ({ids})", "vari_rrlyrae")
        rr_ids += list(res["source_id"])
    out["is_rrlyrae"] = np.isin(np.asarray(out["source_id"]), rr_ids)
    out.write(path, overwrite=True)
    return out


def near_gaia(t: Table, ra: str, dec: str, gaia: Table, radius_arcsec: float = 3.0) -> np.ndarray:
    """Rows that are one of these Gaia stars: the survey's own Gaia ID, or within radius."""
    if not len(t) or not len(gaia):
        return np.zeros(len(t), bool)
    ids = set(np.asarray(gaia["source_id"]).tolist())
    by_id = np.array([_int(x) in ids for x in t["survey_gaia_id"]], bool) if "survey_gaia_id" in t.colnames \
        else np.zeros(len(t), bool)
    _, sep, _ = SkyCoord(np.asarray(t[ra], float), np.asarray(t[dec], float), unit="deg").match_to_catalog_sky(
        SkyCoord(np.asarray(gaia["ra"], float), np.asarray(gaia["dec"], float), unit="deg"))
    return by_id | (sep < radius_arcsec * u.arcsec)


def _per_part(region, cache: Path, fetch, key: str):
    """Run a cone query (tgt, radius, cache) on small cones covering the region and merge
    the results by key (callers then keep what is inside the region)."""
    from astropy.table import unique, vstack
    results = []
    for i, (ra, dec, r) in enumerate(region.cover_cones()):
        sub = cache / f"cone{i + 1}"
        sub.mkdir(parents=True, exist_ok=True)
        results.append(fetch({"ra": ra, "dec": dec}, r, sub))
    if isinstance(results[0], tuple):
        merged = []
        for k, name in enumerate(key):
            ts = [res[k] for res in results if len(res[k])]
            merged.append(unique(vstack(ts, metadata_conflicts="silent"), keys=name) if ts else results[0][k])
        return tuple(merged)
    ts = [t for t in results if len(t)]
    return unique(vstack(ts, metadata_conflicts="silent"), keys=key) if ts else results[0]


# ── Main ─────────────────────────────────────────────────────────────────────

def cone_target(name: str, ra: float, dec: float, radius_arcmin: float, key: str) -> dict:
    """A circular region around any position, shaped like an LVDB target so the
    rest of the pipeline runs unchanged (r_half = radius, round, n_rhalf = 1)."""
    return {"key": key, "name": name, "ra": ra, "dec": dec, "rhalf_arcmin": radius_arcmin,
            # mocpy needs the minor axis strictly shorter; 1e-6 is round to < 1 µas on a degree.
            "ellipticity": 1e-6, "pa_deg": 0.0, "ref_structure": "cone search", "distance_kpc": None,
            "pmra": None, "pmdec": None, "ref_proper_motion": None, "kind": "cone"}


def main():
    global N_RHALF, ellipse_outline
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--out", required=True, type=Path)
    p.add_argument("--lvdb-key", default="draco_1")
    p.add_argument("--n-rhalf", type=float, default=2.0)
    p.add_argument("--ra", type=float, help="cone search instead of an LVDB object: center RA (deg)")
    p.add_argument("--dec", type=float, help="cone center Dec (deg)")
    p.add_argument("--radius-arcmin", type=float, help="cone radius (arcmin)")
    p.add_argument("--name", help="label for the cone or region (and output key)")
    p.add_argument("--region", type=Path,
                   help="JSON region spec (scoop/regions.py: box, polygon, band along a track), with "
                        "optional \"cuts\": {\"gmax\": G limit, \"pm_window\": [w_phi1, w_phi2] mas/yr "
                        "in the stream frame around the track's proper motion}")
    p.add_argument("--count-only", action="store_true",
                   help="with --region: print the Gaia row counts as JSON and stop")
    p.add_argument("--max-gaia-rows", type=int, default=60_000,
                   help="with --region: refuse to fetch more Gaia rows than this (count first)")
    p.add_argument("--show-qso-candidates", action="store_true",
                   help="also plot the (impure) Gaia DR3 QSO candidate table")
    p.add_argument("--serve", action="store_true",
                   help="serve the output folder locally and open the viewer in a browser")
    p.add_argument("--port", type=int, default=8765)
    args = p.parse_args()
    N_RHALF = args.n_rhalf
    cache = args.out / "cache"
    cache.mkdir(parents=True, exist_ok=True)

    qregion, cuts, extras = None, {}, set(OPTIONAL_ARCHIVES)
    if args.region is not None:
        spec, plan_name = read_spec(json.loads(args.region.read_text()))
        cuts = spec.pop("cuts", {}) or {}
        # Region searches fetch Gaia and SIMBAD galaxies; the rest only when asked for.
        extras = set(spec.get("archives") or []) | ({"hst_jwst"} if spec.get("hst_jwst") else set())
        unknown = extras - set(OPTIONAL_ARCHIVES)
        if unknown:
            raise SystemExit(f"unknown archives {sorted(unknown)}; optional ones: {OPTIONAL_ARCHIVES}")
        qregion = Region.from_spec(spec)
        if args.count_only:
            print("COUNTS " + json.dumps(count_region(qregion, cuts)))
            return
        key = re.sub(r"[^a-z0-9]+", "_", (args.name or qregion.name).lower()).strip("_")
        tgt = region_target(qregion, key)
        N_RHALF = 1.0
        ellipse_outline = region_outline(qregion)
        print(f"{tgt['name']}: {json.dumps(qregion.describe())}")
        d = qregion.describe()
        note(f"Searched a {qregion.kind} region ({d['area_deg2']:g} deg², {d['pieces']} piece(s) for the archive "
             f"queries)" + (f"; source: {spec.get('source')}" if spec.get("source") else "") + ".")
        skipped = [OPTIONAL_ARCHIVES[a] for a in OPTIONAL_ARCHIVES if a not in extras]
        note("Fetched Gaia DR3 and SIMBAD galaxies" + (f"; did not look up {', '.join(skipped)} (off by default for "
             "region searches; ask to include them)" if skipped else "") + ".")
        if cuts:
            note("Gaia cuts: " + ", ".join(
                [f"G ≤ {cuts['gmax']}"] * (cuts.get("gmax") is not None)
                + [f"proper motion within ±{cuts['pm_window'][0]} (φ1) and ±{cuts['pm_window'][1]} (φ2) mas/yr "
                   "of the track's, in the stream frame"] * bool(cuts.get("pm_window"))) + ".")
        counts = count_region(qregion, cuts)
        if counts["gaia_with_cuts"] > args.max_gaia_rows:
            raise SystemExit(f"Gaia would return up to {counts['gaia_with_cuts']} rows (limit "
                             f"{args.max_gaia_rows}); add cuts or shrink the region")
    elif args.ra is not None:
        if args.dec is None or not args.radius_arcmin:
            p.error("a cone needs --ra, --dec and --radius-arcmin")
        key = re.sub(r"[^a-z0-9]+", "_", (args.name or f"cone {args.ra:.4f} {args.dec:+.4f}").lower()).strip("_")
        tgt = cone_target(args.name or key, args.ra, args.dec, args.radius_arcmin, key)
        N_RHALF = 1.0
        print(f"{tgt['name']}: {json.dumps(tgt)}")
        note(f"Searched a circular cone of radius {args.radius_arcmin:g}′ around RA {args.ra:.5f}, "
             f"Dec {args.dec:+.5f} (not a Local Volume Database object).")
    else:
        tgt = load_lvdb_target(args.lvdb_key, cache)
        print(f"{tgt['name']}: {json.dumps(tgt)}")
        note(f"Took {tgt['name']}'s center and structure from the Local Volume Database "
             f"({LVDB_RELEASE}; structure ref {tgt['ref_structure']}); r_half is the semi-major axis.")
    a_deg = N_RHALF * tgt["rhalf_arcmin"] / 60
    query_radius = 1.02 * a_deg            # circle enclosing the ellipse

    if qregion is not None and "hst_jwst" not in extras:
        region = region_moc(qregion)
        obs, planned = empty_mast(), empty_mast()
        inside = lambda t, ra="ra", dec="dec": qregion.contains(np.asarray(t[ra], float), np.asarray(t[dec], float))  # noqa: E731
    elif qregion is not None:
        region = region_moc(qregion)
        obs, planned = _per_part(qregion, cache, query_mast, ("obsid", "obsid"))
        inside = lambda t, ra="ra", dec="dec": qregion.contains(np.asarray(t[ra], float), np.asarray(t[dec], float))  # noqa: E731
    else:
        region = ellipse_moc(tgt, N_RHALF)
        obs, planned = query_mast(tgt, query_radius, cache)
        inside = None
    sel, mocs, shapes = select_observations(obs, region, tgt)
    if len(planned):
        pm = [footprint_moc(parse_s_region(r["s_region"])) for r in planned]
        planned = planned[[m is not None and m.intersection(region).sky_fraction > 0
                           for m in pm]]
    if "hst_jwst" in extras:
        note(f"Checked for planned (not yet executed) JWST observations: {len(planned)} "
             f"overlap the region.")

    region_cov = {}
    tel, inst = np.asarray(sel["obs_collection"], str), np.asarray(sel["instrument_name"], str)
    for key in set(zip(tel, inst)):
        idx = np.flatnonzero((tel == key[0]) & (inst == key[1]))
        union = mocs[idx[0]]
        for k in idx[1:]:
            union = union.union(mocs[k])
        region_cov[key] = union.intersection(region).sky_fraction / region.sky_fraction
    best = baselines(sel, mocs)

    gaia = query_gaia_region(qregion, cuts, cache) if qregion is not None else query_gaia(tgt, query_radius, cache)
    center = SkyCoord(tgt["ra"], tgt["dec"], unit="deg")
    check_id_columns(gaia, "Gaia")
    gaia["xi"], gaia["eta"] = tangent_offsets(center, gaia["ra"], gaia["dec"])
    if qregion is not None:
        gaia["r_ell"] = np.full(len(gaia), np.nan)
        n0 = len(gaia)
        crf = as_bool(gaia["gaia_crf_source"]) & as_bool(gaia["in_qso_candidates"])
        gaia = gaia[inside(gaia) & (stream_pm_mask(gaia, qregion, cuts) | crf)]
        n_crf = int((as_bool(gaia["gaia_crf_source"]) & as_bool(gaia["in_qso_candidates"])).sum())
        note(f"Kept {len(gaia)} of {n0} Gaia sources inside the region"
             + (f" and the stream-frame proper-motion window, plus {n_crf} Gaia-CRF3 QSOs (kept whatever their "
                "motion, as zero-motion references)." if cuts.get("pm_window") else "."))
    else:
        gaia["r_ell"] = elliptical_radius(gaia["xi"], gaia["eta"], tgt)
        gaia = gaia[gaia["r_ell"] <= N_RHALF]
    gaia["is_qso_cand"] = as_bool(gaia["in_qso_candidates"])
    gaia["is_gal_cand"] = as_bool(gaia["in_galaxy_candidates"])
    gaia["is_crf"] = as_bool(gaia["gaia_crf_source"]) & gaia["is_qso_cand"]
    gaia["clean"] = ((np.ma.filled(gaia["ruwe"], 99) <= 1.4)
                     & (np.ma.filled(gaia["ipd_gof_harmonic_amplitude"], 99) <= 0.2)
                     & (np.ma.filled(gaia["visibility_periods_used"], 0) >= 9)
                     & (np.ma.filled(gaia["astrometric_excess_noise_sig"], 99) <= 2))
    note("Flagged (did not drop) Gaia sources failing the standard quality cuts.")
    gaia["is_rrlyrae"] = as_bool(gaia["is_rrlyrae"])

    # Trusted QSOs: Gaia-CRF3, Quaia (by source_id), MILLIQUAS (by position).
    if qregion is not None and "qso" not in extras:
        mq, quaia = _with_columns(Table(), "milliquas"), _with_columns(Table(), "quaia")
    elif qregion is not None:
        mq, quaia = _per_part(qregion, cache, query_qso_catalogs, ("Name", "GaiaDR3"))
    else:
        mq, quaia = query_qso_catalogs(tgt, query_radius, cache)
    mq["xi"], mq["eta"] = tangent_offsets(center, mq["RAJ2000"], mq["DEJ2000"])
    mq = mq[inside(mq, "RAJ2000", "DEJ2000")] if inside else mq[elliptical_radius(mq["xi"], mq["eta"], tgt) <= N_RHALF]
    # Class letters Q/A/B/K/N require a spectroscopic redshift; a bare R/X/2 (or S)
    # type is a radio/X-ray-associated candidate (ReadMe note 3).
    mq["confirmed"] = np.array([str(t)[:1] in "QABKN" and str(t)[:1] != ""
                                for t in mq["Type"]], bool)
    gaia["in_quaia"] = np.isin(np.asarray(gaia["source_id"]), np.asarray(quaia["GaiaDR3"]))
    gaia["in_milliquas"] = np.zeros(len(gaia), bool)
    if len(mq):
        idx, sep, _ = SkyCoord(mq["RAJ2000"], mq["DEJ2000"], unit="deg").match_to_catalog_sky(
            SkyCoord(gaia["ra"], gaia["dec"], unit="deg"))
        gaia["in_milliquas"][idx[sep < 1.5 * u.arcsec]] = True
    gaia["trusted_qso"] = gaia["is_crf"] | gaia["in_quaia"] | gaia["in_milliquas"]
    chi2 = sum((np.ma.filled(gaia[c], 0.0) / np.ma.filled(gaia[e], np.inf)) ** 2
               for c, e in (("parallax", "parallax_error"), ("pmra", "pmra_error"),
                            ("pmdec", "pmdec_error")))
    gaia["qso_astrometry_ok"] = chi2 < 14.16  # 3σ for 3 degrees of freedom
    if "qso" not in extras:
        note(f"Trusted QSOs: Gaia-CRF3 only ({int(gaia['is_crf'].sum())}, flagged in Gaia itself).")
    else:
        note(f"Showing trusted QSOs only: Gaia-CRF3 ({int(gaia['is_crf'].sum())}), Quaia "
             f"({int(gaia['in_quaia'].sum())}) and MILLIQUAS ({len(mq)}). Quaia comes from "
             f"VizieR, which has only its cleaner G < 20.0 sample.")
    sg = (_per_part(qregion, cache, query_simbad_galaxies, "main_id") if qregion is not None
          else query_simbad_galaxies(tgt, query_radius, cache))
    sg["xi"], sg["eta"] = tangent_offsets(center, sg["ra"], sg["dec"])
    sg = sg[inside(sg)] if inside else sg[elliptical_radius(sg["xi"], sg["eta"], tgt) <= N_RHALF]
    sg = match_gaia_to_galaxies(sg, gaia)
    note(f"Added {len(sg)} SIMBAD galaxies (not AGN) as an independent check on Gaia's "
         f"galaxy flag: {int(sg['gaia_match'].sum())} have a Gaia source within 2″, and Gaia "
         f"flagged {int(sg['gaia_gal_cand'].sum())} of those as galaxy candidates.")
    if qregion is not None and "spectra" not in extras:
        sdss = Table({c: np.array([], dtype=d) for c, d in SDSS_EMPTY_COLUMNS.items()})
        desi = Table({c: np.array([], dtype=d) for c, d in {**DESI_EMPTY_COLUMNS, **MATCH_COLUMNS}.items()})
    elif qregion is not None:
        big = qregion.area_deg2 > 20
        if big:
            note(f"The region is large ({qregion.area_deg2:.0f} deg²), so only DESI spectra classified as "
                 "stars are listed (galaxies and QSOs would dominate).")
        fetch = lambda t, r, c: query_spectra(t, r, c, stars_only=big)  # noqa: E731
        sdss, desi = _per_part(qregion, cache, fetch, ("allspec_id", ["targetid", "survey", "program"]))
        for t, ra, dec in ((sdss, "ra", "dec"), (desi, "mean_fiber_ra", "mean_fiber_dec")):
            t.remove_rows(np.flatnonzero(~inside(t, ra, dec)))
        if big:
            n_sdss, n_desi = len(sdss), len(desi)
            for t, ra, dec in ((sdss, "ra", "dec"), (desi, "mean_fiber_ra", "mean_fiber_dec")):
                t.remove_rows(np.flatnonzero(~near_gaia(t, ra, dec, gaia)))
            note(f"Kept only spectra of the {len(gaia)} Gaia stars in this field (by Gaia ID or within 3″): "
                 f"{len(sdss)} of {n_sdss} SDSS and {len(desi)} of {n_desi} DESI. Over a large region the "
                 "rest (mostly galaxies) would swamp the field.")
    else:
        sdss, desi = query_spectra(tgt, query_radius, cache)
        for t, ra, dec in ((sdss, "ra", "dec"), (desi, "mean_fiber_ra", "mean_fiber_dec")):
            xi_, eta_ = tangent_offsets(center, t[ra], t[dec])
            t.remove_rows(np.flatnonzero(elliptical_radius(xi_, eta_, tgt) > N_RHALF))
    check_id_columns(sdss, "SDSS spectra")
    check_id_columns(desi, "DESI spectra")
    check_id_columns(quaia, "Quaia")
    if len(sdss) or len(desi):
        match_spectra_to_gaia(gaia, sdss, desi, cache)
    else:  # nothing to match (and no 2MASS lookups for every Gaia source)
        gaia["has_sdss"] = np.zeros(len(gaia), bool)
        gaia["has_desi"] = np.zeros(len(gaia), bool)
    check_id_columns(sdss, "SDSS spectra (matched)")
    check_id_columns(desi, "DESI spectra (matched)")
    for name, t in (("SDSS", sdss), ("DESI", desi)) if "spectra" in extras else ():
        how = Counter(m for m in np.asarray(t["match_method"], str) if m)
        note(f"Matched {name} spectra to Gaia by " + (", ".join(f"{k} ({v})" for k, v in
             how.most_common()) or "nothing") + f"; {int((np.asarray(t['gaia_source_id']) == 0).sum())} "
             "have no Gaia match (often galaxies, or stars too faint for Gaia).")
    if "spectra" in extras:
        note(f"Looked up spectra in the region: {len(sdss)} SDSS spectra (DR20 index, SDSS-I to V "
             f"incl. APOGEE and MaNGA) and {len(desi)} DESI DR1 spectra; "
             f"{int(gaia['has_sdss'].sum())} and {int(gaia['has_desi'].sum())} Gaia sources have "
             f"a match. Click a source in the viewer to see its spectra.")
    note(f"Hid the Gaia QSO candidate table ({int(gaia['is_qso_cand'].sum())} sources here, "
         f"{int((gaia['is_qso_cand'] & gaia['is_rrlyrae']).sum())} of them known Gaia RR "
         f"Lyrae): it's too impure, especially in a dwarf galaxy. Use "
         f"--show-qso-candidates to show it.")

    stem = args.out / tgt["key"]
    keep_cols = [c for c in sel.colnames if c in (
        "obs_collection", "instrument_name", "filters", "obs_id", "target_name", "t_min",
        "t_max", "t_exptime", "proposal_id", "proposal_pi", "dataRights", "calib_level",
        "s_region", "frac_in_region", "obsid")]
    sel[keep_cols].write(f"{stem}_images.csv", overwrite=True)
    gaia.write(f"{stem}_gaia.csv", overwrite=True)
    mq.write(f"{stem}_milliquas.csv", overwrite=True)
    sg.write(f"{stem}_simbad_galaxies.csv", overwrite=True)
    make_plot(tgt, sel, shapes, planned, gaia, mq, sg, Path(f"{stem}_map.png"),
              show_qso_candidates=args.show_qso_candidates)
    sdss.write(f"{stem}_sdss_spectra.csv", overwrite=True)
    desi.write(f"{stem}_desi_spectra.csv", overwrite=True)
    write_summary(tgt, sel, planned, gaia, mq, sg, sdss, desi, best, region_cov,
                  Path(f"{stem}_summary.md"))
    from sky_viewer import write_viewer
    write_viewer(Path(f"{stem}_viewer.html"), tgt, N_RHALF, sel, planned, gaia, mq, sg,
                 sdss, desi, best,
                 region_cov, DECISIONS, ellipse_outline,
                 query_region=None if qregion is None else {**qregion.to_spec(), "cuts": cuts})
    print(f"Wrote {stem}_viewer.html, _map.png, _images.csv, _gaia.csv, _summary.md")
    if args.serve:
        serve(args.out, f"{tgt['key']}_viewer.html", args.port)


def serve(directory: Path, page: str, port: int) -> None:
    """Serve the output directory locally and open the viewer (Ctrl-C to stop).

    The viewer needs an http:// origin: some HiPS image servers reject file:// pages.
    """
    import functools
    import http.server
    import webbrowser
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(directory))
    with http.server.ThreadingHTTPServer(("127.0.0.1", port), handler) as httpd:
        url = f"http://127.0.0.1:{port}/{page}"
        print(f"Serving {url}  (Ctrl-C to stop)")
        webbrowser.open(url)
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            pass


N_RHALF = 2.0

if __name__ == "__main__":
    main()
