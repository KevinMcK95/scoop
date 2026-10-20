# The map as a shared canvas

SCOOP is visualizer-first: the sky map is the product, and chat, literature and
tables serve it. The user and the agent work on the same map. This plan merges
Kevin's field viewer (`smoke_tests/sky_viewer.py`) with the `web/` workspace
into one app, and defines how the agent operates the map.

## Principles

1. **Shared canvas.** The user's selection, visible layers, viewport and drawn
   regions go to the agent with each message. The agent's changes appear on
   the same map, labelled as the agent's, and can be undone.
2. **Layers are views of results.** A layer is `result table + filter + style +
   label`, not a hard-coded overlay. Pipelines and tools produce results; the
   agent (or user) chooses how to show them. Every drawn object links back to
   its archive record.
3. **Small action vocabulary** for the agent (below), structured and validated,
   never generated JavaScript.
4. **Scenes.** After each agent turn the browser captures the map state
   (viewport, layers, highlights, annotations) and attaches it to that answer.
   "Show on map" on an earlier message restores the view it was based on.
5. **The agent sees summaries, not rows.** It gets each table's columns, value
   ranges and counts, and each layer's count; it asks tools for filters or
   object details. The server owns results; derived layers keep provenance.
6. **Attention.** The agent shows what the question needs and explains what it
   hid (Kevin's decision log becomes these explanations). Agent notes are
   visually distinct from archive data.

## Data model

```text
FieldResult (server-owned, by id)
  target        name, center, structure, provenance
  tables        observations | planned | gaia | milliquas | simbad_galaxies | spectra
                rows: {id, ra, dec, s_region?, props{...}}   ids are exact strings
  coverage, baselines, decisions, spectra details

Layer
  id, table, filter[], style{color, shape, size, dash?}, group, label,
  visible, origin: pipeline | agent | user, note?
```

A filter is a list of AND-ed clauses `{column, op, value}` with
`op ∈ == != < <= > >= in exists`. The same evaluator exists in Python (counts and
summaries for the agent) and TypeScript (rendering), checked against shared
test cases.

Kevin's viewer layout becomes the **default presentation** of a field result:
footprints per instrument, planned JWST, Gaia stars and galaxy candidates,
spectra, SIMBAD galaxies by Gaia verdict, trusted QSOs, and the impure Gaia
QSO candidates hidden by default.

## Data sources and interface hooks

A data source (an archive or catalogue: MAST, Gaia, MILLIQUAS, SIMBAD, SDSS/DESI,
the LVDB target, ...) is one module in `scoop/sources/` that calls
`register(Source(...))`. Modules are discovered automatically; adding one
changes no other code (`tests/test_sources.py` checks this). The interface has
fixed hooks that a data source fills through the field JSON, so neither the
server tools nor the web app name data sources:

| Hook | Declared by the data source | Where it shows |
|---|---|---|
| table | `name`, `label`, `provenance`, `kind` (catalog / footprints / annotation), `order` | every tool; Tables tab picker |
| columns | `columns`: units and descriptions | Selected panel, table headers, plot axes, `describe_field`/`summarize` |
| default layers | `layers(rows, data)` with an `order` key | map + Layers panel (groups, colours, shapes, visibility) |
| plot presets | `presets` | Plot tab buttons |
| table browser | `browse` columns | Tables tab |
| row links | `links(row)` → `_links` | Selected panel |
| row details | an `Enrichment` adding `_details` (e.g. spectra) | Selected panel |
| cross-source columns | `Enrichment(needs=...)` | e.g. Gaia `has_spectra` |
| summaries | `summaries(rows, data)`: titled small tables | Summary tab, `describe_field` (e.g. MAST imaging coverage) |
| on-demand query | `query(region)`: rows for a field's region, kept in `<key>_extra.json` | "＋ Add data" (marked *query*), agent `add_data` (e.g. NASA Exoplanet Archive) |

Rows come from the smoke-test pipeline's output, or from a data source's own
`query` (the exoplanet hosts are the first; Kevin's per-archive queries could
move into data-source modules the same way).

## Agent actions

| Action | Purpose |
|---|---|
| `layer.add` | new layer from a table + filter + style (agent-made) |
| `layer.update` | visibility, style, filter or label of a layer |
| `layer.remove` | remove an agent or user layer |
| `focus` | fly to a position, or frame a set of rows |
| `highlight` | emphasize rows of a table, with a reason shown on the map |
| `annotate` | circle or text label, optionally anchored to a row |
| `annotations.clear` | remove annotations |
| `select` | open a row in the Selected panel |

Each action is applied by the browser and acknowledged back to the agent on the
next turn; the agent never claims a change it hasn't seen confirmed.

## Workspace panels (from Kevin's viewer)

- **Left:** field (target facts), layer groups with swatches, counts and
  toggles, background survey.
- **Bottom drawer:** Selected (values, spectra and viewer links), Observations
  (sortable; click to frame the footprint; program links), Coverage &
  baselines, Literature, Decisions.
- **Right:** chat, with "show on map" per answer.

## Build order

1. Field result + layer model + action vocabulary, shared by server and
   browser; render a real Draco field with the default presentation and panels.
2. Agent tools: load a field, summarize/filter tables, add layers, highlight
   with reasons, annotate, focus; scenes per answer.
3. Split the pipeline's queries (MAST, Gaia, QSO catalogs, spectra, SIMBAD)
   into separate tools producing the same table shapes.
