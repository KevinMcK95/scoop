"""Proper-motion expectations for a Local Volume Database object, on the command line.

    python smoke_tests/pm_expectations.py --lvdb-key draco_1 [--n-stars 200] [--star-error 0.03]

The calculations live in scoop/science/pm.py (also used by the chat agent's proper-motion
tools); see scoop/skills/astrometry/proper_motions.md.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from scoop.science import pm


def main():
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--lvdb-key", default="draco_1")
    p.add_argument("--n-stars", type=int, default=100)
    p.add_argument("--star-error", type=float, default=0.05, help="per-star PM error, mas/yr")
    p.add_argument("--floor", type=float, default=0.0, help="systematic floor, mas/yr")
    p.add_argument("--radius-deg", type=float, default=None,
                   help="region radius for the across-region checks (default 2 r_half)")
    p.add_argument("--cache", type=Path, default=Path("out/lvdb_cache"))
    a = p.parse_args()
    comb, pms = pm.load_lvdb(a.cache)
    row = pm.lvdb_row(comb, a.lvdb_key)
    if row is None:
        raise SystemExit(f"No LVDB object {a.lvdb_key!r}")
    rows = pm.published(pms, a.lvdb_key)
    out = pm.expectations(row, n_stars=a.n_stars, star_error=a.star_error, floor=a.floor,
                          radius_deg=a.radius_deg, published_rows=rows)
    out["published_agreement"] = pm.reconcile(rows)
    print(json.dumps(out, indent=1, default=str))


if __name__ == "__main__":
    main()
