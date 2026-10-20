"""Standard observation columns for footprint tables from any archive.

The HST/JWST observations table uses tel, inst, year, date, exptime and program;
tables the data agent finds through ObsCore get the same columns, derived from
the ObsCore ones, so the chat agent's tools (overlaps, region, summarize,
filters) treat every footprint table alike. No third-party dependencies.
"""
from __future__ import annotations

from datetime import datetime

# The columns the HST/JWST observations table uses, so the chat agent's tools
# (overlaps, region, summarize, filters) treat every footprint table alike.
OBS_COLUMNS = {
    "tel": {"unit": None, "description": "facility (ObsCore facility_name or obs_collection)"},
    "inst": {"unit": None, "description": "instrument (ObsCore instrument_name)"},
    "year": {"unit": "yr", "description": "mid-exposure decimal year (from ObsCore t_min/t_max)"},
    "date": {"unit": None, "description": "observation start date (from ObsCore t_min)"},
    "exptime": {"unit": "s", "description": "exposure time (ObsCore t_exptime)"},
    "wavelength_nm": {"unit": "nm", "description": "central wavelength (from ObsCore em_min/em_max)"},
    "program": {"unit": None, "description": "proposal / program id (ObsCore proposal_id)"},
}


def _number(value) -> float | None:
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def add_observation_columns(row: dict) -> dict:
    """Derive tel/inst/year/date/exptime/wavelength_nm/program from ObsCore columns."""
    from datetime import timedelta

    t0, t1 = _number(row.get("t_min")), _number(row.get("t_max"))
    mjd = (t0 + t1) / 2 if t0 is not None and t1 is not None else t0 if t0 is not None else t1
    if mjd is not None and 10000 < mjd < 100000:  # plausible MJD (1886-2132)
        row["year"] = round(2000.0 + (mjd - 51544.5) / 365.25, 3)
        start = t0 if t0 is not None else mjd
        row["date"] = (datetime(1858, 11, 17) + timedelta(days=start)).date().isoformat()
    tel = row.get("facility_name") or row.get("obs_collection")
    if tel and "tel" not in row:
        row["tel"] = tel
    if row.get("instrument_name") and "inst" not in row:
        row["inst"] = row["instrument_name"]
    if _number(row.get("t_exptime")) is not None and "exptime" not in row:
        row["exptime"] = row["t_exptime"]
    lo, hi = _number(row.get("em_min")), _number(row.get("em_max"))
    if lo is not None and hi is not None and 0 < lo <= hi < 1:  # metres
        row["wavelength_nm"] = round((lo + hi) / 2 * 1e9, 1)
    if row.get("proposal_id") and "program" not in row:
        row["program"] = row["proposal_id"]
    return row



__all__ = ["OBS_COLUMNS", "add_observation_columns"]
