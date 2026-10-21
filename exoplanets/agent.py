"""The exoplanet agent: answers questions about planets, every claim quoted and checked.

    from agentbook.models import OpenAIModel
    from exoplanets.agent import ask
    result = ask("Does TOI-561 b have an atmosphere?", OpenAIModel("..."))
    print(result.text); print(result.report.summary())

The loop is agentbook's run_agent. The model answers with a list of claims, each with a
source and a word-for-word quote of the tool result it rests on (checks.check_claims).
If the answer fails, the problems go back to the model once, as a user message, and it
answers again with its tools still available. An answer that still fails is returned
with its report, never passed off as checked: `result.checked` says which.
"""

from __future__ import annotations

from dataclasses import dataclass

from agentbook.loop import AgentResult, run_agent
from agentbook.models import Model
from agentbook.tools import ToolRegistry

from .checks import CLAIM_SOURCES, ClaimsReport, check_claims, parse_claims, render_claims

SYSTEM = f"""You answer questions about exoplanets and their host stars, using only the tools \
provided. Never answer from memory: if the tools do not return something, say so.

Answer with JSON only, no other text:
{{"claims": [{{"text": "...", "source": "...", "quote": "..."}}, ...]}}

- text: one claim, in your words, keeping every condition and uncertainty of the source \
("suggests", "consistent with", "±", "if"). Give units.
- source: the ADS bibcode of the paper it comes from, e.g. 2025ApJ...995L..39T; or, for a \
value from an archive, its name: one of {", ".join(CLAIM_SOURCES)}.
- quote: the exact words the claim rests on, copied from the tool result: for a paper, a \
sentence or phrase of its abstract (call get_abstract first); for an archive, the value as \
returned, e.g. "orbital_period_days": 6.101013. You may join fragments with "...".

Claims about what a paper found need that paper's abstract, fetched with get_abstract in \
this conversation. Archive values cite the archive. If a tool failed or found nothing, \
make that a claim too, quoting the tool's message.

Be complete, not just correct. For a question about what is known (an atmosphere, a mass, \
whether a planet is real), read the abstracts of the 2-4 most relevant research papers, \
including the most recent ones, not only the first: later papers often confirm, revise or \
contradict earlier ones. Say how the results relate (confirms, refines, disagrees), and in \
what order they came. Do not add conclusions of your own, stronger or weaker than the \
papers': a claim says what its quote says.

Your answer is checked in code before the user sees it; anything that fails is sent back \
to you to fix."""

RETRY = ("Your answer failed the checks. Fix every problem below, using your tools if you "
         "need more information, then give the whole corrected answer as JSON.\n\n{problems}")


@dataclass
class CheckedAnswer:
    text: str              # what the user reads: the rendered claims, or the raw answer if unparseable
    report: ClaimsReport
    checked: bool          # True only if the final answer passed every check
    attempts: int          # 1, or 2 if the first answer was sent back
    run: AgentResult       # the last run, with every message (for tracing and the checks)


def ask(question: str, model: Model, tools: ToolRegistry | None = None, max_steps: int = 12,
        retries: int = 1) -> CheckedAnswer:
    """Answer `question`, check the answer, and send failures back `retries` times."""
    if tools is None:
        from .tools import agent_tools
        tools = ToolRegistry(agent_tools())
    run = run_agent(model, tools, question, system=SYSTEM, max_steps=max_steps)
    report = check_claims(run.text, run.messages)
    attempts = 1
    while not report.ok and attempts <= retries and run.stop_reason == "done":
        problems = "\n".join(f"- {p}" for p in report.problems)
        messages = run.messages + [{"role": "user", "content": RETRY.format(problems=problems)}]
        run = run_agent(model, tools, messages, system=SYSTEM, max_steps=max_steps)
        report = check_claims(run.text, run.messages)
        attempts += 1
    claims = parse_claims(run.text)
    text = render_claims(claims) if claims else run.text
    return CheckedAnswer(text, report, report.ok and run.stop_reason == "done", attempts, run)
