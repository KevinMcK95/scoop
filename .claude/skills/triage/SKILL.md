---
name: triage
description: Find what failed in recent SCOOP sessions (chat tool calls, data-agent calls, field runs), reproduce the real bugs, fix them with a test, and propose the fixes for review. Use when asked to triage, to look at the traces/logs, or why things keep failing.
---

# Triage SCOOP failures

SCOOP's running agents notice failures within a turn (tool errors, `job_log`, `gaia_status`)
but never change SCOOP's code. Fixing the code is this skill's job, done in the repo, on a
branch, for a person to review.

## 1. Collect

```bash
python scripts/triage_failures.py --audit-log <server --audit-log> --fields <server --fields> --since <YYYY-MM-DD>
```

Defaults when the server ran with its defaults: `out/turns.jsonl` and the `--fields`
directory it printed at start-up. The report groups failures, most frequent first, each
labelled:

- **transient** (network, an archive down, rate limits): no code change unless the same
  failure recurs across days, or one archive being down takes a whole field with it (then
  make that archive non-fatal, as SDSS and DESI are).
- **stopped by the user**: ignore.
- **SCOOP bug** (an exception in our code; field runs carry the traceback's SCOOP frames
  and a run record under `<fields>/_runs/`): fix.
- **agent mistake or tool limit** (the tool refused the call): fix the cause, which is
  usually the tool's description or the agent's prompt (`scoop/chat/prompt.py`,
  `DATA_AGENT_PROMPT` in `scoop/chat/data_agent.py`), or make the tool accept a harmless
  variant (as `clean_name` does for labels). Several refusals of one kind in a row (the
  agent retrying) mean the error message doesn't say how to fix the call: improve it.

Check that a group still happens before fixing it: compare its last time with the commit
that may already have fixed it (`git log -S '<distinctive text>'`).

## 2. Reproduce each bug before changing anything

- A field run: rerun its arguments (the run record's `args`) with
  `python smoke_tests/lvdb_field.py --out <scratch dir> <args>`, on the smallest input that
  fails (a few arcminutes; never a large region or the Galactic plane). Reuse the record's
  `cache/` when the failure is after the archive queries.
- A tool call: call the tool in a unit test with the failure's `args` (see
  `tests/test_region_tools.py` and `tests/test_data_agent.py` for how tools are invoked).
- Write the failing test first, from the reproduction, in the matching `tests/test_*.py`
  (`tests/test_pipeline.py` for the pipeline). Tests never touch the network.

## 3. Fix

- The smallest change that removes the cause, in the code's own style; say in a comment
  what failed and when, as the code around it does.
- Code by another team member (`git log --format=%an -- <file>`): prefer an additive change
  or a wrapper, and name it in the summary.
- Run every suite: `python -m pytest -q` (in both the full and the light environment if
  both exist), and `npx vitest run` and `npx svelte-check` in `web/` if the web changed.
- Add an entry to `docs/changes_to_team_code.md`.

## 4. Propose

- Commit on a branch `triage/<YYYY-MM-DD>`, one commit per problem, never on `main`.
- Do not push or open a PR until the person agrees; then `gh pr create` with, per fix: the
  failure (count, example), the cause, the change and its test.
- Report in a table: each group, its class, and what was done (fixed / prompt change / left
  as transient / could not reproduce, and why).

## Never

- Retry big queries to reproduce a failure; abort any TAP jobs you start (see
  `scoop/gaia_status.py`).
- Weaken a check to make a failure disappear (Gaia IDs stay exact integers; TLS
  verification stays on; tokens stay out of logs and commits).
- Change behaviour the user asked for (defaults, thresholds) to silence a failure; ask.
