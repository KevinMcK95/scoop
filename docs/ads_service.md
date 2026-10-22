# Shared ADS metadata service

## Scope and attribution

Implemented in `scoop/literature/{models.py,ads.py,__init__.py}` with offline
`unittest` coverage in `tests/test_ads.py`. Adapted from the local TypeScript ADS
extension `/Users/yguan/.agent-sh/extensions/ads/index.ts`, read without modification.
Specifically ports the search endpoint, field selection strategy, sort mappings,
repeated `fq` filters, identifier lookup queries, and citations/references query
operators. This is a service port, not an agent-sh extension or wholesale copy.

Deliberate differences from that source:

- Async Python/httpx transport with caller-injected or context-managed ownership.
- Structured frozen dataclasses instead of formatted tool text and exit codes.
- Strict local validation instead of silently clamping limits or defaulting bad sorts.
- Missing counts/metadata are not fabricated as zero; malformed responses are not
  treated as empty. Invalid documents produce explicit warnings and error outcomes.
- `refereed=False` means exclude refereed papers; `None` leaves the filter unset.
- Identifiers are quoted with backslash/quote escaping, including bibcodes and
  citation operator arguments. Explicit DOI/arXiv prefixes are case-insensitive;
  bare modern and legacy arXiv identifiers are supported. Versions are preserved,
  not stripped or converted to guessed canonical identifiers.
- Canonical metadata URLs use `https://ui.adsabs.harvard.edu/abs/{encoded bibcode}`,
  not the extension's link gateway. These are metadata pages, not PDF URLs.
- No automatic retries, redirect following, response-body error dumps, filesystem
  access, PDF download, skill registration, or tool formatting. BibTeX export is
  deliberately deferred: the optional feature would add a second request and
  separate failure/budget semantics without being necessary for metadata lookup.

## Object-query compatibility

The newly fetched origin implementation in `evaluation/literature.py` documents
that ADS UI `object:` queries require translation through `/v1/objects/query`
before reaching the search endpoint. This client does not yet implement that
translation. Its tool guidance therefore does not advertise direct `object:`
support. Use explicit bibliographic identifiers or text searches for now; text
matches must not be presented as verified object associations.

## Service contract

```python
from scoop.literature.ads import ADSClient
from scoop.literature.models import SearchRequest, PaperRequest, CitationsRequest

# Server-side only; ADS_API_TOKEN is read lazily if token is not supplied.
async with ADSClient(timeout=20.0) as ads:
    page = await ads.search(SearchRequest(
        'title:"stellar feedback"', database="astronomy", refereed=True,
        year_from=2020, page_size=20,
    ))
    paper = await ads.get_paper(PaperRequest("doi:10.1234/example"))
    references = await ads.citations(CitationsRequest(
        "2023ApJ...950L..12A", direction="references", page_size=20,
    ))
```

`httpx` is already declared by the repository's optional `literature` extra; no
packaging change is needed. Importing `scoop.literature` or its models requires
neither httpx nor a chat framework. Import `ADSClient` explicitly from `.ads`.

Each operation issues at most one GET. `page_size` is an integer in 1..50,
`start` an integer >=0, and year bounds integers in 1..9999, ordered when both
present. Booleans/floats are rejected as integer inputs. Database and document
type choices follow the source extension. Sort choices are relevance, date,
citation_count, and read_count; citation requests default to date. Invalid local
arguments raise `ValueError`; using an owned client outside its context is a
`RuntimeError`. Normal cancellation and unexpected programming errors propagate.

A supplied `httpx.AsyncClient` is never closed by the service and can be used
without entering the service context. The default client is created on context
entry and closed on exit, including exceptional exit. `timeout` defaults to 30
seconds, must be finite/positive, and is passed to httpx on every request, including
injected clients. This is httpx's per-phase connect/read/write/pool budget, **not**
an overall elapsed-time deadline or a pagination budget. Callers requiring a
whole-operation deadline may use their own cancellation scope.

An explicit token overrides the environment (including an explicit empty token,
which is an auth failure). Missing/invalid tokens fail lazily as structured auth
outcomes without network traffic. Tokens are private client state, never stored
in requests/results/provenance. Error messages omit response bodies, exception
text and request headers. Redirects are disabled even on injected clients. Do not
log injected clients' HTTP requests/headers or expose server configuration to UI.

## Results, provenance, and pagination

`ADSResult` retains the typed original request and exact ADS `q` string. Search
filters remain in that request (and are sent as repeated `fq` values), not silently
spliced into `q`. Results expose papers, nullable total_count, next_start, warnings,
and a nullable structured error. Status uses the existing `SearchStatus` enum:

