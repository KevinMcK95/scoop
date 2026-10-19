"""Typed requests to the sky renderer, not browser execution or scientific data.

The host must transport these requests and feed renderer acknowledgments back
into the conversation. Publishing a request does not mean it was displayed.
"""
from __future__ import annotations

from collections.abc import Collection
from typing import Annotated, Literal

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import BaseModel, ConfigDict, Field

from .tools import publish_result


class ViewModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Center(ViewModel):
    type: Literal["center"]
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    fov: float = Field(gt=0, le=180)


class LayerVisibility(ViewModel):
    type: Literal["layer.visibility"]
    layerId: str = Field(min_length=1)
    visible: bool


class Highlight(ViewModel):
    type: Literal["sources.highlight"]
    sourceIds: list[str] = Field(max_length=100)


class Circle(ViewModel):
    type: Literal["annotation.circle"]
    ra: float = Field(ge=0, lt=360)
    dec: float = Field(ge=-90, le=90)
    radius: float = Field(gt=0, le=180)
    label: str = Field(min_length=1, max_length=200, pattern=r"\S")


class ClearAnnotations(ViewModel):
    type: Literal["annotations.clear"]


ViewCommand = Annotated[
    Center | LayerVisibility | Highlight | Circle | ClearAnnotations,
    Field(discriminator="type"),
]


def build_view_tools(
    *, layer_ids: Collection[str] = (), source_ids: Collection[str] = ()
) -> list[BaseTool]:
    """Bind authorized current-view IDs; rebuild when the inventory changes.

    Coordinates and angular sizes are ICRS degrees. IDs refer to renderer objects
    supplied by the host, not names the model is allowed to invent.
    """
    layers, sources = frozenset(layer_ids), frozenset(source_ids)

    @tool
    async def update_sky_view(
        command: ViewCommand,
        tool_call_id: Annotated[str, InjectedToolCallId],
    ):
        """Request a sky-view change using a structured command, never JavaScript.

        Center a view, toggle a known layer, highlight known source IDs, draw an
        angular circle, or clear annotations. All angles are ICRS degrees. A
        circle is an annotation, not evidence of an object's size or association.
        The request is not applied until the frontend acknowledges it.
        """
        invalid = (
            isinstance(command, LayerVisibility) and command.layerId not in layers
        ) or (
            isinstance(command, Highlight) and any(id not in sources for id in command.sourceIds)
        )
        if invalid:
            return ToolMessage(
                content="Unknown or unauthorized layer/source ID. Inspect the current view context first.",
                tool_call_id=tool_call_id, status="error",
            )
        return publish_result(
            tool_call_id, tool_call_id,
            {"kind": "view_request", "command_id": tool_call_id,
             "status": "requested", "command": command.model_dump(mode="json")},
            f"Requested sky action {command.type}. No renderer acknowledgment has been received; "
            "do not claim it is displayed yet.",
        )

    return [update_sky_view]
