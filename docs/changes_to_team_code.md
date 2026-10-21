# Changes to teammates' code

## Kevin's smoke-test pipeline

The workspace reuses Kevin's pipeline (`smoke_tests/lvdb_field.py`,
`smoke_tests/sky_viewer.py`) to build fields. These are the changes made to it;
all are additive or bug fixes, and LVDB runs produce the same output as before
(Draco is byte-identical).

### Additions

- `sky_viewer.py`: `viewer_data()` is split out of `write_viewer()`, which also
  writes `<key>_field.json` next to the HTML page. The workspace loads that file.
- The viewer data carries more of what the pipeline already computes: the
  target's systemic proper motion and its LVDB references (every non-empty
  `ref_*` column, as `references`), and Gaia parallax/proper-motion errors and
  elliptical radius `r_ell`. These also show in the standalone page's popups.
- Cone mode: `lvdb_field.py --ra --dec --radius-arcmin --name` runs the same
  pipeline on a circular region around any position (`cone_target()`: r_half =
  radius, n_rhalf = 1, ellipticity 1e-6 because mocpy rejects a round ellipse).
  The target has `kind: "cone"`; the page shows "—" for its distance and
  "Cone search" as its source.

### Fixes (found running Leo II)

- An empty VizieR cone (no MILLIQUAS or Quaia rows) was cached as a table with no
  columns and later failed with `KeyError: 'RAJ2000'`. Empty results now get the
  columns read downstream (`QSO_EMPTY_COLUMNS`, `_with_columns()`).
- With no DESI (or SDSS) spectra, `survey_gaia_id` became an empty *float*
  column, which `ids.check_id_columns` (correctly) refused. Both columns are now
  built as explicit `int64` arrays.

## Xuan's exoplanet tools

The workspace's exoplanet data source (`scoop/sources/exoplanets.py`) calls
Xuan's NASA Exoplanet Archive cone search. Those functions lived in
`exoplanets/tools.py`, which imports the `agentbook` harness (and through it
`jsonschema`) at the top, so using them meant installing a whole second agent
framework. They are split, without changing behavior:

- `exoplanets/archive.py`: the plain functions (`resolve_target`,
  `exoplanet_cone_search`, `get_planets`, the record/replay `http_get`, ...),
  raising `ArchiveError`, with no `agentbook` import.
- `exoplanets/tools.py`: the agentbook `Tool` wrappers as before, importing the
  functions from `archive.py`; `_as_tool_error` turns an `ArchiveError` into the
  `ToolError` the model sees. `from exoplanets.tools import ...` keeps working.

## Yilun's chat agent and server (data agent integration)

Changes made to connect the data agent (`scoop/chat/data_agent.py`,
`scoop/discovery.py`, `scoop/observations.py`); all additive.

- `scoop/server/app.py`: each chat turn also gets `ask_data_agent` (the hand-off
  to the data agent, on the same model). Its `store_rows` saves a table with
  `FieldStore.add_rows(..., meta)` and then refreshes the turn's field object in
  place, so the map tools see a table the data agent added in the same turn
  (before, `describe_field` answered "Unknown table").
- `scoop/fields.py`: `FieldStore.add_rows` takes an optional `meta` (label,
  provenance, kind, columns, style, query) for tables with no source plug-in;
  `field_from_viewer_data` turns stored entries with `meta` into tables
  (`origin: "agent"`) and layers (group "Added by the data agent"). Footprint
  tables get the standard columns (`scoop/observations.py`), and point tables are
  matched to Gaia (`link_to_gaia`: Gaia ID column first, else 1″), adding
  `in_<table>` and a details group to Gaia rows.
- `scoop/chat/visual.py`: `overlaps` / `overlap_report` take `table_b`, to compare
  footprints in two tables (e.g. `observations` vs `cfht_megacam`); pairs carry
  `a_table` / `b_table` and the result gives `layer_filters` per table. Calls
  without `table_b` behave as before.
- `scoop/chat/prompt.py`: describes `ask_data_agent` and `overlaps` across
  tables, and says to answer questions about data already in the field directly
  (not through the data agent).
- The data agent's `save_skill_draft` follows the dataset scout's convention:
  `skills/surveys/<name>.md` with `status: draft`, multi-line `sources_checked`,
  `proposed_by: data agent, <date>`, and a row in the index's surveys table marked
  *(draft, written by the data agent)*; it never overwrites a skill. So
  `scoop.scout.coverage.read_coverage` sees its drafts like the scout's own.
- `run_python` gets the same field object as the map tools, so a table the data
  agent adds in a turn is available to it in that turn too.

## Member selection (lasso) and proper-motion tools

Changes for selecting members in plots (ideas from bp3m's member-selection
notebook; no bp3m code) and for the proper-motion tools (`scoop/pm.py`,
`scoop/chat/pm_tools.py`); all additive.

