# SvelteKit workspace sketch

## Scope

The first frontend lives in `web/`: SvelteKit, Svelte 5, and TypeScript. It is a
fixture-backed interaction sketch, **not a connected astronomical search app**.
The Python library and optional LangGraph chat layer remain separate. No ADS
credentials, model keys, or archive clients belong in the browser.

```text
┌───────────────────────────────────────────────────────────────────────┐
│ SCOOP · compact workspace bar              Layers · Results · Chat    │
├──────────────┬──────────────────────────────────────────┬───────────────┤
│ LAYERS       │                                          │ CHAT          │
│ Visibility   │              SKY MAP                     │ Conversation  │
│ Origins      │        Floating working toolbar          │               │
│ Sources      │                                          │ Selection     │
│              │                                          │ Composer      │
├──────────────┴──────────────────────────────────────────┴───────────────┤
│ RESULTS DRAWER · Literature · Observations · Selected object · Activity │
└───────────────────────────────────────────────────────────────────────┘
```

The map is the primary workspace, not one of three equally weighted cards.
Docked panels can be collapsed to maximize the map. Small screens use compact
panel/drawer behavior rather than stacking a long dashboard above and below the
atlas. Layout controls are application state, not scientific evidence. Persisted
layout preferences and advanced keyboard shortcuts can follow later.

## Component and ownership boundaries

- **Workspace route:** creates one workspace state instance and coordinates
  docked panels and results tabs. Keep the Aladin component mounted when opening
  or closing panels; hiding chat or literature must not reset the sky. Do not
  store user workspace state in an SSR module singleton. Agent view commands do
  not change the user's panel layout unless the user explicitly requests it.
- **Sky panel:** owns the Aladin instance, loaded only in the browser. Translates
  Aladin clicks and viewport changes into application state; translates typed
  view commands into Aladin operations. Does not call an LLM itself.
- **Literature panel:** displays a particular result set and records selected
  publication handles. Paper selection must not clear an independent sky
  selection. Later, direct ADS search can use the same backend as agent tools.
- **Chat panel:** snapshots the current selections and viewport when a message
  is sent. Historical turns must not change if the user subsequently selects a
  different source. Local demonstration responses are visibly labeled.
- **Workspace contracts:** TypeScript types describe selections, viewport,
  messages, and allowed view commands. They are not a substitute for validating
  untrusted network input when the backend connection is added.

Browser display objects, scientific record identities, and result snapshots are
different references. A marker should link to an existing result/record, not
invent an astronomical identity. Fixture IDs must not be mistaken for verified
archive IDs or citations.

## Layer controls and toolbars

The sky view needs a visible layer control, not just commands hidden in chat.
Each layer has a stable ID, a display name, a type, visibility, and a retrieval
or fixture origin. Hiding a layer never deletes scientific records. Removing a
presentation layer is also distinct from deleting a result artifact.

Use a small sky toolbar for working actions: recenter and clear selection first;
region drawing and an explicit “search this view” action when those paths are
implemented. Do not automatically query services on every pan/zoom. Disabled or
future actions must be labeled, not presented as functional integrations.
Literature search/filter controls stay with the literature panel rather than
sharing a generic global toolbar.

UI buttons and eventual agent actions must use the same view-command handlers.
An agent-created layer appears in the same layer list as a user-created layer,
so the user can inspect it, hide it, or remove it. Background imagery, source
markers, footprints, and annotations are separate layer categories; adding a
category to the schema does not claim that data for it has been loaded.

## Initial interactions

1. Select a fixture source from the sky overlay or its accessible source list.
2. Select a fixture publication independently.
3. Send a message and inspect the selection context captured with that turn.
4. Trigger an explicit local view action, such as centering or drawing an angular
   circle. This demonstrates the command surface without pretending a model
   generated the action.

Aladin survey imagery requires a network connection. A failed viewer must show a
useful error and leave the rest of the workspace functional. Loaded imagery does
not make the fixture catalog or fixture bibliography a real search result.

## Implemented visual command boundary

The browser supports `center`, `layer.visibility`, `sources.highlight`,
`annotation.circle`, and `annotations.clear`. A copied command queue preserves
multiple consecutive actions. The renderer reports applied/failed outcomes;
visibility and annotation state update only after successful renderer calls.
Sent messages snapshot the applied layers, highlights, and angular annotations
alongside selections and viewport. An annotation is not an archive record.

The optional Python `scoop.chat.visual.build_view_tools` exposes the same command
shapes to LangGraph with server-supplied allowed layer/source IDs. It publishes
`view_request` artifacts with status `requested`, never `applied`. The transport
bridge and acknowledgment feedback to the graph are not connected yet. This
keeps a future agent from claiming a display update before a renderer confirms it.

## Proposed backend integration (not implemented)

```text
SvelteKit browser
  ├─ submit chat + interaction snapshot ─────┐
  ├─ submit explicit literature search ─────┤
  └─ fetch result details ──────────────────┤
                                           ▼
                                      Python / FastAPI
                                        ├─ LangGraph run
                                        ├─ shared ADS/archive services
                                        └─ authoritative result store
                                           │
  ◀── streamed messages, progress, result IDs, and view commands ──┘
```

Keep an HTTP API for submissions and an event stream for server-to-client
updates. A useful future event envelope includes `event_id`, `run_id`, sequence,
and event type. Distinguish message deltas, search progress, result availability,
view requests, and run completion/failure. Reconnection, replay, cancellation,
and authorization need explicit implementation before this is a production
protocol; the sketch does not provide them.

A view request should identify a command and its source run. The frontend should
acknowledge whether it applied the command; otherwise the agent can only claim
it requested a display update. Commands arriving after newer user interactions
must not unexpectedly steal the viewport: introduce a view revision or an
explicit focus policy when streaming is connected.

### Context and scientific trust

- Snapshot selections at send time, including an explicitly empty selection.
- Send references and a bounded summary, not complete catalog tables.
- Resolve references server-side against the authorized workspace inventory.
- Do not let browser text assert verified provenance or archive membership.
- Make task scope explicit: a selected object, selected region, and current
  viewport are not interchangeable search instructions.
- Track the retrieval basis of literature results; do not infer a paper/source
  association from their simultaneous presence in the interface.
- Keep observational metadata and bibliographic claims in tool results, not
  frontend presentation state.

## Next implementation order

1. Validate this layout and selection behavior in the browser.
2. Define and test wire schemas shared by Python endpoints and frontend types.
3. Connect chat streaming to the existing graph with a fixture tool.
4. Connect one real resolver/search path and ADS through server-side services.
5. Add result inspection, cancellation/reconnection, durable storage, and tests
   across two isolated workspace sessions.

See [LangGraph layout](langgraph_chat.md) for the graph boundary and
[`web/README.md`](../web/README.md) for frontend commands and current behavior.
