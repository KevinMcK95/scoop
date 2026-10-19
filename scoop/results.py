"""Collected query results, with archive outcomes kept separate from inventory."""

from __future__ import annotations

from dataclasses import dataclass

from .domain import DataProduct, ObjectProfile, Observation, Publication, QueryPlan
from .models import HandlerReport, SearchRequest, SearchStatus


@dataclass(frozen=True)
class SearchOutcome:
    """One executed archive request and its report, including failures and empties.

    Preserve the request because an archive can serve several capabilities. The
    report's query string is service-native, not a substitute for this request.
    """

    request: SearchRequest
    report: HandlerReport

    def __post_init__(self) -> None:
        if not self.report.archive.strip():
            raise ValueError("outcome report must identify an archive")

    @property
    def incomplete(self) -> bool:
        """Execution did not finish cleanly; this says nothing about sky coverage."""
        return self.report.status not in (SearchStatus.SUCCESS, SearchStatus.EMPTY)


@dataclass(frozen=True)
class QueryResult:
    """Inventory plus execution evidence; not a claim of exhaustive coverage.

    Typed entities are explicitly constructed by adapters/compilers; raw records
    remain in reports. No implicit deduplication, crossmatching, or conversion
    occurs here. References may point outside this inventory (e.g. a filtered-out
    observation). An optional plan is explanatory context, not a required parsed
    representation of chat. Searches not attempted are not inferred from it.
    """

    plan: QueryPlan | None = None
    searches: tuple[SearchOutcome, ...] = ()
    objects: tuple[ObjectProfile, ...] = ()
    observations: tuple[Observation, ...] = ()
    products: tuple[DataProduct, ...] = ()
    publications: tuple[Publication, ...] = ()

    @property
    def incomplete_searches(self) -> tuple[SearchOutcome, ...]:
        return tuple(search for search in self.searches if search.incomplete)

    @property
    def images(self) -> tuple[DataProduct, ...]:
        return tuple(product for product in self.products if product.product_type == "image")

    @property
    def spectra(self) -> tuple[DataProduct, ...]:
        return tuple(product for product in self.products if product.product_type == "spectrum")
