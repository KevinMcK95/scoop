"""The exoplanet agent: answers questions about planets, every claim quoted and checked.

    from langchain_openai import ChatOpenAI
    from scoop.exoplanets.agent import ask
    result = await ask("Does TOI-561 b have an atmosphere?", ChatOpenAI(model="..."))
    print(result.text); print(result.report.summary())

The loop is the chat's LangGraph loop (agents.graph.build_chat_graph), on any LangChain
chat model that can bind tools. The model answers with a list of claims, each with a
source and a word-for-word quote of the tool result it rests on (checks.check_claims).
If the answer fails, the problems go back to the model once, as a user message after
the whole conversation so far, and it answers again with its tools still available. An
answer that still fails, or a run that hit the step limit, is returned with its report,
never passed off as checked: `result.checked` says which.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from langchain_core.messages import AIMessage, HumanMessage
from langgraph.errors import GraphRecursionError

from ..agents.graph import build_chat_graph

from .checks import (CLAIM_SOURCES, HIGHLIGHT_TABLES, ClaimsReport, check_claims, check_clarify,
                     check_highlight, check_proposal, parse_answer, render_claims)

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

Constraints. A request may come with constraints for a table in the user's field. Never \
apply them before the user confirms, and never invent a definition:
- A constraint that is already a variable with a number range ("radius < 2 Earth radii", \
"Teq 200-300 K", "Tmag < 12"): restate it as a proposal and ask the user to confirm. The user \
chose that range: no "suggested", no source or quote, and no literature search.
- Any other constraint ("habitable", "M dwarfs", "young"): do not choose for the user. First \
count the objects it applies to in the field with a tool (planets for exoplanet_hosts, \
TOIs for tess_tois, stars for tic), then reply with only:
{{"clarify": {{"constraints": ["habitable"], "table": "exoplanet_hosts", "count": 7}}}}
The user is then asked to choose: by a definition (a range of archive values, which they \
give or ask you to suggest), or by claims in the literature (which objects papers describe \
that way, reading each one's papers).
- By a definition, and the user asks for a suggestion: search the literature, read the \
abstract, and propose one definition from one paper, with that paper as source and the \
defining words as quote, e.g. "habitable zone defined by insolation from X to Y" (source), \
and ask "Is this okay?". Such a criterion uses "archive": values from a tool decide it.
- By claims: propose a criterion with "uses": "literature" (e.g. "papers describe the planet \
as habitable or potentially habitable") and literature_planets = the count. Once confirmed, \
each object that passes needs a claim quoting a paper about it, its hedges kept.
A proposal looks like this; "suggested", "source" and "quote" only for a literature suggestion:
{{"proposal": {{"criteria": [{{"constraint": "...", "means": "<variable> <range>", "uses": \
"archive", "suggested": true, "source": "<bibcode>", "quote": "<words from its abstract>"}}], \
"literature_planets": 0, "question": "Is this okay?"}}}}
"uses" is "archive" (values from a tool) or "literature" (papers must be read for each \
planet, e.g. "atmosphere claimed"). For a literature criterion, first count the planets in \
the field with the archive tools and give that number in literature_planets, so the user \
can decide before you read papers for each.
Once the request says the criteria are confirmed, apply exactly those, then answer with claims \
as above (one per planet that passes, with the values or the paper quote that make it pass, and \
say which planets failed and why) and the rows to highlight on the map:
{{"claims": [...], "highlight": {{"table": "...", "ids": [...], "label": "..."}}}}
table is one of {", ".join(f"{t} (id: {d})" for t, d in HIGHLIGHT_TABLES.items())}; use ids exactly \
as a tool returned them.

Your answer is checked in code before the user sees it; anything that fails is sent back \
to you to fix."""

RETRY = ("Your answer failed the checks. Fix every problem below, using your tools if you "
         "need more information, then give the whole corrected answer as JSON.\n\n{problems}")


@dataclass
class CheckedAnswer:
    text: str              # what the user reads: rendered claims or proposal, or the raw answer
    report: ClaimsReport
    checked: bool          # True only if the final answer passed every check
    attempts: int          # 1, or 2 if the first answer was sent back
    messages: list         # the last run's LangChain messages, tool results included (for
                           # tracing and the checks)
    kind: str = "result"   # "result" (claims), "clarify" (asks for a definition), "proposal"
                           # (criteria to confirm), or "invalid"
    proposal: dict | None = None
    highlight: dict | None = None


def check(answer: str, messages: list) -> tuple[str, ClaimsReport, dict]:
    """Which kind of answer this is, its checks, and the parsed JSON."""
    data = parse_answer(answer)
    if data is not None and "clarify" in data:
        return "clarify", ClaimsReport([{"clarify": True}], check_clarify(data["clarify"], messages)), data
    if data is not None and "proposal" in data:
        return "proposal", ClaimsReport([{"proposal": True}], check_proposal(data["proposal"], messages)), data
    report = check_claims(answer, messages)
    report.problems += check_highlight((data or {}).get("highlight"), messages)
    return ("result" if data else "invalid"), report, data or {}


