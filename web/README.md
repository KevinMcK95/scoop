# SCOOP workspace sketch

A standalone Svelte 5 + SvelteKit 3 + TypeScript frontend with a **full-viewport, map-first scientific atlas**. A compact top bar carries the persistent **Demo · No backend** indication. No backend, API keys, LLM, ADS integration, or archive searches.

## Install and run

Requires Node.js 24+ and npm. From this directory:

```sh
npm ci
npm run dev -- --host 127.0.0.1
```

Open the local URL Vite prints (normally `http://127.0.0.1:5173`). For a production build preview:

```sh
npm run check
npm test
npm run build
npm run preview -- --host 127.0.0.1
```

The lockfile records the installed versions. Ranges follow the current Kit 3 peer requirements: Kit `^3.1.0`, Svelte `^5.57.2`, Vite `^8.3.4`, Svelte Vite plugin `^7.3.1`, TypeScript `~6.0.0`. Aladin Lite is pinned to `3.9.2-beta` (upstream prerelease). Kit 3 configuration is in `vite.config.ts`, not a legacy `svelte.config.js`; TypeScript extends `$app/tsconfig`.

## Try the workflow

1. Choose a fixture source using an Aladin marker or its keyboard-accessible row. Its opaque `fixtureId` is mapped back to the fixture list; labels and sky positions are not IDs.
2. Select a fictional paper by its title. Paper selection does **not** replace the sky selection. Each can be cleared independently.
3. Use the sky toolbar to **Center selection**, **Highlight selected**, or draw a **2′ radius** circle. Layer checkboxes show/hide source markers and angular annotations without deleting records. These dispatch typed commands to Aladin; they are explicit local actions, not agent calls. A status line shows each latest command's pending/applied/failed outcome. Controls are disabled until the atlas is ready.
4. Pan/zoom the atlas. Its current center and horizontal field of view are recorded in degrees, in ICRS, with the configured DSS2 survey identifier.
5. Use **Ask about selection** to populate and focus the conversation input, or enter your own question. Press **Send**. The user message receives a copied selection + viewport snapshot. Expand **Attached context snapshot** to inspect it. Later selection or viewport changes do not change earlier messages.
6. The second message is explicitly labeled **Local demo / no model**. It only reports captured IDs and coordinates; it does not answer scientific questions or parse commands from chat text.

At desktop widths the panels are side by side; medium widths put literature below; small screens stack sky, conversation, then literature. Buttons expose pressed/disabled states, the conversation is a live log, and focus indicators and input labels are provided. Fonts use locally available/system fallbacks; no remote font request is needed.

## Code map and future boundaries

- `src/lib/components/{ChatPanel,SkyPanel,LiteraturePanel}.svelte`: panel UI.
- `src/lib/workspace/contracts.ts`: exported `WorkspaceSelection`, `SkyViewport`, `WorkspaceSnapshot`, `ViewCommand`, and `ChatMessage`; pure snapshot, command-validation, and demo-reply functions.
- `src/lib/workspace/state.svelte.ts`: `createWorkspace()` rune-state factory, owned by `src/routes/+page.svelte`. No mutable SSR-global singleton. Public actions: `selectSource`, `selectPublication`, `updateViewport`, `requestView`, `askAboutSelection`, `send`; reactive getters plus writable `draft`.
- `src/lib/workspace/fixtures.ts`: three invented markers near M31 and three invented publications. These are neither source identifications nor real literature/evidence; no association between them is asserted.
- `src/lib/sky/aladin.ts` and `src/aladin-lite.d.ts`: a small, hand-declared vendor boundary because the npm package ships no TypeScript declarations. `fixtureIdFromClick` extracts marker IDs; workspace actions reject unknown IDs.
- `src/lib/workspace/*.test.ts`: Node-environment Vitest tests for pure contracts and compiled rune-state behavior.

`ViewCommand` permits `center`, `layer.visibility`, `sources.highlight`, `annotation.circle`, and `annotations.clear`. Angles are degrees, coordinates/sizes are bounded, and the workspace rejects unknown source/layer IDs. `requestView` queues copied commands; `acknowledgeView` records applied/failed status. Layer state and annotation counts change only after successful renderer operations. Applied means the renderer API operation returned, not that every image tile or a subsequent frame was verified. Commands are retained in order rather than replaced by the latest request.

The optional Python `scoop.chat.visual.build_view_tools` exposes matching requests to LangGraph. It publishes `view_request` artifacts with status `requested`; a future backend bridge must deliver those commands and feed acknowledgments back to the agent. That bridge is **not connected** in this sketch. No JavaScript is evaluated, and chat strings are not executable instructions. A backend still needs authentication, authorization, validation, cancellation, streaming, and persistence.

## Aladin integration and limitations

- The pinned npm package is dynamically imported only inside `SkyPanel`'s `onMount`. No Aladin import or WebGL access runs during SSR. There is no injected CDN script. Upstream Aladin initialization/assets and the real DSS2 HiPS imagery still require external network services; the imagery is not fixture data. Aladin's logo/link remain intact.
- An initialization rejection or 20-second timeout produces a visible fallback and marks the viewport `unavailable`. The fixture list, literature selection, and conversation continue working. Reload to retry. Initial/unavailable coordinates are explicitly labeled in snapshots; they are not presented as a verified live viewport.
- Initialization success does not guarantee individual survey tiles loaded. A persistent note explains the blank-tile fallback. Offline tiles, WebGL context loss, and vendor resource lifecycle behavior are not comprehensively managed in this sketch; do not treat it as a production atlas wrapper.
- Aladin is a large upstream bundle (~2.6 MB before gzip, ~0.85 MB gzip). It is a separate lazy client chunk; the build's chunk-size warning is expected. `adapter-auto` builds successfully but cannot choose a deployment target locally; production hosting needs an explicit supported adapter/configuration.
- Everything except external sky imagery stays in page memory. Reloading clears conversation and selections. No scientific summaries, real citation links, paper downloads, evidence matching, or agent execution are implemented.
- A Chromium smoke test verified source-list/paper selections, captured chat context, Aladin startup, circle/highlight commands, layer toggles, and mobile layout without horizontal overflow. The desktop screenshot was inspected and showed DSS2 tiles and the angular circle. This is not a comprehensive test of marker hit-testing, all browsers, accessibility, or failure recovery.

### Further manual browser acceptance checklist

- Load with network and WebGL2; confirm DSS2 tiles, Aladin attribution, and all three fixture markers.
- Click marker B, choose paper 2, center selection, pan/zoom, send; inspect both IDs and current viewport in the snapshot. Change selection and confirm the old snapshot stays unchanged.
- Check keyboard selection, focus transfer from **Ask about selection**, empty-send disabling, and independent clear actions.
- Check the three-column layout above 1150 px, two columns at medium widths, and a single column at 740 px and below.
- Block atlas initialization or disable WebGL2; confirm the visible fallback and working list/paper/chat interactions. Test a tile-only network failure separately.
