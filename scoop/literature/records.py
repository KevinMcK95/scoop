"""Provenance-bearing literature records: where a publication came from, and its status.

Native records are identified by (kind, archive, native_id), exactly: no whitespace
normalization, name resolution or crossmatching. Names and proximity alone are not
identity evidence, and a successful query is not a claim of completeness.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from types import MappingProxyType
from typing import Mapping


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


@dataclass(frozen=True)
class Provenance:
    """Origin and retrieval context for one record."""

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


@dataclass(frozen=True)
class EntityRef:
    """Exact, case-sensitive composite identity (kind, archive, native_id)."""

    kind: str
    archive: str
    native_id: str

    def __post_init__(self) -> None:
        for name in ("kind", "archive", "native_id"):
            _nonempty(getattr(self, name), name)


@dataclass(frozen=True)
class Publication:
    """Native publication with opaque identifier labels (e.g. DOI or bibcode).

    Identifier mappings are copied and made read-only; no identifier resolution
    or bibliographic deduplication is performed.
    """

    ref: EntityRef
    provenance: Provenance
    title: str
    identifiers: Mapping[str, str] = field(default_factory=dict)
    publication_type: str = "paper"

    def __post_init__(self) -> None:
        if self.ref.kind != "publication":
            raise ValueError("ref kind must be publication")
        if self.ref.archive != self.provenance.archive or self.ref.native_id != self.provenance.native_id:
            raise ValueError("ref must match provenance archive and native_id")
        _nonempty(self.title, "title")
        _nonempty(self.publication_type, "publication_type")
        for key, value in self.identifiers.items():
            _nonempty(key, "identifier key")
            _nonempty(value, "identifier value")
        object.__setattr__(self, "identifiers", MappingProxyType(dict(self.identifiers)))


class SearchStatus(str, Enum):
    SUCCESS = "success"
    EMPTY = "empty"
    PARTIAL = "partial"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


__all__ = ["EntityRef", "Provenance", "Publication", "SearchStatus"]
