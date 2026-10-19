"""Exact handling of 64-bit catalog IDs (Gaia source_id, DESI targetid, SDSS IDs).

Gaia source_ids have 19 digits. A float64 holds only ~15-16 significant digits,
so passing an ID through float silently turns it into a *different* source's ID
(e.g. 1433143832631934848 -> 1433143832631934976). Always keep IDs as integers
(int64) or strings; never as floats.
"""

from __future__ import annotations

import numpy as np

ID_COLUMNS = ("source_id", "gaia_source_id", "survey_gaia_id", "targetid", "GaiaDR3",
              "dr2_source_id", "dr3_source_id")


def to_int_id(x) -> int:
    """Integer ID from an int, numpy int or string (0 if missing). Never via float."""
    if isinstance(x, (int, np.integer)):
        return int(x) if int(x) > 0 else 0
    if isinstance(x, (float, np.floating)):
        raise TypeError(f"ID given as a float ({x!r}); it may already have been rounded")
    v = str(x).strip()
    if v in ("", "--", "None", "nan", "-999"):
        return 0
    v = int(v)
    return v if v > 0 else 0


def check_id_columns(table, what: str) -> None:
    """Raise if any ID column in an astropy Table is not an integer type."""
    for c in ID_COLUMNS:
        if c in table.colnames and table[c].dtype.kind not in "iu":
            raise TypeError(f"{what}: ID column {c!r} has dtype {table[c].dtype}, not an "
                            f"integer; IDs must never be floats (they lose digits)")
