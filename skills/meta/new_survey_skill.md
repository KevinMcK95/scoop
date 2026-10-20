---
name: new_survey_skill
description: How to write a new survey/archive skill when a request needs a
  facility that isn't in skills/index.md (e.g. Euclid, DESI, Rubin, Roman), or
  when a data release changes enough that an existing skill is out of date.
  Produces a draft skill in the standard template, a smoke test, and a list of
  what still needs a human check.
used_by: [data agents, orchestrator]
exemplars: [surveys/gaia.md, surveys/mast_hst_jwst.md, surveys/sdss_spectra.md]
---

# Writing a new survey skill

Read the three exemplar skills first. The new skill must have the same
sections, in the same order, so that every data agent can use any skill the
same way.

## Ground rules

- **Every fact comes from a source you fetched in this session**: official
  archive docs, release notes, the service's own table listing, or a live query.
  Never from memory. Record the URL and date in `sources_checked`.
- **Citations are checked on Crossref (or the publisher / arXiv page)**
  before they go in. If one can't be checked, leave it out and list it under
  "Not yet verified".
- **Table names, column names and limits are read from the service**
  (`TAP_SCHEMA`, `list_tables`, `get_column_list`, ReadMe files), never guessed.
- **Unknowns are listed, not papered over.** The "Not yet verified" section is
  required, even if empty.
- A new skill starts as `status: draft`. An agent may use a draft skill, but
  must tell the user ("Using a new, unreviewed skill for <survey>; results may
  be incomplete"). A person reviews it (as a pull request) before it becomes
  `status: reviewed`.

## Procedure

1. **Find the official access points.**
   - The archive's own data-access and release pages (e.g. the mission's
     science archive, MAST, IRSA, ESA, NOIRLab, CDS).
   - The IVOA registry: `pyvo.registry.search(keywords=[...], servicetype="tap")`
     (also `"sia"`, `"ssa"`, `"conesearch"`) lists VO services for the survey.
   - An `astroquery` module, if there is one; check its docs for supported
     releases (astroquery.sdss, for example, silently lags SDSS-V).
   - Coverage: the CDS MOC server
     (`https://alasky.cds.unistra.fr/MocServer/query?ID=<id>&get=record`) has
     footprints for many surveys and HiPS; archives often publish MOCs too.
2. **Read the docs**: what's in the release, what isn't, magnitude/depth
   limits, footprint, release dates, known issues/caveats pages, citation and
   acknowledgement policy.
3. **Probe the service live**, in a field with a known answer and in one with
   none:
   - list the tables and the columns you plan to use;
   - `COUNT(*)` in a small cone, then fetch a few rows, and time both;
   - find the row limit (sync vs async, `MAXREC`, documented caps) and what a
     truncated result looks like;
   - download one data product and open it;
   - query a position outside the footprint and record what "no coverage"
     looks like (empty table, error, or something else).
4. **Fill in the template** (sections below).
5. **Write a smoke test** in `smoke_tests/`: one known field, checks on counts
   and coverage, timings printed, decisions logged (see
   `smoke_tests/draco_hst_jwst_gaia.py`).
6. **Add the skill to `skills/index.md`** with `status: draft`.
7. **Report back**: what the skill covers, what's unverified, and anything
   surprising found while probing.

## In the workspace (the data agent)

The data agent has tools for each step: `read_skill`, `survey_inventory`
(what covers the region, radio to gamma rays), `find_catalogs` /
`describe_catalog` / `query_catalog` (VizieR), and `find_services` /
`describe_service` / `query_service` (IVOA registry and TAP; ObsCore tables
give image footprints with `region_col="s_region"`). Queries are bounded to the
field's region and stored with their provenance. After a new survey works,
write down what you learned as a draft skill for a person to review.

## Template

