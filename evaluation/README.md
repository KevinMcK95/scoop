# SCOOP evaluation (prototype)

Checks that the data SCOOP returns for a patch of sky is **real**, **in the
requested field**, **complete** and **correct**, by going back to the archives
themselves. See [plans/duo_xu.md](../plans/duo_xu.md) for the full plan.

> **Status:** local trial on two targets (M87 and NGC 4993). The real data and
> literature agents don't exist yet, so `gather_example.py` stands in for them.

## Files

| File | What it does |
| --- | --- |
| `gather_example.py` | Stand-in for the agents: resolves a target, gets Gaia DR3 sources, a sample of HST/JWST observations from MAST, one small image and one small spectrum, the most-cited papers from ADS, and GW sky maps for any `--gw-event`. Writes `result.json` in the provenance format. `--inject-fakes` adds deliberately wrong records to test the checks. |
| `checks.py` | Core checks (provenance, field of view, ID re-checks, sanity, completeness, files) and `run_all`. |
| `gw.py` | Reads flat and multi-order HEALPix sky maps; credible level and line-of-sight distance at the target; GW checks. |
| `literature.py` | Paper checks against ADS, SIMBAD and arXiv; must-include lists. |
| `geometry.py` | Angular separations, parsing of ObsCore `s_region` footprints, footprint–cone overlap. |
| `timeouts.py` | Time limits, so a slow archive is reported as skipped instead of hanging the run. |
| `benchmark.json` | Benchmark targets and what each result must include. |
| `run_checks.py` | Runs every check on a `result.json` and writes `report.md` and `report.json` next to it. |

## Run it

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
| `in_field` | Is everything inside the cone? | Catalog rows: separation ≤ radius. Observations: footprint (`s_region`) overlaps the cone. |
| `id_recheck` | Does it exist, with these values? | Look up every Gaia `source_id` and MAST `obs_id` again; compare position, magnitude, instrument, dates and footprint. |
| `sanity` | Is it physically possible? | Dates inside the mission lifetime (no JWST data before launch), instrument belongs to the telescope, no future dates, no negative exposure times. |
| `completeness` | Is anything missing? | Independent Gaia cone query (async, no row limit): every source must be in the result. MAST: counts per collection vs. the archive (warning, since the agents may sample on purpose). Flags row counts that look like a row limit. |
| `file` | Is the download a real file? | File exists, size matches the archive, starts as FITS, opens and verifies. |
| `file_matches_record` | Is it the file it claims to be? | `TELESCOP`, `INSTRUME` and `EXPSTART` in the header match the observation record. |
| `file_content` | Does the content make sense? | Images: the WCS footprint overlaps the cone and the target pixel is finite. Spectra: target position in the cone, wavelengths inside the instrument's range, flux not all zero. |
| `gw_localization` | Is the target really in this GW event's localization? | Re-download the sky map from its access URL; check its header names the same event, its probabilities sum to 1, and the local copy is identical; recompute the target's credible level and compare with the claim. Reports the line-of-sight distance at the target. |
| `paper_exists` | Is the paper real? | Bibcode found in ADS, with matching title, first author and year. |
| `paper_about_target` | Is it about this target? | Linked to the target in SIMBAD's bibliography or ADS's object search, or the title names it. |
| `arxiv` | Does the arXiv ID match? | arXiv's title for the ID matches the paper's title. |
| `must_include` | Are the essential items there? | Papers and GW events listed for the target in `benchmark.json`. |

## Results so far

| Run | Records | Planted problems caught | Real records failing |
| --- | ---: | --- | ---: |
| M87, with fakes | 181 | **15 of 15** | 0 |
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

## Next steps

- Run the remaining benchmark targets from the plan: an empty field, a
  crowded Galactic-plane field, a target near RA = 0 or a pole, a made-up
  object, the Sculptor Group, QSOs near M31, a topic-only literature query,
  and repeated runs for stability
- More archives (IRSA, HEASARC, SDSS spectra) and data products
- Multi-order GraceDB sky maps from O3/O4 alerts (supported, but so far only
  tested on a GWTC-1 map converted to multi-order form)
- Agree on the record format with the team, then point the checks at the real
  agents' output
- Stretch: check that summary claims are supported by their cited papers
