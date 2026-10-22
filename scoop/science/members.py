"""Quick-look fits to a member sample: bulk proper motion and internal dispersion,
line-of-sight velocity and dispersion, metallicity spread.

Maximum likelihood with each star's errors: a Gaussian of mean m and intrinsic
(isotropic) dispersion s convolved with every star's error covariance. For a given
s the mean is the weighted mean, so the likelihood is profiled over s; the interval
on s is where the profile drops by 0.5 (68%), or an upper limit where it touches 0.
Optional sigma-clipping drops stars far from the fit (Mahalanobis distance).

These are quick looks, not a mixture model: they trust the member selection, and a
systematic floor (spatially correlated error) adds to the mean's error only.
"""
from __future__ import annotations

import math
from collections import Counter

import numpy as np
from scipy import optimize, stats

NOTES = (
    "Quick look: every selected star is treated as a member (no background model), so "
    "contaminants inflate the dispersion; clipping removes only gross outliers.",
    "The mean proper motion is heliocentric as measured: not corrected for perspective, "
    "solar reflex or secular aberration (see pm_expectations for their sizes).",
    "The systematic floor is added to the mean's error, not to each star; small-scale "
    "correlated Gaia systematics can also inflate a measured dispersion.",
    "Maximum-likelihood dispersions are biased low for small samples (order 1/N); "
    "clipping also trims real tails (about 1% low at 4 sigma, 7% at 3 sigma in tests).",
)


def _profile(x: np.ndarray, cov: np.ndarray):
    """-ln L(s) with the mean profiled out; x (N, k), cov (N, k, k)."""
    eye = np.eye(x.shape[1])

    def mean_and_nll(s):
        c = cov + (s * s) * eye
        w = np.linalg.inv(c)
        wsum = w.sum(axis=0)
        mean = np.linalg.solve(wsum, np.einsum("nij,nj->i", w, x))
        r = x - mean
        chi2 = np.einsum("ni,nij,nj->n", r, w, r)
        _, logdet = np.linalg.slogdet(c)
        return mean, np.linalg.inv(wsum), 0.5 * float(np.sum(chi2 + logdet)), chi2

    return mean_and_nll


def _fit(x: np.ndarray, cov: np.ndarray) -> dict:
    f = _profile(x, cov)
    spread = float(np.max(np.std(x, axis=0))) or 1.0
    nll = lambda s: f(abs(s))[2]  # noqa: E731
    grid = np.concatenate([[0.0], np.geomspace(spread * 1e-4, spread * 3, 60)])
    i = int(np.argmin([nll(s) for s in grid]))
    lo, hi = grid[max(i - 1, 0)], grid[min(i + 1, len(grid) - 1)]
    best = optimize.minimize_scalar(nll, bounds=(lo, hi), method="bounded").x if hi > lo else grid[i]
    best = abs(float(best))
    if nll(0.0) <= nll(best):
        best = 0.0
    floor = nll(best)

    def crossing(a, b):  # where nll rises 0.5 above the minimum, between a and b
        return optimize.brentq(lambda s: nll(s) - floor - 0.5, a, b)

    top = max(best * 4, spread * 3)
    upper = crossing(best, top) if nll(top) - floor > 0.5 else math.inf
    lower = crossing(0.0, best) if best > 0 and nll(0.0) - floor > 0.5 else 0.0
    out = {"sigma": best, "sigma_68": [lower, upper]}
    if lower == 0.0:  # consistent with zero: quote a 95% upper limit (Δ ln L = 1.92)
        out["sigma_upper_95"] = (optimize.brentq(lambda s: nll(s) - floor - 1.92, best, top * 3)
                                 if nll(top * 3) - floor > 1.92 else math.inf)
    mean, mean_cov, _, chi2 = f(best)
    out.update(mean=mean, mean_cov=mean_cov, chi2=chi2)
    return out


def fit_gaussian(x, cov, clip_sigma: float | None = 4.0, max_iter: int = 10) -> dict:
    """Fit mean and intrinsic dispersion to values x (N, k) with error covariances cov
    (N, k, k), clipping stars beyond clip_sigma (as the equivalent k-d chi-squared)."""
    x, cov = np.asarray(x, float), np.asarray(cov, float)
    keep = np.ones(len(x), bool)
    limit = stats.chi2.ppf(stats.chi2.cdf(clip_sigma ** 2, 1), x.shape[1]) if clip_sigma else math.inf
    for _ in range(max_iter):
        if keep.sum() < 3:
            raise ValueError("fewer than 3 stars left to fit")
        result = _fit(x[keep], cov[keep])
        if not clip_sigma:
            break
        # Distances for every star, against the current fit.
        c = cov + result["sigma"] ** 2 * np.eye(x.shape[1])
        r = x - result["mean"]
        d2 = np.einsum("ni,nij,nj->n", r, np.linalg.inv(c), r)
        new = d2 <= limit
        if np.array_equal(new, keep):
            break
        keep = new
    result["used"] = keep
    return result


