# The dataset scout

SCOOP's coverage decays. Surveys release data, releases supersede the ones the
skills are pinned to, and nobody notices until a user asks for something SCOOP
quietly answers from a four-year-old catalogue. The scout is a scheduled agent
that looks for that decay once a month and files a pull request about it.

It is the automated counterpart of
[`scoop/skills/meta/new_survey_skill.md`](../scoop/skills/meta/new_survey_skill.md), which
says how to write a survey skill when a *request* needs one. The scout loads
that playbook verbatim into its system prompt, so there is one description of
what a good skill looks like, not two.

- Code: [`scoop/scout/`](../scoop/scout/)
- Operational playbook: [`scoop/skills/meta/dataset_scout.md`](../scoop/skills/meta/dataset_scout.md)
- Schedule: [`.github/workflows/dataset-scout.yml`](../.github/workflows/dataset-scout.yml)
- Tests: [`tests/test_scout.py`](../tests/test_scout.py)

## Shape of a scan

```
                    ADS (4 queries, entdate-windowed)
                                 │
    .scout_state.json ──drop ────┤  candidates.harvest()
    (bibcodes already judged)    ▼
                            candidates
                                 │
  scoop/skills/**  ──┐                 │
  SOURCES   ───┼─ coverage.read_coverage() ──► inventory
               │                 │                 │
               │                 ▼                 ▼
               │          ┌────────────────────────────────┐
               │          │  triage()  — the only model step│
               │          │  verdict per candidate:          │
               │          │  new / update / covered /        │
               │          │  not_a_release                   │
               │          │  tools: coverage_inventory,      │
               │          │  read_skill, fetch_page,         │
               │          │  record_verdict, propose_dataset │
               │          └────────────────────────────────┘
               │                 │
               │                 ▼  scaffold()
               └──────►  scoop/skills/surveys/<slug>.md   (status: draft)
                         scoop/sources/<slug>.py    (registered, queryable)
                         smoke_tests/<slug>_probe.py
                         scoop/skills/index.md            (a row)
                                 │
                                 ▼
                         docs/scout/<date>.md  ──► the pull-request body
                         out/scout_events.jsonl ──► the weekly scorecard
                         .scout_state.json      ──► next month's window
```

## Why these choices

**Why the literature as the discovery channel.** A survey's data release is
announced in a paper before it is visible to a machine anywhere else: the VO
registry entry, the TAP table and the archive's own release page all follow the
announcement, sometimes by weeks. ADS indexes arXiv eprints alongside journal
articles, so one client covers both, and `entdate` (when the record entered ADS)
gives exactly the month-sized window a scheduled scan needs — `year` does not.
The cost is recall on releases nobody wrote a paper about; the
`sources_checked` staleness report catches most of those from the other side.

**Why only one model step.** Harvesting, the inventory, the file writing and the
report are deterministic and tested. The model does the thing that genuinely
needs judgment — is this paper a real public dataset, which survey is it, and
does it supersede what a skill is pinned to — and nothing else. A wrong verdict
costs a line in a report; a wrong `scaffold()` would cost a broken data source.

**Why drafts and pull requests, not commits.** `new_survey_skill` already draws
this line: a new skill is `status: draft`, an agent using it must say so, and a
person promotes it to `reviewed`. The scout keeps that contract. The workflow
only ever pushes a branch, and the report is written as a reviewer's checklist
rather than a changelog.

**Why it writes a data source at all.** A skill that documents a dataset SCOOP
cannot query is half the job. When the scout has read a TAP endpoint, table and
column names from the archive's own schema, it generates a registered
`Source` — which is all `scoop/sources/` needs to make the survey queryable, as
nothing else in the pipeline has to change. When it has not, it says so and
writes the skill alone; a generated source module that cannot query would be
worse than none.

## What is enforced, and where

Every control is code, not prompt text, following `agentbook.guardrails`:

| Risk | Control | Where |
|---|---|---|
| A runaway loop | step, token and wall-clock budget | `triage()`, `agentbook.guardrails.Budget` |
| The same call repeated | refused with a reason the model can act on | `CountingGuard` (a `LoopGuard`) |
| Too much to review | `--max-proposals` (default 3) | `propose_dataset` |
| Facts from memory | `sources_checked` must be URLs fetched this scan | `propose_dataset` |
| The open web | https only, archive-domain allowlist, robots.txt, per-host rate limit, 400 kB and MIME caps, scripts stripped | `scout/fetch.py` |

`scout/fetch.py` is deliberately narrower than
[`scoop/archives/web.py`](../scoop/archives/web.py), which gives the chat agent the open web: that
is the right call there, because a person asked for the page and is reading the
answer. The scout runs unattended once a month, so it may only read archive and
publisher domains, and only pages their robots.txt allows. It shares
`scoop.archives.web.html_to_text` and `check_url`, so there is one HTML reader in the
repository rather than two, and passes JSON and XML through untouched — a
VOTable or `TAP_SCHEMA` reply is only useful with its tags.

| Clobbering the repo | nothing overwritten, nothing outside the checkout, nothing written partially | `scaffold.py` |
| SQL assembled from model output | table and column names must match an identifier pattern | `Service.__post_init__` |
| Silent truncation | a generated source treats a result exactly at `MAX_ROWS` as truncated | the source template |
| Unreviewed work being trusted | `status: draft` in the frontmatter and in the report | `scaffold.py`, `report.py` |

Each scan appends one event in the shape of
[`evaluation/scorecard/metrics_schema.json`](../evaluation/scorecard/metrics_schema.json), so it shows up in
`evaluation/scorecard/metrics_scorecard.py` alongside ordinary requests. `escalated` is true
when the scan found a gap, because a gap is exactly what needs a person, and
`user_accepted` stays false until someone merges the pull request.

## Running it

```bash
pip install -e '.[scout]'

# Harvest and report only: no model, no API key beyond ADS, nothing written
# into skills/ or scoop/sources/. Use this to see what a window contains.
ADS_API_TOKEN=... python -m scoop.scout --dry-run

# A real scan, writing at most two draft skills.
ADS_API_TOKEN=... ANTHROPIC_API_KEY=... python -m scoop.scout --max-proposals 2

# Catch up after a gap, and print machine-readable output.
python -m scoop.scout --since 2026-07-01 --json
```

`--root` points the scan at a different checkout, which is how the tests run it
against a fixture repository.

## Limits

- **Recall is bounded by the literature.** A release with no paper, or whose
  paper does not use the words the channels look for, is missed until the
  staleness report flags the skill.
- **One month of ADS is a few hundred papers at most** in these channels, so the
  scan is cheap; a much wider set of channels would need a cheaper first-pass
  filter before the model sees them.
- **Generated data sources are TAP-only.** Surveys served through SIA, SSA or an
  archive-specific API get a skill but no module; adding generators for those is
  the obvious next step.
- **`update` verdicts are reported, not acted on.** The scout tells you a skill
  is pinned to an old release; re-verifying that skill is still a human job (or
  a `new_survey_skill` run). Re-running the skill's own probe automatically,
  which the pre-release release-watch sections already anticipate, is the
  natural follow-up.
