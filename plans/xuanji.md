# Plan: Exoplanet-host agent

**Owner:** Xuan Ji
**Started:** 2026-10-19
**Status:** draft tools in `exoplanets/`, not yet committed

> This is also a learning project: the goal is to work through the whole
> process of building a tool-using agent (following Tutorial 1,
> [AgenticAI2026_Tutorial-1](https://github.com/ioanazelko/AgenticAI2026_Tutorial-1)),
> one step at a time, so the same process can be used in other projects.

## Goal

Given a sky position or an object name and a radius, find the **known**
(confirmed) exoplanet hosts in that cone and report their planets, with
parameters and discovery papers. Example questions:

- "Are there known planet hosts within 2 arcmin of TRAPPIST-1? What are their planets?"
- "Which planets does Kepler-90 have, and who discovered them?"
- "Is there any confirmed planet host near M87?"

**In scope:** confirmed planets in the NASA Exoplanet Archive; name resolution;
discovery references.

**Out of scope:** discovering *new* host candidates (transit searches on light
curves). That is a research project in itself, and there's no ground truth to
grade it against. TESS candidates (TOIs) are a possible later extension.

### Why an agent, not a script

The next step depends on what came back:

- name doesn't resolve → try another identifier, or ask for coordinates
- no hosts in the cone → for a high proper-motion star, retry once with a
  larger radius; otherwise report "none" (don't widen forever)
- the archive lists the host under another name (Kepler-90 is `KOI-351`) →
  use the hostname from the cone search
- several hosts → fetch planets for each
- archive fails → report it, don't answer from memory

## Steps

| # | Step | Status |
|---|---|---|
| 1 | Choose the question the agent answers; check it actually needs a loop | done |
| 2 | Check the data sources live before writing code | done (findings below); to walk through together |
| 3 | Design the tools: name, description, schema, function, error messages | drafted, `exoplanets/tools.py` |
| 4 | Test the tools by hand, with no model involved | drafted; to walk through together |
| 5 | Build a benchmark of questions with known answers | drafted, `exoplanets/benchmark.json` |
| 6 | Run the loop offline with a `FakeModel` | to do |
| 7 | Checks in code: right hosts, citations returned by a tool, no invented numbers | to do |
| 8 | Run with a real model, read the trace, improve the tool descriptions | to do |
| 9 | Write the skill (`skills/surveys/nasa_exoplanet_archive.md`), commit, pull request | to do |

## Design

- **Harness:** the tutorial's `agentbook` (loop, model adapters, tool registry,
  tracer, guardrails, provenance check), copied into `agentbook/` with its MIT
  licence. The dark-matter registry tools and Lyman-α graders were left out.
- **Tools** (`exoplanets/tools.py`):

  | Tool | Does | Service |
  |---|---|---|
  | `resolve_target(name)` | name → J2000 position, proper motion, object type, and a suggested minimum cone radius | Sesame (CDS) |
  | `exoplanet_cone_search(ra, dec, radius_arcmin)` | confirmed hosts in the cone, nearest first | NASA Exoplanet Archive TAP, `pscomppars` |
  | `get_planets(hostname)` | every planet of a host: period, radius, mass, T_eq, discovery method/year/facility, discovery bibcode | same |

- **Plain HTTP (`requests`)** rather than astroquery/pyvo, so every query is
  visible; can switch later.
- **Offline first:** HTTP responses can be recorded and replayed
  (`SCOOP_HTTP=live|record|replay`), so tests and FakeModel runs need no network.
- **Provenance:** every host and planet is a record in the format of
  `evaluation/checks.py` (`kind`, `archive`, `native_id`, `ra`, `dec`, `query`,
  `access_url`, `retrieved_at`), so Duo's checks can run on it.
- **Arithmetic in code, not in the model:** the resolver computes how far a
  star has moved since J2000 and suggests a cone radius.

## What the live checks found (2026-10-19)

- `pscomppars` has 6,451 planets. Column names were read from `TAP_SCHEMA`,
  not guessed.
- **The archive's firewall returns 403 to cone queries sent as GET**; the same
  query sent as POST works.
- **Archive positions are near epoch 2016, not J2000.** TRAPPIST-1 is 16″ and
  Proxima Cen 60″ from their SIMBAD J2000 positions, which matches proper
  motion × ~16 yr. A small cone at J2000 misses them. (Not yet confirmed in
  the archive's documentation.)
- **Host names differ from common names:** Kepler-90 is `KOI-351`, Proxima
  Centauri is `Proxima Cen`.
- `disc_refname` is an HTML link containing the ADS bibcode of the discovery paper.
- An empty field returns zero rows, not an error; an unknown name in Sesame
  returns "Nothing found".

## Benchmark (`exoplanets/benchmark.json`)

| Target | Tests | Expected |
|---|---|---|
| TRAPPIST-1 | high proper motion (16″) | TRAPPIST-1, 7 planets |
| Proxima Centauri | very high proper motion (60″); hostname `Proxima Cen` | 2 planets |
| Kepler-90 | alias: hostname `KOI-351` | 8 planets |
| HD 209458 | simple | 1 planet |
| 51 Peg | simple | 1 planet |
| Kepler-186 | multi-planet, two discovery papers | 5 planets |
| M87 | negative control | no hosts |
| NotARealStar123 | unresolvable name | reported as not found |

## Links with the rest of SCOOP

- A SCOOP data source: "is there a known planet host in this cone?" fits the
  catalogs/time-domain data agents.
- [Duo's evaluation](duo_xu.md): records use the shared provenance format;
  the benchmark cases could go into `evaluation/benchmark.json`.
- [Kevin's](kevin_mckinnon.md) Gaia skill: hosts carry `gaia_dr3_id` in the
  archive, for a later cross-match.

## Next: exoplanets in a SCOOP field (2026-10-20)

The user starts from a description of the sky area they want. Three stages, built in order:

1. **Planets in this field.** Show the confirmed hosts (and TOIs; later TIC) in the
   field: no agent needed, the data sources do this. If there are any, an
   **exoplanet agent** answers questions about the planets and their properties,
   every claim cited to a paper or archive record. *Building this first.*
2. **Data for a planet search.** Which stars have TESS light curves (SPOC 2-min/20-s,
   QLP, TESS-SPOC, TGLC) or other data usable for an exoplanet search?
3. **Short-cadence candidates.** Which stars are good candidates for a TESS
   short-cadence proposal? Needs stage 2, plus vetting (Gaia RUWE, TIC contamination,
   TOIs, HST/JWST history) and the fact that FFIs are 200 s since 2022, so 2-min
   mainly helps short transits, flares and asteroseismology.

**In SCOOP: a chat tool, not a panel** (decided 2026-10-20). The exoplanet agent becomes
one tool of SCOOP's chat agent, `exoplanet_agent(question)`, chosen by its description
(not by keywords: "planetary nebula" and "minor planets" are not exoplanet questions).
Map actions stay with the chat ("show me the exoplanets" → `add_data`). The checked answer
is shown unchanged, marked checked or not checked; the chat model does not retell it.
Built and tested in agentbook first (`exoplanets/agent.py`, `exoplanets/checks.py`), using
the model set in Settings through agentbook's `OpenAIModel`.

**Fact-checking rules.** Every claim names a source and carries a verbatim excerpt of
the tool result it rests on: for a paper, a sentence from its abstract (conditions and
uncertainty kept); for an archive, the value as returned. Checked in code: the excerpt
appears word for word in that source's tool result, bibcodes and numbers come from
tools, and a claim does not drop the paper's hedging. A failed answer goes back to the
model once; if it fails again it is shown as not checked.

Ideas kept for stage 3: JWST transmission feasibility (TSM, Kempton et al. 2018) and
PLATO coverage are tools, not separate agents. Benchmarks: TIC `Prior` (CTL
priority), back-tests against later TOIs (`toi_created`), accepted TESS GI targets.

## Added to the workspace (2026-10-20)

Data sources in `scoop/sources/`, all queried on demand from "＋ Add data":

- `exoplanets.py`: confirmed hosts now carry their planets (period, radius, mass and
  whether it is measured or estimated from the radius, discovery paper linked to ADS).
- `tess_tois.py`: TESS Objects of Interest, layered by TFOPWG disposition. The archive's
  `toi` table has no spatial index, so the cone is queried as an RA/Dec box.
- `mast_tso.py`: HST/JWST spectra, time series and IFU cubes, executed and planned, with
  one line per program. The field pipeline fetches images only, which missed most
  exoplanet atmosphere observations (1,185 for TRAPPIST-1).

## Future data sources

To add later, in this order:

1. **Kepler/K2 candidates** (Exoplanet Archive `cumulative` KOI table and `k2pandc`):
   candidates and false positives outside the confirmed table. Same code as the TOIs.
2. **Gaia DR3 binary-orbit solutions** (`gaiadr3.nss_two_body_orbit`): stars with a
   detected companion, to rule out binaries or flag planet/brown-dwarf candidates.
3. **PLATO (LOPS2) and Roman (Galactic Bulge Time Domain Survey) footprints**: whether
   another mission will observe a star anyway. Roman's bulge stars rarely suit TESS.

Also considered: TIC as a layer (VizieR `IV/39/tic82`, Tmag < 16), TESS sector coverage
and light-curve availability (needed by the target-selection agent), exoplanet.eu,
the HWO target list, the archive's atmosphere spectra tables, RV archives.

## Open questions

- Confirm the archive's position epoch in its documentation.
- Which real model to use for step 8, and what token budget.
