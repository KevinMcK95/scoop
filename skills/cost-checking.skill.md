# Skill: Cost Checking

## Purpose
Estimate what a command will cost before it runs, check it against the
conversation's budget, and learn from what it actually cost.

## Implementation
`scoop/cost.py` (`CostTracker`); wired into the chat turn in
`scoop/server/app.py` and shown in the workspace by `ChatPanel.svelte`. State
(per-model calibration, per-conversation budgets, requests awaiting
confirmation) lives in one JSON file — `~/.config/scoop/costs.json` under
`python -m scoop.server`.

## When to use
- Every chat turn: the server estimates one before calling the model.
- Before any other request that can consume billable resources (large
  retrieval, web fan-out, long-context calls).

## Flow (deterministic)
1. A conversation may set a budget (`POST /api/budget/{thread}`). It is
   optional: without one, steps 2-6 do not run (no estimate, no calibration,
   no cost events).
2. `CostTracker.estimate(text, model, thread_id, overhead_tokens)` prices the
   user's text plus what rides along with it (system prompt, tool schemas,
   workspace context) across the expected number of model calls, multiplied by
   the model's learned correction factor.
3. The estimate is checked against what the conversation has left. Over that,
   the turn is refused with the shortfall and nothing is sent to the model.
4. The turn runs; `usage_metadata` is accumulated over its model calls.
5. `CostTracker.confirm_usage(request_id, model, usage)` prices the real
   counters, charges them to the conversation, and nudges the model's
   correction factor (EWMA) towards `actual / base_estimate`.
6. The audit log keeps `estimated_cost_usd` and `actual_cost_usd` per turn.

## Inputs
- `query` (string), `model` (model id), `thread_id` (conversation)
- `overhead_tokens`, `model_calls` (what the caller knows about the turn)
- `budget_usd` (per-request override; otherwise the conversation's remaining
  budget, otherwise `AGENT_BUDGET_USD` when `CostTracker` is used directly)

## Outputs
`estimate()`: `request_id`, `estimated_input_tokens`, `estimated_output_tokens`,
`base_estimated_cost_usd`, `estimated_cost_usd`, `correction_factor`,
`budget_usd`, `budget_scope`, `budget`, `within_budget`, `priced`.
`confirm_usage()`: `actual_cost_usd`, `previous_estimated_cost_usd`, `budget`
(what is left), `old_correction_factor`, `new_correction_factor`, `samples`.

## Formula
- Cost: $\frac{t_{in}}{10^6} p_{in} + \frac{t_{cache}}{10^6} p_{cache}
  + \frac{t_{out}}{10^6} p_{out}$, with cache hits priced once, at the cache rate.
- Estimate: $\text{cost}(\text{prompt} + \text{overhead}) \times
  \text{model calls} \times \text{correction factor}$.

## Guardrails
- Fail closed on missing pricing: an unknown model reports `priced: false` and
  is never priced at another model's rate. Prices come from a public list
  fetched at startup (`load_prices`, cached daily); the built-in table is the
  offline fallback. `SCOOP_MODEL_PRICE_USD_PER_MTOK` is only for a model that
  is not listed online.
- Round only for display, not for policy checks.
- A cost estimate never fails a turn: state that cannot be written is dropped.
- Unconfirmed requests are pruned, so the state file cannot grow without bound.

## Edge cases
- Zero/empty prompts: allowed, still estimated and logged.
- A turn that ends in an error still bills what it used: the tokens were spent.
- A cancelled turn (Stop) is charged for the tokens it had used.
- An unpriced model cannot enforce a budget; the workspace says so.

## Operational targets
- Cost estimation error (EWMA relative error, reported per model by
  `calibration()`) under 10% once a model has a handful of samples.
- Every turn produces a cost estimate event and an audit line.
