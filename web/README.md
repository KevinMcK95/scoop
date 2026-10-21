# SCOOP workspace

The browser half of SCOOP: Svelte 5 + SvelteKit 3 + TypeScript, with an Aladin Lite
sky map at its centre. The map, the layer list, the chat and the result tabs (Selected,
Plot, Tables, Summary, Literature, Decisions, Activity, and Debug in developer mode)
share one field and one selection, so the agent sees what you select and you see what
it shows. Model and ADS credentials stay in the Python server; the browser never holds
them.

## Install and run

Requires Node.js 24+ and npm, and the Python API server running alongside (see the
[top-level README](../README.md#web-workspace)):

```sh
python -m scoop.server            # from the repository root; listens on 127.0.0.1:8787
cd web && npm ci
npm run dev -- --host 127.0.0.1   # Vite proxies /api to the server
```

Choose the chat model, API keys, ADS token and analysis kernel in ⚙ Settings. Checks:

```sh
npm run check   # svelte-check
npm test        # vitest
npm run build
```

Aladin Lite is pinned to `3.9.2-beta` (an upstream prerelease) and loaded only in the
browser, in `SkyPanel`'s `onMount`; it is a large lazy chunk, so the build's chunk-size
warning is expected. Sky imagery comes from HiPS servers over the network.

## Code map

- `src/routes/+page.svelte`: the workspace page; creates one workspace and lays out the panels.
- `src/lib/workspace/state.svelte.ts`: `createWorkspace()`, the rune state behind every
  panel: field, map state and undo, chat turns and their streamed events, conversations
  (kept in this browser), budgets, jobs.
- `src/lib/api/client.ts`: the HTTP/SSE client for `scoop/server/app.py`, and its wire types.
- `src/lib/field/`: the field model (tables, layers, filters), map actions, plots and export.
- `src/lib/chat/`: chat rendering (markdown, bibcodes), activity summaries, figures,
  history and debug traces.
- `src/lib/components/`: the panels.
- `src/lib/sky/aladin.ts`: a small, hand-declared boundary to Aladin Lite, which ships no
  TypeScript declarations.

Map changes the agent asks for (`update_map` and friends in `scoop/chat/visual.py`) arrive
as typed actions. The workspace validates and applies them, and reports on the next turn
whether each was applied, so the agent never assumes the user saw something they did not.
No chat text is executed.

See [the workspace architecture](../docs/web_workspace.md) and
[map and agent interaction](../docs/map_interaction.md).