- Filters gain a `lasso` op, in both `scoop/fields.py` and
  `web/src/lib/field/filter.ts` (shared cases in `tests/fixtures/filter_cases.json`):
  `{column: x, op: "lasso", value: {y, polygons: [[[x, y], ...], ...], missing}}`
  keeps rows whose (x, y) is inside any polygon (planar, in the plot's own units).
  `missing: "keep"` lets rows without that panel's data pass (lenient), `"reject"`
  drops them (strict, the default).
- `scoop/chat/visual.py`: `Clause.value` accepts the lasso value (`LassoValue`), so
  the chat agent can write lasso filters too.
- `web/src/lib/components/PlotPanel.svelte`: Box / Lasso tools that draw a selection
  on the current plot. Each panel's selection is one lasso clause in a user layer
  (`user:members:<table>`, "Members", group "Your selections"), so panels intersect;
  a redraw replaces that panel's selection unless Extend is ticked; Strict sets
  `missing: "reject"`. Clear panel / Clear all, a members count, and a Members CSV.
  Helpers `simplifyPath` and `boxPolygon` are in `web/src/lib/field/plot.ts`.
- Sky selection: `field_from_viewer_data` gives every point table `xi` / `eta`
  (`add_tangent_plane`: gnomonic offsets from the target, arcmin, east / north), and
  Gaia has a "Sky (ξ, η)" preset. Great circles are straight in that projection, so
  a lasso on xi/eta is the same polygon on the sky, with no RA wrap. Plot specs (and
  `PlotShow`) take `invert_x` and `equal` (same scale on both axes).
- Plot zoom (`PlotPanel.svelte`, `zoomView` / `equalAxes` / `View` in `plot.ts`):
  scroll to zoom about the cursor, Zoom (drag a box), Pan, ＋/－ and Reset view; a
  zoomed view replaces the 1–99% axes and changing the plot resets it. Selections
  are stored in data units, so zooming does not change them.
- `web/src/lib/workspace/state.svelte.ts`: the field context sent to the chat
  includes the filter of user layers, so the agent knows what "my members" are.
- Gaia rows with a DESI spectrum get `rv`, `rv_err` and `feh`
  (`scoop/sources/spectra.py`), and Gaia has an "RV / G" plot preset.
- `fit_members` (in the proper-motion tools; maths in `scoop/members.py`): a
  quick-look maximum-likelihood fit to the Members sample. The Gaia table now carries
  `pmra_pmdec_corr` when the pipeline provides it (`smoke_tests/lvdb_field.py`
  queries it, `smoke_tests/sky_viewer.py` writes it); older fields fit with it as 0
  and say so.
- `auto_members` (same module; selection in `scoop/members.py`): the agent selects
  members itself (radius, parallax, proper motion about a literature or data-driven
  prior, an empirical CMD region, velocity) and fits them. It publishes the result
  as a `map_actions` artifact adding or updating the layer `agent:members:<table>`,
  whose filter is the exact ID list (plus the readable radius and CMD clauses), so
  Gaia IDs never pass through the model. `build_pm_tools` takes the browser's
  `layers` for that, and `build_map_tools` takes an optional `known_layers` dict that
  `app.py` shares with the PM tools, so `update_map` can restyle a layer
  `auto_members` added earlier in the same turn. The Plot panel draws any visible layer's lasso regions on the
  matching plot, and the browser tests long `in` lists through a cached Set.
- `scoop/server/app.py`: each chat turn also gets the proper-motion tools
  (`pm_expectations`, `pm_published`, `pm_new_epoch`, `pm_wait_time`, `fit_members`,
  `auto_members`), which read
  the Local Volume Database tables cached under `<fields>/lvdb_cache`.
- `scoop/chat/prompt.py`: a "Proper motions" section (what each tool is for; position
  errors are inputs, never chosen silently; ground-based imaging needs its own error
  or should be left out with `instruments`), plus how to use the Members layer.

## Parallel tool results in the chat stream

- `scoop/server/app.py` `_events`: when one step runs several tools that return
  `Command`s in parallel (e.g. `auto_members` next to `ads_paper`), LangGraph's
  `updates` stream gives the tools node a *list* of updates. `_events` skipped
  anything that wasn't a dict, so those tools' results and artifacts (including map
  layers) never reached the browser. It now flattens the list. Test:
  `tests/test_server.py::ParallelCommandEventTests`.

## Tools follow a field loaded mid-turn (with the start page)

With the start page, a turn can begin with no field and `load_field` it (Yilun's
change: the map tools switch to it at once). The other tools now switch too:

- `scoop/server/app.py` keeps the turn's field in `turn["field"]`: the `read_field`
  hook passed to `build_map_tools` records each field it loads, and `store_rows`
  refreshes that field in place. The PM tools, `ask_data_agent` and `run_python` get
  `current_field` (a function) instead of the field.
- `scoop/chat/python_tool.py` `build_python_tools`, `scoop/chat/data_agent.py`
  `build_handoff_tool` and `scoop/chat/pm_tools.py` `build_pm_tools` accept either a
  field or a function returning it; passing a field works as before.
- Test: `tests/test_server.py::StartPageTests` (load_field then a PM tool, in one turn).

## Regions beyond cones (boxes, polygons, stream tracks) and New search

New: `scoop/regions.py` (region shapes, great-circle stream frames), `scoop/streams.py`
(galstreams tracks, read from its files), `scoop/chat/region_tools.py` (`find_stream`,
`plan_region`, `run_region`). Changes to shared code, all additive:

- `smoke_tests/lvdb_field.py`: `--region spec.json` (with `--count-only` and
  `--max-gaia-rows`). Region searches fetch only Gaia (polygon ADQL per piece, optional
  G limit and a stream-frame proper-motion window) and SIMBAD galaxies; HST/JWST, QSO
  catalogs and spectra only when the spec lists them in `archives`. Cone-only archives are
  queried on small cones covering the region (`Region.cover_cones`). DESI failures no
  longer fail the whole run (decision note; not cached, so a re-run retries). LVDB and
  cone runs are unchanged (checked on Draco).
- `smoke_tests/sky_viewer.py`: `viewer_data` / `write_viewer` take `query_region` and
  write it into the field JSON.
- `scoop/fields.py`: fields keep `query_region`; for bands, point tables get `phi1`,
  `phi2`, `dphi2` and (with proper motions) `pm_phi1`, `pm_phi2`, `dpm_phi1`, `dpm_phi2`,
  and Gaia gets stream-frame plot presets.
- `scoop/chat/visual.py`: `_coverage` tests circles against `query_region` when a field
  has one (a long band's outline is too large for the tangent-plane polygon test);
  `run_cone`'s docstring points to `plan_region` for other shapes.
- `scoop/server/jobs.py`: region plans (`save_plan`, `load_plan`, `count_plan`,
  `start_region`), stored under `<fields>/region_plans/`; `_launch` takes a timeout.
- `scoop/server/app.py`: each turn gets the region tools.
- `web/src/lib/workspace/state.svelte.ts` `newSearch()` and a **New search** button in
  `web/src/routes/+page.svelte`: clears the field, layers, plot, highlights and drawings,
  starts a new conversation and returns to the start page.

## Fixes: loose lists in chat, repeated cost note, plan files, QSOs in stream fields

- `web/src/lib/chat/markdown.ts`: the `text` renderer returns marked's pre-rendered
  text when the token is flagged `escaped` (marked does this for loose list items,
  i.e. items separated by blank lines). Before, those answers showed their HTML as text
  (`<strong>`, bibcode links). Raw HTML from the model is still escaped. Test in
  `markdown.test.ts`.
- `web/src/lib/workspace/state.svelte.ts`: when the model has no price, the "cannot be
  costed" note is shown once per conversation instead of on every command.
- `smoke_tests/lvdb_field.py` reads region files with `scoop.regions.read_spec`, which
  accepts a saved plan (`{"name", "spec"}`, as `JobManager.save_plan` writes) or a bare
  spec; before, every `plan_region` count failed with "a region spec needs a kind".
- Stream fields with a proper-motion window also keep the region's Gaia-CRF3 QSOs (G
  limit, no motion cut), which the window would otherwise remove; they are the field's
  zero-motion references.

## Velocities and metallicities from any catalog reach the RV plots

- `scoop/fields.py`: point tables the data agent adds have their velocity / [Fe/H]
  columns (declared `roles`, else recognised by name: `RVel`, `HRV`, `radial_velocity`,
  `[Fe/H]`, ...) copied into the standard `rv`, `rv_err`, `feh`, `feh_err`, with RV
  presets; `link_to_gaia` carries them to the matched Gaia stars (repeats: inverse-variance
  mean), keeping a value a star already has, and records `rv_source` / `feh_source`.
- `scoop/sources/gaia.py`: Gaia presets "RV histogram" and "RV vs [Fe/H]" (every source
  of velocities on the field's stars); `scoop/sources/spectra.py`: its presets renamed
  "DESI RV histogram" / "DESI RV vs [Fe/H]" (what they plot), and DESI values set
  `rv_source`.
- `scoop/chat/data_agent.py`: `query_catalog` / `query_service` take `roles`; their reply
  says which measurement columns were recognised. Prompts: the data agent passes roles
  (and prefers one combined row per star); the chat agent shows the RV histogram once RVs
  arrive and points to RV vs [Fe/H].
