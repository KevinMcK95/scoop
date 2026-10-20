"""Physical expectations for proper motions: sizes, internal dispersions, and the
precision a measurement needs. Deterministic tools for the proper-motion agent
(skills/astrometry/proper_motions.md).

    python smoke_tests/pm_expectations.py --lvdb-key draco_1 [--n-stars 100] [--star-error 0.05]

Conventions: proper motions in mas/yr (μα* = μα cos δ), velocities in km/s,
distances in kpc. Uncertainties are propagated by Monte Carlo from the LVDB's
asymmetric errors (each side drawn as a half-Gaussian).
"""

from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import CartesianDifferential, Galactocentric, SkyCoord
from astropy.table import Table

K = 4.740470463533348  # km/s per (kpc * mas/yr)

# Secular aberration from the Solar System's acceleration, measured from Gaia
# EDR3 QSO proper motions: Gaia Collaboration, Klioner et al. 2021, A&A 649, A9.
# Gaia's published proper motions are not corrected for it (Gaia-CRF3 paper,
# Gaia Collaboration, Klioner et al. 2022, A&A 667, A148). The full effect
# applies to extragalactic sources; for stars only the differential acceleration
# between the Sun and the star matters.
ABERRATION_UAS_YR = 5.05          # ± 0.35 µas/yr
ABERRATION_RA_DEC = (269.1, -31.6)  # direction of the acceleration, deg (± 5.4, ± 4.1)

LVDB_BASE = "https://github.com/apace7/local_volume_database/releases/download/v1.1.1/"


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


def perspective_change(ra, dec, radius_deg, d_kpc, pmra, pmdec, vlos, n=9):
    """Largest change (mas/yr) in the apparent proper motion across a region for
    points sharing the target's 3D velocity (perspective effect of its systemic
    motion; includes the solar-reflex part)."""
    c = SkyCoord(ra=ra * u.deg, dec=dec * u.deg, distance=d_kpc * u.kpc,
                 pm_ra_cosdec=pmra * u.mas / u.yr, pm_dec=pmdec * u.mas / u.yr,
                 radial_velocity=vlos * u.km / u.s)
    gc = Galactocentric()
    v = c.transform_to(gc).velocity
    center = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    off = np.linspace(-radius_deg, radius_deg, n)
    xx, yy = np.meshgrid(off, off)
    inside = np.hypot(xx, yy) <= radius_deg
    pts = SkyCoord(lon=xx[inside] * u.deg, lat=yy[inside] * u.deg,
                   frame=center.skyoffset_frame()).icrs
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
    center = SkyCoord(ra=ra * u.deg, dec=dec * u.deg)
    off = np.linspace(-radius_deg, radius_deg, n)
    xx, yy = np.meshgrid(off, off)
    inside = np.hypot(xx, yy) <= radius_deg
    pts = SkyCoord(lon=xx[inside] * u.deg, lat=yy[inside] * u.deg,
                   frame=center.skyoffset_frame()).icrs
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


# ── LVDB example ─────────────────────────────────────────────────────────────

def load_lvdb(cache: Path):
    cache.mkdir(parents=True, exist_ok=True)
    out = []
    for name in ("comb_all.ecsv", "pm_overview.csv"):
        p = cache / f"lvdb_v1.1.1_{name}"
        if not p.exists():
            import urllib.request
            urllib.request.urlretrieve(LVDB_BASE + name, p)
        out.append(Table.read(p, format="ascii.csv") if name.endswith(".csv") else Table.read(p))
    return out


