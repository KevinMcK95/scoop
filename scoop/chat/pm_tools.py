"""Proper-motion tools for the chat agent: expectations, published values, and planning.

Deterministic calculations from scoop/pm.py on the loaded field: what size of
proper motion to expect and what a measurement needs, how the published values
compare, what a new HST/JWST epoch would give, and how long until a baseline is
long enough for a signal. Per-epoch position errors are always inputs (never
assumed per instrument); the tools say so in their results.
"""
from __future__ import annotations

import asyncio
import json
import math
from datetime import date
from pathlib import Path
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from pydantic import Field

from ..fields import matches, validate_filter
from ..spatial import footprint_contains
from .visual import Clause

ASSUMPTIONS = (
    "Per-epoch position errors are the values given to this tool, not instrument calibrations; "
    "state them and where they came from.",
    "Telescope scheduling and visibility windows are not modelled.",
    "Internal dispersions assume isotropic velocities (sigma_mu = sigma_los / 4.74 d).",
)
PositionError = Annotated[float, Field(gt=0, le=1000, description="per-epoch position error per star, mas")]
VISIT_DAYS = 30.0  # exposures with one instrument this close in time count as one epoch
ByInstrument = Annotated[dict[str, Annotated[float, Field(gt=0, le=1000)]] | None, Field(
    description='position error (mas) per instrument, matched by substring of "tel inst", e.g. '
                '{"MegaPrime": 10, "WFPC2": 1}; others use existing_position_error_mas')]
Instruments = Annotated[list[str] | None, Field(
    description='count only epochs whose "tel inst" contains one of these, e.g. ["HST", "JWST"]')]


def _err(text: str, tool_call_id: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=tool_call_id, status="error")


def _ok(obj, tool_call_id: str) -> ToolMessage:
    return ToolMessage(content=json.dumps(obj, default=_json_default), tool_call_id=tool_call_id)


def _json_default(v):
    return None if isinstance(v, float) and not math.isfinite(v) else str(v)


