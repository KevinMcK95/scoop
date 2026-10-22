---
name: stsci_proposals
description: Find the HST and JWST programs behind the observations in a
  region (executed and planned), fetch their public proposal information, and
  summarize the science that motivated them and what came out of them. Load
  after the mast_hst_jwst skill has listed observations, or whenever the user
  asks why a field or object was observed.
used_by: [proposal agent, literature]
depends_on: [mast_hst_jwst]
sources_checked: 2026-10-19 (stsci.edu program pages and public PDFs for
  JWST 1523 and HST 16000; MAST docs)
---

# HST and JWST proposals

## Goal

For each program that observed (or plans to observe) the region, answer:
**why** (the science goal), **what** (instruments, filters, targets),
**when** (executed or planned, and dates), **who** (PI and team), and **what
came of it** (papers that used the data). Then synthesize across programs:
what has made this patch of sky interesting to people, and what's coming next.

## Inputs

From the mast_hst_jwst skill: every observation's `proposal_id`,
`proposal_pi`, `obs_title`, `target_name`, `instrument_name`, `filters`,
`t_min`/`t_max` and `dataRights`, including planned JWST observations
(`calib_level = -1`). Group observations by `proposal_id` first; one program
often has many observations.

## Where the information is

| What | Where |
|---|---|
| Program page: PI, title, cycle, category, status, exclusive-access period | `https://www.stsci.edu/hst-program-info/program/?program=<ID>` or `https://www.stsci.edu/jwst-program-info/program/?program=<ID>` |
| **Public proposal PDF** | `https://www.stsci.edu/<hst|jwst>-program-info/download/<hst|jwst>/pdf/<ID>/` |
| Visit status (executed, scheduled, planned windows) | `https://www.stsci.edu/<hst|jwst>-program-info/visits/?program=<ID>&download=` (XML) |
| HST abstracts by keyword, PI or cycle | https://archive.stsci.edu/hst/abstract.html |
| JWST approved programs, including unexecuted ones | https://www.stsci.edu/jwst/science-execution/approved-programs |
| Papers that used the data | MAST bibliography (linked from https://archive.stsci.edu/publishing), then the literature agent (ADS) |

**What's in the public PDF** (checked on JWST 1523 and HST 16000): a header
with program number, title, cycle and proposal category (e.g. GO, CAL); the
investigator list with institutions; the list of observations (targets and
observing templates); and an **ABSTRACT** section. The full science
justification was not in either PDF, so summaries rest on the title,
abstract, targets and observing setup.

Extract text with `pdftotext` (or `pypdf`) and take the block after
`ABSTRACT`. Cache PDFs and parsed fields: they rarely change.

## Summarizing

Per program, in 2–4 sentences:
- The science goal, in plain language, **only from the abstract and title**.
- How the observations in *this region* serve that goal (e.g. "the field was
  one of 12 dwarf galaxies imaged to measure star-formation histories").
- Status: executed (dates), partly executed, or planned (expected window).
- Link to the program page and the program ID, so every claim can be checked.

Across programs:
- Group by science theme (e.g. "stellar populations", "proper motions",
  "background QSO absorption lines", "calibration"), with counts.
- Point out what the user's question needs: e.g. for proper motions, "programs
  X and Y imaged the same field 11 years apart."
- Say what's coming: planned or not-yet-public programs.

**Keep separate what the abstract says and what the agent infers.** Write
inferences as inferences ("this appears to be a parallel field").

## Default decisions and what to tell the user

| Situation | Default | Tell the user |
|---|---|---|
| Calibration programs (category CAL) | One grouped line, not full summaries | "4 calibration programs also used this field (e.g. flat fields, photometric standards); say 'show calibration programs' to see them." |
| Observation's footprint covers the region but the program targeted something else (parallels, mosaics, serendipitous coverage) | Summarize briefly, say so | "Program <ID> targeted <other target>; this region was covered in parallel." |
| More than ~10 programs | Rank by relevance to the request; summarize the top 10, list the rest | "N programs observed this region; summarized the 10 most relevant to <topic>. The rest are listed with titles." |
| Planned / not yet executed | Always include, clearly marked | "JWST program <ID> (PI <name>, Cycle <n>) plans NIRCam imaging here, not yet executed: <one-line goal>." |
| Exclusive-access data | Summarize the proposal (it's public), note the data aren't | "Program <ID>'s data are exclusive-access until <date>; the proposal abstract is public." |
| No abstract in the PDF or fetch fails | Fall back to title and observation setup | "Couldn't get the abstract for <ID>; summary is from the title and observation setup only." |
| User asks what came of a program | Ask the literature agent for papers citing the program | "Found K papers using data from <ID>; the main results were …" |

## Tone

These summaries show who is working on a source. Present them to help users
find related work and collaborators and to credit that work (see the note on
SCOOP's name in the README), never as a race. Don't speculate about
unpublished results from exclusive-access data.

## Sanity checks

- The program number in the PDF header should match the `proposal_id`.
- An HST program ID should give an HST page and a JWST ID a JWST page; the two
  number ranges overlap, so always pass the observatory.
- If the abstract mentions a different target than the one in the region,
  check the observation list before claiming the region was the target.

## TLS note

In our test environment, the stsci.edu certificate chain failed verification
with the default bundle used by curl and Python; the system certificate store
should work. Never turn off certificate verification in SCOOP code.

## Cite and acknowledge

Use the HST/JWST acknowledgement text in the mast_hst_jwst skill, with the
program numbers filled in. When summarizing a proposal, credit the PI and
program ID; cite papers (not proposals) for results.
