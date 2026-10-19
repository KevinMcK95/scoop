"""Functional adapter contracts and shared types.

An adapter is a pair of functions: one performs archive I/O and returns a page
of native rows; the other maps a native row to a normalized SCOOP record.
"""

from __future__ import annotations

from datetime import datetime
from dataclasses import dataclass
from typing import Any, Protocol

from .models import AdapterPage, Record, SearchRequest


class AdapterError(Exception):
    """Expected adapter failure, optionally retaining the archive query."""

    def __init__(self, message: str, *, query: str = "") -> None:
        super().__init__(message)
        self.query = query


@dataclass(frozen=True)
class Adapter:
    """Archive-specific functions and metadata, with no handler behavior."""

    archive: str
    fetch_page: FetchPage
    normalize: NormalizeRecord

    def __post_init__(self) -> None:
        if not self.archive.strip():
            raise ValueError("archive must be non-empty")


class FetchPage(Protocol):
    """Fetch one native page; cursor=None requests the first page."""

    async def __call__(
        self, request: SearchRequest, cursor: str | None = None
    ) -> AdapterPage: ...


class NormalizeRecord(Protocol):
    """Convert a native archive row to the shared record envelope."""

    def __call__(
        self,
        native_record: Any,
        request: SearchRequest,
        *,
        query: str,
        retrieved_at: datetime,
    ) -> Record: ...


def record_from_mapping(
    *,
    archive: str,
    kind: str,
    native_id: str,
    data: dict[str, Any],
    query: str,
    retrieved_at: datetime,
    access_url: str | None = None,
    raw: Any = None,
) -> Record:
    """Helper for adapters that map native rows to a simple mapping."""
    from .models import Provenance

    return Record(
        kind=kind,
        provenance=Provenance(
            archive=archive,
            native_id=str(native_id),
            query=query,
            access_url=access_url,
            retrieved_at=retrieved_at,
        ),
        data=data,
        raw=raw,
    )
