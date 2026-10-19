"""
cost_checker.py

Estimate the cost of a user query and check it against a budget.
Supports both user-specified and agent-default budgets.
"""

import os
from typing import Optional

# Example model pricing (USD per 1K tokens)
MODEL_PRICING = {
    "gpt-4": 0.03,  # $0.03 per 1K tokens (input)
    "gpt-3.5": 0.002,
}

# Default agent budget (USD)
DEFAULT_BUDGET_USD = float(os.environ.get("AGENT_BUDGET_USD", 1.00))  # $1.00 default

# Simple token estimator (1 token ≈ 4 chars for English)
def estimate_tokens(text: str) -> int:
    return max(1, len(text) // 4)

def estimate_cost(tokens: int, model: str = "gpt-4") -> float:
    price_per_1k = MODEL_PRICING.get(model, MODEL_PRICING["gpt-4"])
    return (tokens / 1000) * price_per_1k

def check_budget(query: str, budget_usd: Optional[float] = None, model: str = "gpt-4") -> dict:
    tokens = estimate_tokens(query)
    est_cost = estimate_cost(tokens, model)
    budget = budget_usd if budget_usd is not None else DEFAULT_BUDGET_USD
    within_budget = est_cost <= budget
    return {
        "tokens": tokens,
        "estimated_cost_usd": round(est_cost, 6),
        "budget_usd": round(budget, 6),
        "within_budget": within_budget,
    }

if __name__ == "__main__":
    # Example usage
    import sys
    prompt = sys.argv[1] if len(sys.argv) > 1 else "Summarize the latest arXiv paper on transformers."
    user_budget = float(sys.argv[2]) if len(sys.argv) > 2 else None
    result = check_budget(prompt, user_budget)
    print(f"Prompt: {prompt}\nTokens: {result['tokens']}\nEstimated Cost: ${result['estimated_cost_usd']} (Budget: ${result['budget_usd']})\nWithin Budget: {result['within_budget']}")
