# Skill: Cost Checking

## Purpose
Estimate request cost before execution and enforce budget policies consistently.

## When to use
- Any LLM/tool request that can consume billable resources.
- Before executing expensive tools (large retrieval, web fan-out, long context calls).

## Inputs
- `query_text` (string)
- `model_id` (string)
- `estimated_output_tokens` (int, optional)
- `user_id` or `session_id` (string)
- `request_budget_usd` (float, optional)
- `tenant_budget_usd_remaining` (float, optional)
- `pricing_table` (model -> input/output/cached token pricing)

## Outputs
- `estimated_input_tokens`
- `estimated_total_tokens`
- `estimated_cost_usd`
- `budget_limit_used_usd`
- `decision` (`allow` | `warn` | `block` | `needs_approval`)
- `reason_codes` (list)

## Policy order (deterministic)
1. Validate required inputs and known `model_id`.
2. Estimate tokens.
3. Estimate cost using current pricing table.
4. Choose applicable budget limit:
   - request override (if provided)
   - session/user remaining
   - default system budget
5. Evaluate decision thresholds:
   - `block` if estimate > hard cap
   - `needs_approval` if estimate > soft cap
   - `warn` near cap (e.g., >80%)
   - `allow` otherwise
6. Emit audit event with full calculation trace.

## Formula
- Estimated cost:
  - $\text{cost} = \frac{t_{in}}{10^6} p_{in} + \frac{t_{out}}{10^6} p_{out} + \frac{t_{cache}}{10^6} p_{cache}$

## Guardrails
- Fail closed on missing pricing for a model.
- Never silently fallback to a different model’s price.
- Round only for display, not policy checks.
- Include tool-call costs separately where applicable.

## Edge cases
- Zero/empty prompts: allow but still log.
- Extremely large prompts: early block before model call.
- Unknown model or stale pricing: block + emit `PRICING_UNKNOWN`.

## Minimal audit payload
```json
{
  "event": "cost_check",
  "request_id": "req_123",
  "model_id": "gpt-6-luna",
  "estimated_input_tokens": 1200,
  "estimated_output_tokens": 600,
  "estimated_cost_usd": 0.00042,
  "budget_limit_used_usd": 0.002,
  "decision": "allow",
  "reason_codes": ["UNDER_HARD_CAP"]
}
```

## Operational targets
- False blocks < 1%
- Cost estimation error (MAPE) < 10% over rolling 7 days
- 100% of requests produce a cost-check audit event
