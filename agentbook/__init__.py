"""agentbook: a small agent harness (loop, models, tools, traces, checks).

Copied from Tutorial 1 of the Fall School on Agentic AI in Science 2026,
https://github.com/ioanazelko/AgenticAI2026_Tutorial-1 at commit ca64a8d
(MIT licence, see LICENSE in this folder). Only the general-purpose modules
were copied; the dark-matter registry tools (registry.py), the Lyman-alpha
graders (evals.py) and reproducibility.py were left out.

    from agentbook import FakeModel, call, ToolRegistry, run_agent
    from exoplanets.tools import exoplanet_tools
    result = run_agent(FakeModel([...]), ToolRegistry(exoplanet_tools()), "Which planets ...?")
"""

from .loop import AgentResult, run_agent
from .models import (AnthropicModel, FakeModel, Model, ModelResponse, OpenAIModel, Usage,
                     call, text_block, tool_result_block, tool_use_block)
from .tools import Tool, ToolError, ToolRegistry, truncate
from .tracing import Tracer, format_messages, format_trace, load_trace, print_trace, summarize_trace

__all__ = [
    "AgentResult", "run_agent", "AnthropicModel", "FakeModel", "Model", "ModelResponse",
    "OpenAIModel", "Usage", "call", "text_block", "tool_result_block", "tool_use_block",
    "Tool", "ToolError", "ToolRegistry", "truncate", "Tracer", "format_messages",
    "format_trace", "load_trace", "print_trace", "summarize_trace",
]
