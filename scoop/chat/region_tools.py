"""Chat tools for fields beyond cones: boxes, polygons, and bands along stellar streams.

find_stream looks a stream up in galstreams. plan_region turns a shape into a region
spec, stores it as a plan, counts the Gaia rows it would return (with and without
cuts), proposes cuts for streams in the stream frame, and shows the outline on the map.
run_region builds the field from a plan, only once the user has confirmed it. The plan
id carries the spec, so a long track is passed once (to plan_region), never retyped.
"""
from __future__ import annotations

import asyncio
import json
import math
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool
from pydantic import Field

from .tools import publish_result

MAX_GAIA_ROWS = 60_000  # what the browser shows comfortably; beyond this, cut or shrink
CONFIRM_ABOVE = 500_000  # estimated Gaia sources in a request: only with the user's go-ahead
COUNT_MAX = 3_000_000  # above this an exact count takes minutes: plan on the estimate
Pair = Annotated[list[float], Field(min_length=2, max_length=2)]


def _err(text: str, tool_call_id: str) -> ToolMessage:
    return ToolMessage(content=text, tool_call_id=tool_call_id, status="error")


def proposed_stream_cuts(widths: dict) -> dict:
    """Default cuts for a stream, in its own frame: G <= 20 and a proper-motion window of
    twice galstreams' widths (at least 1 mas/yr) around the track's motion."""
    w1, w2 = widths.get("width_pm_phi1_cosphi2"), widths.get("width_pm_phi2")
    return {"gmax": 20.0, "pm_window": [round(max(1.0, 2 * (w1 or 0.75)), 2), round(max(1.0, 2 * (w2 or 0.75)), 2)]}


def _outline_points(outline: list, n: int = 64) -> list[list[float]]:
    step = max(1, math.ceil(len(outline) / n))
    return [[round(a, 5), round(d, 5)] for a, d in outline[::step]][:n]


def crowding_note(region) -> str | None:
    """A warning when the region reaches low Galactic latitude: millions of Gaia stars, so
    counts and queries take minutes and the PM cut matters most."""
    try:
        from astropy.coordinates import SkyCoord
    except ModuleNotFoundError:
        return None
    pts = region.outline()
    b = SkyCoord([p[0] for p in pts], [p[1] for p in pts], unit="deg").galactic.b.deg
    low, area = float(min(abs(b))), region.describe()["area_deg2"]
    if min(b) < 0 < max(b):
        low = 0.0
    if low >= 15 or area < 1:
        return None
    return (f"The region reaches |b| = {low:.1f} deg{' (it crosses the Galactic plane)' if low == 0 else ''}: Gaia "
            f"has ~10^5-10^6 stars per deg^2 there, so over {area:.0f} deg^2 the count alone can take minutes. "
            "Suggest a segment farther from the plane, a smaller area, or tighter cuts.")


async def diagnose_failure(exc: Exception, region=None) -> str:
    """Why a count or build failed: the error, what scoop.gaia_status finds (servers, our
    job queue), and the region's crowding, so the agent can tell the user what to change
    instead of retrying the same request."""
    from .. import gaia_status
    parts = [str(exc)]
    try:
        st = await asyncio.to_thread(gaia_status.status)
        parts += st["diagnosis"]
    except Exception as e:  # the check itself failing is a finding too
        parts.append(f"Could not check the Gaia servers ({type(e).__name__}: {e}): likely a network problem.")
    note = crowding_note(region) if region is not None else None
    if note:
        parts.append(note)
    parts.append("Do not repeat the same request unchanged; tell the user what failed and what would help.")
    return " ".join(parts)


def _region_or_none(cls, spec: dict):
    try:
        return cls.from_spec({k: v for k, v in spec.items() if k not in ("cuts", "archives")})
    except (KeyError, ValueError):
        return None


