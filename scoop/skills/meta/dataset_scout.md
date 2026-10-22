---
name: dataset_scout
description: The monthly scan for datasets SCOOP is missing or has gone stale
  on. Read this to run a scan, to review what a scan produced, or to change what
  it looks for. It is the scheduled, automated counterpart of new_survey_skill.
used_by: [operations, orchestrator]
exemplars: [meta/new_survey_skill.md, cost-checking.skill.md]
---

# The dataset scout

`new_survey_skill` says how to write a skill when a *request* needs a facility
SCOOP does not cover. The scout answers the other half: nobody asked, but a
survey released data last month and SCOOP does not know. It runs on a schedule
rather than per request, and it produces pull requests, not answers.

Code: [`scoop/scout/`](../../scoop/scout/). Design:
[`docs/dataset_scout.md`](../../docs/dataset_scout.md).

## Trigger

- Monthly, by `.github/workflows/dataset-scout.yml` (06:00 UTC on the 1st).
- By hand, before a release or after a long gap:
  `python -m scoop.scout --since 2026-07-01`.
- `workflow_dispatch` on the same workflow, for an off-cycle scan.

## Inputs and outputs

| | |
|---|---|
| **Needs** | `ADS_API_TOKEN`; `ANTHROPIC_API_KEY` unless `--dry-run`; a checkout it may write to |
| **Reads** | ADS (four queries, `entdate`-windowed), `skills/**`, `scoop.sources.SOURCES`, allowlisted archive pages |
| **Writes** | `skills/surveys/<slug>.md` (draft), `scoop/sources/<slug>.py`, `smoke_tests/<slug>_probe.py`, a row in `skills/index.md`, `docs/scout/<date>.md`, one JSONL line in `out/scout_events.jsonl`, `.scout_state.json` |
| **Never writes** | anything that already exists; anything outside the checkout; `status: reviewed` |

## Workflow

1. **Harvest.** Four ADS queries over `entdate:[since TO until]`: data-release
   titles, catalogue papers that say they are public, survey papers whose
   abstract mentions a data release, and `doctype:catalog` records. Bibcodes
   judged in an earlier scan are dropped before the model sees them.
2. **Inventory.** `read_coverage()` reads every skill's frontmatter (name,
   status, `sources_checked`) and the releases named in its body, and the
   registered data sources. A skill with no source module is documented but not
   queryable; that is reported too.
3. **Triage** (the model's only job). One verdict per candidate:
   `not_a_release`, `covered`, `update` (covered, but at an older release), or
   `new`. For the best gaps it fetches the archive's own pages and calls
   `propose_dataset`.
4. **Scaffold.** A proposal becomes the files above, all `status: draft`.
5. **Report and record.** `docs/scout/<date>.md` becomes the pull-request body;
   the verdicts go into `.scout_state.json` so the next scan is incremental.

## Guardrails (all in code, none in the prompt)

| Guardrail | Where |
|---|---|
| Step, token and wall-clock budget | `run_loop()` in `scoop/scout/triage.py` |
| An identical repeated tool call is refused | `CountingGuard` |
| At most `--max-proposals` drafts per scan | `propose_dataset` |
| Facts must come from a page fetched this scan | `propose_dataset` checks `sources_checked` against `Fetcher.fetched` |
| https, archive-domain allowlist, robots.txt, rate limit, size and MIME caps, scripts stripped | `scout/fetch.py` |
| Nothing overwritten, nothing outside the checkout, nothing partial | `scaffold.py` |
| A gap verdict is required before a proposal | `propose_dataset` |
| Human review before anything is trusted | `status: draft`, and the workflow only ever pushes a branch |

## Reviewing a scan

A scan's output is a *hypothesis*, in the shape of a skill. Review it as one:

1. Run `python smoke_tests/<slug>_probe.py`. It checks the table exists, counts
   rows in the field the scout actually queried, and checks a position outside
   the footprint is empty. Compare its numbers with the skill's.
2. Check the table and column names against the service's own `TAP_SCHEMA`.
   The scout reads them, but a wrong column makes a data source that returns
   plausible nonsense.
3. Check `sources_checked` resolves, and that every citation checks out on
   Crossref. Move anything you cannot confirm into *Not yet verified*.
4. Check the release really is newer than the one an `update` verdict claims to
   supersede, then re-verify that skill rather than only noting it.
5. Set `status: reviewed` on what you accept. Delete what you do not — a wrong
   draft skill is worse than a missing one, because an agent will read it.

## Edge cases

- **No candidates.** Normal for a quiet month. The report still gets written;
  no pull request is opened.
- **ADS fails on one channel.** The other three still run; the failure is a
  warning in the report, and the window is still advanced, so re-run with an
  explicit `--since` if a channel failed on something important.
- **The scan stops on budget.** Verdicts recorded before the stop are kept and
  saved; candidates never reached stay unjudged and are picked up next scan.
- **A month was skipped.** `State.window()` resumes from the last scan, not from
  today minus a month, so nothing falls through the gap.
- **A proposal clashes with an existing file.** The scaffolder refuses the whole
  proposal and says so in the report; pick a different slug or update the
  existing skill by hand.
- **A pre-release facility** (Roman, Gaia DR4): `new_survey_skill` already
  covers this — the skill is written in pre-release mode with a release-watch
  section, and its probe is what a later scan re-runs on release day.

## Changing what it looks for

- Queries: `CHANNELS` in `scoop/scout/candidates.py`. Keep them broad; triage
  filters. A missed release costs a month, a false positive costs a few hundred
  tokens.
- Fetchable domains: `ALLOWED_DOMAINS` in `scoop/scout/fetch.py`, in a reviewed
  commit — never at runtime.
- Staleness threshold: `STALE_AFTER_DAYS` in `scoop/scout/coverage.py`, or
  `--stale-days`.
- The skill template and the ground rules: `meta/new_survey_skill.md`, which the
  scout loads verbatim into its system prompt. Changing it changes the scout.

## Operational targets

| Metric | Target |
|---|---|
| Cost per scan | < $2 |
| Proposals per scan | 0–3 (a reviewable amount) |
| Proposals accepted at review | > 50%; below that, tighten the triage prompt |
| `domain_allowed` (no refused fetches) | true; a refusal means the allowlist needs a reviewed addition |
| Skills older than `STALE_AFTER_DAYS` | 0 after review |
