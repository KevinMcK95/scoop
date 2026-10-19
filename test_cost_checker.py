# Test harness for cost_checker.py
from cost_checker import check_budget

# Example queries and budgets
tests = [
    {"query": "Summarize the latest arXiv paper on transformers.", "budget": 0.05},
    {"query": "Explain quantum entanglement in detail.", "budget": 0.001},
    {"query": "A" * 4000, "budget": 0.10},  # Large prompt
    {"query": "Short.", "budget": None},  # Use default budget
]

for i, test in enumerate(tests, 1):
    result = check_budget(test["query"], test["budget"])
    print(f"Test {i}:\n  Query: {test['query'][:40]}...\n  Tokens: {result['tokens']}\n  Estimated Cost: ${result['estimated_cost_usd']} (Budget: ${result['budget_usd']})\n  Within Budget: {result['within_budget']}\n")
