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

## Open questions

- Confirm the archive's position epoch in its documentation.
- Include TESS candidates (TOIs), clearly marked as candidates?
- Which real model to use for step 8, and what token budget.
