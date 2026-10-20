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

    def crossing(a, b):  # where nll rises 0.5 (or `level`) above the minimum, between a and b
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


def fit_pm(rows: list[dict], floor: float = 0.0, clip_sigma: float | None = 4.0) -> dict:
    """Bulk proper motion and internal PM dispersion from Gaia-like rows (pmra, pmdec,
    pmra_error, pmdec_error, optional pmra_pmdec_corr)."""
    x = np.array([[r["pmra"], r["pmdec"]] for r in rows], float)
    ea = np.array([r["pmra_error"] for r in rows], float)
    ed = np.array([r["pmdec_error"] for r in rows], float)
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
        "median_star_error": float(np.median(np.sqrt((ea ** 2 + ed ** 2) / 2))),
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
