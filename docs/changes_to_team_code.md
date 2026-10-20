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
