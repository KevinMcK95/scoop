"""run_kernel: the agent's computations in the user's own analysis environment (scoop/kernel.py)."""
from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import Field

from ..sandbox import table_payload


def build_kernel_tools(kernel, field=None) -> list[BaseTool]:
    # field: the field, or a function returning the turn's current field (load_field can switch it).
    current_field = field if callable(field) else (lambda: field)

    @tool
    async def run_kernel(
        code: Annotated[str, Field(min_length=1, max_length=50_000)],
        tool_call_id: Annotated[str, InjectedToolCallId],
        tables: Annotated[list[str], Field(max_length=10)] = [],
        restart: bool = False,
    ):
        """Run Python in the user's own analysis environment: a persistent Jupyter
        kernel with their installed packages (e.g. pixell, healpy, so3g, sotodlib) and
        their data files. Use it for map-level work: reading maps, cutouts, stacking,
        filtering, power spectra, and figures of them.

        Variables persist between calls: load a map once and reuse it. restart=True
        starts a fresh kernel first. Each table named in `tables` becomes a pandas
        DataFrame of that name (and in `tables`). The working directory is a scratch
        directory; write files only there. Read only data paths the user has given or
        that you found by listing directories they named.

        Output: what you print, plus the value of a final expression. The first
        matplotlib figure is shown to you and the user; put panels in one figure.
        Print summaries (shapes, units, statistics), never whole arrays.
        Long computations are interrupted after the time limit; variables are kept.
        """
        current = current_field()
        unknown = [t for t in tables if current is None or t not in current["tables"]]
        if unknown:
            offered = sorted(current["tables"]) if current else []
            return ToolMessage(content=f"Unknown tables {unknown}; this field has {offered or 'no tables (none loaded)'}.",
                               tool_call_id=tool_call_id, status="error")
        payload = {name: table_payload(current["tables"][name]) for name in tables}
        try:
            if restart:
                await kernel.restart()
            result = await kernel.run(code, payload)
        except Exception as exc:  # the kernel failing is an outcome
            return ToolMessage(content=f"The kernel failed ({type(exc).__name__}: {exc}).",
                               tool_call_id=tool_call_id, status="error")
        parts = [result.get("output") or "(no output)"]
        if result.get("error"):
            parts.append(f"Error:\n{result['error']}")
        figures = result.get("figures") or []
        if figures:
            extra = result.get("figure_count", len(figures)) - 1
            parts.append("The figure follows." + (f" ({extra} more not shown; put panels in one figure.)" if extra else ""))
        artifact = {"code": code, "output": result.get("output", ""), "error": result.get("error")}
        if figures:
            artifact["image"] = "data:image/png;base64," + figures[0]
        return ToolMessage(content="\n\n".join(parts), tool_call_id=tool_call_id, name="run_kernel",
                           status="error" if result.get("error") else "success", artifact=artifact)

    return [run_kernel]


__all__ = ["build_kernel_tools"]
