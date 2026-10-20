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
from collections.abc import Callable
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from pydantic import BaseModel, ConfigDict, Field

from ..fields import matches, validate_filter
from ..spatial import footprint_contains
from .tools import publish_result
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


class LiteraturePM(BaseModel):
    """A published bulk proper motion to compare with (from a paper you read)."""
    model_config = ConfigDict(extra="forbid")
    citation: Annotated[str, Field(max_length=200)]
    pmra: float
    pmra_err: Annotated[float, Field(gt=0)]
    pmdec: float
    pmdec_err: Annotated[float, Field(gt=0)]


LiteratureList = Annotated[list[LiteraturePM] | None, Field(max_length=20)]


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


AGENT_MEMBERS = "agent:members:{table}"


def build_pm_tools(*, field: dict | None | Callable[[], dict | None], lvdb_cache: Path | None, loader=None,
                   layers=(), known_layers: dict | None = None) -> list:
    """``loader(cache) -> (comb_all, pm_overview)`` reads the LVDB tables (scoop.pm.load_lvdb
    by default; injectable for tests). ``layers`` are the browser's current layers, so
    auto_members updates its layer rather than adding it twice; ``known_layers`` is the
    dict shared with the map tools (build_map_tools), which then see the layer too."""
    current_field = field if callable(field) else (lambda: field)  # the field may change mid-turn
    current = known_layers if known_layers is not None else {}
    current.update({layer["id"]: layer for layer in layers if isinstance(layer, dict) and "id" in layer})
    from .. import pm

    state: dict = {}

    async def lvdb():
        if "tables" not in state:
            load = loader or pm.load_lvdb
            state["tables"] = await asyncio.to_thread(load, lvdb_cache)
        return state["tables"]

    async def target_row():
        field = current_field()
        if field is None:
            return None, "No field is loaded."
        comb, pms = await lvdb()
        row = pm.lvdb_row(comb, field["key"])
        if row is None:
            return None, (f"Field {field['key']!r} is not a Local Volume Database object, so its distance and "
                          "dispersion aren't known here; these tools need an LVDB field.")
        return (row, pms), None

    def point(ra, dec):
        t = current_field()["target"]
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
        field = current_field()
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
        field = current_field()
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

    async def fit_report(selected: list[dict], *, floor: float, clip_sigma: float | None, require_clean: bool,
                         max_pm_error: float | None, literature: list | None = None,
                         distance: tuple[float, str] | None = None,
                         error_scale: float = 1.0) -> tuple[dict | None, str | None]:
        """Fit a member sample and compare with the LVDB and any literature values given."""
        import numpy as np

        from .. import members, pm

        num = members._finite
        dropped = {"no proper motion or error": 0, "not clean": 0, "error above max_pm_error_mas_yr": 0}
        stars = []
        for r in selected:
            if not all(num(r.get(c)) and (c in ("pmra", "pmdec") or r[c] > 0)
                       for c in ("pmra", "pmdec", "pmra_error", "pmdec_error")):
                dropped["no proper motion or error"] += 1
            elif require_clean and r.get("clean") is False:
                dropped["not clean"] += 1
            elif max_pm_error and max(r["pmra_error"], r["pmdec_error"]) > max_pm_error:
                dropped["error above max_pm_error_mas_yr"] += 1
            else:
                stars.append(r)
        if len(stars) < 3:
            return None, (f"Only {len(stars)} of {len(selected)} selected stars have usable proper motions; "
                          f"dropped: {dropped}.")
        out: dict = {"n_selected": len(selected), "dropped": dropped, "notes": list(members.NOTES)}
        fit = await asyncio.to_thread(members.fit_pm, stars, floor, clip_sigma, error_scale)
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
        compare: dict = {}
        found, _ = await target_row()
        row = found[0] if found else None
        if distance is None and row and math.isfinite(row["distance"]):
            distance = (row["distance"], "LVDB")
        if distance:
            d, source = distance
            kms = lambda mu: None if mu is None or not math.isfinite(mu) else pm.K * d * mu  # noqa: E731
            expected = (pm.internal_pm_dispersion(row["vlos_sigma"], d)
                        if row and math.isfinite(row["vlos_sigma"]) else None)
            out["at_distance"] = {
                "distance_kpc": d, "distance_from": source, "sigma_int_kms": kms(fit["sigma_int"]),
                "sigma_int_68_kms": [kms(v) for v in fit["sigma_int_68"]],
                "expected_sigma_int_mas_yr": expected, "lvdb_vlos_sigma_kms": row["vlos_sigma"] if row else None,
                "note": "the PM dispersion at that distance; distance errors are not propagated"}
            low = fit["sigma_int_68"][0]
            if expected and low and low > 3 * expected:
                out["notes"].insert(0, (
                    f"WARNING: the fitted PM dispersion ({fit['sigma_int']:.3f} mas/yr, "
                    f"{kms(fit['sigma_int']):.0f} km/s) is far above the {expected:.4f} mas/yr expected from the "
                    "velocity dispersion: read it as unmodelled error (underestimated Gaia errors, e.g. from "
                    "crowding; systematics; binaries or contaminants), not internal motion. Try error_scale "
                    "(e.g. 1.1-1.3) or brighter stars (max_pm_error_mas_yr) to see how the result depends on it."))
        if row:
            pms = found[1]
            compare["lvdb_adopted"] = {"pmra": row["pmra"], "pmdec": row["pmdec"],
                                       "pmra_err": pm._sym(row["pmra_em"], row["pmra_ep"]),
                                       "pmdec_err": pm._sym(row["pmdec_em"], row["pmdec_ep"]),
                                       "ref": row["ref_proper_motion"]}
            published = pm.published(pms, row["key"])
            if len(published) >= 2:
                agree = pm.reconcile(published)
                if "weighted_mean" in agree["pmra"] and "weighted_mean" in agree["pmdec"]:
                    compare["published_weighted_mean"] = {
                        "pmra": agree["pmra"]["weighted_mean"], "pmra_err": agree["pmra"]["weighted_mean_err"],
                        "pmdec": agree["pmdec"]["weighted_mean"], "pmdec_err": agree["pmdec"]["weighted_mean_err"],
                        "n": len(published), "note": "independent errors assumed; the papers share Gaia data"}
            if "line_of_sight_velocity_kms" in out and math.isfinite(row["vlos_systemic"]):
                out["line_of_sight_velocity_kms"]["lvdb"] = {"vlos_systemic": row["vlos_systemic"],
                                                             "vlos_sigma": row["vlos_sigma"], "ref": row["ref_vlos"]}
        for lit in literature or []:
            compare[lit.citation] = lit.model_dump()
        for ref in compare.values():
            ref["pull_pmra"] = members.pull(fit["pmra"], fit["pmra_err"], ref["pmra"], ref["pmra_err"])
            ref["pull_pmdec"] = members.pull(fit["pmdec"], fit["pmdec_err"], ref["pmdec"], ref["pmdec_err"])
        if compare:
            out["compared_with"] = compare
        return out, None

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
        literature: LiteratureList = None,
        error_scale: Annotated[float, Field(ge=0.5, le=3)] = 1.0,
    ):
        """Quick-look fit to a member sample (usually the user's Members layer): the bulk
        proper motion and internal PM dispersion by maximum likelihood with each star's
        errors (and pmra-pmdec correlation where known), sigma-clipped at clip_sigma; the
        line-of-sight velocity and dispersion where stars have rv / rv_err; the [Fe/H]
        spread. For an LVDB object, also km/s at its distance and comparison with the
        expected dispersion and the published proper motions. floor_mas_yr is a systematic
        floor on the mean (an input: ask or state it). literature adds values from papers
        you read (ads_paper) to the comparison. Not a mixture model: it trusts the
        selection. error_scale multiplies every star's PM errors (a sensitivity test for
        underestimated errors)."""
        field = current_field()
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
        out, problem = await fit_report(selected, floor=floor_mas_yr, clip_sigma=clip_sigma,
                                        require_clean=require_clean, max_pm_error=max_pm_error_mas_yr,
                                        literature=literature, error_scale=error_scale)
        if problem:
            return _err(problem, tool_call_id)
        return _ok(_clean({"table": table, "filter": clauses, **out}), tool_call_id)

    @tool
    async def auto_members(
        tool_call_id: Annotated[str, InjectedToolCallId],
        table: str = "gaia",
        r_max: Annotated[float | None, Field(gt=0, le=20, description="half-light radii (default: the whole field)")] = None,
        pm_prior: Annotated[list[float] | None, Field(min_length=2, max_length=2, description=(
            "starting [pmra, pmdec] (mas/yr); default the LVDB's adopted value, else the densest "
            "point of the central stars"))] = None,
        pm_prior_source: Annotated[str | None, Field(max_length=200)] = None,
        use_literature_pm: bool = True,
        distance_kpc: Annotated[float | None, Field(gt=0, le=10000)] = None,
        distance_source: Annotated[str | None, Field(max_length=200)] = None,
        vlos_kms: Annotated[float | None, Field(ge=-3000, le=3000)] = None,
        vlos_sigma_kms: Annotated[float | None, Field(gt=0, le=500)] = None,
        sigma_pm_mas_yr: Annotated[float | None, Field(ge=0, le=5, description=(
            "expected internal PM dispersion; default from the LVDB velocity dispersion and distance"))] = None,
        use_cmd: bool = True,
        use_rv: bool = True,
        require_clean: bool = True,
        floor_mas_yr: Annotated[float, Field(ge=0, le=1)] = 0.0,
        literature: LiteratureList = None,
        error_scale: Annotated[float, Field(ge=0.5, le=3)] = 1.0,
        max_pm_error_mas_yr: Annotated[float | None, Field(gt=0, le=100)] = None,
        show: bool = True,
    ):
        """Select likely members of the target yourself and fit them: stars within r_max
        half-light radii, parallax consistent with the distance, proper motion within 3
        sigma of a prior (each star's errors plus the expected internal dispersion), in
        the CMD region where those stars outnumber the foreground (found from the data,
        no isochrone), and with a velocity, within 3 sigma of the systemic velocity; then
        once more about the fitted proper motion. Priors default to the LVDB (distance,
        proper motion, velocity and dispersion); pass values from papers (with their
        source) to use those instead, or use_literature_pm=false to start from the data
        alone. Then fits the members like fit_members and compares with the LVDB, the
        published values and `literature`. With show, the members appear on the map as
        the layer "Members (SCOOP)", with its CMD region drawn on the CMD; the user's own
        Members layer is not changed."""
        from .. import members, pm

        field = current_field()
        if field is None:
            return _err("No field is loaded.", tool_call_id)
        if table not in field["tables"]:
            return _err(f"Unknown table {table!r}; tables: {sorted(field['tables'])}.", tool_call_id)
        rows = field["tables"][table]["rows"]
        found, _ = await target_row()
        row = found[0] if found else None
        priors = {}

        def prior(name, given, source, lvdb_value, unit):
            if given is not None:
                priors[name] = {"value": given, "from": source or "given by the agent (say where it came from)",
                                "unit": unit}
                return given
            if row is not None and lvdb_value is not None and math.isfinite(lvdb_value):
                priors[name] = {"value": lvdb_value, "from": "LVDB", "unit": unit}
                return lvdb_value
            return None

        d = prior("distance", distance_kpc, distance_source, row and row["distance"], "kpc")
        lit_pm = (row["pmra"], row["pmdec"]) if row and use_literature_pm and math.isfinite(row["pmra"]) else None
        pm0 = tuple(pm_prior) if pm_prior else lit_pm
        if pm_prior:
            priors["pm"] = {"value": list(pm_prior), "from": pm_prior_source or "given by the agent", "unit": "mas/yr"}
        elif lit_pm:
            priors["pm"] = {"value": list(lit_pm), "from": f"LVDB ({row['ref_proper_motion']})", "unit": "mas/yr"}
        v0 = prior("vlos", vlos_kms, None, row and row["vlos_systemic"], "km/s") if use_rv else None
        sv = prior("vlos_sigma", vlos_sigma_kms, None, row and row["vlos_sigma"], "km/s") if use_rv else None
        if sigma_pm_mas_yr is None:
            sigma_pm_mas_yr = pm.internal_pm_dispersion(sv, d) if sv and d else 0.0
        priors["sigma_pm"] = {"value": sigma_pm_mas_yr, "unit": "mas/yr",
                              "from": "vlos_sigma / (4.74 d)" if sv and d else "given or 0"}
        if not any(members._finite(r.get("r_ell")) for r in rows):
            return _err("This table has no r_ell (elliptical radius) column, so stars can't be ordered by "
                        "distance from the target; select members by hand on the plots.", tool_call_id)
        try:
            sel = await asyncio.to_thread(
                members.select_members, rows, distance_kpc=d, pm_prior=pm0, sigma_pm=sigma_pm_mas_yr,
                r_max=r_max or float(field.get("n_rhalf") or 3.0), vlos=v0, vlos_sigma=sv,
                require_clean=require_clean, use_cmd=use_cmd)
        except ValueError as exc:
            return _err(f"Selection failed: {exc}.", tool_call_id)
        chosen = sel.pop("members")
        out, problem = await fit_report(chosen, floor=floor_mas_yr, clip_sigma=4.0, require_clean=require_clean,
                                        max_pm_error=max_pm_error_mas_yr, literature=literature,
                                        distance=(d, priors["distance"]["from"]) if d else None,
                                        error_scale=error_scale)
        if problem:
            return _err(problem, tool_call_id)
        polygons = sel.pop("cmd_polygons")
        result = {"table": table, "priors": priors, "selection": {**sel, "n_members": len(chosen),
                                                                  "cmd_polygons": len(polygons)},
                  **out, "notes": [*members.SELECTION_NOTES, *out["notes"]]}
        if not show:
            return _ok(_clean(result), tool_call_id)
        layer_id = AGENT_MEMBERS.format(table=table)
        filt = [{"column": "r_ell", "op": "<=", "value": sel["r_max"]}]
        if polygons:
            filt.append({"column": "bp_rp", "op": "lasso", "value": {"y": "G", "polygons": polygons,
                                                                    "missing": "reject"}})
        filt.append({"column": "id", "op": "in", "value": [r["id"] for r in chosen]})  # exact: per-star cuts
        label = f"Members (SCOOP, {len(chosen)})"
        note = "SCOOP's own selection: " + "; ".join(f"{k} {v['value']} from {v['from']}" for k, v in priors.items()
                                                      if k != "sigma_pm")
        action = ({"type": "layer.update", "layerId": layer_id, "filter": filt, "label": label, "note": note[:300],
                   "visible": True} if layer_id in current else
                  {"type": "layer.add", "id": layer_id, "table": table, "filter": filt, "label": label,
                   "group": "SCOOP layers", "note": note[:300],
                   "style": {"color": "#ffd84d", "shape": "circle", "size": 14, "hollow": True}})
        current[layer_id] = {"id": layer_id, "table": table, "origin": "agent"}
        payload = {"kind": "map_actions", "status": "requested", "actions": [action]}
        return publish_result(tool_call_id, tool_call_id, payload, json.dumps(
            _clean({**result, "layer": {"id": layer_id, "label": label,
                                        "note": "requested; shown once the browser acknowledges it"}}),
            default=_json_default))

    return [pm_expectations, pm_published, pm_new_epoch, pm_wait_time, fit_members, auto_members]


__all__ = ["build_pm_tools", "field_epochs", "visits"]
