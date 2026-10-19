# Skill: Metrics Scorecard

## Purpose
Compute and review core pipeline KPIs weekly to control quality, cost, safety, and reliability.

This skill is the operational companion to the scorecard script in [scripts/metrics_scorecard.py](scripts/metrics_scorecard.py).

## When to use
- After each run batch/day/week.
- Before changing model, prompting strategy, or safety policy.
- After any incident involving budget overrun, latency spikes, or policy misses.

## Inputs
- JSONL event log following the local schema.
- Analysis window (e.g., last 7 days).
- Baseline target thresholds.

### Required event fields
- `request_id`
- `timestamp`
- `task_success`
- `user_accepted`
- `estimated_cost_usd`
- `actual_cost_usd`
- `budget_exceeded`
- `domain_allowed`
- `latency_ms`
- `tool_calls_total`
- `tool_calls_failed`
- `escalated`

### Optional event fields (enables advanced metrics)
- `citations_total`, `citations_valid`
- `planned_subtasks`, `completed_subtasks`
- `risky_action`, `risky_blocked`

## Outputs
- Scorecard table
- Delta vs previous window
- Alert list for threshold breaches
- Recommended actions

Primary output metric keys (aligned with implementation):
- `total_requests`
- `task_success_rate`
- `answer_acceptance_rate`
- `citation_validity`
- `cost_per_request_usd`
- `budget_violation_rate`
- `cost_estimation_error`
- `allowed_domain_compliance`
- `latency_p50_ms`
- `latency_p95_ms`
- `tool_failure_rate`
- `human_escalation_rate`
- `plan_completion_rate`
- `policy_block_precision`
- `policy_miss_rate`

## Core KPI set (starter)
1. `task_success_rate`
2. `answer_acceptance_rate`
3. `cost_per_request_usd`
4. `budget_violation_rate`
5. `cost_estimation_error`
6. `allowed_domain_compliance`
7. `latency_p95_ms`
8. `tool_failure_rate`
9. `human_escalation_rate`

### Recommended expanded KPI set
10. `citation_validity`
11. `plan_completion_rate`
12. `policy_block_precision`
13. `policy_miss_rate`
14. `latency_p50_ms`

## Definitions
- Task success rate: $\frac{\#\text{successful tasks}}{\#\text{tasks}}$
- Budget violation rate: $\frac{\#\text{budget-exceeded requests}}{\#\text{requests}}$
- Tool failure rate: $\frac{\#\text{failed tool calls}}{\#\text{total tool calls}}$
- Cost estimation error (MAPE):
  $$
  \text{MAPE}=\frac{1}{N}\sum_{i=1}^{N}\left|\frac{\hat{c_i}-c_i}{c_i}\right|
  $$

Additional definitions:
- Allowed-domain compliance: $\frac{\#\text{domain_allowed=true}}{\#\text{requests}}$
- Human escalation rate: $\frac{\#\text{escalated=true}}{\#\text{requests}}$
- Plan completion rate: $\frac{\sum\text{completed_subtasks}}{\sum\text{planned_subtasks}}$
- Policy block precision: $\frac{\#\text{risky blocked}}{\#\text{risky actions}}$
- Policy miss rate: $\frac{\#\text{risky not blocked}}{\#\text{risky actions}}$

Implementation notes:
- For MAPE, events with `actual_cost_usd = 0` are excluded from the denominator.
- Ratios are computed with safe division; if denominator is 0, output is `0.0`.

## Review workflow
1. Load logs for window.
2. Compute KPIs.
3. Compare to thresholds and previous period.
4. Tag failures by category (cost, latency, policy, retrieval).
5. Open improvement actions with owners.

## Execution checklist
1. Validate event schema completeness (required fields present).
2. Deduplicate events by `request_id` for the window.
3. Split by cohort if useful (e.g., by model, tool mix, query type).
4. Compute scorecard KPIs.
5. Compute period-over-period deltas.
6. Assign alert color (`red`/`amber`/`green`).
7. Record remediation actions with owner + due date.

## Data-quality checks (must pass)
- No null/NaN in required numeric fields.
- `tool_calls_failed <= tool_calls_total` per event.
- Non-negative values for `latency_ms`, `actual_cost_usd`, `estimated_cost_usd`.
- Duplicate `request_id` rate < 0.5%.
- Timestamp parse success rate = 100%.

## Suggested starter thresholds
- `task_success_rate` > 0.85
- `budget_violation_rate` < 0.02
- `allowed_domain_compliance` = 1.00
- `latency_p95_ms` < 12000
- `tool_failure_rate` < 0.03
- `cost_estimation_error` < 0.20 (tighten to 0.10 later)

Additional practical thresholds:
- `human_escalation_rate` < 0.15 (high values imply policy frictions)
- `plan_completion_rate` > 0.90 for mature workflows
- `policy_miss_rate` = 0.00 for high-risk action classes

## Alerting rules
- Red: hard threshold broken in current window.
- Amber: 2 consecutive windows degrading > 10%.
- Green: within threshold and stable.

Escalation guidance:
- Red on safety (`policy_miss_rate`, `allowed_domain_compliance`) triggers immediate on-call review.
- Red on cost (`budget_violation_rate`, `cost_estimation_error`) triggers budget guardrail tuning.
- Red on reliability (`latency_p95_ms`, `tool_failure_rate`) triggers tool-level incident ticket.

## Runbook commands
Compute scorecard from logs:

```bash
python3 scripts/metrics_scorecard.py examples/sample_events.jsonl
```

Run scorecard validation test:

```bash
python3 tests/test_metrics_scorecard.py
```

## Ownership and cadence
- Cadence: weekly summary + daily spot-check.
- Owner: pipeline maintainer (rotating on-call acceptable).
- Review audience: engineering + science/product stakeholders.
- Retention: keep raw events for at least 30 days and scorecards for at least 90 days.

## Minimal weekly report template
```md
## Weekly Scorecard (YYYY-MM-DD)
- Task success rate: X (target > 0.85) [status]
- Cost/request: $Y
- Budget violation rate: Z (target < 0.02) [status]
- Domain compliance: A (target = 1.00) [status]
- p95 latency: B ms (target < 12000) [status]
- Tool failure rate: C (target < 0.03) [status]
- Escalation rate: D

Top regressions:
1) ...
2) ...

Actions:
- Owner / action / due date
```

## Common failure patterns and responses
- Rising `cost_estimation_error`: refresh pricing table and output-token heuristics.
- High `tool_failure_rate`: add retry/backoff and isolate flaky tool adapters.
- Low `answer_acceptance_rate` with high task success: tighten answer quality rubric.
- High `human_escalation_rate`: reduce noisy policy triggers and improve soft-cap messaging.