def _clean(obj):
    """Replace non-finite floats (inf, nan) with None, recursively."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: _clean(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [_clean(v) for v in obj]
    return obj


def field_epochs(field: dict, ra: float, dec: float) -> list[dict]:
    """Imaging exposures covering (ra, dec): every footprint row with a year, from every
    footprint table (HST/JWST observations and any the data agent added)."""
    out = []
    for name, table in field["tables"].items():
        if table.get("kind") != "footprints":
            continue
        for row in table["rows"]:
            year, region = row.get("year"), row.get("s_region")
            if isinstance(year, (int, float)) and region and footprint_contains(region, ra, dec):
                out.append({"table": name, "id": row["id"], "year": float(year),
                            "instrument": " ".join(str(row.get(k, "")) for k in ("tel", "inst")).strip() or name})
    return sorted(out, key=lambda e: e["year"])


def visits(exposures: list[dict], instruments: list[str] | None = None) -> list[dict]:
    """Exposures grouped into epochs: one instrument within VISIT_DAYS is one visit (its
    exposures share systematics, so they don't count as independent epochs)."""
    keep = [e for e in exposures if not instruments or any(s.lower() in e["instrument"].lower() for s in instruments)]
    out: list[dict] = []
    for e in sorted(keep, key=lambda e: (e["instrument"], e["year"])):
        last = out[-1] if out and out[-1]["instrument"] == e["instrument"] else None
        if last and (e["year"] - last["last_year"]) * 365.25 <= VISIT_DAYS:
            last["exposures"] += 1
            last["last_year"] = e["year"]
            last["year"] = (last["year"] * (last["exposures"] - 1) + e["year"]) / last["exposures"]
        else:
            out.append({"instrument": e["instrument"], "year": e["year"], "last_year": e["year"], "exposures": 1,
                        "first_id": e["id"], "table": e["table"]})
    for v in out:
        del v["last_year"]
        v["year"] = round(v["year"], 3)
    return sorted(out, key=lambda v: v["year"])


def _epoch_errors(epochs: list[dict], default: float, by_instrument: dict | None) -> list[tuple[float, float]]:
    def error(instrument):
        for key, value in (by_instrument or {}).items():
            if key.lower() in instrument.lower():
                return value
        return default
    return [(v["year"], error(v["instrument"])) for v in epochs]


def _summary(epochs: list[dict], errors: list[tuple[float, float]]) -> list[dict]:
    groups: dict[str, dict] = {}
    for v, (_, err) in zip(epochs, errors):
        g = groups.setdefault(v["instrument"], {"instrument": v["instrument"], "visits": 0, "exposures": 0,
                                                 "first": v["year"], "last": v["year"], "position_error_mas": err})
        g["visits"] += 1
        g["exposures"] += v["exposures"]
        g["first"], g["last"] = min(g["first"], v["year"]), max(g["last"], v["year"])
    return sorted(groups.values(), key=lambda g: g["first"])


def build_pm_tools(*, field: dict | None, lvdb_cache: Path | None, loader=None) -> list:
    """``loader(cache) -> (comb_all, pm_overview)`` reads the LVDB tables (scoop.pm.load_lvdb
    by default; injectable for tests)."""
    from .. import pm

    state: dict = {}

    async def lvdb():
        if "tables" not in state:
            load = loader or pm.load_lvdb
            state["tables"] = await asyncio.to_thread(load, lvdb_cache)
        return state["tables"]

    async def target_row():
        if field is None:
            return None, "No field is loaded."
        comb, pms = await lvdb()
        row = pm.lvdb_row(comb, field["key"])
        if row is None:
            return None, (f"Field {field['key']!r} is not a Local Volume Database object, so its distance and "
                          "dispersion aren't known here; these tools need an LVDB field.")
        return (row, pms), None

    def point(ra, dec):
        t = field["target"]
        return (t["ra"] if ra is None else ra), (t["dec"] if dec is None else dec)

    @tool
    async def pm_expectations(
        tool_call_id: Annotated[str, InjectedToolCallId],
        n_stars: Annotated[int, Field(ge=1, le=100000)] = 200,
        star_error_mas_yr: Annotated[float, Field(gt=0, le=10)] = 0.03,
        floor_mas_yr: Annotated[float, Field(ge=0, le=1)] = 0.0,
    ):
        """What size of proper motion to expect for the loaded LVDB object and what a
        measurement needs: internal dispersion (from the LVDB's sigma_los and distance),
        solar reflex, proper motion of 50/100/200 km/s, how reflex, perspective and
        secular aberration change across the region, Galactocentric velocity, the best
        published error, and what n_stars at star_error_mas_yr (plus a floor) measure."""
        found, problem = await target_row()
        if problem:
            return _err(problem, tool_call_id)
        row, pms = found
        out = await asyncio.to_thread(pm.expectations, row, n_stars=n_stars, star_error=star_error_mas_yr,
                                      floor=floor_mas_yr, published_rows=pm.published(pms, row["key"]))
        return _ok(_clean({**out, "assumptions": ASSUMPTIONS[2:]}), tool_call_id)

    @tool
    async def pm_published(tool_call_id: Annotated[str, InjectedToolCallId],
                           floor_mas_yr: Annotated[float, Field(ge=0, le=1)] = 0.0):
        """Every published proper motion of the loaded LVDB object (the LVDB's
        pm_overview: citation, bibcode, method, value, error, comments such as the
        baseline), grouped by method (Gaia only, HST only, HST + Gaia, ground-based,
        radio), with their agreement: weighted mean, chi-squared, p-value, >3 sigma
        outliers. floor_mas_yr adds a systematic floor to every error."""
        found, problem = await target_row()
        if problem:
            return _err(problem, tool_call_id)
        row, pms = found
        rows = pm.published(pms, row["key"])
        if not rows:
            return _ok({"object": row["name"], "measurements": [], "note": "No proper motions in the LVDB; "
                        "search the literature."}, tool_call_id)
        return _ok(_clean({"object": row["name"], "adopted": {"pm": [row["pmra"], row["pmdec"]],
                                                             "ref": row["ref_proper_motion"]},
                           "measurements": rows, "agreement": pm.reconcile(rows, floor_mas_yr)}), tool_call_id)

    @tool
    async def pm_new_epoch(
        year: Annotated[float, Field(ge=1990, le=2100)],
        position_error_mas: PositionError,
        existing_position_error_mas: PositionError,
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: float | None = None, dec: float | None = None,
        gaia_pm_error_mas_yr: Annotated[float | None, Field(gt=0, le=10)] = None,
        n_stars: Annotated[int | None, Field(ge=1, le=100000)] = None,
        floor_mas_yr: Annotated[float, Field(ge=0, le=1)] = 0.0,
        position_errors_by_instrument: ByInstrument = None,
        instruments: Instruments = None,
    ):
        """What one new HST/JWST epoch in `year` would give at (ra, dec) (default the
        target center): the existing imaging there (all footprint tables), grouped into
        visits (one instrument within 30 days = one epoch), the per-star proper-motion
        error now and with the new epoch (optionally combined with Gaia's own per-star
        error), and with n_stars the bulk-motion and internal-dispersion precision,
        compared with the best published value. Position errors are per star per epoch
        (mas); ground-based imaging is far less precise than HST/JWST, so give
        position_errors_by_instrument or restrict instruments. Say what you assumed."""
        if field is None:
            return _err("No field is loaded.", tool_call_id)
        ra, dec = point(ra, dec)
        epochs = visits(field_epochs(field, ra, dec), instruments)
        existing = _epoch_errors(epochs, existing_position_error_mas, position_errors_by_instrument)
        now = pm.combine_errors(pm.pm_error_from_epochs(existing), gaia_pm_error_mas_yr)
        after = pm.combine_errors(pm.pm_error_from_epochs([*existing, (year, position_error_mas)]),
                                  gaia_pm_error_mas_yr)
        out = {"position": [ra, dec], "existing_by_instrument": _summary(epochs, existing),
               "n_existing_visits": len(epochs),
               "span_years_now": round(epochs[-1]["year"] - epochs[0]["year"], 2) if len(epochs) > 1 else 0,
               "per_star_pm_error_now_mas_yr": now, "per_star_pm_error_with_new_epoch_mas_yr": after,
               "improvement_factor": (now / after) if math.isfinite(now) and after > 0 else None,
               "assumptions": ASSUMPTIONS}
        if n_stars:
            found, problem = await target_row()
            if found:
                row, pms = found
                exp = pm.expectations(row, n_stars=n_stars, star_error=after, floor=floor_mas_yr,
                                      published_rows=pm.published(pms, row["key"]), n_mc=4000)
                out["with_new_epoch"] = {k: exp.get(k) for k in ("sample", "internal_dispersion_uas_yr",
                                                                 "best_published")}
            else:
                out["note"] = problem
        return _ok(_clean(out), tool_call_id)

    @tool
    async def pm_wait_time(
        goal: Literal["per_star", "bulk", "dispersion"],
        target: Annotated[float, Field(gt=0, le=10)],
        position_error_mas: PositionError,
        existing_position_error_mas: PositionError,
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: float | None = None, dec: float | None = None,
        n_stars: Annotated[int | None, Field(ge=1, le=100000)] = None,
        gaia_pm_error_mas_yr: Annotated[float | None, Field(gt=0, le=10)] = None,
        floor_mas_yr: Annotated[float, Field(ge=0, le=1)] = 0.0,
        position_errors_by_instrument: ByInstrument = None,
        instruments: Instruments = None,
    ):
        """How long until one new epoch (with the existing imaging at (ra, dec)) could
        reach a goal: goal="per_star" (target: per-star error, mas/yr), "bulk" (target:
        bulk-motion error, mas/yr; needs n_stars) or "dispersion" (target: fractional
        error on the internal dispersion, e.g. 0.3; needs n_stars). Existing imaging is
        grouped into visits; use instruments (e.g. ["HST"] for HST+HST) and
        position_errors_by_instrument as in pm_new_epoch. Returns the earliest year for
        the new epoch, or that it is out of reach and why."""
        if field is None:
            return _err("No field is loaded.", tool_call_id)
        ra, dec = point(ra, dec)
        epochs = visits(field_epochs(field, ra, dec), instruments)
        if not epochs:
            return _err("No existing imaging (of those instruments) covers that position; with no first "
                        "epoch there is no baseline to wait for.", tool_call_id)
        needed, why = target, None
        if goal in ("bulk", "dispersion"):
            if not n_stars:
                return _err(f"goal={goal!r} needs n_stars (e.g. the user's Members count).", tool_call_id)
            found, problem = await target_row()
            if problem:
                return _err(problem, tool_call_id)
            row, _ = found
            exp = pm.expectations(row, n_stars=n_stars, n_mc=4000)
            disp = exp.get("internal_dispersion_uas_yr")
            if not disp:
                return _err("The LVDB has no velocity dispersion for this object.", tool_call_id)
            sigma_int = disp[0] / 1e3
            if goal == "bulk":
                needed = pm.star_error_for_bulk(target, sigma_int, n_stars, floor_mas_yr)
                why = "the floor or the internal dispersion with this many stars" if not math.isfinite(needed) else None
            else:
                needed = pm.star_error_for_dispersion(sigma_int, n_stars, target)
                why = "too few stars for that precision at any per-star error" if not math.isfinite(needed) else None
        existing = _epoch_errors(epochs, existing_position_error_mas, position_errors_by_instrument)
        out = {"goal": goal, "target": target, "needed_per_star_pm_error_mas_yr": needed,
               "existing_by_instrument": _summary(epochs, existing),
               "per_star_pm_error_now_mas_yr": pm.combine_errors(pm.pm_error_from_epochs(existing), gaia_pm_error_mas_yr),
               "first_epoch": epochs[0]["year"], "last_epoch": epochs[-1]["year"], "assumptions": ASSUMPTIONS}
        if why:
            return _ok(_clean({**out, "reachable": False, "why": why}), tool_call_id)
        start = max(date.today().year + date.today().timetuple().tm_yday / 365.25, epochs[-1]["year"])
        year, err = pm.year_to_reach(existing, position_error_mas, needed, start_year=start,
                                     gaia_pm_error=gaia_pm_error_mas_yr)
        out.update({"reachable": year is not None, "earliest_new_epoch_year": round(year, 2) if year else None,
                    "per_star_pm_error_then_mas_yr": err,
                    "why": None if year else "not within 100 years with these errors"})
        return _ok(_clean(out), tool_call_id)

    @tool
    async def fit_members(
        filter: Annotated[list[Clause], Field(min_length=1, description="the member selection, e.g. the "
                                                                          "Members layer's filter as-is")],
        tool_call_id: Annotated[str, InjectedToolCallId],
        table: str = "gaia",
        floor_mas_yr: Annotated[float, Field(ge=0, le=1)] = 0.0,
        clip_sigma: Annotated[float | None, Field(ge=2, le=10)] = 4.0,
        require_clean: bool = True,
        max_pm_error_mas_yr: Annotated[float | None, Field(gt=0, le=100)] = None,
    ):
        """Quick-look fit to a member sample (usually the user's Members layer): the bulk
        proper motion and internal PM dispersion by maximum likelihood with each star's
        errors (and pmra-pmdec correlation where known), sigma-clipped at clip_sigma; the
        line-of-sight velocity and dispersion where stars have rv / rv_err; the [Fe/H]
        spread. For an LVDB object, also km/s at its distance and comparison with the
        expected dispersion and the published proper motions. floor_mas_yr is a systematic
        floor on the mean (an input: ask or state it). Not a mixture model: it trusts the
        selection."""
        import numpy as np

        from .. import members, pm

        if field is None:
            return _err("No field is loaded.", tool_call_id)
        if table not in field["tables"]:
            return _err(f"Unknown table {table!r}; tables: {sorted(field['tables'])}.", tool_call_id)
        t = field["tables"][table]
        clauses = [c.model_dump(exclude_unset=True) for c in filter]
        try:
            validate_filter(clauses, {"id", *t["columns"]})
        except ValueError as exc:
            return _err(f"Invalid filter: {exc}", tool_call_id)
        selected = [r for r in t["rows"] if matches(r, clauses)]
        num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)  # noqa: E731
        dropped = {"no proper motion or error": 0, "not clean": 0, "error above max_pm_error_mas_yr": 0}
        stars = []
        for r in selected:
            if not all(num(r.get(c)) and (c in ("pmra", "pmdec") or r[c] > 0)
                       for c in ("pmra", "pmdec", "pmra_error", "pmdec_error")):
                dropped["no proper motion or error"] += 1
            elif require_clean and r.get("clean") is False:
                dropped["not clean"] += 1
            elif max_pm_error_mas_yr and max(r["pmra_error"], r["pmdec_error"]) > max_pm_error_mas_yr:
                dropped["error above max_pm_error_mas_yr"] += 1
            else:
                stars.append(r)
        out: dict = {"table": table, "filter": clauses, "n_selected": len(selected), "dropped": dropped,
                     "notes": list(members.NOTES)}
        if len(stars) < 3:
            return _err(f"Only {len(stars)} of {len(selected)} selected stars have usable proper motions; "
                        f"dropped: {dropped}.", tool_call_id)
        fit = await asyncio.to_thread(members.fit_pm, stars, floor_mas_yr, clip_sigma)
        out["proper_motion"] = fit
        if fit["correlations_missing"]:
            out["notes"].append(f"{fit['correlations_missing']} stars have no pmra_pmdec_corr in this field; "
                                "taken as 0 (rebuild the field to include it).")
        vel = [r for r in selected if num(r.get("rv")) and num(r.get("rv_err")) and r["rv_err"] > 0]
        if len(vel) >= 3:
            out["line_of_sight_velocity_kms"] = await asyncio.to_thread(members.fit_1d, vel, "rv", "rv_err",
                                                                        clip_sigma)
        feh = [r["feh"] for r in selected if num(r.get("feh"))]
        if len(feh) >= 3:
            q = [float(v) for v in np.percentile(feh, [16, 50, 84])]
            out["feh"] = {"n": len(feh), "median": q[1], "p16_p84": [q[0], q[2]],
                          "note": "spread of the values; no [Fe/H] errors are stored, so not an intrinsic spread"}
        found, _ = await target_row()
        if found:
            row, pms = found
            d = row["distance"]
            if math.isfinite(d):
                kms = lambda mu: None if mu is None or not math.isfinite(mu) else pm.K * d * mu  # noqa: E731
                expected = pm.internal_pm_dispersion(row["vlos_sigma"], d) if math.isfinite(row["vlos_sigma"]) else None
                out["at_lvdb_distance"] = {
                    "distance_kpc": d, "sigma_int_kms": kms(fit["sigma_int"]),
                    "sigma_int_68_kms": [kms(v) for v in fit["sigma_int_68"]],
                    "expected_sigma_int_mas_yr": expected, "lvdb_vlos_sigma_kms": row["vlos_sigma"],
                    "note": "the PM dispersion at the LVDB distance; distance errors are not propagated"}
            compare = {"lvdb_adopted": {"pmra": row["pmra"], "pmdec": row["pmdec"],
                                        "pmra_err": pm._sym(row["pmra_em"], row["pmra_ep"]),
                                        "pmdec_err": pm._sym(row["pmdec_em"], row["pmdec_ep"]),
                                        "ref": row["ref_proper_motion"]}}
            published = pm.published(pms, row["key"])
            if len(published) >= 2:
                agree = pm.reconcile(published)
                if "weighted_mean" in agree["pmra"] and "weighted_mean" in agree["pmdec"]:
                    compare["published_weighted_mean"] = {
                        "pmra": agree["pmra"]["weighted_mean"], "pmra_err": agree["pmra"]["weighted_mean_err"],
                        "pmdec": agree["pmdec"]["weighted_mean"], "pmdec_err": agree["pmdec"]["weighted_mean_err"],
                        "n": len(published)}
            for ref in compare.values():
                ref["pull_pmra"] = members.pull(fit["pmra"], fit["pmra_err"], ref["pmra"], ref["pmra_err"])
                ref["pull_pmdec"] = members.pull(fit["pmdec"], fit["pmdec_err"], ref["pmdec"], ref["pmdec_err"])
            out["compared_with"] = compare
            if "line_of_sight_velocity_kms" in out and math.isfinite(row["vlos_systemic"]):
                out["line_of_sight_velocity_kms"]["lvdb"] = {"vlos_systemic": row["vlos_systemic"],
                                                             "vlos_sigma": row["vlos_sigma"], "ref": row["ref_vlos"]}
        return _ok(_clean(out), tool_call_id)

    return [pm_expectations, pm_published, pm_new_epoch, pm_wait_time, fit_members]


__all__ = ["build_pm_tools", "field_epochs", "visits"]
