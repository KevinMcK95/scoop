"""Core interfaces for SCOOP's archive handlers and adapters."""

from .adapters import Adapter, AdapterError, FetchPage, NormalizeRecord
from .domain import (
    DataProduct,
    EntityRef,
    Measurement,
    ObjectProfile,
    Observation,
    Publication,
    QueryPlan,
)
from .results import QueryResult, SearchOutcome
from .handlers import make_handler
from .models import (
    AdapterPage,
    Cone,
    HandlerReport,
    Provenance,
    Record,
    RecordPayload,
    SearchRequest,
    SearchStatus,
)

__all__ = [
    "Adapter",
    "DataProduct",
    "EntityRef",
    "Measurement",
    "ObjectProfile",
    "Observation",
    "Publication",
    "QueryPlan",
    "QueryResult",
    "SearchOutcome",
    "AdapterError",
    "AdapterPage",
    "FetchPage",
    "Cone",
    "HandlerReport",
    "Provenance",
    "Record",
    "RecordPayload",
    "NormalizeRecord",
    "make_handler",
    "SearchRequest",
    "SearchStatus",
]
