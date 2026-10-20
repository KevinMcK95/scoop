# SCOOP as a LangGraph chat application

## The boundary

The user talks to a chat agent. The model chooses typed tools; Python code
executes archive requests. There is no required intermediate natural-language
parser, and not every turn must produce a plan or a search.

```mermaid
flowchart TD
    START([New user message / restored thread]) --> A[Chat agent\nmodel bound to tools]
    A -->|tool calls| T[ToolNode\nvalidate arguments and execute tools]
    T -->|ToolMessages + artifact updates| A
    A -->|answer or clarification question| END([Return to user])
    T --> H[Deterministic tools and data sources]
    H --> S[(Archive services)]
```

A clarification is a normal assistant response ending this graph invocation;
when the user replies, invoke the same compiled graph with the same thread ID.
It is not a separate graph node or a fabricated archive result.

## Three kinds of state

1. **Messages** — human/assistant/tool conversation history, merged with
   LangGraph's message reducer. Tool responses are concise evidence summaries
   and result handles, not an entire catalog in the model context.
2. **Artifacts** — immutable, JSON-compatible tool result snapshots keyed by
   an opaque result ID. This is the first session inventory. Tools publish these
   with `Command(update=...)`; an ordinary returned dictionary is only a tool
   message, not an update to this state. Each parallel tool publishes only its
   own new artifact. The reducer rejects an attempt to overwrite an existing ID
   with different content; equal replay is allowed.
3. **External services and dependencies** — archive clients, model credentials,
   caches, download storage, and production databases live outside conversation
   state. Inject them when building tools/the graph. Never checkpoint clients or
   credentials in artifacts.

A result handle is not an astronomical object ID. A native `EntityRef` identifies
an archive entity; a result ID identifies one retrieval snapshot. Re-querying an
archive should create a new snapshot, not replace old evidence.

`QueryPlan` can describe an agent's proposed work, but it is neither a gate
before each tool call nor the source of authoritative results.

## Scientific tools

The optional [ADS service and tool wrappers](ads_service.md) now implement
`ads_search`, `ads_paper`, and `ads_citations`. Supply them through
`build_ads_tools(client)`. They are not yet connected to the browser.

The broader application-level tools below remain proposed:

| Tool | Model-visible arguments | Result |
| --- | --- | --- |
| `resolve_target` | Name; optional resolver preference | Candidate identities, ICRS positions, ambiguity, provenance |
| `search_observations` | Explicit region, archives/facilities, product kinds | Observation/product records plus every archive outcome |
| `search_publications` | Targets and/or literature topic, date filters | Publication records and retrieval evidence |
| `inspect_result` | Result ID, filters, pagination | Bounded detail from an existing artifact; no new archive search |
| `prepare_download` | Existing product/result references | Proposed files, access restrictions, sizes if known |
| `download_products` | Approved selection handle | Per-file status and provenance-bearing manifest |

Tool schemas should be action-specific rather than a single free-form
`constraints` argument exposed to the model. The current `SearchRequest` remains
an internal handler request. Separate discovery from downloads: do not register
an unrestricted file-writing tool in the initial chat loop. An approval-gated
workflow can be added when downloads actually exist.

The first graph accepts an injected tool list. It does **not** advertise these
proposed tools until real implementations exist. A tool that discovers an
expected service failure should return the normal failed/partial archive report,
not claim an empty result. Invalid model-generated tool arguments return an
error ToolMessage so the agent can correct them. Unexpected programming errors
propagate from the initial ToolNode so they are visible during development.

## Module layout

```text
scoop/
  models.py          # records, provenance, search status
  domain.py          # native entities and scientific assertions
  fields.py          # field tables, layers, filters, statistics
  spatial.py         # separations, footprints, regions
  sources/           # one module per data source (rows, query, presentation)
  lookup.py, resolve.py, images.py, lvdb.py   # SIMBAD, Sesame, sky cutouts, LVDB
  literature/        # ADS client and tools
  server/            # HTTP API, settings, background pipeline jobs
  chat/
    state.py         # messages and parallel-safe artifact reducer
    tools.py         # publish a result + matching ToolMessage as one Command
    graph.py         # injected model/tools, routing, checkpointer
    __init__.py      # optional chat API
```

Importing `scoop` does not import LangGraph. Install the chat extra separately:

```bash
pip install -e '.[chat]'
python3 -m unittest discover -s tests -v
```

Model-provider SDKs are deliberately not selected by this package. Supply an
initialized tool-calling model supporting `bind_tools` and `ainvoke`.

```python
from langgraph.checkpoint.memory import InMemorySaver
from scoop.chat import build_chat_graph

# model: initialized provider model; tools: actual application tool definitions.
# Keep this saver and graph alive across turns. InMemorySaver is not durable.
graph = build_chat_graph(model, tools, checkpointer=InMemorySaver())
config = {
    "configurable": {"thread_id": "conversation-123"},
    "recursion_limit": 24,
}
first = await graph.ainvoke(
    {"messages": [{"role": "user", "content": "Find spectra of M87"}]},
    config=config,
)
# Supply just the new message, not the complete previous state.
second = await graph.ainvoke(
    {"messages": [{"role": "user", "content": "Only HST, please"}]},
    config=config,
)
```

Thread IDs must be scoped to an authorized user/session by the host application;
LangGraph checkpointing is not an authentication boundary. Use a persistent
checkpointer for process restarts. Do not run concurrent turns on the same
thread without host-level coordination.

## What the scaffold proves, and what it does not

Offline tests use a scripted model and local tools, not fabricated live archive
responses. They exercise model-to-tool routing, artifact publication, parallel
result merges, checkpointed follow-ups, and thread isolation.

The system prompt asks the agent to cite source evidence and explain gaps, but
this is not a guarantee of faithful synthesis. Enforcing scientific claims
requires structured evidence links and evaluation later. There is also no
context compaction, inventory paging tool, durable artifact store, token/cost
budgeting, or global archive concurrency limiter yet. Start with bounded results
and a recursion limit; large native tables/files should ultimately be stored
outside checkpoints and referenced by handle.

## Next implementation slice

1. Implement `resolve_target` and one observation-search tool with fixture tests.
2. Define explicit JSON projections for their domain models: native identities,
   provenance, execution reports, and stable product/result handles; omit arbitrary
   raw objects from checkpoints.
3. Add `inspect_result` so follow-ups can inspect authoritative snapshots rather
   than reconstructing evidence from conversational text.
4. Only then add durable persistence, a chat frontend, and approval-gated downloads.

## References

- [LangGraph workflows and agents](https://docs.langchain.com/oss/python/langgraph/workflows-agents)
- [Graph API and MessagesState](https://docs.langchain.com/oss/python/langgraph/use-graph-api)
- [Tools: state updates and Command](https://docs.langchain.com/oss/python/langchain/tools)
- [Checkpointed conversation memory](https://docs.langchain.com/oss/python/langgraph/add-memory)
