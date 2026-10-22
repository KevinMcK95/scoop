"""The LangGraph chat agent and its tools; deliberately not imported by scoop's root.

Importing this package requires the optional LangGraph/LangChain dependencies.
The graph takes its model and tools from callers (scoop.server builds them per turn).
"""

from .graph import DEFAULT_SYSTEM_PROMPT, build_chat_graph
from .state import ChatState, merge_artifacts
from .tools import publish_result

__all__ = [
    "ChatState",
    "DEFAULT_SYSTEM_PROMPT",
    "build_chat_graph",
    "merge_artifacts",
    "publish_result",
]
