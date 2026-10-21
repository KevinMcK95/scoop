"""Proper-motion expectations, requirements and planning (deterministic).

Used by the proper-motion tools (scoop/chat/pm_tools.py) and the command line
(smoke_tests/pm_expectations.py); see skills/astrometry/proper_motions.md.

Conventions: proper motions in mas/yr (μα* = μα cos δ), velocities in km/s,
distances in kpc, epochs in decimal years. Uncertainties from the LVDB are
propagated by Monte Carlo (each side of an asymmetric error a half-Gaussian).
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import CartesianDifferential, Galactocentric, SkyCoord
from astropy.table import Table

from .lvdb import LVDB_BASE, LVDB_RELEASE

K = 4.740470463533348  # km/s per (kpc * mas/yr)

# Secular aberration from the Solar System's acceleration, measured from Gaia
# EDR3 QSO proper motions: Gaia Collaboration, Klioner et al. 2021, A&A 649, A9.
# Gaia's published proper motions are not corrected for it (Gaia-CRF3 paper,
# Gaia Collaboration, Klioner et al. 2022, A&A 667, A148). The full effect
# applies to extragalactic sources; for stars only the differential acceleration
# between the Sun and the star matters.
ABERRATION_UAS_YR = 5.05          # ± 0.35 µas/yr
ABERRATION_RA_DEC = (269.1, -31.6)  # direction of the acceleration, deg (± 5.4, ± 4.1)



# ── Conversions ──────────────────────────────────────────────────────────────

def pm_from_velocity(v_kms, d_kpc):
    """Proper motion (mas/yr) of a tangential velocity v (km/s) at distance d (kpc)."""
    return np.asarray(v_kms) / (K * np.asarray(d_kpc))


def velocity_from_pm(mu_masyr, d_kpc):
    """Tangential velocity (km/s) of a proper motion mu (mas/yr) at distance d (kpc)."""
    return K * np.asarray(mu_masyr) * np.asarray(d_kpc)


# ── Uncertainty draws ────────────────────────────────────────────────────────

def draw(value, em, ep, n, rng):
    """Samples from a split normal with lower/upper 1σ errors em/ep."""
    em = 0.0 if em is None or not np.isfinite(em) else em
    ep = em if ep is None or not np.isfinite(ep) else ep
    z = rng.standard_normal(n)
    return value + np.where(z < 0, z * em, z * ep)


def summary(samples) -> tuple[float, float, float]:
    """(median, lower error, upper error) from the 16/50/84th percentiles."""
    p16, p50, p84 = np.percentile(samples, [16, 50, 84])
    return float(p50), float(p50 - p16), float(p84 - p50)


# ── Expectations ─────────────────────────────────────────────────────────────

def internal_pm_dispersion(sigma_los, d_kpc):
    """Internal proper-motion dispersion (mas/yr), assuming isotropic velocities."""
    return pm_from_velocity(sigma_los, d_kpc)


def reflex_pm(ra, dec, d_kpc, vlos=0.0):
    """Proper motion (mas/yr) of an object at rest in the Galactocentric frame:
    pure solar reflex motion, using astropy's default Galactocentric parameters."""
    gc = Galactocentric()
    c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, distance=d_kpc * u.kpc)
    xyz = c.transform_to(gc).cartesian
    at_rest = SkyCoord(xyz.with_differentials(CartesianDifferential([0, 0, 0] * u.km / u.s)),
                       frame=gc).icrs
    return at_rest.pm_ra_cosdec.to_value(u.mas / u.yr), at_rest.pm_dec.to_value(u.mas / u.yr)


def _east_north(ra, dec):
    """Unit vectors toward east and north at (ra, dec) [deg], in ICRS Cartesian."""
    a, d = np.deg2rad(ra), np.deg2rad(dec)
    east = np.stack([-np.sin(a), np.cos(a), np.zeros_like(a)])
    north = np.stack([-np.sin(d) * np.cos(a), -np.sin(d) * np.sin(a), np.cos(d)])
    return east, north


