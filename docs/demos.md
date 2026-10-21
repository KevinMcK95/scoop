# SCOOP demos

A sequence for a live presentation (about 15 minutes), from a question on the start page
to SCOOP checking itself. Every demo is also a spot check in
[`evaluation/spot_checks.json`](../evaluation/spot_checks.json), so a rehearsal is one
command, and its transcripts are the fallback if the network fails on stage.

## Before the talk

1. Start the workspace: `python -m scoop.server` and, in `web/`, `npm run dev`; open
   <http://127.0.0.1:5173>. A model and an ADS token are set in ⚙ Settings.
2. Have the fields on disk (they load instantly; nothing downloads on stage except
   demo 2): `draco_1`, `leo_2`, `trappist_1_surroundings`.
3. Rehearse and keep the transcripts:
   `python -m evaluation.spot_checks --out out/spot_checks` (≈15 min; or `--only <id>`).
   The report (`out/spot_checks/<time>_report.md`) is also the evaluation slide.
4. The network: Gaia, SIMBAD and the model are remote. If the wifi drops, SCOOP says so
   (a sized-first request fails fast with the reason); show the saved transcript instead.

## The sequence

| # | Say | Type (or do) | Point at | ≈ time |
| --- | --- | --- | --- | --- |
| 1 | "One question, from nothing" | Start page → **New search**, then: *can you do a bulk proper motion measurement for draco? population fitting after identifying likely members?* | The map flies to Draco (loaded, not downloaded); the chat agent hands the question to the **proper-motion agent**; the **Members (SCOOP)** layer and the CMD / proper-motion plots appear; the answer: ~1,470 members, μ ≈ (+0.043, −0.186) mas/yr, compared with Pace et al. 2022, and why no dispersion is claimed (it vanishes with errors × 1.2). | 1.5 min |
| 2 | "Any patch of sky; sized before it downloads" | **New search**, then: *Show me the Gaia stars within 8 arcmin of TRAPPIST-1.* | The **data agent** sizes it (a few hundred stars, in a second), starts a field with only Gaia and background galaxies (the default), which loads by itself in ~15 s. | 1 min |
| 3 | "It asks before anything big" | *Get me the Gaia data in a 60 arcmin cone around Sgr A\*.* | Hundreds of thousands of sources: nothing starts; it gives the size and the options and waits for a yes. | 0.5 min |
| 4 | "Not just cones: stellar streams, in the stream's own frame" | *I want Gaia data along the GD-1 stream, within 10 degrees of phi1 on either side of the spur (phi1 around -33 in the Koposov frame). What would that look like?* | The galstreams track as a band on the map, its area and Gaia count, and proposed proper-motion cuts in the stream frame for the user to confirm. | 1.5 min |
| 5 | "Archive data as one picture" | Load `draco_1` (Layers → field), then: *Which trusted QSOs fall inside HST or JWST images here, and what time baselines do they have?* | Three QSO catalogs matched against the HST/JWST footprints, highlighted on the map, with the epochs: what a proper-motion reference frame needs. | 1 min |
| 6 | "Your selection, your fit" | In the Plot panel: CMD (`bp_rp` vs `G`), lasso the red giant branch; proper motions, lasso the clump; then: *fit my members* | The user's **Members** layer (one lasso per panel) and a fit of exactly those stars, next to SCOOP's own. | 1.5 min |
| 7 | "Data from the literature onto the plots" | Load `leo_2`, then: *Show me the line-of-sight velocity distribution of Leo II's stars.* | Velocities from a VizieR catalog matched to the Gaia stars; the RV histogram opens; *RV vs [Fe/H]* is one click. | 1 min |
| 8 | "Specialists with guarantees" | Load `trappist_1_surroundings`, then: *How many confirmed planets does TRAPPIST-1 have, and what are their orbital periods?* | The **exoplanet agent**'s answer, every claim quoted from its source and checked in code ("✓ Checked"). | 1 min |
| 9 | "Literature" | *What are the most recent papers measuring Draco's proper motion? Give bibcodes.* | ADS results; bibcodes are links. | 0.5 min |
| 10 | "How it's built" | The README's agent diagram | Six agents: chat, data, proper motions and exoplanets in the workspace; the triage agent on a timer; the dataset scout monthly. The chat agent never downloads; the data agent sizes everything first. | 1.5 min |
| 11 | "How we know it works" | The spot-check report; `GET /api/triage` (a triage report); Duo's data verification (`python -m evaluation.run_checks draco_1`) | Behaviour checks (right agent, right tools, right answer) and data checks (records real and in the field); failures become suggested fixes with file, line and a test. | 2 min |

## If something goes wrong

- **A turn fails or is slow**: the chat says why (network, an archive down, a dense
  region). Move on to the next demo and show the saved transcript.
- **The map shows the old field**: **New search** (or ＋) starts clean, looking at the whole sky.
- **Debug**: ⚙ Settings → developer mode shows the Debug tab with every step of each turn.
