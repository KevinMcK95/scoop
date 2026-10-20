"""Small domain values layered on the existing Record/Provenance envelope.

References preserve archive-native identity, not crossmatched identity. Regions
are supplied metadata, not inferred coverage. These models do not perform I/O,
unit conversions, serialization, or conversion from archive records.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from math import isfinite
from types import MappingProxyType
from typing import Mapping

from .models import Cone, Provenance


def _nonempty(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")


def _check_ref(ref: EntityRef, kind: str, provenance: Provenance | None = None) -> None:
    if ref.kind != kind:
        raise ValueError(f"ref kind must be {kind}")
    if provenance is not None and (
        ref.archive != provenance.archive or ref.native_id != provenance.native_id
    ):
        raise ValueError("ref must match provenance archive and native_id")


@dataclass(frozen=True)
class QueryPlan:
    """User text plus explicit intent; no parsing or capability inference.

    At least one target, region, literature topic, or capability is required.
    A literature-only plan does not need a target or sky position.
    """

    original_text: str
    targets: tuple[str, ...] = ()
    region: Cone | None = None
    literature_topics: tuple[str, ...] = ()
    capabilities: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        _nonempty(self.original_text, "original_text")
        for name in ("targets", "literature_topics", "capabilities"):
            for value in getattr(self, name):
                _nonempty(value, name)
        if not (self.targets or self.region is not None or self.literature_topics or self.capabilities):
            raise ValueError("plan must contain non-empty intent")


@dataclass(frozen=True)
class EntityRef:
    """Exact, case-sensitive composite identity (kind, archive, native_id).

    No whitespace normalization, name resolution, or crossmatching is applied.
    Kinds are open strings; domain entities enforce their own required kind.
    """

    kind: str
    archive: str
    native_id: str

    def __post_init__(self) -> None:
        for name in ("kind", "archive", "native_id"):
            _nonempty(getattr(self, name), name)


@dataclass(frozen=True)
class Measurement:
    """One scalar assertion, not a universal astronomy measurement schema.

    Units are explicit labels (use e.g. '1' for dimensionless quantities), not
    parsed or converted. Uncertainty is in the same unit as value; its statistical
    interpretation is deliberately unspecified. Provenance identifies the source
    assertion, which need not share the object's native identity.
    """

    quantity: str
    value: float
    unit: str
    provenance: Provenance
    uncertainty: float | None = None

    def __post_init__(self) -> None:
        _nonempty(self.quantity, "quantity")
        _nonempty(self.unit, "unit")
        if not isfinite(self.value):
            raise ValueError("value must be finite")
        if self.uncertainty is not None and (
            not isfinite(self.uncertainty) or self.uncertainty < 0
        ):
            raise ValueError("uncertainty must be finite and nonnegative")


@dataclass(frozen=True)
class ObjectProfile:
    """One native object, retaining all supplied (possibly competing) assertions.

    Aliases are labels, not evidence that other archive objects are identical.
    Measurements are neither deduplicated nor ranked.
    """

    ref: EntityRef
    provenance: Provenance
    name: str
    aliases: tuple[str, ...] = ()
    measurements: tuple[Measurement, ...] = ()

    def __post_init__(self) -> None:
        _check_ref(self.ref, "object", self.provenance)
        _nonempty(self.name, "name")
        for alias in self.aliases:
            _nonempty(alias, "alias")


@dataclass(frozen=True)
class Observation:
    """Native observation metadata; a region does not establish coverage."""

    ref: EntityRef
    provenance: Provenance
    facility: str
    instrument: str | None = None
    region: Cone | None = None

    def __post_init__(self) -> None:
        _check_ref(self.ref, "observation", self.provenance)
        _nonempty(self.facility, "facility")
        if self.instrument is not None:
            _nonempty(self.instrument, "instrument")


@dataclass(frozen=True)
class DataProduct:
    """Native product with explicit observation links, not inferred associations.

    Only this product's access_url signals a download candidate. A provenance
    URL may be a metadata page and is not used as a fallback.
    """

    ref: EntityRef
    provenance: Provenance
    product_type: str
    access_url: str | None = None
    observation_refs: tuple[EntityRef, ...] = ()
    media_type: str | None = None

    def __post_init__(self) -> None:
        _check_ref(self.ref, "product", self.provenance)
        _nonempty(self.product_type, "product_type")
        for name in ("access_url", "media_type"):
            value = getattr(self, name)
            if value is not None:
                _nonempty(value, name)
        for ref in self.observation_refs:
            _check_ref(ref, "observation")

    @property
    def downloadable(self) -> bool:
        """An explicit URL exists; reachability and authorization are unverified."""
        return self.access_url is not None


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
        _check_ref(self.ref, "publication", self.provenance)
        _nonempty(self.title, "title")
        _nonempty(self.publication_type, "publication_type")
        for key, value in self.identifiers.items():
            _nonempty(key, "identifier key")
            _nonempty(value, "identifier value")
        object.__setattr__(self, "identifiers", MappingProxyType(dict(self.identifiers)))
