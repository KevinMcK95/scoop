"""Plan, act, reflect: an explicit plan the model keeps up to date, and a
reflection check in code after the run.

    run = run_with_plan(model, ToolRegistry(registry_tools()), task)
    run.plan.steps          # the latest plan
    run.citations.ok        # did every cited model ID come from a tool result?

The plan is just a tool, `update_plan`, so the model decides when to call it
and the plan shows up in the message list and the trace like any other call.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from .guardrails import CitationReport, check_cited_ids
from .loop import DEFAULT_SYSTEM, AgentResult, run_agent
from .models import Model
from .tools import Tool, ToolRegistry

PLAN_SYSTEM = DEFAULT_SYSTEM + (
    " Before using other tools, call update_plan with the steps you intend to take."
    " After each tool result, check it against the plan: is it what you expected,"
    " is anything missing? Update the plan when it changes. Finish only when every"
    " step is done or you have explained why it cannot be done."
)

STATUSES = ["pending", "in_progress", "done", "dropped"]


@dataclass
class Plan:
    """The current plan and every version the model wrote."""
    steps: list[dict] = field(default_factory=list)
    history: list[list[dict]] = field(default_factory=list)

    def update(self, steps: list[dict], note: str = "") -> dict:
        self.steps = steps
        self.history.append(steps)
        done = sum(s["status"] == "done" for s in steps)
        return {"ok": True, "version": len(self.history), "done": done, "total": len(steps),
                "note_recorded": bool(note)}


def plan_tool(plan: Plan) -> Tool:
    """The update_plan tool, writing into `plan`."""
    return Tool(
        "update_plan",
        "Write or revise your plan. Send the full list of steps each time, each with a "
        "status. Use `note` for what you learned that changed the plan.",
        {"type": "object", "properties": {
            "steps": {"type": "array", "minItems": 1, "items": {
                "type": "object", "properties": {
                    "step": {"type": "string"},
                    "status": {"type": "string", "enum": STATUSES}},
                "required": ["step", "status"], "additionalProperties": False}},
            "note": {"type": "string"}},
         "required": ["steps"], "additionalProperties": False},
        plan.update)


@dataclass
class PlannedRun:
    result: AgentResult
    plan: Plan
    citations: CitationReport
    revisions: int


def run_with_plan(model: Model, tools: ToolRegistry, task: str, system: str = PLAN_SYSTEM,
                  max_revisions: int = 1, **run_kwargs) -> PlannedRun:
    """Run the agent with an update_plan tool, then reflect in code.

    Reflection: if the answer cites model IDs that no tool returned, send the
    model a message naming them and let it revise (up to `max_revisions` times).
    """
    plan = Plan()
    planned = ToolRegistry([*tools.tools.values(), plan_tool(plan)], tools.max_output_chars)
    result = run_agent(model, planned, task, system=system, **run_kwargs)
    report = check_cited_ids(result.text, result.messages)
    revisions = 0
    while not report.ok and revisions < max_revisions and result.stop_reason == "done":
        revisions += 1
        feedback = (f"Reflection check: these model IDs do not appear in any tool result: "
                    f"{', '.join(report.unsupported)}. Verify them with a tool or remove them, "
                    "then give the corrected answer.")
        messages = result.messages + [{"role": "user", "content": feedback}]
        result = run_agent(model, planned, messages, system=system, **run_kwargs)
        report = check_cited_ids(result.text, result.messages)
    return PlannedRun(result, plan, report, revisions)
