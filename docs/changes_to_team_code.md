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
- `scoop/server/app.py`: each chat turn also gets the proper-motion tools
  (`pm_expectations`, `pm_published`, `pm_new_epoch`, `pm_wait_time`, `fit_members`), which read
  the Local Volume Database tables cached under `<fields>/lvdb_cache`.
- `scoop/chat/prompt.py`: a "Proper motions" section (what each tool is for; position
  errors are inputs, never chosen silently; ground-based imaging needs its own error
  or should be left out with `instruments`), plus how to use the Members layer.
