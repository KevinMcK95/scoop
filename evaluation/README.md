# SCOOP evaluation (prototype)

Checks that the data SCOOP returns for a patch of sky is **real**, **in the
requested field**, **complete** and **correct**, by going back to the archives
themselves. See [plans/duo_xu.md](../plans/duo_xu.md) for the full plan.

> **Status:** verifies the pipeline's real output (fields from
> `smoke_tests/lvdb_field.py`, as loaded by the workspace server), and any
> `result.json`. Also available as an **MCP server**, so Claude Code, the
> LangGraph chat or any MCP client can call the checks as tools.

## Files

| File | What it does |
| --- | --- |
| `verify.py` | One-call API: `verify_field`, `verify_result_file`, plus quick lookups (`lookup_ids`, `check_papers`, `check_links`, `check_positions`, `check_gw`). Accepts teammates' shapes too (nested `provenance`, ADS payloads from `scoop.literature`). Returns JSON-friendly summaries. |
| `mcp_server.py` | The same functions as MCP tools. |
| `field_adapter.py` | Turns a SCOOP field (`<key>_field.json`, via `scoop.fields`) into the result format the checks read: records with their links, the target's reference papers, the ellipse region, the pipeline's MAST selection and tolerances for its rounding. |
| `links.py` | Link checks: dead links, HTTP errors, "not found" pages and generic "soft 404" pages. |
| `checks.py` | Core checks (provenance, region, ID re-checks for Gaia/MAST/SIMBAD, sanity, completeness, files), sampling, and `run_all`. Gaia queries go to the Heidelberg ARI mirror, with ESA as the fallback. |
| `gw.py` | Reads flat and multi-order HEALPix sky maps; credible level and line-of-sight distance at the target; GW checks. |
| `literature.py` | Paper checks against ADS, SIMBAD and arXiv; must-include lists. |
| `geometry.py` | Angular separations, `s_region` parsing, and `Region` (a cone or a polygon such as an ellipse) with containment and footprint-overlap tests. |
| `timeouts.py` | Time limits and retries, so a slow or flaky archive is retried and then reported as skipped instead of hanging the run. |
| `gather_example.py` | Stand-in data agent for testing the checks: gathers real data for one target into `result.json`; `--inject-fakes` adds deliberately wrong records. |
| `benchmark.json` | Benchmark targets and what each result must include. |
| `run_checks.py` | Command line: verify a field or a `result.json` and write a report. |
| `requirements.txt` | Extra dependency for the MCP server (`mcp`). |

## Verify pipeline output

```bash
python smoke_tests/lvdb_field.py --out out/draco        # Kevin's pipeline (if not run yet)
python -m evaluation.run_checks draco_1 --root out       # or: out/draco/draco_1_field.json
```

This writes `out/draco/draco_1_verify_report.md` (and `.json`, plus the
converted `draco_1_verify_result.json`). `--only in_field id_recheck` runs a
subset of checks. From Python: `from evaluation.verify import verify_field`.

## Use it over MCP

The MCP server offers six tools:

| Tool | Use |
| --- | --- |
| `verify_field(field, root="out", sample=100, seed=0, only=None)` | Verify a pipeline field by key (`draco_1`) or path |
| `verify_result(path, target=None, sample=100, seed=0, only=None)` | Verify a `result.json`, or a saved ADS payload (give `target`) |
| `lookup_ids(archive, ids)` | Do these Gaia `source_id`s / MAST `obs_id`s / SIMBAD identifiers / ADS bibcodes exist? |
| `check_papers(bibcodes, target=None, titles=None)` | Papers exist in ADS, titles match, and are about the target |
| `check_links(urls)` | Links work and point at what they claim |
| `check_positions(ra, dec, radius_arcmin or polygon, positions, s_regions)` | Which positions and footprints fall in a cone or polygon |
| `check_gw(event, ra, dec, skymap_url=None)` | Credible level and distance of a position in a GW sky map |

Tool errors (e.g. an archive timing out) come back as `{"error": ...}`.

## Sampling: a quick check first, everything on request

