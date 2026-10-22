"""Stellar-stream tracks from galstreams (Mateu 2023), as region bands.

Reads the library's files directly (master log, track and summary tables), without
importing galstreams: its MWStreams start-up computes angular-momentum tracks with
gala's reflex correction, which fails with astropy 8 (seen 2026-10-20), and SCOOP
needs only the tracks. Each track's frame is built as galstreams builds it
(gala.GreatCircleICRSFrame.from_pole_ra0 with the track's mid-point pole and RA),
here with scoop.science.regions.GreatCircleFrame.
"""
from __future__ import annotations

import csv
import importlib.metadata
import importlib.util
import re
from functools import lru_cache
from pathlib import Path

import numpy as np

from .regions import GreatCircleFrame

CITATION = "galstreams (Mateu 2023, MNRAS 520, 5225)"


def library_dir() -> Path | None:
    spec = importlib.util.find_spec("galstreams")
    if spec is None or not spec.submodule_search_locations:
        return None
    return Path(list(spec.submodule_search_locations)[0])


def version() -> str | None:
    try:
        return importlib.metadata.version("galstreams")
    except importlib.metadata.PackageNotFoundError:
        return None


def _number(v: str) -> float | None:
    try:
        x = float(v)
    except ValueError:
        return None
    return None if x in (-1.0, -9.0) else x  # galstreams' "not available" sentinels


@lru_cache(maxsize=1)
def catalog() -> tuple[dict, ...]:
    """Every track in the library: track_name, stream, reference, default (On) flag,
    and the widths the master log gives (None when it defers to the members)."""
    root = library_dir()
    if root is None:
        return ()
    lines = (root / "lib" / "master_log.txt").read_text().splitlines()
    header = lines[0].lstrip("#").split()
    out = []
    for line in lines[1:]:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        row = dict(zip(header, line.split()))
        out.append({"track_name": row["TrackName"], "stream": row["Name"], "reference": row["TrackRefs"],
                    "kind": row["Imp"], "default": row["On"] == "1",
                    "width_phi2_deg": _number(row.get("width_phi2", "-1")),
                    "width_pm_phi1_mas_yr": _number(row.get("width_pm_phi1_cosphi2", "-1")),
                    "width_pm_phi2_mas_yr": _number(row.get("width_pm_phi2", "-1"))})
    return tuple(out)


def _key(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", s.lower())


def find(query: str) -> list[dict]:
    """Tracks whose stream or track name matches the query (e.g. "GD1", "gd-1", "Pal 5")."""
    q = _key(query)
    exact = [t for t in catalog() if _key(t["stream"]) == q or _key(t["track_name"]) == q]
    return exact or [t for t in catalog() if q and (q in _key(t["stream"]) or q in _key(t["track_name"]))]


def _read_csv(path: Path) -> list[dict]:
    with path.open() as fh:
        return list(csv.DictReader(line for line in fh if not line.startswith("#")))


@lru_cache(maxsize=64)
def track(track_name: str) -> dict:
    """One track: points (ra, dec, distance, pm, rv), its frame, widths and flags."""
    entry = next((t for t in catalog() if t["track_name"] == track_name), None)
    if entry is None:
        raise KeyError(f"no galstreams track {track_name!r}")
    stem = library_dir() / "tracks" / f"track.{entry['kind']}.{entry['stream']}.{entry['reference']}"
    rows = _read_csv(stem.with_name(stem.name + ".ecsv"))
    summary = _read_csv(stem.with_name(stem.name + ".summary.ecsv"))[0]
    col = lambda k: np.array([float(r[k]) for r in rows])  # noqa: E731
    flags = summary.get("InfoFlags", "0000")
    frame = GreatCircleFrame.from_pole_ra0(float(summary["pole.ra"]), float(summary["pole.dec"]),
                                           float(summary["mid.ra"]),
                                           disambiguate=(float(summary["mid.ra"]), float(summary["mid.dec"])))
    widths = {k: _number(summary.get(k, "-1")) for k in ("width_phi2", "width_pm_phi1_cosphi2", "width_pm_phi2")}
    return {**entry, "ra": col("ra"), "dec": col("dec"), "distance": col("distance"),
            "pmra": col("pm_ra_cosdec"), "pmdec": col("pm_dec"), "rv": col("radial_velocity"),
            "frame": frame, "widths": widths,
            # InfoFlags, one character per bit: 0 great circle by construction; 1 distance,
            # 2 proper-motion, 3 velocity track available (else a single value).
            "has": {"distance_track": flags[1:2] == "1", "pm_track": flags[2:3] == "1", "rv_track": flags[3:4] == "1"}}


def describe(track_name: str) -> dict:
    """What a tool reports about a track: extent, distances, widths, proper motions in
    the stream frame along it."""
    t = track(track_name)
    f = t["frame"]
    phi1, phi2 = f.to_frame(t["ra"], t["dec"])
    p1, p2 = f.pm_to_frame(t["ra"], t["dec"], t["pmra"], t["pmdec"])
    rng = lambda a: [round(float(np.min(a)), 3), round(float(np.max(a)), 3)]  # noqa: E731
    return {"track_name": track_name, "stream": t["stream"], "reference": t["reference"], "default": t["default"],
            "points": len(phi1), "phi1_range_deg": rng(phi1), "phi2_range_deg": rng(phi2),
            "length_deg": round(float(np.ptp(phi1)), 2),
            "distance_kpc": rng(t["distance"]), "pm_phi1_cosphi2_mas_yr": rng(p1), "pm_phi2_mas_yr": rng(p2),
            "widths": t["widths"], "tracks_available": t["has"], "frame": f.to_spec(),
            "source": f"{CITATION}, track {track_name} ({t['reference']}), galstreams {version()}"}


def band_spec(track_name: str, *, width_deg: float, phi1_range: list[float] | None = None,
              max_points: int = 400, name: str | None = None) -> dict:
    """A Region "band" spec along a galstreams track, in that track's frame, carrying the
    proper-motion and distance tracks so cuts can be made relative to the stream."""
    t = track(track_name)
    step = max(1, len(t["ra"]) // max_points)
    idx = np.unique(np.r_[np.arange(0, len(t["ra"]), step), len(t["ra"]) - 1])
    r6 = lambda a: [round(float(x), 6) for x in a]  # noqa: E731
    spec = {"kind": "band", "name": name or t["stream"], "track": [[a, d] for a, d in zip(r6(t["ra"][idx]), r6(t["dec"][idx]))],
            "width_deg": float(width_deg), "frame": t["frame"].to_spec(),
            "pm_track": [[a, d] for a, d in zip(r6(t["pmra"][idx]), r6(t["pmdec"][idx]))],
            "distance_track": r6(t["distance"][idx]),
            "source": describe(track_name)["source"], "track_name": track_name}
    if phi1_range is not None:
        spec["phi1_range"] = [float(phi1_range[0]), float(phi1_range[1])]
    return spec


__all__ = ["CITATION", "band_spec", "catalog", "describe", "find", "library_dir", "track", "version"]