def secular_aberration_pm(ra, dec, kappa=ABERRATION_UAS_YR, direction=ABERRATION_RA_DEC):
    """Apparent proper motion (µas/yr) from secular aberration: κ times the part of
    the acceleration direction perpendicular to the line of sight. It is the same
    for every object in a given direction, whatever its distance."""
    ga, gd = np.deg2rad(direction)
    g = np.array([np.cos(gd) * np.cos(ga), np.cos(gd) * np.sin(ga), np.sin(gd)])
    east, north = _east_north(np.asarray(ra, float), np.asarray(dec, float))
    return kappa * np.tensordot(g, east, 1), kappa * np.tensordot(g, north, 1)


def _region_points(ra, dec, radius_deg, n):
    """An n x n grid of offsets across a circular region, keeping the points inside it (ICRS)."""
    center = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    off = np.linspace(-radius_deg, radius_deg, n)
    xx, yy = np.meshgrid(off, off)
    inside = np.hypot(xx, yy) <= radius_deg
    return SkyCoord(lon=xx[inside] * u.deg, lat=yy[inside] * u.deg, frame=center.skyoffset_frame()).icrs


def perspective_change(ra, dec, radius_deg, d_kpc, pmra, pmdec, vlos, n=9):
    """Largest change (mas/yr) in the apparent proper motion across a region for
    points sharing the target's 3D velocity (perspective effect of its systemic
    motion; includes the solar-reflex part)."""
    c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, distance=d_kpc * u.kpc,
                 pm_ra_cosdec=pmra * u.mas / u.yr, pm_dec=pmdec * u.mas / u.yr,
                 radial_velocity=vlos * u.km / u.s)
    gc = Galactocentric()
    v = c.transform_to(gc).velocity
    pts = _region_points(ra, dec, radius_deg, n)
    xyz = SkyCoord(ra=pts.ra, dec=pts.dec, distance=d_kpc * u.kpc).transform_to(gc).cartesian
    npts = len(pts)
    same_v = CartesianDifferential(np.full(npts, v.d_x.value) * v.d_x.unit,
                                   np.full(npts, v.d_y.value) * v.d_y.unit,
                                   np.full(npts, v.d_z.value) * v.d_z.unit)
    moving = SkyCoord(xyz.with_differentials(same_v), frame=gc).icrs
    dx = moving.pm_ra_cosdec.to_value(u.mas / u.yr) - pmra
    dy = moving.pm_dec.to_value(u.mas / u.yr) - pmdec
    return float(np.max(np.hypot(dx, dy)))


def region_variation(ra, dec, radius_deg, d_kpc, n=9):
    """How solar reflex (for objects at distance d) and secular aberration change
    across a circular region: values at the center and the largest difference
    from the center over a grid inside the region."""
    pts = _region_points(ra, dec, radius_deg, n)
    rx, ry = np.array([reflex_pm(a, b, d_kpc) for a, b in zip(pts.ra.deg, pts.dec.deg)]).T
    ax, ay = secular_aberration_pm(pts.ra.deg, pts.dec.deg)
    cx, cy = reflex_pm(ra, dec, d_kpc)
    bx, by = secular_aberration_pm(ra, dec)
    return {"reflex_center": (cx, cy),
            "reflex_max_change": float(np.max(np.hypot(rx - cx, ry - cy))),
            "aberration_center": (float(bx), float(by)),
            "aberration_max_change": float(np.max(np.hypot(ax - bx, ay - by)))}


