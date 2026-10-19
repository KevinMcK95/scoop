"""Optional LangGraph chat scaffold; deliberately not imported by scoop's root.

Importing this package requires the optional LangGraph/LangChain dependencies.
Models and tools are supplied by callers; no live services are constructed here.
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
