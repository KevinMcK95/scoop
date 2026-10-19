"""Functional orchestration for archive adapter functions."""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from .adapters import Adapter, AdapterError
from .models import HandlerReport, SearchRequest, SearchStatus


def make_handler(*, adapter: Adapter, timeout_seconds: float = 60.0):
    """Build a search function around an adapter's I/O and mapping functions.

    This closure is the handler/adapter boundary: the handler owns pagination,
    timeouts and reporting; ``fetch_page`` and ``normalize`` own service details.
    """
    if timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be positive")

    async def search(request: SearchRequest) -> HandlerReport:
        started = datetime.now(timezone.utc)
        records = []
        warnings: list[str] = []
        pages_fetched = 0
        total_count: int | None = None
        last_query = ""
        cursor: str | None = None
        seen_cursors: set[str] = set()
        truncated = False

        try:
            async with asyncio.timeout(timeout_seconds):
                while True:
                    if request.max_pages is not None and pages_fetched >= request.max_pages:
                        truncated = True
                        warnings.append(f"Stopped at max_pages={request.max_pages}")
                        break

                    page = await adapter.fetch_page(request, cursor)
                    pages_fetched += 1
                    last_query = page.query or last_query
                    if page.total_count is not None:
                        total_count = page.total_count
                    truncated = truncated or page.truncated
                    retrieved_at = datetime.now(timezone.utc)
                    records.extend(
                        adapter.normalize(
                            row,
                            request,
                            query=page.query,
                            retrieved_at=retrieved_at,
                        )
                        for row in page.records
                    )

                    if page.next_cursor is None:
                        break
                    if page.next_cursor in seen_cursors or page.next_cursor == cursor:
                        warnings.append("Adapter returned a repeated pagination cursor")
                        truncated = True
                        break
                    seen_cursors.add(page.next_cursor)
                    cursor = page.next_cursor

        except TimeoutError:
            return HandlerReport(
                status=SearchStatus.TIMED_OUT,
                records=tuple(records),
                archive=adapter.archive,
                query=last_query,
                pages_fetched=pages_fetched,
                total_count=total_count,
                error=f"Search exceeded {timeout_seconds:g} seconds",
                warnings=tuple(warnings),
                started_at=started,
                finished_at=datetime.now(timezone.utc),
            )
        except AdapterError as exc:
            return _failure_report(
                archive=adapter.archive,
                started=started,
                records=records,
                warnings=warnings,
                pages_fetched=pages_fetched,
                total_count=total_count,
                query=exc.query or last_query,
                error=str(exc),
            )
        except Exception as exc:
            return _failure_report(
                archive=adapter.archive,
                started=started,
                records=records,
                warnings=warnings,
                pages_fetched=pages_fetched,
                total_count=total_count,
                query=last_query,
                error=f"{type(exc).__name__}: {exc}",
            )

        status = (
            SearchStatus.PARTIAL if truncated else
            SearchStatus.SUCCESS if records else
            SearchStatus.EMPTY
        )
        return HandlerReport(
            status=status,
            records=tuple(records),
            archive=adapter.archive,
            query=last_query,
            pages_fetched=pages_fetched,
            total_count=total_count,
            warnings=tuple(warnings),
            started_at=started,
            finished_at=datetime.now(timezone.utc),
        )

    return search


def _failure_report(
    *, archive: str, started: datetime, records, warnings, pages_fetched,
    total_count, query: str, error: str
) -> HandlerReport:
    """Preserve already-fetched records while distinguishing failure/partial."""
    return HandlerReport(
        status=SearchStatus.PARTIAL if records else SearchStatus.FAILED,
        records=tuple(records),
        archive=archive,
        query=query,
        pages_fetched=pages_fetched,
        total_count=total_count,
        error=error,
        warnings=tuple(warnings),
        started_at=started,
        finished_at=datetime.now(timezone.utc),
    )