```markdown
---
name: <survey>
description: <what it holds; when to load it>
used_by: [imaging | spectra | astrometry | photometry | literature ...]
status: draft
sources_checked: <date> (<urls or domains>)
---

# <Survey>

## What's here           (current release, contents, counts, depth, footprint)
## What's NOT here       (limits that should make an agent look elsewhere)
## Release status        (current release; next release and its date, verified)
## Coverage check and budget  (how to tell cheaply if a region is covered; counts first)
## How to query          (services, URLs, code, key tables and columns, limits)
## Gotchas               (schema quirks, flags, duplicates, units, epochs, TLS)
## Default decisions and what to tell the user   (table: situation | default | message)
## Sanity checks         (what a wrong answer looks like)
## Cite and acknowledge  (verified references and official acknowledgement text)
## Not yet verified
```

## Best practices learned so far (apply to every new skill)

- **Count before you fetch**, and use real counts and sizes for budget
  questions to the user.
- **Treat a result exactly at a known row limit as truncated**; split and
  re-run. Some servers truncate silently (the Gaia Heidelberg mirror caps at
  100,000 rows unless `MAXREC` is given).
- **Prefer fast mirrors, with a fallback**, and say which one answered.
- **Look up secondary tables by ID** (`source_id IN (...)`), not by spatial
  joins: seconds instead of minutes.
- **Use real footprints** (polygons/MOCs), never pointing centers, for coverage.
- **Cross-match by ID first, position second.** Record which IDs the survey
  carries (Gaia DR2 or DR3 source_id, 2MASS designation, its own IDs) and the
  release they refer to. Map Gaia DR2 IDs to DR3 through
  `gaiadr3.dr2_neighbourhood`, and 2MASS IDs through
  `gaiadr3.tmass_psc_xsc_best_neighbour`; fall back to a small radius (~1″),
  flagging pairs with two candidates. Check every ID match against position,
  allowing for proper motion between epochs: in Draco an APOGEE star matched
  by 2MASS ID was 6.3″ from Gaia because it moves 0.4″/yr, so a position match
  would have missed it.
- **Never pass 19-digit IDs through floats** (Gaia source_id, DESI targetid):
  a float can't hold them exactly and silently turns one star's ID into
  another's. Parse them as integers or strings.
- **Fetch index rows, not data products,** when building a result; download
  files only when a user asks for them.
- **Flag, don't drop**: add quality columns; report how many fail each cut.
- **Separate trusted from candidate samples** and show only the trusted ones
  by default (the Gaia QSO candidate table in Draco was mostly RR Lyrae).
- **Pin the data release** explicitly; never rely on a library default.
- **Keep provenance on every record** (archive, native ID, position,
  footprint, query, access URL) for the evaluation checks.
- **Retry with back-off** on slow archives (MAST can take ~100 s per cone) and
  cache results with their query parameters.
- **Never disable TLS verification**; if certificates fail, use the system
  certificate store and note it under Gotchas.
- **Every default decision has a user-facing message and an override phrase.**

## Facilities whose data aren't out yet (e.g. Roman, Gaia DR4)

Write the skill anyway, in **pre-release mode** (`status: pre-release`), so
SCOOP can answer "what will exist here, and when?":

- **What's planned:** surveys, footprints (often published as MOCs or tiling
  files), depths, cadences, and the archive that will host the data. Verify
  dates against the mission's official pages.
- **What's in the archive already:** simulated data, commissioning or test
  data (e.g. Roman WFI test data at MAST), and data-model documentation.
  Probe these like real data, so the query code is ready.
- **Planned observations:** where the archive lists them (as MAST does for
  JWST), include them, clearly marked as not yet taken.
- **A release-watch section:** the release date, what must be re-verified on
  release (table names, column names, counts, limits, reference epoch,
  citation), and a smoke test to re-run that day. A scheduled task can re-run
  the smoke test after the release date and flag the skill for review.
- **Tell the user plainly** that the data don't exist yet: "Roman's
  High Latitude Wide Area Survey is planned to cover this field; no data
  are public yet (first release expected <verified date>)."