def galactocentric_velocity(ra, dec, d_kpc, pmra, pmdec, vlos):
    """Galactocentric velocity (km/s): total speed, radial and tangential parts."""
    c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, distance=d_kpc * u.kpc,
                 pm_ra_cosdec=pmra * u.mas / u.yr, pm_dec=pmdec * u.mas / u.yr,
                 radial_velocity=vlos * u.km / u.s).transform_to(Galactocentric())
    pos = np.array([c.x.to_value(u.kpc), c.y.to_value(u.kpc), c.z.to_value(u.kpc)])
    vel = np.array([c.v_x.to_value(u.km / u.s), c.v_y.to_value(u.km / u.s),
                    c.v_z.to_value(u.km / u.s)])
    r = np.linalg.norm(pos, axis=0)
    v_rad = np.sum(pos * vel, axis=0) / r
    v_tot = np.linalg.norm(vel, axis=0)
    return v_tot, v_rad, np.sqrt(np.maximum(v_tot**2 - v_rad**2, 0)), r


# ── Requirements for a measurement ───────────────────────────────────────────

def bulk_pm_error(sigma_int, star_error, n_stars, floor=0.0):
    """Error on a mean proper motion (per component) from n stars, plus a floor."""
    return np.sqrt((sigma_int**2 + star_error**2) / n_stars + floor**2)


def dispersion_error(sigma_int, star_error, n_stars):
    """Error on an internal dispersion measured from n stars with per-star error e
    (from Var(s^2) = 2 (σ² + e²)² / n for a Gaussian, σ² = s² − e²)."""
    return (sigma_int**2 + star_error**2) / (sigma_int * np.sqrt(2 * n_stars))


def star_error_for_dispersion(sigma_int, n_stars, frac=0.3):
    """Largest per-star error that still measures the dispersion to `frac`
    (fractional error) with n stars; NaN if n is too small at any precision."""
    x = frac * np.sqrt(2 * n_stars) - 1
    return sigma_int * np.sqrt(x) if x > 0 else np.nan




# ── Planning: proper-motion errors from epochs ───────────────────────────────

def pm_error_from_epochs(epochs) -> float:
    """Per-component proper-motion error (mas/yr) from positions at several epochs:
    the error of a weighted least-squares slope, 1 / sqrt(Σ w (t - t̄)²) with
    w = 1/σ² and t̄ the weighted mean epoch. epochs: [(year, position error in mas)].
    Two equal epochs give √2 σ / Δt. Infinite with fewer than two distinct epochs."""
    t = np.array([e[0] for e in epochs], float)
    s = np.array([e[1] for e in epochs], float)
    if len(t) < 2 or np.any(s <= 0):
        return math.inf
    w = 1.0 / s**2
    tbar = np.sum(w * t) / np.sum(w)
    spread = float(np.sum(w * (t - tbar) ** 2))
    return 1.0 / math.sqrt(spread) if spread > 0 else math.inf


def combine_errors(*errors) -> float:
    """Independent measurements of one quantity combined: 1 / sqrt(Σ 1/e²)."""
    good = [e for e in errors if e is not None and math.isfinite(e) and e > 0]
    return 1.0 / math.sqrt(sum(1.0 / e**2 for e in good)) if good else math.inf


def star_error_for_bulk(target, sigma_int, n_stars, floor=0.0) -> float:
    """Largest per-star error that still gives a bulk (mean) proper-motion error of
    `target` from n stars with internal dispersion sigma_int and a systematic floor;
    the inverse of bulk_pm_error. NaN if the target is unreachable (floor or too few stars)."""
    x = n_stars * (target**2 - floor**2) - sigma_int**2
    return math.sqrt(x) if x > 0 and target > floor else math.nan


def year_to_reach(existing_epochs, new_position_error, needed_pm_error, *, start_year,
                  gaia_pm_error=None, horizon_years=100.0):
    """Earliest year for one new epoch so the per-star proper-motion error (all
    epochs, optionally combined with Gaia's own proper motion) reaches
    needed_pm_error. Returns (year, error_then) or (None, best_error_within_horizon)."""
    if not needed_pm_error or not math.isfinite(needed_pm_error) or needed_pm_error <= 0:
        return None, math.inf

    def error_at(year):
        return combine_errors(pm_error_from_epochs([*existing_epochs, (year, new_position_error)]),
                              gaia_pm_error)

    lo, hi = start_year, start_year + horizon_years
    if error_at(lo) <= needed_pm_error:
        return lo, error_at(lo)
    if error_at(hi) > needed_pm_error:
        return None, error_at(hi)
    for _ in range(60):  # the error falls as the new epoch moves later
        mid = 0.5 * (lo + hi)
        lo, hi = (lo, mid) if error_at(mid) <= needed_pm_error else (mid, hi)
    return hi, error_at(hi)