def fit_pm(rows: list[dict], floor: float = 0.0, clip_sigma: float | None = 4.0, error_scale: float = 1.0) -> dict:
    """Bulk proper motion and internal PM dispersion from Gaia-like rows (pmra, pmdec,
    pmra_error, pmdec_error, optional pmra_pmdec_corr); error_scale multiplies the errors."""
    x = np.array([[r["pmra"], r["pmdec"]] for r in rows], float)
    ea = np.array([r["pmra_error"] for r in rows], float) * error_scale
    ed = np.array([r["pmdec_error"] for r in rows], float) * error_scale
    rho = np.array([r.get("pmra_pmdec_corr") if r.get("pmra_pmdec_corr") is not None else 0.0 for r in rows])
    cov = np.zeros((len(rows), 2, 2))
    cov[:, 0, 0], cov[:, 1, 1] = ea ** 2, ed ** 2
    cov[:, 0, 1] = cov[:, 1, 0] = rho * ea * ed
    fit = fit_gaussian(x, cov, clip_sigma)
    stat = np.sqrt(np.diag(fit["mean_cov"]))
    total = np.sqrt(stat ** 2 + floor ** 2)
    return {
        "n_used": int(fit["used"].sum()), "n_clipped": int((~fit["used"]).sum()),
        "clipped_ids": [rows[i]["id"] for i in np.flatnonzero(~fit["used"])][:50],
        "pmra": float(fit["mean"][0]), "pmdec": float(fit["mean"][1]),
        "pmra_err_stat": float(stat[0]), "pmdec_err_stat": float(stat[1]),
        "pmra_err": float(total[0]), "pmdec_err": float(total[1]), "floor": floor,
        "pmra_pmdec_corr": float(fit["mean_cov"][0, 1] / (stat[0] * stat[1])),
        "sigma_int": fit["sigma"], "sigma_int_68": fit["sigma_68"], "sigma_int_upper_95": fit.get("sigma_upper_95"),
        "median_star_error": float(np.median(np.sqrt((ea ** 2 + ed ** 2) / 2))), "error_scale": error_scale,
        "correlations_missing": int(sum(r.get("pmra_pmdec_corr") is None for r in rows)),
    }


def fit_1d(rows: list[dict], column: str, error_column: str, clip_sigma: float | None = 4.0) -> dict:
    """Mean and intrinsic dispersion of one column (e.g. rv with rv_err)."""
    x = np.array([[r[column]] for r in rows], float)
    cov = np.array([[[r[error_column] ** 2]] for r in rows], float)
    fit = fit_gaussian(x, cov, clip_sigma)
    return {"n_used": int(fit["used"].sum()), "n_clipped": int((~fit["used"]).sum()),
            "mean": float(fit["mean"][0]), "mean_err": float(math.sqrt(fit["mean_cov"][0, 0])),
            "sigma_int": fit["sigma"], "sigma_int_68": fit["sigma_68"],
            "sigma_int_upper_95": fit.get("sigma_upper_95"),
            "median_error": float(np.median(np.sqrt(cov[:, 0, 0])))}


def pull(value: float, err: float, other: float, other_err: float) -> float:
    return (value - other) / math.hypot(err, other_err) if err or other_err else math.nan


# ── Automatic member selection ────────────────────────────────────────────────

PM_CUT = float(stats.chi2.ppf(stats.chi2.cdf(9.0, 1), 2))  # 3-sigma equivalent for 2 dof (11.8)
RING = (20.0, 60.0)  # chi-squared ring in PM space that samples the foreground near the prior
SELECTION_NOTES = (
    "Selection: within r_max half-light radii, parallax consistent with the distance, proper "
    "motion within 3 sigma of the prior (star errors plus the expected internal dispersion), "
    "in the CMD region where members outnumber the foreground, and (stars with a velocity) "
    "within 3 sigma of the systemic velocity; then repeated once about the fitted proper motion.",
    "The CMD region is empirical, not an isochrone: Hess-diagram cells where stars passing the "
    "proper-motion cut outnumber the foreground expected there, estimated from stars in a ring "
    "of proper-motion space around the prior (assumed locally flat).",
    "No parallax zero-point correction; at dwarf-galaxy distances the parallax cut mostly "
    "removes nearby foreground.",
)


