"""How many Gaia DR3 sources a region holds, in seconds, before anything is counted
exactly or fetched.

An exact COUNT(*) scans every source in the region, so over a dense one it is itself
the expensive query (over the Galactic plane, ~15 million sources took minutes and
clogged the server's job queue on 2026-10-21). Instead this counts a few small cones
inside the region -- the target's centre and points spread over it -- on the TAP
synchronous endpoint (no async jobs left behind) and scales their mean density by the
region's area. Good to a factor of ~2 where density varies smoothly; it underestimates
a dense core much smaller than the region only where no sample falls in it.
"""
from __future__ import annotations

import math
import os
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import numpy as np

from . import gaia_status

N_SAMPLES = 8
SAMPLE_RADIUS_DEG = 0.05


def _count(url: str, ra: float, dec: float, radius: float, gmax: float | None, timeout_s: float) -> int:
    where = f"1=CONTAINS(POINT('ICRS', ra, dec), CIRCLE('ICRS', {ra:.6f}, {dec:.6f}, {radius:.6f}))"
    if gmax is not None:
        where += f" AND phot_g_mean_mag <= {float(gmax):.3f}"
    body = urllib.parse.urlencode({"REQUEST": "doQuery", "LANG": "ADQL", "FORMAT": "csv",
                                   "QUERY": f"SELECT COUNT(*) AS n FROM gaiadr3.gaia_source WHERE {where}"}).encode()
    with urllib.request.urlopen(url + "/sync", data=body, timeout=timeout_s) as r:
        lines = r.read().decode().split()
    return int(lines[-1])


def sample_points(region, n: int = N_SAMPLES, seed: int = 1) -> list[tuple[float, float]]:
    """The region's centre (if inside) and points drawn uniformly over it."""
    ra0, dec0, radius = region.bounding_cone()
    rng = np.random.default_rng(seed)
    pts = [(ra0, dec0)] if bool(np.atleast_1d(region.contains(np.array([ra0]), np.array([dec0])))[0]) else []
    # Uniform on the sphere within the bounding cone, kept when inside the region.
    for _ in range(50):
        if len(pts) >= n:
            break
        k = 64
        cos_r = math.cos(math.radians(radius))
        z = rng.uniform(cos_r, 1.0, k)
        phi = rng.uniform(0, 2 * math.pi, k)
        x, y = np.sqrt(1 - z ** 2) * np.cos(phi), np.sqrt(1 - z ** 2) * np.sin(phi)
        # Rotate the cone's axis from the pole to (ra0, dec0).
        a, d = math.radians(ra0), math.radians(dec0)
        X = x * math.sin(d) * math.cos(a) - y * math.sin(a) + z * math.cos(d) * math.cos(a)
        Y = x * math.sin(d) * math.sin(a) + y * math.cos(a) + z * math.cos(d) * math.sin(a)
        Z = -x * math.cos(d) + z * math.sin(d)
        ra = np.degrees(np.arctan2(Y, X)) % 360
        dec = np.degrees(np.arcsin(np.clip(Z, -1, 1)))
        inside = np.atleast_1d(region.contains(ra, dec))
        pts += [(float(r), float(dd)) for r, dd in zip(ra[inside], dec[inside])][: n - len(pts)]
    return pts


def estimate(region, gmax: float | None = None, n: int = N_SAMPLES, timeout_s: float = 30.0) -> dict:
    """{sources, low, high, area_deg2, samples, server, seconds, method}: an estimate of
    the Gaia DR3 sources in the region (G <= gmax when given). low/high span the
    sampled densities times the area."""
    t = time.time()
    order = (["esa", "heidelberg"] if os.environ.get("SCOOP_GAIA_SERVER", "").strip().lower() == "esa"
             else ["heidelberg", "esa"])
    area = float(region.area_deg2)
    _, _, radius = region.bounding_cone()
    r = min(SAMPLE_RADIUS_DEG, max(radius / 4, 0.005))
    cone_area = 2 * math.pi * (1 - math.cos(math.radians(r))) * (180 / math.pi) ** 2
    pts = sample_points(region, n)
    errors = []
    for server in order:
        url = gaia_status.SERVERS[server]
        try:
            with ThreadPoolExecutor(max_workers=4) as pool:
                counts = list(pool.map(lambda p: _count(url, p[0], p[1], r, gmax, timeout_s), pts))
            break
        except Exception as e:  # the other server, then give up with both reasons
            errors.append(f"{server}: {type(e).__name__}: {e}")
    else:
        raise RuntimeError("could not estimate the Gaia source count (" + "; ".join(errors) + ")")
    density = np.array(counts) / cone_area
    return {"sources": int(round(density.mean() * area)), "low": int(round(density.min() * area)),
            "high": int(round(density.max() * area)), "area_deg2": round(area, 4),
            "density_per_deg2": [int(round(x)) for x in density], "gmax": gmax,
            "samples": len(pts), "sample_radius_deg": round(r, 4), "server": server,
            "seconds": round(time.time() - t, 1),
            "method": f"{len(pts)} cones of {r * 60:.1f}' counted exactly, mean density x area"}


__all__ = ["estimate", "sample_points"]
