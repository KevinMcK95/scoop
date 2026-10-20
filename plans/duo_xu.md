# Plan: Evaluation & benchmarking

**Owner:** Duo Xu ([@xuduo117](https://github.com/xuduo117))
**Started:** 2026-10-19
**Status:** working on `main`. It verifies the pipeline's real output (fields
from `smoke_tests/lvdb_field.py`, as the workspace loads them), including data
links and the papers attached to each source, on a random sample first and
fully on request. It's also available as an MCP server (`evaluation/mcp_server.py`).
See [evaluation/README.md](../evaluation/README.md).

> **Update (2026-10-20):** besides correctness, completeness and field of
> view, the evaluation now checks that **data links work** (no dead links,
> HTTP errors or generic "not found" pages) and that **each paper is real and
> actually about its source** (ADS, SIMBAD links, title/abstract/full-text
> mentions of the source's names). Big results are checked on a reproducible
> random sample (100 records per archive by default) with a 95% upper limit on
> the failure rate; a full check runs when asked.

> **Change of scope (2026-10-19):** I was originally going to work on data
> reduction and cross-matching across data sets. After discussing it as a
> team, we decided that's too much for a two-day workshop, so SCOOP will stick
> to literature search and data gathering/downloading. I'm now working on
> **evaluating** what SCOOP returns instead.

The README promises that SCOOP *"never invents data: every object, dataset and
paper in a result comes from an archive query."* My job is to check that
promise, and more generally to answer four questions about every result:

1. **Is it real?** Every object, observation, file and paper actually exists
   in the archive it supposedly came from.
2. **Is it in the right place?** Everything returned actually lies in or
   overlaps the region that was asked for.
3. **Is it complete?** Nothing the archives hold for that region is silently
   missing.
4. **Is it correct?** Values (positions, dates, instruments, redshifts,
   citations) match what the archives say.

The main idea: **the checks are deterministic code that goes back to the
archives itself**, not an LLM grading another LLM. An agent can easily produce
a plausible-looking obs ID, bibcode or URL; only re-querying the archive
catches that.

If you're writing an agent, the one thing I need from you is under
[Interfaces](#interfaces-what-i-need-from-other-modules) below.

---

## Scope

**In scope**

- Checks that run on any SCOOP result (a verifier)
- A fixed benchmark set of queries, with expected answers
- Scores per query and per archive, collected into a scorecard

**Out of scope (owned elsewhere)**

- Querying the archives, literature search, summaries and downloads
  (the agents being evaluated)
- Data reduction and cross-matching (dropped from the workshop scope)

## 1. Is it real? (catching made-up results)

- [x] **Provenance required** – every record must carry its archive, native ID,
      the query that produced it, an access URL and a retrieval time. A record
      without provenance fails.
- [x] **Look each ID up again** – look up MAST `obs_id`, Gaia `source_id`,
      SIMBAD identifiers, ADS bibcodes and arXiv IDs directly, and confirm they
      exist and that RA/Dec, instrument, date, title and authors match what
      SCOOP reported.
- [x] **Downloaded files** – each file opens as valid FITS (not an HTML error
      page saved as `.fits`), its size or checksum matches the archive's
      listing, and header keywords (`TELESCOP`, `INSTRUME`, `DATE-OBS`, WCS)
      match the reported metadata.
- [x] **Papers** – the bibcode resolves, and the paper is actually linked to
      the object (it appears in the object's SIMBAD bibliography or an ADS
      `object:` search).
- [x] **Sanity rules** – cheap checks that are good at catching inventions:
      JWST data before 2022 or HST before 1990, a filter that doesn't exist on
      that instrument, exposure time ≤ 0, a spectrum's wavelength range
      outside what its instrument covers.
- [ ] **Trick queries** – a nonexistent object (e.g. `NGC 99999`) or an empty
      patch of sky should come back empty or "not resolved", with nothing
      invented.

## 2. Is it in the right place? (field of view)

- [x] **Name resolution** – SCOOP's coordinates for a named target agree with
      Sesame/SIMBAD/NED queried directly, within a tolerance.
- [x] **Catalog sources** – the distance from the center is at most the radius
      (plus a small tolerance for positional errors).
- [x] **Images and observations** – the actual footprint (`s_region`, via
      `mocpy`/`regions`) overlaps the cone, not just the pointing center.
      Report what fraction of the cone each image covers.
- [x] **Downloaded images** – using the file's WCS, the target falls inside
      the image and not on a blank or NaN region.
- [x] **Spectra** – the fiber or slit position is inside the cone.
- [x] **GW localizations** – re-download the sky map, confirm it is for the
      claimed event, and recompute the target's credible level (tested on
      NGC 4993 / GW170817).
- [ ] **Plain-language queries** – "within 1 degree of M31" becomes a 1° radius
      in the query plan, not 1 arcmin.
- [ ] **Known coordinate bugs** – RA in hours mistaken for degrees, wrap-around
      at RA = 0/360, targets near the poles, Galactic vs. ICRS, mistakes
      reading sexagesimal coordinates.

## 3. Is it complete? (nothing missing)

- [ ] **Independent "truth" queries** – for each benchmark field, script direct
      `astroquery`/`pyvo` calls to each archive (MAST, Gaia, SIMBAD, NED,
      IRSA, HEASARC, ADS) and compare with SCOOP's result:
      - **recall**: how much of what the archive has SCOOP found;
      - **precision**: how much of what SCOOP returned is genuinely in the
        archive's answer.
- [x] **Truncated results** – a row count of exactly 50 or 2000 usually means a
      default row limit was hit (e.g. VizieR's 50-row default in
      `astroquery`). Compare against a `COUNT(*)` query to the archive.
- [x] **Archive status table** – archive × query: returned / empty / failed /
      timed out. "The archive has nothing here" must be distinguishable from
      "the agent failed"; the README promises failures are reported, not
      hidden.
- [x] **Must-include lists** – for a few famous targets, a hand-written list of
      what has to appear (e.g. the 2019 EHT papers for M87).
- [x] **Downloads** – every entry in the download manifest has a non-empty file
      on disk.

## 4. Is it correct? (values)

- [ ] **SIMBAD vs. NED** – where both give a value (position, redshift, type),
      they agree, or the conflict is flagged as the README promises.
- [ ] **Summary vs. data** – every number in the summary matches the data table
      it came from.
- [ ] **Citations** – every claim in the summary has a real, resolvable
      citation.
- [ ] **(Stretch) Claim support** – an LLM judge checks whether the cited
      abstract actually supports the claim. Reported as a separate, weaker
      score, since it is an LLM grading an LLM.

## 5. Benchmark set

About ten queries, each chosen to test something different:

| Query | What it tests |
| --- | --- |
| M87, 2 arcmin | Rich data in many archives, plus well-known papers |
| NGC 4993 / GW170817 | GW localization, ATels and GCN Circulars, transients |
| Crowded Galactic-plane field | Truncation, large numbers of sources |
| Empty high-latitude field | Should come back nearly empty with nothing made up |
| Target near RA = 0 or near a pole | Coordinate edge cases |
| `NGC 99999` | Made-up object should be refused, not answered |
| Sculptor Group | Group → member list |
| QSOs within 1° of M31 | Selecting a class of objects, units in the plan (overlaps with [Kevin's plan](kevin_mckinnon.md)) |
| A topic-only literature question | Citation validity |
| The same query run 3 times | Whether results are stable from run to run |

## 6. Scorecard

For each query, and summed over the benchmark:

- **Fabrication rate** – records that fail the ID re-check
- **Out-of-field rate** – records outside the requested region
- **Recall and precision** – per archive
- **Citation validity** – citations that resolve and are linked to the object
- **Failure reporting** – archive failures that were reported rather than hidden
- **Stability** – overlap between repeated runs of the same query
- **Cost** – wall-clock time and tokens per query

## Interfaces (what I need from other modules)

**Every record an agent returns should carry its provenance.** Without it,
almost none of the checks above can be run. A minimal set of fields:

```python
{
    "kind": "image",          # object | observation | image | spectrum | catalog_row | paper | proposal | telegram
    "archive": "MAST",        # where it came from
    "native_id": "<obs_id>",  # the archive's own ID (obs_id, source_id, bibcode, ...)
    "ra": 187.7059,           # deg, ICRS (for sky records)
    "dec": 12.3911,
    "s_region": "<polygon>",  # footprint, for images and observations
    "query": "<the call or ADQL that produced it>",
    "access_url": "<download / landing URL>",
    "retrieved_at": "2026-10-19T15:00:00Z",
}
```

Also:

- **Archive status** – each data agent reports, per archive, whether the query
  returned results, came back empty, failed or timed out.
- **Query plan** – the orchestrator's structured plan (targets, radius with
  units, constraints), so I can check it against the original request.
- **Download manifest** – file path, source URL and expected size/checksum.

## Proposed code layout (provisional)

```
scoop/
  evaluation/
    checks/      # real / in-field / complete / correct checks on one result
    truth/       # direct archive queries that build expected answers
    benchmark/   # benchmark queries and must-include lists
    scorecard.py # runs the benchmark and writes the scorecard
```

The checks could also run as a **verifier agent** after the compiler agent in
the [agent architecture](../README.md#agent-architecture), flagging bad records
before the summary is written.

## Milestones

Two-day workshop, so in priority order:

1. **Day 1:** agree on the provenance fields with the team; write the core
   checks (ID re-check, in-field test, FITS checks, bibcode check); fix the
   benchmark list
2. **Day 2:** write the truth queries for 3–5 benchmark targets; run the checks
   on teammates' outputs; produce the first scorecard
3. **Stretch:** LLM judging of summary claims; stability runs; verifier agent
   in the pipeline

## Open questions

- What tolerance should count as "in the field" for sources with large
  positional errors (radio, X-ray)?
- For completeness, which archives do we expect SCOOP to cover by the end of
  the workshop? Recall should only be scored against those.
- Should a record that fails a check be dropped from the result, or kept and
  flagged?

Comments and suggestions welcome — open an issue or ping me.