def build_region_tools(*, jobs=None, resolver=None, confirmed: bool | None = None, estimate=None) -> list:
    """``confirmed`` is the user's go-ahead as the data agent's hand-off carries it (None:
    the caller has no hand-off, so run_region's own user_confirmed decides). ``estimate``
    sizes a region (scoop.gaia_estimate.estimate); None disables sizing."""
    from .. import streams
    from ..regions import Region

    @tool
    async def find_stream(name: str, tool_call_id: Annotated[str, InjectedToolCallId]):
        """Look up a stellar stream in galstreams (Mateu 2023): every track it has for that
        stream (by different papers), with each track's extent in the stream frame (phi1,
        phi2), distances, proper motions along it (mu_phi1 cos phi2, mu_phi2), widths, and
        which track galstreams uses by default. Use a track_name with plan_region."""
        if streams.library_dir() is None:
            return _err("galstreams is not installed on this server (pip install galstreams).", tool_call_id)
        found = streams.find(name)
        if not found:
            return _err(f"galstreams has no stream matching {name!r}. Try another name, or get the track from "
                        "a paper (ads_search / ads_paper / web_fetch) and plan a band with it.", tool_call_id)
        return ToolMessage(content=json.dumps({"query": name, "citation": streams.CITATION,
                                               "galstreams_version": streams.version(),
                                               "tracks": [streams.describe(t["track_name"]) for t in found[:8]]},
                                              default=str), tool_call_id=tool_call_id)

    @tool
    async def plan_region(
        shape: Literal["box", "polygon", "band", "stream"],
        name: Annotated[str, Field(min_length=1, max_length=60, description="label, e.g. 'COSMOS 0.5 deg2'")],
        tool_call_id: Annotated[str, InjectedToolCallId],
        ra: Annotated[float | None, Field(ge=0, lt=360)] = None,
        dec: Annotated[float | None, Field(ge=-90, le=90)] = None,
        center_name: Annotated[str | None, Field(description="box centre by name (Sesame), instead of ra/dec")] = None,
        width_deg: Annotated[float | None, Field(gt=0, le=30, description=(
            "box: east-west size; band/stream: full width across the track"))] = None,
        height_deg: Annotated[float | None, Field(gt=0, le=30)] = None,
        pa_deg: float = 0.0,
        vertices: Annotated[list[Pair] | None, Field(min_length=3, max_length=200)] = None,
        track: Annotated[list[Pair] | None, Field(min_length=2, max_length=2000, description=(
            "band: [[ra, dec], ...] along the track, from a paper (with source)"))] = None,
        track_widths_deg: Annotated[list[float] | None, Field(description="band: a full width per track point")] = None,
        pm_track: Annotated[list[Pair] | None, Field(description=(
            "band: [[pmra_cosdec, pmdec], ...] mas/yr per track point, if the paper gives it"))] = None,
        source: Annotated[str | None, Field(max_length=300, description=(
            "band: where the track came from (citation / bibcode / URL)"))] = None,
        track_name: Annotated[str | None, Field(description="stream: a galstreams track, from find_stream")] = None,
        phi1_range: Annotated[Pair | None, Field(description="band/stream: [min, max] phi1 (deg) to keep")] = None,
        gmax: Annotated[float | None, Field(ge=5, le=22)] = None,
        pm_window: Annotated[Pair | None, Field(description=(
            "[w_phi1, w_phi2] mas/yr: keep stars within this of the track's proper motion, in the stream frame"))] = None,
        include: Annotated[list[Literal["hst_jwst", "qso", "spectra"]], Field(description=(
            "archives beyond Gaia and SIMBAD galaxies, only if the user asks: hst_jwst (observation "
            "footprints; slow over large areas), qso (MILLIQUAS, Quaia), spectra (SDSS/DESI)"))] = [],
    ):
        """Plan a field over a region that isn't a cone, and count it before anything is
        fetched. Shapes: box (centre + width/height in degrees on the sky, optional pa),
        polygon (vertices), band (a track from a paper: points, a width, and source; a
        pm_track enables proper-motion cuts) or stream (a galstreams track_name; its frame,
        distance and proper-motion tracks come with it). Long bands are split into pieces
        for the archives. Cuts apply to Gaia: gmax and, for bands with a proper-motion
        track, pm_window in the stream frame (relative to the track's motion at each
        star's phi1). Returns a plan_id, the region, Gaia counts with and without the cuts,
        and (streams) proposed cuts; shows the outline on the map. Then ask the user to
        confirm the region and cuts; run_region only after they do."""
        if jobs is None:
            return _err("Running new fields is not available on this server.", tool_call_id)
        spec: dict = {"kind": shape if shape != "stream" else "band", "name": name}
        proposed = None
        try:
            if shape == "box":
                if center_name and ra is None and dec is None:
                    if resolver is None:
                        return _err("Name resolution is not available; give ra and dec.", tool_call_id)
                    found = await resolver(center_name)
                    if found is None:
                        return _err(f"Sesame could not resolve {center_name!r}; try another name (e.g. "
                                    f"'{center_name} field') or give ra and dec.", tool_call_id)
                    ra, dec = found["ra"], found["dec"]
                    spec["source"] = f"centre from Sesame ({center_name!r} -> {found.get('name') or center_name})"
                if ra is None or dec is None or width_deg is None:
                    return _err("A box needs ra and dec (or center_name) and width_deg (height_deg defaults "
                                "to the width).", tool_call_id)
                spec.update(ra=ra, dec=dec, width_deg=width_deg, height_deg=height_deg or width_deg, pa_deg=pa_deg)
            elif shape == "polygon":
                if not vertices:
                    return _err("A polygon needs vertices [[ra, dec], ...].", tool_call_id)
                spec["vertices"] = vertices
            elif shape == "band":
                if not track or not source:
                    return _err("A band needs a track [[ra, dec], ...] and the source it came from (paper, "
                                "table or URL); never type a track from memory.", tool_call_id)
                if width_deg is None and not track_widths_deg:
                    return _err("A band needs width_deg (full width across the track) or track_widths_deg.",
                                tool_call_id)
                spec.update(track=track, width_deg=track_widths_deg or width_deg, source=source)
                if pm_track:
                    if len(pm_track) != len(track):
                        return _err("pm_track needs one [pmra, pmdec] per track point.", tool_call_id)
                    spec["pm_track"] = pm_track
                if phi1_range:
                    spec["phi1_range"] = phi1_range
            else:
                if streams.library_dir() is None:
                    return _err("galstreams is not installed on this server.", tool_call_id)
                if not track_name:
                    return _err("A stream needs a track_name from find_stream.", tool_call_id)
                info = streams.describe(track_name)
                w = info["widths"].get("width_phi2")
                width = width_deg or round(max(1.0, 4 * (w or 0.5)), 2)
                spec = streams.band_spec(track_name, width_deg=width, phi1_range=phi1_range, name=name)
                proposed = proposed_stream_cuts(info["widths"])
            region = Region.from_spec(spec)
        except (KeyError, ValueError) as exc:
            return _err(f"Invalid region: {exc}", tool_call_id)
        if include:
            spec["archives"] = sorted(set(include))
        cuts = {k: v for k, v in (("gmax", gmax), ("pm_window", pm_window)) if v is not None}
        if cuts.get("pm_window") and (region.kind != "band" or region.track_values("pm_track") is None):
            return _err("pm_window needs a band with a proper-motion track (a galstreams stream, or a band "
                        "with pm_track).", tool_call_id)
        counted_cuts = cuts or proposed or {}
        try:
            plan_id = jobs.save_plan({**region.to_spec(), "cuts": counted_cuts}, name)
        except ValueError as exc:
            return _err(f"Could not save the plan: {exc}", tool_call_id)
        _, _, radius = region.bounding_cone()
        actions = [{"type": "annotate", "shape": "polygon", "ra": region.ra, "dec": region.dec,
                    "text": f"{name} (planned)", "points": _outline_points(region.outline())},
                   {"type": "focus", "ra": region.ra, "dec": region.dec, "fov": min(180.0, max(0.1, 2.4 * radius))}]
        # Size it first (seconds): an exact count scans every source, so over a dense
        # region the count is itself the expensive query.
        sized = None
        if estimate is not None:
            try:
                sized = await asyncio.to_thread(estimate, region)
            except Exception as exc:
                return _err(f"Could not size the region (plan {plan_id}): " + await diagnose_failure(exc, region),
                            tool_call_id)
        big = sized is not None and sized["sources"] > CONFIRM_ABOVE
        out = {"plan_id": plan_id, "region": region.describe(), "source": spec.get("source"),
               "archives": ["gaia", "simbad_galaxies", *sorted(set(include))],
               "cuts": counted_cuts, "cuts_are": "proposed (not chosen by the user yet)" if (not cuts and proposed)
               else ("given" if cuts else "none")}
        if sized is not None:
            out["gaia_estimate"] = sized
        if region.kind == "band":
            out["cut_frame"] = ("pm_window is in the stream frame: |mu_phi1 cos phi2 - track| <= w_phi1 and "
                                "|mu_phi2 - track| <= w_phi2, with the track's motion taken at each star's phi1")
        if big and not confirmed or (sized is not None and sized["sources"] > COUNT_MAX):
            out.update(needs_confirmation=big and not confirmed, notes=[
                f"About {sized['sources']:,} Gaia sources in the region (all G; sampled range "
                f"{sized['low']:,}-{sized['high']:,}), above the {CONFIRM_ABOVE:,} that needs the user's "
                "go-ahead" + (" and too many to count exactly in reasonable time" if sized["sources"] > COUNT_MAX
                              else "") + ". Not counted exactly.",
                *([crowding_note(region)] if crowding_note(region) else []),
                "Tell the user the size and offer: a smaller area or shorter phi1 range, a brighter gmax, "
                "a tighter pm_window, or going ahead (the run counts with the cuts first and stops if more "
                f"than {MAX_GAIA_ROWS:,} rows pass)."])
            return publish_result(tool_call_id, tool_call_id, {"kind": "map_actions", "status": "requested",
                                                               "actions": actions}, json.dumps(out))
        try:
            counts = await jobs.count_plan(plan_id)
        except (ValueError, RuntimeError) as exc:
            return _err(f"Could not count the region (plan {plan_id}): " + await diagnose_failure(exc, region),
                        tool_call_id)
        out.update({
               "gaia_rows": {"in_region": counts["gaia_all"], "with_cuts_at_most": counts["gaia_with_cuts"]},
               "limit": MAX_GAIA_ROWS, "fits": counts["gaia_with_cuts"] <= MAX_GAIA_ROWS,
               "notes": [counts["note"],
                         "Fetched by default: Gaia DR3 (with the cuts) and SIMBAD galaxies. Also included: "
                         + (", ".join(sorted(set(include))) or "nothing else (HST/JWST, QSO catalogs and spectra "
                            "only if the user asks: include)") + ".",
                         "Confirm the region and cuts with the user before run_region."]})
        crowded = crowding_note(region)
        if crowded:
            out["notes"].insert(0, crowded)
        return publish_result(tool_call_id, tool_call_id, {"kind": "map_actions", "status": "requested",
                                                           "actions": actions}, json.dumps(out))

    @tool
    async def run_region(
        plan_id: str,
        user_confirmed: Annotated[bool, Field(description="true only after the user agreed to this region and cuts")],
        tool_call_id: Annotated[str, InjectedToolCallId],
        gmax: Annotated[float | None, Field(ge=5, le=22)] = None,
        pm_window: Annotated[Pair | None, Field(description="[w_phi1, w_phi2] mas/yr, stream frame")] = None,
    ):
        """Build the field for a plan from plan_region, in the background (minutes for long
        streams), once the user has confirmed the region and cuts. Passing gmax or
        pm_window replaces the plan's cuts (and recounts). The browser loads the field
        when it is ready."""
        if jobs is None:
            return _err("Running new fields is not available on this server.", tool_call_id)
        if not user_confirmed or confirmed is False:
            return _err("Ask the user to confirm the region and cuts first (show the counts from plan_region)"
                        + ("; their go-ahead reaches the data agent as the hand-off's user_confirmed."
                           if confirmed is False else "."), tool_call_id)
        plan = jobs.load_plan(plan_id)
        if plan is None:
            return _err(f"No plan {plan_id!r}; call plan_region first.", tool_call_id)
        spec = plan["spec"]
        if gmax is not None or pm_window is not None:
            cuts = dict(spec.get("cuts") or {})
            if gmax is not None:
                cuts["gmax"] = gmax
            if pm_window is not None:
                cuts["pm_window"] = pm_window
            plan_id = jobs.save_plan({**spec, "cuts": cuts}, plan["name"])
            try:
                counts = await jobs.count_plan(plan_id)
            except (ValueError, RuntimeError) as exc:
                return _err("Could not recount with the new cuts: "
                            + await diagnose_failure(exc, _region_or_none(Region, spec)), tool_call_id)
            if counts["gaia_with_cuts"] > MAX_GAIA_ROWS:
                return _err(f"With those cuts Gaia returns up to {counts['gaia_with_cuts']} rows (limit "
                            f"{MAX_GAIA_ROWS}); tighten them or shrink the region.", tool_call_id)
        try:
            job = jobs.start_region(plan_id, MAX_GAIA_ROWS)
        except ValueError as exc:
            return _err(str(exc), tool_call_id)
        used = jobs.load_plan(plan_id)["spec"].get("cuts") or {}
        return publish_result(tool_call_id, tool_call_id, {"kind": "field_job", "job": job.id, "key": job.key},
                              f"Started field {job.key!r} (job {job.id}) for plan {plan_id} with cuts "
                              f"{json.dumps(used)}; not loaded yet. Long streams take several minutes.")

    @tool
    async def gaia_status(tool_call_id: Annotated[str, InjectedToolCallId], clean_up: bool = False):
        """Check the Gaia archives before retrying anything slow or failed: whether the
        Heidelberg mirror and ESA answer a one-row query (and how fast), and the async
        job queue from this IP -- abandoned SCOOP jobs (from stopped runs) make every new
        query wait behind them. clean_up=true aborts those stale SCOOP jobs only; other
        unfinished jobs (a running field, a teammate's) are reported, never touched.
        Returns findings in plain language to pass on to the user."""
        from .. import gaia_status as gs
        try:
            st = await asyncio.to_thread(gs.status, clean_up)
        except Exception as exc:
            return _err(f"Could not check the Gaia servers ({type(exc).__name__}: {exc}): likely a network "
                        "problem on this machine.", tool_call_id)
        return ToolMessage(content=json.dumps(st, default=str), tool_call_id=tool_call_id)

    return [find_stream, plan_region, run_region, gaia_status]


__all__ = ["MAX_GAIA_ROWS", "build_region_tools", "proposed_stream_cuts"]