def report(key: str, n_stars: int, star_error: float, floor: float, cache: Path, n_mc=20000,
           radius_deg: float | None = None):
    comb, pms = load_lvdb(cache)
    r = comb[comb["key"] == key][0]
    rng = np.random.default_rng(1)
    f = lambda c: float(np.ma.filled(r[c], np.nan))
    d = draw(f("distance"), f("distance_em"), f("distance_ep"), n_mc, rng)
    sig = draw(f("vlos_sigma"), f("vlos_sigma_em"), f("vlos_sigma_ep"), n_mc, rng)
    pmra = draw(f("pmra"), f("pmra_em"), f("pmra_ep"), n_mc, rng)
    pmdec = draw(f("pmdec"), f("pmdec_em"), f("pmdec_ep"), n_mc, rng)
    vlos = draw(f("vlos_systemic"), f("vlos_systemic_em"), f("vlos_systemic_ep"), n_mc, rng)

    print(f"== {r['name']} ({key}); LVDB v1.1.1")
    print(f"distance {f('distance'):.2f} kpc; σ_los {f('vlos_sigma'):.2f} km/s "
          f"({r['ref_vlos']}); adopted PM ({f('pmra'):.3f}, {f('pmdec'):.3f}) mas/yr "
          f"({r['ref_proper_motion']})")

    s_mu = internal_pm_dispersion(sig, d)
    m, lo, hi = summary(s_mu)
    print(f"\nExpected internal PM dispersion (isotropic): {m * 1e3:.1f} -{lo * 1e3:.1f} "
          f"+{hi * 1e3:.1f} µas/yr per component")

    rx, ry = reflex_pm(f("ra"), f("dec"), f("distance"))
    print(f"Solar reflex alone (object at rest in the Galactocentric frame): "
          f"({rx:.3f}, {ry:.3f}) mas/yr")
    for v in (50, 100, 200):
        print(f"  a {v} km/s tangential velocity at this distance: "
              f"{pm_from_velocity(v, f('distance')):.3f} mas/yr")

    rad = radius_deg if radius_deg else 2 * f("rhalf") / 60   # default: 2 r_half (semi-major)
    rv = region_variation(f("ra"), f("dec"), rad, f("distance"))
    bx, by = rv["aberration_center"]
    print(f"Across the region (radius {rad:.2f} deg):")
    print(f"  solar reflex changes by up to {rv['reflex_max_change'] * 1e3:.1f} µas/yr "
          f"(for objects at {f('distance'):.0f} kpc; nearer foreground stars differ more)")
    print(f"  secular aberration (in Gaia PMs; full size for QSOs): ({bx:.2f}, {by:.2f}) µas/yr at the center "
          f"(|{np.hypot(bx, by):.2f}| µas/yr), changing by up to "
          f"{rv['aberration_max_change']:.3f} µas/yr across the region")
    pc = perspective_change(f("ra"), f("dec"), rad, f("distance"), f("pmra"), f("pmdec"),
                            f("vlos_systemic"))
    print(f"  perspective effect of the systemic motion (adopted PM and v_los): the apparent "
          f"PM changes by up to {pc * 1e3:.1f} µas/yr across the region")

    vt, vr, vtan, rg = galactocentric_velocity(f("ra"), f("dec"), d, pmra, pmdec, vlos)
    for name, x in (("total", vt), ("radial", vr), ("tangential", vtan)):
        m, lo, hi = summary(x)
        print(f"Galactocentric {name} velocity from the adopted PM: {m:.0f} -{lo:.0f} +{hi:.0f} km/s")
    print(f"  (Galactocentric distance {np.median(rg):.1f} kpc; astropy default frame)")

    sub = pms[np.asarray(pms["key"], str) == key]
    if len(sub):
        err = np.hypot(0.5 * (np.asarray(sub["pmra_em"], float) + np.asarray(sub["pmra_ep"], float)),
                       0.5 * (np.asarray(sub["pmdec_em"], float) + np.asarray(sub["pmdec_ep"], float))) / np.sqrt(2)
        k = int(np.nanargmin(err))
        print(f"\nPublished measurements: {len(sub)}; smallest error {err[k] * 1e3:.1f} µas/yr "
              f"per component ({sub['citation'][k]}, {sub['method'][k]}), i.e. "
              f"{velocity_from_pm(err[k], f('distance')):.1f} km/s")

    s0 = float(np.median(s_mu))
    ab = float(np.hypot(bx, by)) / 1e3
    if len(sub) and err[k] < 3 * ab:
        print(f"  NOTE: the best published error ({err[k] * 1e3:.1f} µas/yr) is within 3x of the "
              f"secular aberration ({ab * 1e3:.1f} µas/yr): say whether each measurement "
              f"accounts for it (e.g. tied to QSOs assumed at rest, or corrected).")

    print(f"\nWith {n_stars} member stars at {star_error * 1e3:.0f} µas/yr each "
          f"(floor {floor * 1e3:.0f} µas/yr):")
    be = bulk_pm_error(s0, star_error, n_stars, floor)
    print(f"  bulk PM error {be * 1e3:.1f} µas/yr = {velocity_from_pm(be, f('distance')):.1f} km/s")
    de = dispersion_error(s0, star_error, n_stars)
    print(f"  internal dispersion measured to ±{de * 1e3:.1f} µas/yr ({de / s0:.0%} of its value)")
    for frac in (0.3, 0.1):
        e = star_error_for_dispersion(s0, n_stars, frac)
        txt = f"{e * 1e3:.1f} µas/yr" if np.isfinite(e) else "not reachable with this many stars"
        print(f"  per-star error needed to measure the dispersion to {frac:.0%}: {txt}")


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lvdb-key", default="draco_1")
    p.add_argument("--n-stars", type=int, default=100)
    p.add_argument("--star-error", type=float, default=0.05, help="per-star PM error, mas/yr")
    p.add_argument("--floor", type=float, default=0.0, help="systematic floor, mas/yr")
    p.add_argument("--radius-deg", type=float, default=None,
                   help="region radius for the across-region checks (default 2 r_half)")
    p.add_argument("--cache", type=Path, default=Path("out/lvdb_cache"))
    a = p.parse_args()
    report(a.lvdb_key, a.n_stars, a.star_error, a.floor, a.cache, radius_deg=a.radius_deg)


if __name__ == "__main__":
    main()
