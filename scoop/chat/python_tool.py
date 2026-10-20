"""run_python: the agent's own computations on the field's tables, in the sandbox (scoop/sandbox.py)."""
from __future__ import annotations

from typing import Annotated

from langchain_core.messages import ToolMessage
from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import Field

from ..sandbox import table_payload


def build_python_tools(sandbox, field: dict | None = None) -> list[BaseTool]:
    @tool
    async def run_python(
        code: Annotated[str, Field(min_length=1, max_length=20_000)],
        tool_call_id: Annotated[str, InjectedToolCallId],
        tables: Annotated[list[str], Field(max_length=10)] = [],
    ):
        """Run Python 3 in a sandbox (no network, no files) for computations the other
        tools cannot do: custom statistics, fits, cross-matches, coordinate and unit
        conversions, quick plots. numpy (np), pandas (pd), matplotlib.pyplot (plt), scipy,
        astropy, scikit-learn, statsmodels, sympy and uncertainties are available.
        Each named field table is a pandas DataFrame, as a variable of that name and in
        `tables` (e.g. tables=["gaia"] -> gaia.G, gaia.bp_rp). Print what you need; the
        value of a final expression is shown too. A matplotlib figure is shown to you
        (and the user) as an image. Every call starts fresh; limit 30 s.
        """
        unknown = [t for t in tables if field is None or t not in field["tables"]]
        if unknown:
            offered = sorted(field["tables"]) if field else []
            return ToolMessage(content=f"Unknown tables {unknown}; this field has {offered or 'no tables (none loaded)'}.",
                               tool_call_id=tool_call_id, status="error")
        payload = {name: table_payload(field["tables"][name]) for name in tables}
        try:
            result = await sandbox.run(code, payload)
        except Exception as exc:  # the sandbox failing is an outcome
            return ToolMessage(content=f"The sandbox failed ({type(exc).__name__}: {exc}).",
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
        return ToolMessage(content="\n\n".join(parts), tool_call_id=tool_call_id, name="run_python",
                           status="error" if result.get("error") else "success", artifact=artifact)

    return [run_python]


__all__ = ["build_python_tools"]
