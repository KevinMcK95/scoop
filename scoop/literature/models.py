"""Framework-independent request and result values for the ADS service."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from .records import Publication, SearchStatus

SORTS = ("relevance", "date", "citation_count", "read_count")
DATABASES = ("astronomy", "physics", "general")
DOCTYPES = (
    "article", "eprint", "inproceedings", "proceedings", "inbook", "book",
    "phdthesis", "mastersthesis", "catalog", "software", "proposal",
)


def _text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _integer(value: int, name: str, minimum: int, maximum: int | None = None) -> None:
    if type(value) is not int or value < minimum or (maximum is not None and value > maximum):
        raise ValueError(f"{name} must be an integer in the permitted range")


def _choice(value: str, choices: tuple[str, ...], name: str) -> None:
    if not isinstance(value, str) or value not in choices:
        raise ValueError(f"invalid {name}")


def _page(page_size: int, start: int, sort_by: str) -> None:
    _integer(page_size, "page_size", 1, 50)
    _integer(start, "start", 0)
    _choice(sort_by, SORTS, "sort_by")


@dataclass(frozen=True)
class SearchRequest:
    query: str
    page_size: int = 10
    start: int = 0
    sort_by: str = "relevance"
    database: str | None = None
    doctype: str | None = None
    refereed: bool | None = None
    year_from: int | None = None
    year_to: int | None = None

    def __post_init__(self) -> None:
        _text(self.query, "query")
        _page(self.page_size, self.start, self.sort_by)
        if self.database is not None:
            _choice(self.database, DATABASES, "database")
        if self.doctype is not None:
            _choice(self.doctype, DOCTYPES, "doctype")
        if self.refereed is not None and type(self.refereed) is not bool:
            raise ValueError("refereed must be a bool or None")
        for name in ("year_from", "year_to"):
            value = getattr(self, name)
            if value is not None:
                _integer(value, name, 1, 9999)
        if self.year_from is not None and self.year_to is not None and self.year_from > self.year_to:
            raise ValueError("year_from must not exceed year_to")


@dataclass(frozen=True)
class PaperRequest:
    """An exact bibcode, DOI (optionally doi:), or arXiv ID (optionally arXiv:)."""

    id: str

    def __post_init__(self) -> None:
        _text(self.id, "id")
        value = self.id.strip()
        if value.lower().startswith(("doi:", "arxiv:")):
            _text(value.split(":", 1)[1], "identifier")


@dataclass(frozen=True)
class CitationsRequest:
    bibcode: str
    direction: Literal["citations", "references"] = "citations"
    page_size: int = 10
    start: int = 0
    sort_by: str = "date"

    def __post_init__(self) -> None:
        _text(self.bibcode, "bibcode")
        _choice(self.direction, ("citations", "references"), "direction")
        _page(self.page_size, self.start, self.sort_by)


@dataclass(frozen=True)
class BibliographicPaper:
    publication: Publication
    authors: tuple[str, ...] = ()
    abstract: str | None = None
    year: int | None = None
    venue: str | None = None
    citation_count: int | None = None
    raw: dict[str, Any] | None = None


@dataclass(frozen=True)
class ADSError:
    kind: Literal["auth", "rate_limit", "http", "timeout", "invalid_response"]
    message: str
    http_status: int | None = None
    retry_after: str | None = None


@dataclass(frozen=True)
class ADSResult:
    request: SearchRequest | PaperRequest | CitationsRequest
    query: str
    status: SearchStatus
    papers: tuple[BibliographicPaper, ...] = ()
    total_count: int | None = None
    next_start: int | None = None
    warnings: tuple[str, ...] = ()
    error: ADSError | None = None
    retrieved_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
