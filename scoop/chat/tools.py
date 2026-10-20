"""Publishing contract for injected tools; no live archive tools are defined.

Tools should obtain the real call ID through ``ToolRuntime.tool_call_id`` or an
``Annotated[str, InjectedToolCallId]`` argument, then return ``publish_result``.
Archive/service failures should be represented in their JSON result payloads;
unexpected exceptions propagate from the graph's ToolNode.
"""
from __future__ import annotations

from langchain_core.messages import ToolMessage
from langgraph.types import Command

from .state import _copy_payload, _nonempty_text


def publish_result(
    tool_call_id: str, artifact_id: str, payload: dict, summary: str
) -> Command:
    """Store a full JSON result and expose only a brief summary and exact handle.

    Prefer the tool-call ID as the artifact ID. Handles identify immutable result
    snapshots, not astronomical entities. The supplied payload is defensively
    copied; no domain conversion or result schema is invented here.
    """
    _nonempty_text(tool_call_id, "tool_call_id")
    _nonempty_text(artifact_id, "artifact_id")
    _nonempty_text(summary, "summary")
    return Command(
        update={
            "artifacts": {artifact_id: _copy_payload(payload)},
            "messages": [
                ToolMessage(
                    content=f"{summary}\nArtifact: {artifact_id}",
                    tool_call_id=tool_call_id,
                )
            ],
        }
    )


__all__ = ["publish_result"]
