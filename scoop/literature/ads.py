"""Async ADS metadata client, adapted from ~/.agent-sh/extensions/ads/index.ts.

httpx is an optional dependency of this module, not of literature.models.
No agent framework, presentation formatting, PDF I/O, or automatic retries.
"""
from __future__ import annotations

import math
import os
import re
from copy import deepcopy
from datetime import datetime, timezone
from urllib.parse import quote

import httpx

from ..domain import EntityRef, Publication
from ..models import Provenance, SearchStatus
from .models import (
    ADSError, ADSResult, BibliographicPaper, CitationsRequest, PaperRequest,
    SearchRequest,
)

SEARCH_API = "https://api.adsabs.harvard.edu/v1/search/query"
FIELDS = "bibcode,title,author,abstract,year,citation_count,doi,identifier,pub,doctype"
SORTS = {
    "relevance": "score desc", "date": "date desc",
    "citation_count": "citation_count desc", "read_count": "read_count desc",
}


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def _id_query(identifier: str) -> str:
    value = identifier.strip()
    if value.lower().startswith("doi:"):
        return "doi:" + _quoted(value[4:])
    if value.startswith("10."):
        return "doi:" + _quoted(value)
    if value.lower().startswith("arxiv:"):
        return "arxiv:" + _quoted(value[6:])
    if re.fullmatch(r"\d{4}\.\d{4,5}(?:v\d+)?|[A-Za-z-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?", value):
        return "arxiv:" + _quoted(value)
    return "bibcode:" + _quoted(value)


def _strings(value: object) -> tuple[str, ...]:
    if value is None:
        return ()
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
        raise ValueError("expected string or string list")
    return tuple(values)


def _optional_text(value: object) -> str | None:
    if value is None or value == "":
        return None
    if not isinstance(value, str):
        raise ValueError("expected text")
    return value if value.strip() else None


def _paper(doc: object, query: str, retrieved_at: datetime) -> BibliographicPaper:
    if not isinstance(doc, dict):
        raise ValueError("expected document object")
    bibcode = doc.get("bibcode")
    if not isinstance(bibcode, str) or not bibcode.strip():
        raise ValueError("missing bibcode")
    title = " ".join(_strings(doc.get("title")))
    if not title.strip():
        raise ValueError("missing title")
    year = doc.get("year")
    if isinstance(year, str) and re.fullmatch(r"\d{4}", year):
        year = int(year)
    if year is not None and (type(year) is not int or not 1 <= year <= 9999):
        raise ValueError("invalid year")
    count = doc.get("citation_count")
    if count is not None and (type(count) is not int or count < 0):
        raise ValueError("invalid citation count")
    identifiers = {"bibcode": bibcode}
    dois = _strings(doc.get("doi"))
    if dois:
        identifiers["doi"] = dois[0]
    # Publication has scalar identifier labels; retain all other values in raw.
    for identifier in _strings(doc.get("identifier")):
        if identifier.lower().startswith("arxiv:"):
            identifiers.setdefault("arxiv", identifier[6:])
    provenance = Provenance(
        archive="ADS", native_id=bibcode, query=query,
        access_url="https://ui.adsabs.harvard.edu/abs/" + quote(bibcode, safe=""),
        retrieved_at=retrieved_at,
    )
    publication = Publication(
        ref=EntityRef("publication", "ADS", bibcode), provenance=provenance,
        title=title, identifiers=identifiers,
        publication_type=_optional_text(doc.get("doctype")) or "paper",
    )
    return BibliographicPaper(
        publication=publication, authors=_strings(doc.get("author")),
        abstract=_optional_text(doc.get("abstract")), year=year,
        venue=_optional_text(doc.get("pub")), citation_count=count, raw=deepcopy(doc),
    )