# ── Published proper motions ─────────────────────────────────────────────────

METHOD_GROUPS = {"GAIA_EDR3": "Gaia only", "GAIA_DR2": "Gaia only", "HST": "HST only",
                 "GAIA_EDR3_HST": "HST + Gaia", "GAIA_DR2_HST": "HST + Gaia", "GaiaHub": "HST + Gaia",
                 "Ground": "ground-based", "VVV": "ground-based", "HST_Ground": "ground-based",
                 "VLBI": "radio", "maser": "radio"}


def _sym(em, ep) -> float:
    vals = [float(v) for v in (em, ep) if v is not None and np.isfinite(float(v)) and float(v) > 0]
    return sum(vals) / len(vals) if vals else math.nan


RECONCILE_NOTES = (
    "The chi-squared assumes independent measurements with complete errors. Many published "
    "proper motions reuse the same Gaia data (so they are not independent), and many quote "
    "statistical errors only (Gaia DR2-era values have larger known systematics), so treat "
    "it as a flag for tension to investigate, not a formal test. Use floor_mas_yr to add a "
    "systematic floor to every error and see how much tension remains.",
    "Values from the LVDB's pm_overview are as published (heliocentric, ICRS). Absolute "
    "motions tied to QSOs/galaxies at rest and Gaia-only motions differ in how secular "
    "aberration (~5 µas/yr) is handled; differences that small may come from that.",
)


def reconcile(measurements: list[dict], floor: float = 0.0) -> dict:
    """Agreement of published proper motions of one object (per component): the
    weighted mean, χ² about it, its p-value, and measurements more than 3σ away.
    measurements: dicts with pmra, pmra_err, pmdec, pmdec_err (symmetric), citation.
    ``floor`` (mas/yr) is added in quadrature to every error."""
    out = {"n": len(measurements), "floor_mas_yr": floor, "notes": list(RECONCILE_NOTES),
           "by_method": {g: sum(1 for m in measurements if m.get("group", "other") == g)
                         for g in sorted({m.get("group", "other") for m in measurements})}}
    for comp in ("pmra", "pmdec"):
        rows = [m for m in measurements if np.isfinite(m.get(comp, np.nan)) and np.isfinite(m.get(f"{comp}_err", np.nan))
                and m[f"{comp}_err"] > 0]
        if len(rows) < 2:
            out[comp] = {"n": len(rows)}
            continue
        x = np.array([m[comp] for m in rows])
        s = np.sqrt(np.array([m[f"{comp}_err"] for m in rows]) ** 2 + floor**2)
        w = 1 / s**2
        mean = float(np.sum(w * x) / np.sum(w))
        chi2 = float(np.sum(((x - mean) / s) ** 2))
        dof = len(rows) - 1
        try:
            from scipy.stats import chi2 as chi2_dist
            p = float(chi2_dist.sf(chi2, dof))
        except ImportError:
            p = None
        pulls = (x - mean) / s
        out[comp] = {"n": len(rows), "weighted_mean": mean, "weighted_mean_err": float(1 / math.sqrt(np.sum(w))),
                     "chi2": chi2, "dof": dof, "p_value": p,
                     "outliers_3sigma": [{"citation": m["citation"], "method": m.get("method"), "value": m[comp],
                                          "err": m[f"{comp}_err"], "pull": round(float(q), 1)}
                                         for m, q in zip(rows, pulls) if abs(q) > 3]}
    return out


# ── LVDB ─────────────────────────────────────────────────────────────────────

