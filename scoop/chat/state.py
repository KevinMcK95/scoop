"""Checkpoint-friendly chat state with immutable, result-scoped artifacts."""
from __future__ import annotations

import json
import math
from typing import Annotated, Any

from langgraph.graph import MessagesState


def _nonempty_text(value: str, name: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a nonempty string")


def _copy_payload(payload: dict) -> dict:
    """Validate strict JSON data and detach all mutable containers."""
    active: set[int] = set()

    def copy(value: Any) -> Any:
        if value is None or type(value) in (str, bool, int):
            return value
        if type(value) is float:
            if not math.isfinite(value):
                raise ValueError("JSON numbers must be finite")
            return value
        if type(value) not in (dict, list):
            raise ValueError("Artifacts must contain only JSON data")
        if id(value) in active:
            raise ValueError("Artifacts cannot contain cycles")
        active.add(id(value))
        try:
            if type(value) is list:
                return [copy(item) for item in value]
            if any(type(key) is not str for key in value):
                raise ValueError("JSON object keys must be strings")
            return {key: copy(item) for key, item in value.items()}
        finally:
            active.remove(id(value))

    if type(payload) is not dict:
        raise ValueError("Artifact payload must be a JSON object")
    return copy(payload)


def merge_artifacts(left: dict[str, dict], right: dict[str, dict]) -> dict[str, dict]:
    """Merge parallel results without mutation; replay is safe, overwrites are not.

    Keys are exact result handles (typically tool-call IDs), not entity identities.
    Full tool payloads remain JSON data, without domain/schema conversion.
    """
    merged: dict[str, dict] = {}
    for artifacts in (left, right):
        for key, payload in artifacts.items():
            _nonempty_text(key, "artifact_id")
            copied = _copy_payload(payload)
            # Python equality conflates True with 1. Compare canonical JSON so
            # distinct JSON values cannot silently replace retrieval evidence.
            if key in merged and json.dumps(merged[key], sort_keys=True) != json.dumps(
                copied, sort_keys=True
            ):
                raise ValueError(f"Conflicting artifact ID: {key!r}")
            merged[key] = copied
    return merged


class ChatState(MessagesState):
    """Conversation messages plus JSON tool results retained across turns."""

    artifacts: Annotated[dict[str, dict], merge_artifacts]


__all__ = ["ChatState", "merge_artifacts"]