class ADSClient:
    """One request per operation. Injected clients remain caller-owned.

    Without injection use ``async with ADSClient(...) as ads``. Token lookup is
    lazy and server-side; missing configuration is an auth outcome, not an import
    error. timeout is a finite positive httpx per-phase timeout in seconds.
    """

    def __init__(
        self, token: str | None = None, *, client: httpx.AsyncClient | None = None,
        timeout: float = 30.0,
    ) -> None:
        if token is not None and not isinstance(token, str):
            raise ValueError("token must be a string or None")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        self._token = token
        self._client = client
        self._owns_client = client is None
        self._timeout = float(timeout)

    async def __aenter__(self) -> ADSClient:
        if self._owns_client:
            if self._client is not None:
                raise RuntimeError("ADSClient context is already active")
            self._client = httpx.AsyncClient(timeout=self._timeout, follow_redirects=False)
        return self

    async def __aexit__(self, *exc: object) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
            self._client = None

    async def search(self, request: SearchRequest) -> ADSResult:
        if not isinstance(request, SearchRequest):
            raise ValueError("expected SearchRequest")
        filters = []
        if request.database is not None:
            filters.append(f"database:{request.database}")
        if request.doctype is not None:
            filters.append(f"doctype:{request.doctype}")
        if request.refereed is not None:
            filters.append("property:refereed" if request.refereed else "-property:refereed")
        if request.year_from is not None or request.year_to is not None:
            filters.append(f"year:[{request.year_from or '*'} TO {request.year_to or '*'}]")
        return await self._fetch(request, request.query, request.page_size, request.start, request.sort_by, filters)

    async def get_paper(self, request: PaperRequest) -> ADSResult:
        if not isinstance(request, PaperRequest):
            raise ValueError("expected PaperRequest")
        return await self._fetch(request, _id_query(request.id), 1, 0, "relevance", [])

    async def citations(self, request: CitationsRequest) -> ADSResult:
        if not isinstance(request, CitationsRequest):
            raise ValueError("expected CitationsRequest")
        query = f"{request.direction}({_quoted(request.bibcode)})"
        return await self._fetch(request, query, request.page_size, request.start, request.sort_by, [])

    async def _fetch(self, request, query, page_size, start, sort_by, filters) -> ADSResult:
        def failure(kind, message, status=None, retry_after=None):
            error = ADSError(kind, message, status, retry_after)
            outcome = SearchStatus.TIMED_OUT if kind == "timeout" else SearchStatus.FAILED
            return ADSResult(request, query, outcome, error=error)

        token = self._token if self._token is not None else os.environ.get("ADS_API_TOKEN")
        if not token or any(not 33 <= ord(char) <= 126 for char in token):
            return failure("auth", "ADS token is missing or invalid")
        if self._client is None:
            raise RuntimeError("Use ADSClient as an async context manager or inject an AsyncClient")
        params = [("q", query), ("fl", FIELDS), ("rows", str(page_size)),
                  ("start", str(start)), ("sort", SORTS[sort_by])]
        params.extend(("fq", value) for value in filters)
        try:
            response = await self._client.get(
                SEARCH_API, params=params, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
                timeout=self._timeout, follow_redirects=False,
            )
        except httpx.TimeoutException:
            return failure("timeout", "ADS request timed out")
        except httpx.RequestError:
            return failure("http", "ADS transport failed")
        if not 200 <= response.status_code < 300:
            status = response.status_code
            kind = "auth" if status in (401, 403) else "rate_limit" if status == 429 else "http"
            # Never include response bodies, exception text, URLs, or request headers.
            return failure(kind, "ADS request failed", status, response.headers.get("retry-after"))
        try:
            data = response.json()
            envelope = data.get("response", data) if isinstance(data, dict) else None
            if not isinstance(envelope, dict):
                raise ValueError("invalid response envelope")
            docs = envelope.get("docs")
            total = envelope.get("numFound")
            if not isinstance(docs, list) or type(total) is not int or total < 0:
                raise ValueError("missing or invalid docs/numFound")
            if len(docs) > page_size or (docs and start + len(docs) > total):
                raise ValueError("inconsistent pagination")
        except (ValueError, TypeError):
            return failure("invalid_response", "ADS returned malformed JSON or an invalid response schema")
        papers = []
        warnings = []
        retrieved_at = datetime.now(timezone.utc)
        for index, doc in enumerate(docs):
            try:
                papers.append(_paper(doc, query, retrieved_at))
            except (ValueError, TypeError):
                warnings.append(f"Document at page offset {index} has invalid bibliographic metadata")
        next_start = start + len(docs) if docs and start + len(docs) < total else None
        if not docs and start < total:
            warnings.append("ADS returned an empty page before total_count; completeness is unknown")
        if warnings:
            status = SearchStatus.PARTIAL if papers or not docs else SearchStatus.FAILED
            error = ADSError("invalid_response", "ADS returned incomplete or invalid documents")
        else:
            status = SearchStatus.SUCCESS if papers else SearchStatus.EMPTY
            error = None
        return ADSResult(
            request=request, query=query, status=status, papers=tuple(papers),
            total_count=total, next_start=next_start, warnings=tuple(warnings), error=error,
        )