def load_lvdb(cache: Path):
    """The LVDB's combined table and its per-paper proper motions (pm_overview),
    downloaded once into `cache` (small reference tables, ~1.2 MB together)."""
    cache = Path(cache)
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ("comb_all.ecsv", "pm_overview.csv"):
        p = cache / f"lvdb_{LVDB_RELEASE}_{name}"
        if not p.exists():
            import urllib.request
            urllib.request.urlretrieve(LVDB_BASE + name, p)
        out.append(Table.read(p, format="ascii.csv") if name.endswith(".csv") else Table.read(p))
    return out


def published(pms: Table, key: str) -> list[dict]:
    """The LVDB's published proper motions of one object, one dict per paper."""
    sub = pms[np.asarray(pms["key"], str) == key]
    num = lambda v: float(np.ma.filled(v, np.nan))  # noqa: E731
    rows = []
    for r in sub:
        rows.append({"citation": str(r["citation"]), "bibcode": str(r["ref"]), "method": str(r["method"]),
                     "group": METHOD_GROUPS.get(str(r["method"]), "other"),
                     "pmra": num(r["pmra"]), "pmra_err": _sym(num(r["pmra_em"]), num(r["pmra_ep"])),
                     "pmdec": num(r["pmdec"]), "pmdec_err": _sym(num(r["pmdec_em"]), num(r["pmdec_ep"])),
                     "comment": None if str(r["comments"]) in ("--", "") else str(r["comments"])})
    return rows


def lvdb_row(comb: Table, key: str) -> dict | None:
    sub = comb[comb["key"] == key]
    if not len(sub):
        return None
    r = sub[0]
    num = lambda v: float(np.ma.filled(v, np.nan))  # noqa: E731
    cols = ("ra", "dec", "distance", "distance_em", "distance_ep", "vlos_sigma", "vlos_sigma_em", "vlos_sigma_ep",
            "pmra", "pmra_em", "pmra_ep", "pmdec", "pmdec_em", "pmdec_ep", "vlos_systemic", "vlos_systemic_em",
            "vlos_systemic_ep", "rhalf")
    return {"key": key, "name": str(r["name"]), **{c: num(r[c]) for c in cols},
            "ref_vlos": str(r["ref_vlos"]), "ref_proper_motion": str(r["ref_proper_motion"])}


