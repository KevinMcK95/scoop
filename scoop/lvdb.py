"""Find Local Volume Database objects by name, for choosing a field to run.

Reads the LVDB table the smoke-test pipeline caches under
``<fields>/<dir>/cache/`` (``CACHE_NAME``) (astropy needed to read it).
"""
from __future__ import annotations

import re
from pathlib import Path

# The Local Volume Database release every part of SCOOP reads (pipeline, name search, PM tools).
LVDB_RELEASE = "v1.1.1"
LVDB_BASE = f"https://github.com/apace7/local_volume_database/releases/download/{LVDB_RELEASE}/"
CACHE_NAME = f"lvdb_comb_all_{LVDB_RELEASE}.ecsv"  # as the pipeline caches comb_all.ecsv


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", str(text).lower())


class LVDBIndex:
    def __init__(self, fields_root: Path) -> None:
        self.root = Path(fields_root)
        self._rows: list[dict] | None = None

    def _load(self) -> list[dict] | None:
        if self._rows is None:
            path = next(iter(sorted(self.root.glob(f"*/cache/{CACHE_NAME}"))), None)
            if path is None:
                path = self._shared_copy()
            if path is None:
                return None
            from astropy.table import Table

            table = Table.read(path)
            num = lambda v: float(v) if v is not None and str(v) not in ("--", "nan") else None  # noqa: E731
            self._rows = [{"key": str(r["key"]), "name": str(r["name"]), "ra": num(r["ra"]), "dec": num(r["dec"]),
                           "rhalf_arcmin": num(r["rhalf"]), "distance_kpc": num(r["distance"])}
                          for r in table]
        return self._rows

    def _shared_copy(self) -> Path | None:
        """The table the proper-motion tools keep in <fields>/lvdb_cache/, downloaded
        once if no field has cached it yet: on a fresh install find_lvdb had nothing to
        search, so a dwarf galaxy became a plain cone (2026-10-21)."""
        from .pm import LVDB_RELEASE, load_lvdb

        path = self.root / "lvdb_cache" / f"lvdb_{LVDB_RELEASE}_comb_all.ecsv"
        if not path.exists():
            try:
                load_lvdb(path.parent)
            except Exception:  # offline: no index yet, as before
                return None
        return path if path.exists() else None

    def search(self, text: str, limit: int = 10) -> list[dict] | None:
        """Objects whose key or name contains the text (ignoring case, spaces and
        punctuation), exact matches first; None if the index isn't cached yet."""
        rows = self._load()
        if rows is None:
            return None
        query = _norm(text)
        if not query:
            return []
        hits = [r for r in rows if query in _norm(r["key"]) or query in _norm(r["name"])]
        hits.sort(key=lambda r: (_norm(r["name"]) != query and _norm(r["key"]) != query, len(r["name"])))
        return hits[:limit]

    def has(self, key: str) -> bool | None:
        rows = self._load()
        return None if rows is None else any(r["key"] == key for r in rows)


__all__ = ["CACHE_NAME", "LVDBIndex"]