def _finite(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _pm_chi2(r: dict, pm0, s: float) -> float:
    ea, ed = r["pmra_error"], r["pmdec_error"]
    rho = r.get("pmra_pmdec_corr") if _finite(r.get("pmra_pmdec_corr")) else 0.0
    c = np.array([[ea * ea + s * s, rho * ea * ed], [rho * ea * ed, ed * ed + s * s]])
    d = np.array([r["pmra"] - pm0[0], r["pmdec"] - pm0[1]])
    return float(d @ np.linalg.solve(c, d))


def cells_to_polygons(cells: set[tuple[int, int]], x0: float, dx: float, y0: float, dy: float) -> list:
    """Outlines of a set of grid cells as polygons in data units (holes filled)."""
    edges: dict[tuple, tuple] = {}
    for i, j in cells:
        corners = [(i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1)]
        for a, b in zip(corners, corners[1:] + corners[:1]):
            if (b, a) in edges:
                del edges[(b, a)]  # shared with a neighbour: inside the region
            else:
                edges[(a, b)] = True
    starts: dict[tuple, list] = {}
    for a, b in edges:
        starts.setdefault(a, []).append(b)
    loops = []
    while starts:
        a = next(iter(starts))
        loop = [a]
        while True:
            b = starts[a].pop()
            if not starts[a]:
                del starts[a]
            if b == loop[0]:
                break
            loop.append(b)
            a = b
        # Drop collinear vertices.
        n = len(loop)
        keep = [p for k, p in enumerate(loop)
                if (loop[k - 1][0] - p[0]) * (loop[(k + 1) % n][1] - p[1])
                != (loop[k - 1][1] - p[1]) * (loop[(k + 1) % n][0] - p[0])]
        area = 0.5 * sum(p[0] * q[1] - q[0] * p[1] for p, q in zip(keep, keep[1:] + keep[:1]))
        if area > 0:  # counter-clockwise outer boundary; clockwise loops are holes, filled
            loops.append((area, [[round(x0 + p[0] * dx, 4), round(y0 + p[1] * dy, 4)] for p in keep]))
    return [poly for _, poly in sorted(loops, key=lambda t: -t[0])]


def cmd_region(passing: list[dict], ring: list[dict], ring_scale: float, *, min_fraction: float = 0.5,
               min_stars: int = 2, max_polygons: int = 10, max_vertices: int = 200) -> dict:
    """Hess-diagram cells (bp_rp, G) where stars passing the PM cut outnumber the expected
    foreground (ring stars x ring_scale), outlined as lasso polygons. Tries coarser cells
    until the outline fits the lasso limits."""
    has = lambda r: _finite(r.get("bp_rp")) and _finite(r.get("G"))  # noqa: E731
    p = [r for r in passing if has(r)]
    q = [r for r in ring if has(r)]
    if len(p) < 5:
        return {"polygons": [], "why": "too few stars with colours to define a CMD region"}
    x0 = math.floor(min(r["bp_rp"] for r in p) * 10) / 10 - 0.1
    y0 = math.floor(min(r["G"] for r in p)) - 0.5
    for dx, dy in ((0.1, 0.5), (0.15, 0.5), (0.2, 0.5), (0.2, 1.0), (0.3, 1.0)):
        cell = lambda r: (int((r["bp_rp"] - x0) // dx), int((r["G"] - y0) // dy))  # noqa: E731
        n_p, n_q = Counter(map(cell, p)), Counter(map(cell, q))
        kept = {c for c, n in n_p.items()
                if n >= min_stars and (n - ring_scale * n_q.get(c, 0)) / n >= min_fraction}
        # Keep connected groups holding at least three stars more than the foreground.
        groups, seen = [], set()
        for c in kept:
            if c in seen:
                continue
            stack, group = [c], set()
            while stack:
                k = stack.pop()
                if k in seen or k not in kept:
                    continue
                seen.add(k)
                group.add(k)
                stack += [(k[0] + 1, k[1]), (k[0] - 1, k[1]), (k[0], k[1] + 1), (k[0], k[1] - 1)]
            excess = sum(n_p[k] - ring_scale * n_q.get(k, 0) for k in group)
            if excess >= 3:
                groups.append((excess, group))
        groups.sort(key=lambda g: -g[0])
        cells = set().union(*(g for _, g in groups[:max_polygons])) if groups else set()
        polygons = cells_to_polygons(cells, x0, dx, y0, dy)[:max_polygons]
        if sum(len(poly) for poly in polygons) <= max_vertices:
            expected = ring_scale * sum(n_q.get(c, 0) for c in cells)
            inside = sum(n_p.get(c, 0) for c in cells)
            return {"polygons": polygons, "cell": [dx, dy], "cells": len(cells), "stars_inside": inside,
                    "expected_foreground_inside": round(expected, 1)}
    return {"polygons": [], "why": "the CMD region is too fragmented to outline"}


def select_members(rows: list[dict], *, distance_kpc: float | None, pm_prior: tuple[float, float] | None,
                   sigma_pm: float, r_max: float = 3.0, vlos: float | None = None, vlos_sigma: float | None = None,
                   require_clean: bool = True, use_cmd: bool = True, iterations: int = 2) -> dict:
    """Pick likely members of a stellar system from a Gaia-like table (see SELECTION_NOTES).

    pm_prior is the starting proper motion (literature); without one, the densest point
    of the central stars' proper motions. sigma_pm (mas/yr) is the expected internal PM
    dispersion, added to every star's error in the PM cut."""
    steps: list[dict] = []
    usable = [r for r in rows if all(_finite(r.get(c)) for c in ("pmra", "pmdec", "pmra_error", "pmdec_error"))
              and r["pmra_error"] > 0 and r["pmdec_error"] > 0]
    steps.append({"step": "has proper motion and errors", "n": len(usable)})
    if require_clean:
        usable = [r for r in usable if r.get("clean") is not False]
        steps.append({"step": "clean astrometry", "n": len(usable)})
    if distance_kpc and distance_kpc > 0:
        plx0 = 1.0 / distance_kpc
        usable = [r for r in usable if not (_finite(r.get("parallax")) and _finite(r.get("parallax_error")))
                  or abs(r["parallax"] - plx0) <= 3 * r["parallax_error"]]
        steps.append({"step": f"parallax within 3 sigma of 1/d = {plx0:.4f} mas", "n": len(usable)})
    near = [r for r in usable if _finite(r.get("r_ell")) and r["r_ell"] <= r_max]
    steps.append({"step": f"within {r_max} half-light radii", "n": len(near)})
    if len(near) < 5:
        raise ValueError(f"only {len(near)} stars left before the proper-motion cut")

    start = "literature"
    if pm_prior is None:  # the densest point of the central stars' proper motions
        start = "densest proper motion of the central stars"
        core = [r for r in near if r["r_ell"] <= min(1.0, r_max)] or near
        w = np.array([1 / (r["pmra_error"] ** 2 + r["pmdec_error"] ** 2 + 0.01) for r in core])
        x = np.array([[r["pmra"], r["pmdec"]] for r in core])
        h = 0.3
        density = [(w * np.exp(-np.sum((x - x[k]) ** 2, axis=1) / (2 * h * h))).sum() for k in range(len(x))]
        pm_prior = tuple(float(v) for v in x[int(np.argmax(density))])
    pm0 = tuple(pm_prior)
    history = []
    for it in range(max(1, iterations)):
        chi2 = {r["id"]: _pm_chi2(r, pm0, sigma_pm) for r in near}
        passing = [r for r in near if chi2[r["id"]] <= PM_CUT]
        ring = [r for r in near if RING[0] <= chi2[r["id"]] <= RING[1]]
        ring_scale = PM_CUT / (RING[1] - RING[0])  # acceptance area / ring area, per star
        cmd = {"polygons": [], "why": "not used"}
        selected = passing
        if use_cmd:
            cmd = cmd_region(passing, ring, ring_scale)
            if cmd["polygons"]:
                from .fields import plane_polygon_contains
                selected = [r for r in passing if _finite(r.get("bp_rp")) and _finite(r.get("G"))
                            and any(plane_polygon_contains(poly, r["bp_rp"], r["G"]) for poly in cmd["polygons"])]
        rv_rejected = 0
        if vlos is not None and vlos_sigma is not None:
            keep = []
            for r in selected:
                if _finite(r.get("rv")) and _finite(r.get("rv_err")) and \
                        abs(r["rv"] - vlos) > 3 * math.hypot(vlos_sigma, r["rv_err"]):
                    rv_rejected += 1
                else:
                    keep.append(r)
            selected = keep
        history.append({"iteration": it + 1, "pm_prior": [round(v, 4) for v in pm0], "pm_cut": len(passing),
                        "ring_stars": len(ring), "expected_foreground_in_pm_cut": round(ring_scale * len(ring), 1),
                        "after_cmd": len(selected) + rv_rejected, "rv_rejected": rv_rejected, "members": len(selected),
                        "cmd": {k: v for k, v in cmd.items() if k != "polygons"} | {"polygons": len(cmd["polygons"])}})
        if it + 1 < iterations and len(selected) >= 3:
            fit = fit_pm(selected)
            pm0 = (fit["pmra"], fit["pmdec"])
    return {"members": selected, "cmd_polygons": cmd["polygons"], "steps": steps, "iterations": history,
            "pm_start": {"value": [round(v, 4) for v in pm_prior], "from": start},
            "r_max": r_max, "sigma_pm": sigma_pm}