def expectations(row: dict, *, n_stars: int = 200, star_error: float = 0.03, floor: float = 0.0,
                 radius_deg: float | None = None, published_rows: list[dict] | None = None,
                 n_mc: int = 20000) -> dict:
    """Everything the proper-motion skill's "what to expect" section asks for, for one
    LVDB object: sizes, internal dispersion, reflex/perspective/aberration across the
    region, Galactocentric velocity, the best published error, and what a sample of
    n_stars at star_error per star (mas/yr) and a floor would measure."""
    rng = np.random.default_rng(1)
    d = draw(row["distance"], row["distance_em"], row["distance_ep"], n_mc, rng)
    out = {"object": row["name"], "key": row["key"], "lvdb_release": LVDB_RELEASE,
           "distance_kpc": row["distance"], "sigma_los_kms": row["vlos_sigma"], "ref_vlos": row["ref_vlos"],
           "adopted_pm": [row["pmra"], row["pmdec"]], "ref_proper_motion": row["ref_proper_motion"]}
    s_mu = None
    if np.isfinite(row["vlos_sigma"]):
        sig = draw(row["vlos_sigma"], row["vlos_sigma_em"], row["vlos_sigma_ep"], n_mc, rng)
        s_mu = internal_pm_dispersion(sig, d)
        m, lo, hi = summary(s_mu)
        out["internal_dispersion_uas_yr"] = [round(m * 1e3, 2), round(lo * 1e3, 2), round(hi * 1e3, 2)]
    rx, ry = reflex_pm(row["ra"], row["dec"], row["distance"])
    out["solar_reflex_mas_yr"] = [round(float(rx), 4), round(float(ry), 4)]
    out["pm_of_tangential_velocity_mas_yr"] = {v: round(float(pm_from_velocity(v, row["distance"])), 4)
                                               for v in (50, 100, 200)}
    rad = radius_deg if radius_deg else 2 * row["rhalf"] / 60
    rv = region_variation(row["ra"], row["dec"], rad, row["distance"])
    bx, by = rv["aberration_center"]
    out["across_region"] = {"radius_deg": round(rad, 4),
                            "reflex_change_uas_yr": round(rv["reflex_max_change"] * 1e3, 2),
                            "aberration_uas_yr": [round(bx, 2), round(by, 2)],
                            "aberration_change_uas_yr": round(rv["aberration_max_change"], 3)}
    if np.isfinite(row["pmra"]) and np.isfinite(row["vlos_systemic"]):
        out["across_region"]["perspective_change_uas_yr"] = round(perspective_change(
            row["ra"], row["dec"], rad, row["distance"], row["pmra"], row["pmdec"], row["vlos_systemic"]) * 1e3, 2)
        pmra = draw(row["pmra"], row["pmra_em"], row["pmra_ep"], n_mc, rng)
        pmdec = draw(row["pmdec"], row["pmdec_em"], row["pmdec_ep"], n_mc, rng)
        vlos = draw(row["vlos_systemic"], row["vlos_systemic_em"], row["vlos_systemic_ep"], n_mc, rng)
        vt, vr, vtan, rg = galactocentric_velocity(row["ra"], row["dec"], d, pmra, pmdec, vlos)
        out["galactocentric_kms"] = {name: [round(v, 1) for v in summary(x)]
                                     for name, x in (("total", vt), ("radial", vr), ("tangential", vtan))}
    if published_rows:
        errs = [math.hypot(m["pmra_err"], m["pmdec_err"]) / math.sqrt(2) for m in published_rows]
        k = int(np.nanargmin(errs))
        out["best_published"] = {"citation": published_rows[k]["citation"], "method": published_rows[k]["method"],
                                 "error_uas_yr": round(errs[k] * 1e3, 2),
                                 "error_kms": round(float(velocity_from_pm(errs[k], row["distance"])), 2),
                                 "n_measurements": len(published_rows)}
        if errs[k] < 3 * math.hypot(bx, by) / 1e3:
            out["best_published"]["note"] = ("within 3x of the secular aberration here: say whether each "
                                             "measurement accounts for it")
    if s_mu is not None:
        s0 = float(np.median(s_mu))
        be = bulk_pm_error(s0, star_error, n_stars, floor)
        de = dispersion_error(s0, star_error, n_stars)
        out["sample"] = {"n_stars": n_stars, "star_error_mas_yr": star_error, "floor_mas_yr": floor,
                         "bulk_error_uas_yr": round(float(be) * 1e3, 2),
                         "bulk_error_kms": round(float(velocity_from_pm(be, row["distance"])), 2),
                         "dispersion_fractional_error": round(float(de / s0), 3),
                         "star_error_for_dispersion_30pc_uas_yr": _round_uas(star_error_for_dispersion(s0, n_stars, 0.3)),
                         "star_error_for_dispersion_10pc_uas_yr": _round_uas(star_error_for_dispersion(s0, n_stars, 0.1))}
    return out


def _round_uas(v) -> float | None:
    return round(float(v) * 1e3, 2) if v is not None and math.isfinite(v) else None


__all__ = ["K", "bulk_pm_error", "combine_errors", "dispersion_error", "expectations",
           "galactocentric_velocity", "internal_pm_dispersion", "load_lvdb", "lvdb_row", "perspective_change",
           "pm_error_from_epochs", "pm_from_velocity", "published", "reconcile", "reflex_pm", "region_variation",
           "secular_aberration_pm", "star_error_for_bulk", "star_error_for_dispersion", "velocity_from_pm",
           "year_to_reach"]
