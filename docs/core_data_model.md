# Core data model: first implementation

> **Update (2026-10-20):** the archive execution layer described here
> (`SearchRequest`, `AdapterPage`, `HandlerReport`, `SearchOutcome`,
> `QueryResult`, and `scoop/adapters.py`, `handlers.py`, `results.py`) was removed:
> nothing used it beyond its own tests. Archive access now lives in data-source
> modules (`scoop/sources/`, see [map_interaction.md](map_interaction.md)). The
> provenance and domain values (`Provenance`, `Record`, `SearchStatus`,
> `EntityRef`, `Publication`, ...) remain; the ADS client uses them.

## Review of the initial idea

The useful core of SCOOP is a **provenance-bearing inventory**, not the number
of agents. A user should be able to inspect what was requested, what each service
returned, and which gaps remain, without trusting an LLM summary.

Three changes to the initial sketch guide this implementation:

1. **Do not make every question a cone.** A topic-only literature question is
   valid. The chat agent chooses typed tools directly; `QueryPlan` is optional
   explanatory intent, not a required parsed representation of a conversation.
   `SearchRequest` specifies one internal service capability with its own
   constraints and pagination limits. See the [LangGraph layout](langgraph_chat.md).
2. **Do not merge identity or scientific assertions prematurely.** Native
   records are identified by `(kind, archive, native_id)`. Names and proximity
   alone are not identity evidence. Multiple distance or velocity measurements
   remain separate assertions with their own sources and units. This first
   version does not automatically detect conflicts, choose preferred values,
   or create crossmatched objects.
3. **Do not promise completeness from successful execution.** A successful
   request says nothing about unqueried archives, missing metadata, or exact
   footprint overlap. Retain empty searches, failures, timeouts, and truncation.
   Coverage evaluation needs service capabilities and geometry evidence later.

## Implemented layers

| Layer | Types | Responsibility |
| --- | --- | --- |
| Scientific intent | `QueryPlan` | Original text, target labels, optional cone, literature topics, requested capabilities |
| Archive execution | *(removed; see the update above)* | Now: data-source modules with `rows` / `query` |
| Identity and evidence | `EntityRef`, `Provenance`, `Measurement` | Exact native identity, retrieval context, separate scalar assertions |
| Inventory | `ObjectProfile`, `Observation`, `DataProduct`, `Publication` | Distinct domain entities and explicit product-to-observation links |

The existing generic `Record` is the adapter boundary. It is not silently
converted into a domain entity: a service-specific mapper must understand the
semantics. Native payloads stay available in report records. Typed inventory
and reports can therefore coexist while adapters are developed incrementally.

All domain entities retain provenance, including object profiles; measurement
provenance can refer to a different source record from the object itself.
A product's download URL is distinct from a provenance URL, which might only
point to a metadata page. `downloadable` signals a candidate URL, not verified
reachability, permission, or successful transfer.

```python
from scoop import Cone, QueryPlan

plan = QueryPlan(
    original_text="Find spectra near M87",
    targets=("M87",),
    region=Cone(187.7059, 12.3911, 2 / 60),
    capabilities=("spectra",),
)

literature_plan = QueryPlan(
    original_text="Papers on dwarf galaxy internal velocities",
    literature_topics=("dwarf galaxy internal velocities",),
)
assert literature_plan.region is None
```

## Deliberate limits

- Dataclasses and stdlib only; no network, LLM, database, or runtime dependency
  is required to import and test the core.
- These are typed Python models, not a validated JSON interchange protocol.
  Callers should supply tuples and the declared types. Frozen dataclasses do
  not recursively freeze arbitrary native payloads or request constraints.
- Measurement units are explicit labels, not converted quantities. Only finite
  scalar values with optional symmetric nonnegative uncertainty are modeled.
  Positions with epochs/covariance, upper limits, asymmetric errors, photometric
  systems, and distance methodologies need richer measurement types later.
- `Observation.region` is supplied search/footprint metadata, not proof that an
  observation contains an object. No footprint-intersection logic exists yet.
- Identifiers and kinds are case-sensitive, archive-scoped values; publication
  identifiers are preserved without DOI/arXiv normalization or deduplication.
- `images` and `spectra` filter the exact product types `image` and `spectrum`.
  Other product types are retained in `products`.
- References may point to entities not included in the current inventory.
  No reference resolution or global graph consistency checking is performed.
- `incomplete_searches` reports execution problems only. An empty tuple can mean
  no searches were attempted. There is intentionally no `complete` flag.
- Group selection, typed physical constraints, non-conical regions/MOCs,
  localization probabilities, summaries with evidence links, manifests, and
  versioned serialization remain future work. Avoid encoding these as vaguely
  named dictionaries in the public domain model before one real use case exists.

## What happened next

The vertical slice went through data-source modules instead of an adapter
contract: the smoke-test pipeline's archives (MAST, Gaia, QSO catalogs, SIMBAD,
SDSS/DESI) and an on-demand NASA Exoplanet Archive query, each declaring its
table, columns, provenance and presentation. See
[map_interaction.md](map_interaction.md).

Run the offline checks from the repository root:

```bash
python3 -m unittest discover -s tests -v
```
