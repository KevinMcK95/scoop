"""Gravitational-wave sky maps: is the target inside an event's localization?

Reads HEALPix sky maps in both formats LIGO/Virgo/KAGRA publish: flat maps
(one ``PROB`` per pixel, e.g. GWTC-1) and multi-order maps (``UNIQ`` +
``PROBDENSITY``, e.g. O3/O4 public alerts on GraceDB).

The *credible level* of a position is the probability inside the smallest
region that contains it: the total probability of every pixel at least as
probable (per unit area) as the target's. The target is inside the 90%
region when its credible level is at most 0.9.
"""
from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import astropy.units as u
import numpy as np
import requests
from astropy.io import fits

from .checks import FAIL, PASS, CheckResult, record_key

GWTC1_SKYMAP = "https://dcc.ligo.org/public/0157/P1800381/007/{event}_skymap.fits.gz"


def _healpix_index(nside: int, ra, dec, nested: bool = True) -> int:
    """HEALPix pixel containing (ra, dec), with healpy or astropy-healpix."""
    try:
        import healpy as hp
        return int(hp.ang2pix(nside, ra, dec, nest=nested, lonlat=True))
    except ImportError:
        from astropy_healpix import HEALPix
        order = "nested" if nested else "ring"
        return int(HEALPix(nside=nside, order=order).lonlat_to_healpix(ra * u.deg, dec * u.deg))


def fetch(url: str, dest: Path, timeout: float = 120) -> Path:
    """Download ``url`` to ``dest``; raises ``requests.HTTPError`` on 4xx/5xx."""
    with requests.get(url, stream=True, timeout=timeout) as r:
        r.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        with open(dest, "wb") as fh:
            for chunk in r.iter_content(1 << 20):
                fh.write(chunk)
    return dest


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_skymap(path: Path) -> dict:
    """Read a flat or multi-order HEALPix sky map into a common form.

    Returns ``density`` (probability per steradian), ``prob`` (probability per
    pixel), the target lookup function ``index_of(ra, dec)`` and the header.
    """
    with fits.open(path) as hdul:
        hdu = next(h for h in hdul if isinstance(h, fits.BinTableHDU))
        header = hdu.header
        data = hdu.data
        cols = data.columns.names
        if header.get("PIXTYPE", "").upper() != "HEALPIX":
            raise ValueError(f"not a HEALPix map (PIXTYPE = {header.get('PIXTYPE')!r})")
        dist = {c: np.asarray(data[c], float) for c in ("DISTMU", "DISTSIGMA") if c in cols}

        if "UNIQ" in cols:  # multi-order map
            uniq = np.asarray(data["UNIQ"], np.int64)
            level = (np.floor(np.log2(uniq / 4)) // 2).astype(int)
            ipix = uniq - 4 * 4**level.astype(np.int64)
            area = 4 * np.pi / (12 * 4.0**level)
            density = np.asarray(data["PROBDENSITY"], float)
            prob = density * area
            lookup = {int(q): i for i, q in enumerate(uniq)}

            def index_of(ra, dec):
                for lev in np.unique(level):
                    q = 4 * 4 ** int(lev) + _healpix_index(2 ** int(lev), ra, dec)
                    if q in lookup:
                        return lookup[q]
                raise ValueError("position not covered by the sky map")
        else:  # flat map
            prob = np.asarray(data["PROB"], float).ravel()
            nside = int(np.sqrt(prob.size / 12))
            density = prob / (4 * np.pi / prob.size)
            nested = header.get("ORDERING", "RING").upper() == "NESTED"

            def index_of(ra, dec):
                return _healpix_index(nside, ra, dec, nested)
    return {"prob": prob, "density": density, "index_of": index_of, "header": header, **dist}


def credible_level(skymap: dict, ra, dec) -> float:
    i = skymap["index_of"](ra, dec)
    d = skymap["density"]
    return float(np.sum(skymap["prob"][d >= d[i]]))


def distance_at(skymap: dict, ra, dec):
    """Mean and std of the line-of-sight distance (Mpc) at a position, or None.

    The sky map stores per-pixel ``DISTMU``/``DISTSIGMA`` of the ansatz
    p(r) ∝ r² N(r; DISTMU, DISTSIGMA), so DISTMU itself is not the mean.
    """
    if "DISTMU" not in skymap:
        return None
    i = skymap["index_of"](ra, dec)
    mu, sigma = skymap["DISTMU"][i], skymap["DISTSIGMA"][i]
    if not (np.isfinite(mu) and np.isfinite(sigma) and sigma > 0):
        return None
    r = np.linspace(0, max(mu, 0) + 10 * sigma, 4000)
    w = r**2 * np.exp(-0.5 * ((r - mu) / sigma) ** 2)
    mean = np.sum(r * w) / np.sum(w)
    std = np.sqrt(np.sum((r - mean) ** 2 * w) / np.sum(w))
    return float(mean), float(std)


def check_localizations(result, base_dir: Path, level_tol=0.01):
    """Re-download each sky map from its access URL and recompute the claims."""
    q = result["query"]
    out = []
    if q.get("ra") is None:
        return out
    for rec in result["records"]:
        if rec.get("kind") != "localization":
            continue
        key = record_key(rec)
        with tempfile.TemporaryDirectory() as tmp:
            try:
                path = fetch(rec["access_url"], Path(tmp) / "skymap.fits.gz")
            except requests.HTTPError as exc:
                out.append(CheckResult("gw_localization", FAIL, key, f"sky map not found at access_url ({exc})"))
                continue
            problems = []
            local = base_dir / rec["path"] if rec.get("path") else None
            if local is not None and local.is_file() and sha256(local) != sha256(path):
                problems.append("local copy differs from the file at access_url")
            try:
                sm = read_skymap(path)
            except Exception as exc:
                out.append(CheckResult("gw_localization", FAIL, key, f"not a readable sky map: {exc}"))
                continue

        obj = str(sm["header"].get("OBJECT", "")).strip()
        if obj and obj != rec["native_id"]:
            problems.append(f"sky map is for {obj}, not {rec['native_id']}")
        total = float(np.sum(sm["prob"]))
        if abs(total - 1) > 1e-3:
            problems.append(f"probabilities sum to {total:.4f}, not 1")
        level = credible_level(sm, q["ra"], q["dec"])
        claimed = rec.get("credible_level")
        if claimed is not None and abs(claimed - level) > level_tol:
            problems.append(f"claimed credible level {claimed:.3f}, recomputed {level:.3f}")
        if rec.get("in_90") is not None and bool(rec["in_90"]) != (level <= 0.9):
            problems.append(f"claims in_90 = {rec['in_90']}, but the credible level is {level:.3f}")

        detail = f"target at credible level {level:.3f} ({'inside' if level <= 0.9 else 'outside'} the 90% region)"
        dist = distance_at(sm, q["ra"], q["dec"])
        if dist:
            detail += f"; line-of-sight distance {dist[0]:.0f} ± {dist[1]:.0f} Mpc"
        out.append(CheckResult("gw_localization", FAIL if problems else PASS, key,
                               "; ".join(problems) if problems else detail))
    return out
