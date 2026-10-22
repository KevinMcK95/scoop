"""Optional LangGraph wrappers around the same ADS client used by the web API.

Requires both the ``chat`` and ``literature`` extras. The host owns client
lifecycle; these tools never read browser credentials or download files.
"""
from __future__ import annotations

from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import Field, StringConstraints

from ..agents.tools import publish_result
from .ads import ADSClient
from .models import ADSResult, CitationsRequest, PaperRequest, SearchRequest
from .serialization import result_payload

NonEmptyText = Annotated[str, StringConstraints(strict=True, strip_whitespace=True, min_length=1)]
PageSize = Annotated[int, Field(strict=True, ge=1, le=50)]
Offset = Annotated[int, Field(strict=True, ge=0)]
Year = Annotated[int, Field(strict=True, ge=1, le=9999)]
Sort = Literal["relevance", "date", "citation_count", "read_count"]


def _summary(result: ADSResult, *, detail: bool = False) -> str:
    lines = [
        f"ADS {result.status.value}: returned {len(result.papers)} records; "
        f"total matches: {result.total_count if result.total_count is not None else 'unknown'}.",
        f"Query: {result.query}",
    ]
    if result.error:
        lines.append(f"Error: {result.error.kind}: {result.error.message}")
        if result.error.retry_after:
            lines.append(f"Retry-After: {result.error.retry_after}")
    for paper in result.papers[:5]:
        lines.append(f"[{paper.publication.ref.native_id}] {paper.publication.title}")
        if detail:
            lines.append(f"Authors: {'; '.join(paper.authors)}")
            lines.append(f"ADS: {paper.publication.provenance.access_url}")
            abstract = paper.abstract or "Abstract unavailable."
            lines.append("Abstract: " + abstract[:6000])
            if len(abstract) > 6000:
                lines.append("[Abstract truncated in this message; full text is in the result artifact.]")
    if len(result.papers) > 5:
        lines.append("Additional returned papers are stored in the artifact.")
    if result.next_start is not None:
        lines.append(f"More matches available: start={result.next_start}.")
    lines.extend(result.warnings)
    lines.append("Search matches are not proof that a paper discusses a selected object.")
    return "\n".join(lines)


def build_ads_tools(client: ADSClient) -> list[BaseTool]:
    """Bind a server-owned ADS client. Use an active client for graph invocations."""

    @tool
    async def ads_search(
        query: NonEmptyText,
        tool_call_id: Annotated[str, InjectedToolCallId],
        page_size: PageSize = 10,
        start: Offset = 0,
        sort_by: Sort = "relevance",
        database: Literal["astronomy", "physics", "general"] | None = None,
        doctype: Literal[
            "article", "eprint", "inproceedings", "proceedings", "inbook", "book",
            "phdthesis", "mastersthesis", "catalog", "software", "proposal",
        ] | None = None,
        refereed: Annotated[bool, Field(strict=True)] | None = None,
        year_from: Year | None = None,
        year_to: Year | None = None,
    ):
        """Search NASA ADS using its fielded query syntax, filters, and pagination.

        Use abs: for topics or target-name text. ADS UI object: queries require
        a separate object-service translation, not implemented by this tool;
        do not submit object: directly. Matches do not imply object association
        or complete literature coverage. Use ads_paper for abstracts.
        """
        try:
            request = SearchRequest(
                query=query, page_size=page_size, start=start, sort_by=sort_by,
                database=database, doctype=doctype, refereed=refereed,
                year_from=year_from, year_to=year_to,
            )
        except ValueError as exc:
            return ToolMessage(
                content=f"Invalid ADS arguments: {exc}",
                tool_call_id=tool_call_id, status="error",
            )
        result = await client.search(request)
        return publish_result(tool_call_id, tool_call_id, result_payload(result), _summary(result))

    @tool
    async def ads_paper(id: NonEmptyText, tool_call_id: Annotated[str, InjectedToolCallId]):
        """Retrieve a paper and its abstract by exact ADS bibcode, DOI, or arXiv ID."""
        try:
            request = PaperRequest(id)
        except ValueError as exc:
            return ToolMessage(
                content=f"Invalid ADS arguments: {exc}",
                tool_call_id=tool_call_id, status="error",
            )
        result = await client.get_paper(request)
        return publish_result(
            tool_call_id, tool_call_id, result_payload(result), _summary(result, detail=True)
        )

    @tool
    async def ads_citations(
        bibcode: NonEmptyText,
        tool_call_id: Annotated[str, InjectedToolCallId],
        direction: Literal["citations", "references"] = "citations",
        page_size: PageSize = 10,
        start: Offset = 0,
        sort_by: Sort = "date",
    ):
        """List papers citing an ADS bibcode, or the references that it cites."""
        result = await client.citations(CitationsRequest(
            bibcode=bibcode, direction=direction, page_size=page_size,
            start=start, sort_by=sort_by,
        ))
        return publish_result(tool_call_id, tool_call_id, result_payload(result), _summary(result))

    return [ads_search, ads_paper, ads_citations]
