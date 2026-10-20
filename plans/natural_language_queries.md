# Plan: Natural-language queries on top of the data tools

**Owners:** shared across the team; several of us are working on this, so add
your name and the part you're taking on here.
**Started:** 2026-10-19
**Status:** planning

Goal: type a request like *"display data within 2 degrees of the GD-1 spur
feature"* and get back the same kind of result the smoke tests produce today
(sky viewer, summary, decision log), with the model explaining how it read the
request and what it chose to do.

The principle throughout: **the model decides, code executes.** The LLM
interprets the request, resolves names, plans the queries and explains its
choices. It never produces data, coordinates or IDs itself: every number in a
plan or an answer has to come from a tool result.

---

## Architecture

```
User: "display data within 2 degrees of the GD-1 spur feature"
  │
  ▼  LLM (agentbook run_with_plan): interpret, resolve, plan
Query plan (structured): target, region, layers, cuts, budget  ──► shown to the user to approve or edit
  │
  ▼  deterministic tools (today's smoke-test code, refactored)
resolve → region → count → query MAST / Gaia / QSOs / spectra / SIMBAD → build viewer
  │
  ▼
Viewer + summary + decision log + the model's explanation
```

Built on [`agentbook/`](../agentbook/) (already in the repo, from the
workshop's Tutorial 1): agent loop, Anthropic/OpenAI models, `Tool` and
`ToolRegistry`, the `update_plan` tool and `run_with_plan`, `Budget`,
`LoopGuard`, the citation and number-provenance checks, and tracing.
[Xuan's exoplanet agent](xuanji.md) uses the same pattern, so tools from both
can live in one registry.

## The worked example: "the GD-1 spur"

A useful test because it needs real reasoning and honesty:

1. **Resolve GD-1**: a known stream, so galstreams gives its track.
2. **Find "the spur"**: not in any catalog. The agent has to find the paper
   that describes it (literature tool) and the location it reports, probably in
   GD-1's own stream coordinates.
3. **Convert** that location to RA/Dec.
4. **Say how sure it is**, and ask the user if it can't pin the feature down.
5. **Budget**: a 2° cone is ~12.6 deg². Count Gaia rows before fetching and
   report the number (at GD-1's high Galactic latitude probably tens of
   thousands, which is fine).

The failure we must prevent: the model supplying the spur's coordinates from
memory. The number-provenance check rejects any plan whose coordinates don't
appear in a tool result.

## Steps

### 1. Turn the smoke test into a library of tools

`smoke_tests/lvdb_field.py` is one script built around LVDB ellipses. Split it
into a package (e.g. `scoop/`) of functions with clear inputs and outputs:

- [ ] **`Region`**: one type for every region shape: cone, ellipse, polygon,
      MOC, and a buffer around a stream track ("along GD-1"). Methods for
      "contains", a bounding cone (for queries) and a MOC (for footprint overlap).
- [ ] **Resolvers**: `resolve_object` (SIMBAD/NED, plus the LVDB for nearby
      galaxies and clusters), `resolve_stream` (galstreams), and a literature
      lookup for named features that aren't in catalogs.
- [ ] **Counts** for every source (`count_gaia`, `count_mast`, …), so plans
      can be budgeted before anything is fetched.
- [ ] **Queries**: `query_mast_images`, `query_gaia`, `query_trusted_qsos`,
      `query_spectra`, `query_simbad_galaxies`; the existing code, unchanged in
      behavior.
- [ ] **`build_viewer`** and **`write_summary`** from the current code.
- [ ] Every record keeps the provenance [Duo's evaluation](duo_xu.md) needs
      (archive, native ID, position, footprint, query, access URL), and IDs
      stay exact integers (`smoke_tests/ids.py`).
- [ ] The smoke tests become thin scripts calling the library, and must give
      the same results as today (a regression check).

### 2. Wrap the functions as agentbook tools

- [ ] One `Tool` per function. Descriptions come from the skills: what it
      returns, what it **doesn't** cover, typical cost, and what to do when it
      fails (e.g. "Gaia QSO candidates are impure; use `query_trusted_qsos`").
- [ ] Tools return compact summaries to the model (counts, a few example rows,
      file paths), not whole tables; the full results go to disk for the viewer.
- [ ] Load the relevant skills into the system prompt per request, from
      `skills/index.md`.

### 3. Plan, approve, execute

- [ ] A structured **query plan** the model fills in, e.g.
      `{target, how_resolved, region, layers, cuts, estimated_rows, open_questions}`.
- [ ] Show it to the user before running anything heavy, in plain language:
      *"I'll treat 'GD-1 spur' as the feature reported in <paper>, at φ1 ≈ …,
      φ2 ≈ … (RA, Dec = …); 2° cone; Gaia + HST/JWST + spectra; ~N Gaia rows.
      Proceed?"*
- [ ] Execution only after approval; edits ("make it 1 degree", "skip
      JWST") update the plan instead of starting over.
- [ ] Guardrails: `Budget` (tokens and cost), `LoopGuard` (no repeated calls),
      number provenance on the plan and the final answer.
- [ ] Every run's trace is saved, so decisions can be reviewed afterwards.

### 4. Interfaces

- [ ] Command line first: `scoop ask "…" --out DIR [--yes]`.
- [ ] Then a chat box in the viewer, using the local server `--serve` already
      runs: a request goes to the agent, and the answer regenerates the page.

### 5. A proper-motion agent

A specialist agent that answers two questions for a field:

1. **Could we measure proper motions here that are as good as, or better
   than, Gaia's**, with the HST, JWST and Gaia data (or other telescopes) that
   exist, and with what precision, for which stars?
2. **What has already been done?** Is there a bulk proper motion for this
   structure, how was it measured (Gaia alone, HST+HST, HST+Gaia, JWST, ground
   based), with which data, and does any of the available data look unused?

The answer is an assessment with reasons ("promising / marginal / not with
current data"), not a measurement. Measuring the motions is left to dedicated
pipelines.

Its skill is [skills/astrometry/proper_motions.md](../skills/astrometry/proper_motions.md).
The first step depends on the target:

- **Dwarf galaxy or globular cluster:** always check the LVDB first. Its
  `pm_overview.csv` lists every published proper motion per object, with the
  method and reference (790 measurements of 307 objects in v1.1.1; Draco
  alone has 17).
- **Individual stars:** Gaia is almost always the best source.
- **Beyond the Local Group:** almost certainly no proper motions (the most
  distant LVDB objects with one are at ~1.4 Mpc); say so instead of searching.

**Deterministic tools it uses** (built on what the smoke test already computes):

- [ ] **Overlap epochs**: for every region covered by more than one image, the
      epochs, instruments, filters, exposure times and depth estimates, plus
      the baseline to the Gaia reference epoch (2016.0 for DR3). The smoke test
      already finds the longest HST–HST, HST–JWST and JWST–JWST baselines.
- [ ] **Precision estimate** per overlap region and magnitude range:
      per-epoch position errors from signal-to-noise and PSF width, with a
      systematic floor per instrument (to be set from instrument documentation,
      and flagged as an estimate); proper-motion error ≈ √(σ₁² + σ₂²)/Δt for two
      epochs, a fit for more; compared with Gaia's own errors for the same
      stars, giving an **improvement factor**. For a structure's bulk motion,
      scale by the number of likely members, and convert to km/s at its
      distance (LVDB).
- [ ] **Physical expectations** (`smoke_tests/pm_expectations.py`, done as a
      first version): expected proper-motion size at a distance, the solar
      reflex, implied Galactocentric velocities, the internal proper-motion
      dispersion from the LVDB's line-of-sight dispersion (σ_μ = σ_los / 4.74 d),
      and the per-star precision and number of stars a bulk motion or a
      dispersion measurement needs, all with errors propagated.
- [ ] **µas/yr effects**: how solar reflex and the systemic motion's
      perspective effect change across the region, and secular aberration
      (~5 µas/yr toward the Galactic center) in that direction. In Draco's
      2 r_half region reflex and perspective each change by ~4 µas/yr,
      comparable to an internal-dispersion measurement.
- [ ] **Feasibility, not just precision**: compare the achievable precision
      with what each goal needs (bulk motion better than published, orbit,
      internal dispersion, rotation), and give the verdict per goal.
- [ ] **Reference frame**: how many Gaia stars fall in each overlap (to tie
      images to Gaia), and how many trusted QSOs or background galaxies (for an
      absolute frame).
- [ ] **Practical limits**: the magnitude range where sources are unsaturated
      in the images and still measured by Gaia; crowding; mixed filters or
      instruments between epochs.
- [ ] **What's coming**: planned JWST observations, and approved HST/JWST
      programs not yet executed, that would extend a baseline.

**What it asks the literature and proposal agents:**

- [ ] Papers on proper motions of this structure or field, with the method
      (Gaia DR2/EDR3/DR3 alone, HST+HST, HST+Gaia, JWST+HST, ground-based),
      the bulk proper motion and its uncertainty, and **which data they used**
      (program IDs, epochs).
- [ ] The LVDB's adopted systemic proper motion and its reference, as the
      starting point.
- [ ] Which of the available observing programs were proposed for proper
      motions (from their abstracts), and which papers already used them (MAST
      links papers to the datasets they used).
- [ ] The comparison that matters: **is there an overlap with a long baseline
      that no published proper-motion work seems to have used?**

**Judgement calls the agent makes, with what it tells the user:**

| Situation | Tell the user |
|---|---|
| Long baseline, deep images, enough Gaia stars in the overlap | "HST (2004) and JWST (2025) overlap in this 3′ field: a 21-year baseline. Expected precision ~X mas/yr at F814W ≈ 24, about Y× better than Gaia for G ≈ 20 stars." |
| Long baseline but very few reference stars or QSOs | "The baseline is long, but only N Gaia stars fall in the overlap, so tying the images to Gaia will be weak. Relative motions are possible; absolute ones are harder." |
| Only short baselines | "Overlapping images span only 2 years; Gaia alone does better here. JWST program <ID>, not yet executed, would extend this to ~T years." |
| Published work already used the best data | "The bulk proper motion was measured with HST+Gaia using programs <IDs> (<paper>); the overlaps found here add nothing new." |
| An overlap no paper seems to use | "The 2006–2025 HST/JWST overlap isn't used in any proper-motion paper found; this looks like an opportunity (check with the program PIs)." |

The last message ties back to the note on SCOOP's name: the point is to
find related work and collaborators, not to race anyone.

**Evaluation:** fields with published HST+Gaia, HST+HST and Gaia-only proper
motions (e.g. Draco and Leo I) are natural tests. The agent's predicted
precision should be in line with what those papers achieved, and its
literature summary should find them. Picking the specific papers is part of
building the benchmark, checked against ADS, not taken from memory.

### 6. Evaluation

- [ ] **Deterministic tests** with `FakeModel`: scripted model responses
      exercise the plan → tools path without API calls.
- [ ] **A benchmark of natural-language requests** with known answers, e.g.:
      - "Draco to 2 half-light radii" → must reproduce today's Draco smoke test;
      - "Leo I to 3 half-light radii, only HST" → the HST subset of the Leo I test;
      - "QSOs within 1 degree of the center of M31" → trusted QSO catalogs only;
      - "display data within 2 degrees of the GD-1 spur feature" → literature
        lookup, coordinate conversion, budget question;
      - "Could we measure better proper motions than Gaia in Draco?" →
        overlap baselines, precision estimate, and the published proper-motion
        work, with methods and the data each paper used;
      - ambiguous requests ("the spur", with no stream named) → the agent asks
        instead of guessing.
- [ ] Results go through Duo's verifier (real, in the field, complete,
      correct); the benchmark also scores the plans (right target, right
      region, right layers, no invented numbers).

## Interfaces with other work

- **[Duo](duo_xu.md):** provenance fields on every record; the verifier runs on
  every benchmark result.
- **[Xuan](xuanji.md):** same agentbook harness; exoplanet-host tools join the
  registry as another layer.
- **Literature agent:** needed for features like the GD-1 spur, for the
  references behind LVDB values and QSO catalogs, and for the proper-motion
  agent's review of previous work.
- **Proposal agent** ([stsci_proposals skill](../skills/literature/stsci_proposals.md)):
  which programs were proposed for proper motions, and what's planned.
- **[Kevin's plan](kevin_mckinnon.md):** stellar streams (galstreams) and the
  LVDB are the main resolvers for Local Group requests; the proper-motion
  agent builds on its image inventory and proper-motion figure of merit.

## Milestones

1. Library refactor with regression checks against both smoke tests
2. Tools registered; `scoop ask "Draco to 2 half-light radii"` reproduces the smoke test
3. Plan approval and edits on the command line
4. galstreams and literature lookup; the GD-1 spur request works end to end
5. Proper-motion agent: precision estimates from overlaps, then the literature
   comparison; checked on Draco and Leo I
6. Benchmark with scores; chat box in the viewer

## Open questions

- How much should the agent do before asking? (e.g. always confirm plans over
  some row count, or only when the request is ambiguous?)
- Where does the literature lookup live: here, or in the literature agent?
- How do we show the model's uncertainty about a resolved feature on the map
  (e.g. draw the region it chose, plus alternatives)?
- Which model and settings by default, and what's the per-request cost budget?
- What systematic floor to assume per instrument in the precision estimates,
  and how to show that the numbers are estimates?
