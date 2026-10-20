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

**LangGraph is the default framework** (as suggested by the workshop), using
the chat agent already in [`scoop/chat/`](../scoop/chat/) and described in
[docs/langgraph_chat.md](../docs/langgraph_chat.md):

```
User: "display data within 2 degrees of the GD-1 spur feature"
  │
  ▼  chat agent (model bound to typed tools)  ⇄  ToolNode: deterministic tools
  │     resolve → region → count → query MAST / Gaia / QSOs / spectra / SIMBAD → build field
  │     (full results stored as artifacts; the model sees short summaries and result IDs)
  │
  ├─► clarification or confirmation when the request is ambiguous or expensive
  │     ("I'll treat 'GD-1 spur' as …; ~N Gaia rows. Proceed?") — the user replies in the same thread
  ▼
Field in the web workspace (web/) or the standalone viewer + summary + decision log + the model's explanation
```

Points from the existing design that this plan follows:
- **Typed, action-specific tools**, executed by LangGraph's `ToolNode`. Tools
  publish their full results as immutable **artifacts** (`publish_result` in
  `scoop/chat/tools.py`) and return only a short summary and result ID to the
  model.
- **No mandatory plan gate.** A query plan can describe proposed work, but
  isn't required before every tool call. Asking the user is an ordinary
  assistant turn (a clarification), used for ambiguous or expensive requests.
- **Archive clients and credentials stay outside conversation state**; they're
  injected when the graph is built.

