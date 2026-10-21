# SvelteKit workspace

## Scope

The frontend lives in `web/`: SvelteKit, Svelte 5, and TypeScript. It is the
user's side of SCOOP, connected to the Python server (`python -m scoop.server`),
which runs the LangGraph agent and every archive client. No ADS credentials,
model keys, or archive clients belong in the browser.

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
Each layer has a stable ID, a display name, a type, visibility, and an origin
(pipeline, agent or user). Hiding a layer never deletes scientific records. Removing a
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

## Map actions

The user's controls and the agent's `update_map` share one set of typed actions:
`layer.add`, `layer.update`, `layer.remove`, `focus`, `focus.rows`, `highlight`,
`annotate`, `annotations.clear`, `select`, `plot.show` and `plot.hide`
(`scoop/chat/visual.py`, `web/src/lib/field/actions.ts`). The browser validates
each action against the loaded field, applies it, and acknowledges it on the next
turn; the server only ever reports an agent's action as `requested`. Sent messages
snapshot the selection, viewport and layers alongside the text. An annotation is
not an archive record. See [map and agent interaction](map_interaction.md).

Aladin survey imagery requires a network connection. A failed viewer must show a
useful error and leave the rest of the workspace functional.

## Debug traces (developer mode)

⚙ Settings → *Developer: show the Debug tab* adds a Debug tab to the results
panel. Each chat turn is recorded on the server by a LangChain callback
(`scoop/server/trace.py`), like an observability service but kept on this machine:

- every model call: the messages sent (the first call in full, system prompt
  included; later calls only what was added), the reply and its tool calls,
  tokens and prompt-cache hits, and duration;
- every tool call: arguments, result, status and duration;
- the turn's error, if any, and totals.

The tab shows the turns of the current conversation as a timeline, and each
step opens to show its details; ⧉ copies a turn as JSON to share a bug.
Traces are saved per conversation under `~/.config/scoop/traces/` (owner-only,
newest 50 turns) and deleted with the conversation. Unlike the audit log they
contain message text; images are replaced by their size. They are served at
`GET /api/traces/{thread}`.

## Budgets and what a command costs

A conversation starts by setting a budget: until it has one the composer is
disabled and the question waits in the box (`ChatPanel.svelte`, the budget
card). The context strip then shows what is left, and opens the card again to
change it.

- `GET /api/budget/{thread}` — what this conversation may spend, has spent and
  has left (`budget` is null until it sets one), the model turns are priced
  against, and whether the server has a price for it.
- `POST /api/budget/{thread}` `{"budget_usd": 5}` — set or raise it; what the
  conversation has already spent is kept. Same-origin only, like Settings.
- The chat stream carries two extra events per turn:
  `{"type": "cost", "phase": "estimate", …}` before the model is called and
  `{"type": "cost", "phase": "actual", …}` once its tokens are known. Each
  carries a `note` to show and the conversation's budget after it. The browser
  shows one note per turn: the estimate, rewritten with what it really cost.
- A command the remaining budget cannot cover is refused before the model is
  called, as an `error` event naming the shortfall.

Estimates are calibrated per model from what turns actually cost, so they
improve with use; see [What a command costs](../README.md#what-a-command-costs)
and [`scoop/cost.py`](../scoop/cost.py).

## Backend integration

```text
SvelteKit browser
  ├─ POST /api/chat (message + workspace context) ──┐
  ├─ fields, sources, jobs, literature, settings ──┤
  └─ figures and traces per conversation ──────────┤
                                                   ▼
                                   Python (Starlette, scoop/server)
                                     ├─ LangGraph turn, per-thread lock
                                     ├─ shared ADS / archive services
                                     └─ field store, budgets, audit log
                                                   │
  ◀── server-sent events: deltas, tool calls, map actions, usage, done ──┘
```

Each turn is one POST answered by a stream of server-sent events. A turn can be
stopped from the browser; a thread runs one turn at a time. Map actions the agent
requests arrive as typed actions; the browser applies them and acknowledges each
on the next turn, so the agent can say only that it *requested* a display change
until the browser confirms it.

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

See [LangGraph layout](langgraph_chat.md) for the graph boundary and
[`web/README.md`](../web/README.md) for frontend commands and the code map.