- **success**: a valid nonempty page, not a claim to have retrieved all matches.
- **empty**: a valid empty page at/beyond the reported total; a paper not found is
  explicitly empty, not an authentication or network failure.
- **partial**: some valid papers alongside rejected documents, or an unexpectedly
  empty page before the reported total (unknown completeness).
- **failed**: HTTP/auth/rate-limit/transport failures, invalid JSON/envelope/counts,
  inconsistent pagination, or a page whose documents are all invalid.
- **timed_out**: httpx timeout, distinct from an empty or generic failed result.

HTTP 401/403 map to `auth`, 429 to `rate_limit`, other unsuccessful statuses and
transport errors to `http`; malformed responses use `invalid_response`. HTTP
errors retain the status and optional `Retry-After` header as an opaque string,
including HTTP-date forms. There is no sleep, retry, or inferred backoff. Callers
choose whether and when to retry. Failed envelopes have unknown total_count,
not zero. Both nested ADS `response` and flat envelopes must have list `docs`
and a nonnegative integer `numFound` (not boolean).

`next_start = start + number of raw documents` only when nonempty documents leave
more matches according to numFound. Invalid documents still consume offsets.
There is no automatic next-page fetching, no total-count inference from page size,
and no invented advancing cursor for an unexpectedly empty page. `next_start=None`
means only that no safe continuation is offered; callers **must** inspect status,
error and warnings before treating it as exhaustion. A valid short page can still
have next_start. Counts are ADS's reported values, not a snapshot guarantee.
`get_paper` fetches one match; an ambiguous identifier may report total_count >1
and next_start. Inspect that count rather than inferring unique resolution; use
`SearchRequest(result.query, start=...)` to inspect additional matches if needed.

Each bibliographic wrapper contains an existing `Publication` with matching
`EntityRef("publication", "ADS", bibcode)` and `Provenance`: exact ADS bibcode,
exact executed query, canonical encoded access URL, and a timezone-aware UTC
retrieval timestamp. Title is required; authors are a tuple. Abstract, integer
year, venue and citation_count are optional, with absent citations remaining
`None` and explicit zero preserved. The first DOI and arXiv label populate the
existing scalar identifier map; all returned metadata, including additional
identifiers and otherwise unused fields, is preserved in a copied JSON `raw` doc.
This JSON is not deeply immutable even though its dataclass wrapper is frozen;
callers should treat it as a result snapshot. No crossmatching, deduplication,
coverage, or PDF availability is inferred.

## Layer boundaries and future adapters

The service depends only on the existing domain values, standard library, and
optional httpx. It does not change chat state, the tool registry, the frontend,
or package root exports. The separate
`serialization.result_payload` function projects records into strict JSON,
omitting raw documents and encoding timestamps explicitly. The optional
`literature.tools.build_ads_tools` wrapper registers `ads_search`, `ads_paper`,
and `ads_citations`, publishing results under actual tool-call IDs. Paper-detail
messages include the abstract (bounded to 6000 characters); search messages give
a bounded title/identifier list. The client itself imports no LangGraph and
returns no framework messages.

Install both extras and supply the client to the graph:

```sh
pip install -e '.[chat,literature]'
```

```python
from scoop.agents import build_chat_graph
from scoop.literature.ads import ADSClient
from scoop.literature.tools import build_ads_tools

# model is an initialized tool-calling model; credentials remain server-side.
async with ADSClient() as ads:
    graph = build_chat_graph(model, build_ads_tools(ads))
    state = await graph.ainvoke({
        "messages": [{"role": "user", "content": "Find papers on dwarf galaxy velocities"}]
    })
```

A future HTTP API should inject the same server-managed client and use the same
JSON projection for the literature panel. No HTTP routes or frontend live ADS
connection are implemented yet. Retry policy, multi-page budgets, BibTeX,
caching, and live UI rendering remain separate work.

## Verification

Run from the repository root with the existing `/tmp/scoop-langgraph-venv`:

```sh
/tmp/scoop-langgraph-venv/bin/python -m unittest discover -s tests -p 'test_ads.py' -v
/tmp/scoop-langgraph-venv/bin/python -m unittest discover -s tests -v
```

Tests use `httpx.MockTransport` and synthetic tokens exclusively, including the
owned-client lifecycle test (constructor patched to a mock-transport client).
Coverage includes authorization/timeout headers and options, encoded queries,
filters/sorts, pagination and last/empty/inconsistent pages, DOI/arXiv and escaped
identifier lookups, both citation directions, nullable counts and metadata,
normalization/provenance/raw JSON, lazy configuration/ownership, 401/403/429/HTTP
failures, Retry-After seconds/date, transport/timeouts, malformed JSON/schema,
and partially invalid documents. No real API request or token printing is needed.