Checks that query an archive or a website for each record (ID re-checks,
files, GW maps, papers, links) run by default on a **reproducible random
sample**: at most 100 records per archive and 100 distinct links. Local checks
(region, sanity, provenance) and the single-query completeness checks always
cover everything. For each sampled group the report says how many were checked
and gives a 95% upper limit on the failure rate of the whole group, e.g.
"checked 100 of 3243 at random (seed 0): 0 failed; at 95% confidence at most
3.0% of all 3243 would fail". For a full check pass `sample=0` (MCP) or
`--sample 0` (command line); `seed` picks a different sample.

**Claude Code** (from the repository root, with the environment from the
main README plus `pip install -r evaluation/requirements.txt`):

```bash
claude mcp add scoop-eval -- "$PWD/.venv/bin/python" "$PWD/evaluation/mcp_server.py"
```

Then ask, for example, "verify the draco_1 field" or "do these bibcodes
exist?". Use the path to your own environment's `python` if it isn't `.venv`.
A full verification can take a few minutes; if your client times out, raise
its tool timeout (`MCP_TOOL_TIMEOUT` in Claude Code) or pass `only`.

**LangGraph chat** (`scoop/server`), a sketch using
[langchain-mcp-adapters](https://github.com/langchain-ai/langchain-mcp-adapters)
(not yet tried here):

```python
from langchain_mcp_adapters.client import MultiServerMCPClient

client = MultiServerMCPClient({"scoop-eval": {
    "command": ".venv/bin/python", "args": ["evaluation/mcp_server.py"], "transport": "stdio"}})
eval_tools = asyncio.run(client.get_tools())
app = create_app(..., extra_tools=lambda: eval_tools)
```

**agentbook** can skip MCP and wrap the functions directly, e.g.
`Tool("verify_field", "...", {"type": "object", "properties": {"field": {"type": "string"}},
"required": ["field"]}, verify.verify_field)`.

## What the pipeline should record

The checks work on today's output, but some can only warn until the pipeline
records a little more. Per row (or per table, with the query saved):

- `query`: the ADQL / API call that produced the row
- `retrieved_at`: when it was retrieved (UTC)
- `access_url`: the service endpoint or download link
- for MAST observations: `t_min`/`t_max` (MJD) rather than only a date, and
  `dataURL` where MAST has one

Right now each table has one provenance label (e.g. `Gaia DR3 (ARI Heidelberg
TAP)`), so the provenance check reports one warning per table.

## Test the checks themselves

From the repository root:

```bash
python -m evaluation.gather_example M87 --inject-fakes
python -m evaluation.run_checks evaluation/runs/M87/result.json

python -m evaluation.gather_example "NGC 4993" --gw-event GW170817 --inject-fakes
python -m evaluation.run_checks evaluation/runs/NGC_4993/result.json
```

Leave out `--inject-fakes` for a clean run. Outputs go to
`evaluation/runs/<target>/` (git-ignored). Each run queries Sesame, Gaia,
MAST, ADS, SIMBAD, arXiv and the LIGO DCC, so it needs network access and
takes a few minutes. `--timeout` sets how long each archive or check may take
(default 300 s).

**Needs:** `astropy`, `astroquery`, `numpy`, `requests`, and `healpy` or
`astropy-healpix` for the GW check. Paper gathering and the definitive
"does this bibcode exist" check need an
[ADS API token](https://ui.adsabs.harvard.edu/user/settings/token) in
`ADS_API_TOKEN` or `~/.ads/dev_key`. Without one, existence falls back to
SIMBAD and only warns.

## Checks

| Check | Question | How |
| --- | --- | --- |
| `provenance` | Can each record be traced back? | Required fields present: archive, native ID, query, retrieval time, plus access URL for catalog rows, files, papers and sky maps. Missing dates or download links on observations only warn, since MAST leaves them empty for some records. |
| `archive_status` | Were failures reported? | Every archive has a returned / empty / failed status. |
| `target` | Is the target in the right place? | Query position vs. Sesame, within 2″. |
| `in_field` | Is everything inside the region? | Region is a cone or a polygon (e.g. a field's ellipse). Catalog rows: inside it (1″ tolerance). Observations: footprint (`s_region`) overlaps it. |
| `id_recheck` | Does it exist, with these values? | Look up Gaia `source_id`s, MAST `obs_id`s and SIMBAD identifiers again; compare position, magnitude, instrument, dates and footprint (within the source's rounding). |
| `sanity` | Is it physically possible? | Dates inside the mission lifetime (no JWST data before launch), instrument belongs to the telescope, no future dates, no negative exposure times. |
| `completeness` | Is anything missing? | Gaia: an independent query of the region; every source must be in the result. MAST: if the result states its selection (as fields do: HST/JWST science images, `calib_level` ≥ 2, no HAP), every matching observation overlapping the region must be present; otherwise counts per collection, as warnings. Flags row counts that look like a row limit. |
| `file` | Is the download a real file? | File exists, size matches the archive, starts as FITS, opens and verifies. |
| `file_matches_record` | Is it the file it claims to be? | `TELESCOP`, `INSTRUME` and `EXPSTART` in the header match the observation record. |
| `file_content` | Does the content make sense? | Images: the WCS footprint overlaps the cone and the target pixel is finite. Spectra: target position in the cone, wavelengths inside the instrument's range, flux not all zero. |
| `gw_localization` | Is the target really in this GW event's localization? | Re-download the sky map from its access URL; check its header names the same event, its probabilities sum to 1, and the local copy is identical; recompute the target's credible level and compare with the claim. Reports the line-of-sight distance at the target. |
| `paper_exists` | Is the paper real? | Bibcode found in ADS, with matching title, first author and year. |
| `paper_about_target` | Is it about this source, not made up or misattributed? | Linked to the target in SIMBAD's bibliography or ADS's object search, or the title or abstract names the target or one of its SIMBAD identifiers. |
| `arxiv` | Does the arXiv ID match? | arXiv's title for the ID matches the paper's title. |
| `links` | Do the data links work? | Each distinct URL is fetched once (streamed, so data files aren't downloaded). Fails on dead hosts, HTTP errors, "not found" pages, and STScI program pages that don't name their program; warns on timeouts and pages that never mention the ID in their own URL or look the same as the page for a made-up ID. ADS abstract links are checked through the ADS API. A failing link is fetched once more, 5 s later. |
| `must_include` | Are the essential items there? | Papers and GW events listed for the target in `benchmark.json`. |

## Results so far

### Pipeline output: Draco (`smoke_tests/lvdb_field.py`, 2 r_half ellipse)

| Check | Result |
| --- | --- |
| Gaia DR3 sources re-checked against the archive | 3243 of 3243 match (full run) |
| Gaia completeness (independent query of the ellipse) | 3243 of 3243 in the field: nothing missing |
| MAST observations re-checked | 119 of 119 match |
| MAST completeness (the pipeline's own selection rules) | every matching observation is present |
| SIMBAD galaxies re-checked | 35 of 35 match |
| Records inside the ellipse | 3758 of 3758 |
| Reference papers (LVDB) real and about Draco | 6 of 6 |
| Data links (sample of 100 of 451) | 100 of 100 work: at most 3.0% of all 451 would fail (95%) |

Warnings: provenance is recorded per table rather than per row (see "What the
pipeline should record").

### Planted problems

| Run | Records | Planted problems caught | Real records failing |
| --- | ---: | --- | ---: |
| M87, with fakes (incl. 3 bad links) | 184 | **18 of 18** | 0 |
| NGC 4993, with fakes | 73 | **18 of 18** | 0 |
| M87, clean | 178 | – | 0 (609 pass, 20 warnings) |
| NGC 4993, clean | 67 | – | 0 (236 pass, 9 warnings); 1 must-include paper missing |

The warnings in the clean runs are expected: MAST observations the stand-in
didn't sample, and observations MAST lists without a date or download link.

Planted problems, and which check caught them:

| Planted problem | Caught by |
| --- | --- |
| Made-up Gaia `source_id` | `id_recheck` |
| Real Gaia source, position shifted 5″ and G off by 0.8 mag | `id_recheck` |
| Real Gaia source from outside the cone | `in_field` |
| Record with no provenance | `provenance` |
| 3 real Gaia sources dropped from the result | `completeness` |
| Made-up JWST `obs_id` | `id_recheck` |
| Real JWST observation dated before launch (M87) | `id_recheck`, `sanity` |
| Real HST observation with its footprint moved 1° away | `in_field`, `id_recheck` |
| HTML error page saved as `.fits` | `file` |
| Made-up bibcode | `paper_exists` |
| Real bibcode with another paper's title | `paper_exists`, `arxiv` |
| Real paper with another paper's arXiv ID | `arxiv` |
| Real paper about a different object | `paper_about_target` |
| GW170817 with a wrong credible level | `gw_localization` |
| GW150914 wrongly claimed to contain NGC 4993 | `gw_localization` |
| GW170814 record pointing at GW170817's sky map | `gw_localization` |
| Made-up GW event | `gw_localization` |

GW170817 check: NGC 4993 is at credible level 0.38 (inside the 90% region),
and the sky map's line-of-sight distance there is 39 ± 7 Mpc, consistent with
NGC 4993's distance of about 40 Mpc.

## What we learned about the archives

- **MAST** often has no `dataURL` in its observation table (the download link
  has to come from the product list), and it lists **planned JWST
  observations** (`calib_level = -1`) with no date yet. Neither is a sign of a
  made-up record.
- HST has an `FGS` instrument (the Fine Guidance Sensors), which the first
  version of the instrument list missed. Lists like this need care.
- M87 has no SDSS spectrum, so spectra came from HST/STIS.
- The **ADS search API has no `object:` field**; the web interface's object
  search goes through ADS's object service, which translates the name into
  SIMBAD/NED identifiers. Agents need to do the same.
- **SIMBAD doesn't cover every journal.** It has no record of the GW170817
  discovery paper in PRL, so ADS, not SIMBAD, has to decide whether a paper
  exists.
- **Searching by host galaxy misses the GW170817 discovery paper**
  (PRL 119, 161101), the single most important paper for that field, because
  it is indexed under the event rather than NGC 4993. The literature agent
  should also search the names of transients and events associated with the
  target (GW170817, AT2017gfo).
- **"Linked to the object" isn't "about the object".** Surveys and catalogs
  that only list the target in a table (6dF, the 2MASS Redshift Survey,
  HyperLEDA, the WMAP first-year paper) count as linked. Ranking for relevance
  needs more than SIMBAD/ADS links.
- GraceDB requires a login for GW170817's files; the public GWTC-1 sky maps
  are on the LIGO DCC (LIGO-P1800381).
- A slow archive once hung a run for 18 minutes with no output, hence the
  time limits.
- The **ESA Gaia archive can't run `CONTAINS` on big polygons** (a
  181-vertex ellipse gives an HTTP 500, 60 vertices times out). The checks
  query the enclosing circle and cut to the region themselves, and use the
  faster **Heidelberg ARI mirror** first, with ESA as the fallback.
- **SkyServer serves "Error" pages to bursts of requests.** At 8 requests at
  once, spectrum and explore links came back as error pages; at 2 at a time
  all of them worked. Users opening many spectrum links from the viewer may
  hit the same thing.
- **STScI program pages return status 200 for programs that don't exist**
  (a generic "JWST Program Information" page), so a link check that only looks
  at the status would pass a made-up program. A real page names its program in
  its title.
- **ADS abstract pages refuse scripted requests** (HTTP 405), so ADS links
  are checked through the ADS API.
- SIMBAD doesn't resolve the bare name "Draco" (its main identifier is
  "Z 300-12"; "Draco dSph" works), although Sesame does.
- The LVDB's metallicity reference for Draco (an arXiv paper on many dwarfs)
  names Draco only in its full text, not its title or abstract. Searching ADS
  full text avoids flagging such papers.
- On some machines the certificate bundle that `requests` ships with fails
  for `www.stsci.edu` and SkyServer; the link check falls back to the system
  bundle (`$SCOOP_CA_BUNDLE` overrides).

## Next steps

- Run the remaining benchmark targets from the plan: an empty field, a
  crowded Galactic-plane field, a target near RA = 0 or a pole, a made-up
  object, the Sculptor Group, QSOs near M31, a topic-only literature query,
  and repeated runs for stability
- More archives (IRSA, HEASARC, SDSS spectra) and data products
- Multi-order GraceDB sky maps from O3/O4 alerts (supported, but so far only
  tested on a GWTC-1 map converted to multi-order form)
- Try the MCP tools from the LangGraph chat (`create_app(extra_tools=...)`)
- Run on more LVDB fields (`--lvdb-key leo_1`, `sculptor_1`, ...)
- Ask the pipeline to record per-row provenance (query, retrieval time, URL)
- Stretch: check that summary claims are supported by their cited papers
