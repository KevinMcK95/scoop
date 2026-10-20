"""Small, archive-neutral models shared by handlers and adapters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping, TypeAlias


@dataclass(frozen=True)
class Cone:
    """Circular ICRS sky region. Angles are expressed in degrees."""

    ra_deg: float
    dec_deg: float
    radius_deg: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.ra_deg < 360.0:
            raise ValueError("ra_deg must be in [0, 360)")
        if not -90.0 <= self.dec_deg <= 90.0:
            raise ValueError("dec_deg must be in [-90, 90]")
        if not 0.0 < self.radius_deg <= 180.0:
            raise ValueError("radius_deg must be in (0, 180]")


@dataclass(frozen=True)
class Provenance:
    """Origin and retrieval context for one normalized record."""

    archive: str
    native_id: str
    query: str
    access_url: str | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        if not self.archive.strip():
            raise ValueError("archive must be non-empty")
        if not self.native_id.strip():
            raise ValueError("native_id must be non-empty")
        if self.retrieved_at.tzinfo is None:
            raise ValueError("retrieved_at must be timezone-aware")


RecordPayload: TypeAlias = Mapping[str, Any]


@dataclass(frozen=True)
class Record:
    """A normalized record while preserving its archive-native payload."""

    kind: str
    provenance: Provenance
    data: RecordPayload
    raw: Any = None

    def __post_init__(self) -> None:
        if not self.kind.strip():
            raise ValueError("kind must be non-empty")


class SearchStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
