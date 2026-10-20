# Test harness for cost_checker.py
from cost_checker import (
    check_budget,
    confirm_actual_cost,
    get_model_calibration,
    reset_calibration,
    run_with_auto_calibration,
)

reset_calibration()

# Example queries and budgets
tests = [
    {"query": "Summarize the latest arXiv paper on transformers.", "budget": 0.05},
    {"query": "Explain quantum entanglement in detail.", "budget": 0.001},
    {"query": "A" * 4000, "budget": 0.10},  # Large prompt
    {"query": "Short.", "budget": None},  # Use default budget
]

for i, test in enumerate(tests, 1):
    result = check_budget(test["query"], test["budget"], model="gpt-4")
    print(
        f"Test {i}:\n"
        f"  Query: {test['query'][:40]}...\n"
        f"  Request ID: {result['request_id']}\n"
        f"  Tokens: {result['tokens']}\n"
        f"  Base Estimate: ${result['base_estimated_cost_usd']}\n"
        f"  Estimated Cost: ${result['estimated_cost_usd']} (Budget: ${result['budget_usd']})\n"
        f"  Within Budget: {result['within_budget']}\n"
    )

# Learning check: confirm actual is higher than estimate and verify correction factor increases.
first = check_budget("B" * 2000, 1.0, model="gpt-4")
before = get_model_calibration("gpt-4")
feedback = confirm_actual_cost(first["request_id"], actual_cost_usd=first["estimated_cost_usd"] * 1.5)
after = get_model_calibration("gpt-4")

assert feedback["samples"] >= 1
assert after["correction_factor"] > before["correction_factor"], "Correction factor should increase after underestimation"
print("Calibration learning check passed")


# Auto-update check: estimator should update after each wrapped command.
reset_calibration("gpt-4")
before_auto = get_model_calibration("gpt-4")

auto = run_with_auto_calibration(
    query="Summarize this paper and list three key findings.",
    model="gpt-4",
    budget_usd=1.0,
    command_fn=lambda: {"ok": True},
    usage={"input_tokens": 1200, "output_tokens": 600},
)

after_auto = get_model_calibration("gpt-4")
assert auto["calibration_feedback"] is not None, "Expected auto calibration feedback"
assert after_auto["samples"] == before_auto["samples"] + 1, "Expected samples to increment after wrapped command"
print("Auto calibration command wrapper check passed")


# Auto extraction check: no explicit usage/actual passed, wrapper reads from command result payload.
reset_calibration("gpt-4")
before_extract = get_model_calibration("gpt-4")
auto_extract = run_with_auto_calibration(
    query="Find related papers and summarize methods.",
    model="gpt-4",
    budget_usd=1.0,
    command_fn=lambda: {
        "ok": True,
        "usage": {"input_tokens": 500, "output_tokens": 250, "cached_input_tokens": 0},
    },
)
after_extract = get_model_calibration("gpt-4")
assert auto_extract["calibration_feedback"] is not None
assert after_extract["samples"] == before_extract["samples"] + 1
print("Auto extraction from command result check passed")


# Strict mode check: missing actual/usage should raise.
error_seen = False
try:
    run_with_auto_calibration(
        query="No usage signal",
        model="gpt-4",
        budget_usd=1.0,
        command_fn=lambda: {"ok": True},
    )
except ValueError:
    error_seen = True

assert error_seen, "Expected strict calibration mode to require usage or actual cost"
print("Strict calibration signal enforcement check passed")