The [`agentbook/`](../agentbook/) harness (from the workshop's Tutorial 1) is
not the runtime. Some of its pieces are still useful as plain functions, such
as the number-provenance and citation checks (reuse or port them), and
[Xuan's exoplanet functions](xuanji.md) are already split out of their
agentbook wrappers (`exoplanets/archive.py`) so they can become LangGraph tools.

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
memory. A number-provenance check rejects any proposed region whose
coordinates don't appear in a tool result.

## Steps

### 1. Turn the smoke-test pipeline into tools

Much of this exists: [`scoop/sources/`](../scoop/sources/) has one plug-in per
data source (Gaia, MAST, MILLIQUAS, SIMBAD, spectra, exoplanets) that reads the
pipeline's output, and `scoop/fields.py` builds fields. What's missing is
mostly what natural-language requests need:

- [ ] **`Region`**: one type for every region shape: cone, ellipse, polygon,
      MOC, and a buffer around a stream track ("along GD-1"). Methods for
      "contains", a bounding cone (for queries) and a MOC (for footprint
      overlap). `scoop/spatial.py` is the natural home; check what it covers.
- [ ] **Resolvers**: `resolve_target` (SIMBAD/NED, plus the LVDB for nearby
      galaxies and clusters; see the proposed tool in docs/langgraph_chat.md),
      `resolve_stream` (galstreams), and a literature lookup for named features
      that aren't in catalogs (the ADS tools in `scoop/literature/`).
- [ ] **Counts** for every source, so expensive requests can be confirmed with
      real numbers before anything is fetched.
- [ ] **Queries** that run the existing pipeline functions for an arbitrary
      region (cone mode already exists: `lvdb_field.py --ra --dec
      --radius-arcmin`), unchanged in behavior.
- [ ] Every record keeps the provenance [Duo's evaluation](duo_xu.md) needs
      (archive, native ID, position, footprint, query, access URL), and IDs
      stay exact integers (`smoke_tests/ids.py`).
- [ ] Regression checks: both smoke tests give the same results as today.

### 2. Register them as LangGraph tools

- [ ] One typed tool per action, following the proposed set in
      docs/langgraph_chat.md (`resolve_target`, `search_observations`,
      `search_publications`, `inspect_result`, …) plus the ones these requests
      need (`count_sources`, `build_field`).
- [ ] Tool descriptions come from the skills: what it returns, what it
      **doesn't** cover, typical cost, and what to do when it fails (e.g. "Gaia
      QSO candidates are impure; use the trusted QSO catalogs").
- [ ] Full results go into artifacts and to disk for the viewer and workspace;
      the model gets counts, a few example rows and result IDs.
- [ ] Load the relevant skills into the system prompt per request
      (`scoop/chat/prompt.py`), from `skills/index.md`.

### 3. Confirm, execute, explain

- [ ] For ambiguous or expensive requests, the agent describes what it will do
      and asks, in plain language: *"I'll treat 'GD-1 spur' as the feature
      reported in <paper>, at φ1 ≈ …, φ2 ≈ … (RA, Dec = …); 2° cone; Gaia +
      HST/JWST + spectra; ~N Gaia rows. Proceed?"* The user's reply continues
      the same thread; edits ("make it 1 degree", "skip JWST") change the
      request instead of starting over.
- [ ] Guardrails: a recursion/step limit, cost limits (see Radha's
      `scoop/cost.py`), and number provenance on proposed regions and the
      final answer.
- [ ] Conversations and artifacts are checkpointed, so decisions can be
      reviewed afterwards.

### 4. Interfaces

- [ ] The web workspace's chat panel (`web/`, served by `python -m
      scoop.server`), which already streams agent turns and shows fields.
- [ ] A command line for scripted runs and benchmarks: `scoop ask "…" --out DIR`.

### 5. A proper-motion agent

A specialist that answers two questions for a field. In LangGraph it starts
as a set of tools the chat agent calls (with its own skill in the prompt), and
becomes a separate subgraph only if a workflow needs one:

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

**What the agent does, in priority order.** It is more than an expert answering
"which catalog comes from which data" (the LVDB's `pm_overview` and the skill
already cover that); it acts on the answers.

*First:*

- [ ] **(1) A feasibility planner that keeps looking.** Combine the overlaps and
      baselines (across HST, JWST, CFHT and any footprint table the data agent
      adds), depths and exposure times, the Gaia reference epoch and
      `pm_expectations.py` into a verdict per science goal (bulk motion better
      than published, internal dispersion, rotation). If the verdict is
      "marginal", don't stop: ask the data agent for more epochs (other archives,
      planned JWST observations, approved programs) and re-assess. End with the
      smallest addition that would change the verdict.
- [ ] **(2) Reconcile the published proper motions.** Collect every value (the
      LVDB's `pm_overview` plus newer ADS papers), put them in one frame (the
      same solar-reflex correction, a stated treatment of secular aberration),
      test whether they agree (χ²), and record which data each used (program
      IDs, epochs). Output a "state of the measurement" table and plot, and flag
      long-baseline overlaps no published work seems to use.
- [ ] **(3) Forward planning: what would new data get us?** Two questions:
      - *"What would a new JWST/HST observation give us here?"* For a proposed
        instrument, filter, exposure time (or depth) and date: the baseline to
        every existing overlapping image and to Gaia, the per-star and bulk
        precision it reaches, which science goals become feasible, and how that
        compares with the best published value.
      - *"How long until HST+HST (or HST+JWST) could constrain a given signal?"*
        Invert the precision estimate: the baseline needed is
        Δt = √(σ₁² + σ₂²) / σ_μ,needed per star; for a bulk motion or a
        dispersion, σ_μ,needed per star follows from the target precision and
        the number of members (the formulas in the proper-motion skill). Report
        the year it is reached from the existing epoch, or say it never is
        (when the systematic floor is above the target).
      Deterministic functions for both go into `pm_expectations.py`; the agent
      chooses the inputs and explains the result. Telescope scheduling and
      visibility windows are not modelled; say so.

*Next:*

- [x] **(5) Member samples:** lasso/box selection panels in the Plot tab (CMD,
      proper motion, parallax vs G, RV vs G, sky ξ/η), after the BP3M
      member-selection notebook: panels intersect, Extend / Strict, zoom, a
      Members layer the agent can read, and a Members CSV. N feeds the planners.
- [~] **(4) Quick-look measurements:** `fit_members` (`scoop/members.py`) fits
      the Members sample's bulk proper motion and internal dispersion by maximum
      likelihood with each star's errors (4σ clipping), the line-of-sight velocity
      and dispersion from DESI, the [Fe/H] spread, and compares with the LVDB and
      the published values. On Draco it reproduces the LVDB's adopted proper
      motion within 0.5σ. `auto_members` lets the agent pick members itself
      (radius, parallax, PM about a literature or data-driven prior, an empirical
      CMD region, velocity), fit them and compare with the LVDB, the published
      values and papers it read; it converges to the same sample on Draco with
      or without the literature prior. Still to do: a member/field mixture model (so the fit
      doesn't depend on hard lasso edges), membership probabilities to save, and
      fields rebuilt with `pmra_pmdec_corr`. HST+Gaia measurements stay with
      dedicated pipelines, through an export.
- [ ] **(6) A critic for claimed proper motions:** check a number from the user
      or a paper for physical plausibility (implied speed against escape speed,
      solar reflex), systematic floors (e.g. the 0.026 mas/yr floor in the Gaia
      skill, after Vasiliev & Baumgardt 2021) and whether secular aberration is handled.

*Later:*

- [ ] **Release watching:** when Gaia DR4 arrives (2 December 2026), or a new
      JWST program overlaps a field of interest, re-run (1)–(3) and report what
      changed.
- [ ] **Spotting unusual objects:** high-proper-motion foreground stars in a
      dwarf's field, "QSOs" that move, members with discrepant velocities; often
      the interesting objects, or the systematics.

### 6. Making the data side more agentic

- [ ] **Checking in the loop:** after the data agent adds a table, run
      [Duo's verifier](duo_xu.md) on it (are the rows real, in the field,
      complete?) and let the data agent fix what fails (re-query, change cuts)
      before it reports. Evaluation becomes self-correction, not only a score.
- [ ] **A skill-review agent:** draft skills now come from two places (the
      monthly dataset scout and the data agent's `save_skill_draft`). A reviewer
      agent re-runs each draft's probe, compares row counts, checks citations on
      Crossref and table/column names against the service, and recommends accept
      or reject. A person still sets `status: reviewed`.
- [ ] **Learning from failures:** the server logs every failed tool call with its
      reason (`out/turns.jsonl`). A periodic agent reads them and proposes prompt
      or skill fixes as pull requests, like the scout. (The first data-agent test
      alone showed two: a `tel` filter on a CADC table, and a stale field.)
- [ ] **Budget negotiation:** before anything heavy, the data agent quotes counts,
      sizes and expected time ("419 MegaCam images, 33 s to count; level 2 only
      gives 209") and asks, using Radha's cost checking for model costs.

### 7. Evaluation

- [ ] **Deterministic tests** with a scripted model (like `ScriptedModel` in
      `tests/test_chat_graph.py`): scripted responses exercise the agent →
      tools path without API calls.
- [ ] **A benchmark of natural-language requests** with known answers, e.g.:
      - "Draco to 2 half-light radii" → must reproduce today's Draco smoke test;
      - "Leo I to 3 half-light radii, only HST" → the HST subset of the Leo I test;
      - "QSOs within 1 degree of the center of M31" → trusted QSO catalogs only;
      - "display data within 2 degrees of the GD-1 spur feature" → literature
        lookup, coordinate conversion, budget question;
      - "Could we measure better proper motions than Gaia in Draco?" →
        overlap baselines, precision estimate, and the published proper-motion
        work, with methods and the data each paper used;
      - "Do the published proper motions of Leo I agree?" → every value in one
        frame, a χ² test, and the data each used;
      - "What would a new JWST NIRCam epoch of Draco in 2027 give us?" →
        baselines to existing images, per-star and bulk precision, which goals
        become feasible;
      - "How long until HST+HST could measure Leo I's internal dispersion to
        30%?" → the year, or "never" with the reason;
      - ambiguous requests ("the spur", with no stream named) → the agent asks
        instead of guessing.
- [ ] Results go through Duo's verifier (real, in the field, complete,
      correct); the benchmark also scores the plans (right target, right
      region, right layers, no invented numbers).

## Interfaces with other work

- **[Duo](duo_xu.md):** provenance fields on every record; the verifier runs on
  every benchmark result.
- **[Xuan](xuanji.md):** the exoplanet functions (`exoplanets/archive.py`)
  become LangGraph tools; Yilun's workspace already has an exoplanet data source.
- **Yilun:** the LangGraph chat agent, the `scoop/` package, the server and the
  web workspace this plan builds on.
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
5. Proper-motion agent, first priorities: the feasibility planner (1), the
   literature reconciliation (2) and forward planning (3); checked on Draco and Leo I
6. Proper-motion agent, next: quick-look measurements, member samples, the critic
7. Data side: checking in the loop, then the skill-review agent and learning from failures
8. Benchmark with scores

## Open questions

- How much should the agent do before asking? (e.g. always confirm plans over
  some row count, or only when the request is ambiguous?)
- Where does the literature lookup live: here, or in the literature agent?
- How do we show the model's uncertainty about a resolved feature on the map
  (e.g. draw the region it chose, plus alternatives)?
- Which model and settings by default, and what's the per-request cost budget?
  (Radha is working on cost checking and the pricing it uses.)
- What systematic floor to assume per instrument in the precision estimates,
  and how to show that the numbers are estimates?
- Forward planning ignores scheduling and visibility (when HST/JWST can point
  at a target, guide-star and orientation constraints). Is a note enough, or
  should the planner check visibility windows?