NOUNS = {"exoplanet_hosts": ("planet", "planets"), "tess_tois": ("TOI", "TOIs"), "tic": ("star", "stars")}
RANGES = {"exoplanet_hosts": "equilibrium temperature or insolation", "tess_tois": "equilibrium temperature or radius",
          "tic": "Teff, radius or log g"}


def render_clarify(clarify: dict) -> str:
    """The two ways to treat a vague constraint, worded the same every time."""
    what = " and ".join(f"“{c}”" for c in clarify["constraints"])
    one, many = NOUNS[clarify["table"]]
    n = clarify["count"]
    count = f"{n} {one if n == 1 else many}"
    return (f"How should I select {what} {many}?\n"
            f"1. **By a definition:** give me a range (e.g. of {RANGES[clarify['table']]}), or ask me "
            f"to suggest one from the literature (one reference, quoted).\n"
            f"2. **By claims in the literature:** find which of the {count} in this field papers "
            f"describe that way. This reads each one's papers: a few model calls per {one}.")


def render_proposal(proposal: dict) -> str:
    lines = ["I read your constraints as:"]
    for c in proposal["criteria"]:
        lines.append(f"- **{c.get('constraint')}** → {c.get('means')} ({c.get('uses')})"
                     + (f", suggested from {c['source']}" if c.get("suggested") else ""))
        if c.get("suggested") and c.get("quote"):
            lines.append(f"  > “{c['quote']}”")
    if proposal.get("literature_planets"):
        n = proposal["literature_planets"]
        lines.append(f"\nLiterature criteria need the papers of {n} planet{'s' if n != 1 else ''} checked "
                     "(a few model calls each).")
    lines.append(f"\n{proposal['question']}")
    return "\n".join(lines)


def request_text(question: str, context: str | None, criteria: str | None) -> str:
    """The user message the agent sees: the question, the field, and any confirmed criteria."""
    parts = [question]
    if context:
        parts.append(f"Field: {context}")
    if criteria:
        parts.append(f"Confirmed criteria (apply exactly these): {criteria}")
    return "\n\n".join(parts)


def final_text(message) -> str:
    """The text of the model's last message (content may be a list of blocks)."""
    content = message.content if isinstance(message, AIMessage) else ""
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content if isinstance(b, dict) and b.get("type") == "text")


async def run(graph, messages: list, max_steps: int) -> tuple[str, list, bool]:
    """Run the loop on `messages`: the final answer, every message, and whether the model
    finished (False when it was still calling tools after `max_steps` model calls; the
    answer is then empty)."""
    state = {"messages": messages}
    try:  # each model call and each round of tools is one LangGraph step
        async for state in graph.astream({"messages": messages, "artifacts": {}},
                                         config={"recursion_limit": 2 * max_steps}, stream_mode="values"):
            pass
    except GraphRecursionError:
        return "", state["messages"], False
    return final_text(state["messages"][-1]), state["messages"], True


async def ask(question: str, model: Any, tools: list | None = None, max_steps: int = 12,
              retries: int = 1, context: str | None = None, criteria: str | None = None) -> CheckedAnswer:
    """Answer `question` with `model` (a LangChain chat model), check the answer, and send
    failures back `retries` times.

    `context` describes the user's field (center, radius, tables); `criteria` are constraints
    the user has confirmed. Without confirmed criteria, a request with constraints gets a
    proposal back, to show the user and confirm."""
    if tools is None:
        from .tools import agent_tools
        tools = agent_tools()
    # Tool results stay whole after a retry note: the quotes are checked against them.
    graph = build_chat_graph(model, list(tools), system_prompt=SYSTEM, old_result_chars=None)
    answer, messages, done = await run(graph, [HumanMessage(content=request_text(question, context, criteria))],
                                       max_steps)
    kind, report, data = check(answer, messages)
    attempts = 1
    while not report.ok and attempts <= retries and done:
        problems = "\n".join(f"- {p}" for p in report.problems)
        answer, messages, done = await run(graph, messages + [HumanMessage(content=RETRY.format(problems=problems))],
                                           max_steps)
        kind, report, data = check(answer, messages)
        attempts += 1
    if kind == "clarify" and report.ok:
        text = render_clarify(data["clarify"])
    elif kind == "proposal" and report.ok:
        text = render_proposal(data["proposal"])
    elif data.get("claims"):
        text = render_claims(data["claims"])
    else:
        text = answer
    return CheckedAnswer(text, report, report.ok and done, attempts, messages, kind,
                         data.get("proposal"), data.get("highlight") if kind == "result" else None)
