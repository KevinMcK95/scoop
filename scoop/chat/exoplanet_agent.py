"""The exoplanet agent as one chat tool: ``ask_exoplanet_agent(question, criteria)``.

The chat agent hands exoplanet questions to it: planet properties and what papers found,
TESS candidates and light curves, short-cadence candidates, and soft constraints on the
field's planet hosts, TOIs or TIC stars ("habitable", "atmosphere claimed"). It runs Xuan
Ji's agent (exoplanets/agent.py, on agentbook) with the model from Settings: every claim
is quoted from the tool result it rests on and checked in code; vague constraints come
back as a question, concrete ones as a proposal to confirm, and confirmed ones as a
checked answer plus rows to highlight on the map. The chat relays the answer unchanged.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from typing import Annotated, Callable

from langchain_core.messages import ToolMessage
from langchain_core.tools import InjectedToolCallId, tool

from ..fields import field_region
from .tools import publish_result

ROOT = Path(__file__).resolve().parents[2]
TABLES = ("exoplanet_hosts", "tess_tois", "tic")


def field_context(field: dict) -> str:
    """What the exoplanet agent is told about the field: where it is, and what is loaded."""
    region = field_region(field)
    tables = ", ".join(f"{name} ({len(field['tables'][name]['rows'])} rows)" for name in TABLES
                       if name in field["tables"]) or "none of exoplanet_hosts, tess_tois, tic"
    return (f"{field['key']} ({field['target'].get('name')}), center RA {region['ra']:.5f}, "
            f"Dec {region['dec']:.5f}, radius {region['radius_deg'] * 60:.1f} arcmin. "
            f"Loaded tables: {tables}.")


def relay_text(answer) -> str:
    """The agent's answer as the user should see it, with whether it passed the checks."""
    if answer.checked and answer.kind == "result":
        status = "✓ Checked: every claim is quoted from the source it cites."
    elif answer.checked:
        status = ""
    else:
        status = ("⚠ Not checked: this answer failed the exoplanet agent's checks:\n"
                  + "\n".join(f"- {p}" for p in answer.report.problems))
    return "\n\n".join(part for part in (answer.text, status) if part)


def build_exoplanet_tool(*, field, make_model: Callable[[], object | None]):
    """``field`` is the field or a function returning the turn's field; ``make_model``
    returns an agentbook model (e.g. OpenAIModel) from Settings, or None."""
    current_field = field if callable(field) else (lambda: field)

    @tool
    async def ask_exoplanet_agent(question: str, tool_call_id: Annotated[str, InjectedToolCallId],
                                  criteria: str | None = None):
        """Hand an exoplanet question to SCOOP's exoplanet agent: planets and their properties,
        what papers found (atmospheres, masses), TESS candidates (TOIs), HST/JWST programs,
        which TIC stars have or could have TESS light curves, short-cadence candidates, and
        constraints on the field's exoplanet_hosts, tess_tois or tic tables ("habitable",
        "atmosphere claimed"). Not for planetary nebulae, minor planets or Solar System bodies.
        Pass the user's words as question. For constraints the agent first asks for a
        definition or proposes criteria; when the user confirms, call again with the same
        question and the confirmed criteria in criteria. Show its answer to the user word for
        word, including the checked / not checked line."""
        model = make_model()
        if model is None:
            return ToolMessage(content="The exoplanet agent needs a chat model (set one in Settings).",
                               tool_call_id=tool_call_id, status="error")
        if str(ROOT) not in sys.path:  # exoplanets/ is a top-level package beside scoop/
            sys.path.insert(0, str(ROOT))
        from exoplanets.agent import ask

        current = current_field()
        context = field_context(current) if current else None
        try:
            answer = await asyncio.to_thread(ask, question, model, context=context, criteria=criteria)
        except Exception as exc:  # a model or network failure is an outcome to report
            return ToolMessage(content=f"The exoplanet agent failed ({type(exc).__name__}: {exc}); say so.",
                               tool_call_id=tool_call_id, status="error")
        text = relay_text(answer)
        summary = f"Exoplanet agent answer (show it to the user word for word):\n\n{text}"
        highlight = answer.highlight if answer.checked else None
        if highlight and current and highlight["table"] in current["tables"]:
            action = {"type": "highlight", "table": highlight["table"], "ids": highlight["ids"][:200],
                      **({"reason": highlight["label"]} if highlight.get("label") else {})}
            payload = {"kind": "map_actions", "status": "requested", "actions": [action]}
            summary += f"\n\n(Highlighted {len(action['ids'])} rows of {highlight['table']} on the map.)"
        else:
            payload = {"kind": "exoplanet_answer", "checked": answer.checked, "answer_kind": answer.kind}
            if highlight:
                summary += (f"\n\n(Not highlighted: table {highlight['table']} is not in the field; "
                            "add it with add_data, then ask again.)")
        return publish_result(tool_call_id, tool_call_id, payload, summary)

    return ask_exoplanet_agent


__all__ = ["build_exoplanet_tool", "field_context", "relay_text"]
