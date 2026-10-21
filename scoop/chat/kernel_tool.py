"""run_kernel: the agent's computations in the user's own analysis environment (scoop/kernel.py)."""
from __future__ import annotations

from typing import Annotated

from langchain_core.tools import BaseTool, InjectedToolCallId, tool
from pydantic import Field

from .python_tool import code_result, table_inputs
from .tools import tool_error


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
        kernel with their installed packages and their data files. Use it when the
        work needs a package or data run_python does not have, more memory or time, or
        state kept across steps. Check that a package is installed before relying on it.

        Variables persist between calls: load large data once and reuse it. restart=True
        starts a fresh kernel first. Each table named in `tables` becomes a pandas
        DataFrame of that name (and in `tables`). The working directory is a scratch
        directory; write files only there. Read only data paths the user has given or
        that you found by listing directories they named.

        Output: what you print, plus the value of a final expression. The first
        matplotlib figure is shown to you and the user; put panels in one figure.
        Print summaries (shapes, units, statistics), never whole arrays.
        Long computations are interrupted after the time limit; variables are kept.
        """
        inputs = table_inputs(current_field(), tables)
        if isinstance(inputs, str):
            return tool_error(inputs, tool_call_id)
        try:
            if restart:
                await kernel.restart()
            result = await kernel.run(code, inputs)
        except Exception as exc:  # the kernel failing is an outcome
            return tool_error(f"The kernel failed ({type(exc).__name__}: {exc}).", tool_call_id)
        return code_result("run_kernel", code, result, tool_call_id)

    return [run_kernel]


__all__ = ["build_kernel_tools"]
