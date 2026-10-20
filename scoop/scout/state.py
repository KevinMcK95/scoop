"""What the scout has already judged, so a monthly scan is incremental.

Without this, every scan re-reads the same papers, re-asks the model about them
and re-proposes the same drafts. The state file records one verdict per bibcode
and the date each survey was last proposed; both are small, human-readable and
safe to delete (a scan with no state just re-judges everything).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

DEFAULT_WINDOW_DAYS = 31  # one month, the intended cadence


@dataclass
class State:
    """Verdicts keyed by bibcode, and proposal dates keyed by survey slug."""

    last_scan: str | None = None
    verdicts: dict[str, str] = field(default_factory=dict)
    proposed: dict[str, str] = field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> State:
        if not path.exists():
            return cls()
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise ValueError(f"{path} is not readable scout state: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"{path} is not readable scout state: expected an object")
        last = data.get("last_scan")
        return cls(
            last_scan=last if isinstance(last, str) else None,
            verdicts={k: v for k, v in (data.get("verdicts") or {}).items() if isinstance(v, str)},
            proposed={k: v for k, v in (data.get("proposed") or {}).items() if isinstance(v, str)},
        )

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"last_scan": self.last_scan, "verdicts": self.verdicts,
                                    "proposed": self.proposed}, indent=1, sort_keys=True) + "\n",
                        encoding="utf-8")

    def window(self, until: date, *, days: int = DEFAULT_WINDOW_DAYS) -> date:
        """The start of the period to harvest: the last scan, or ``days`` back.

        A late scan covers the whole gap rather than only the last month, so
        skipping a month loses nothing.
        """
        if self.last_scan:
            try:
                return date.fromisoformat(self.last_scan)
            except ValueError:
                pass
        return until - timedelta(days=days)

    def judged(self, bibcode: str) -> str | None:
        return self.verdicts.get(bibcode)
