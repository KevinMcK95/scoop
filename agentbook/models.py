"""Models: one message format, several providers.

The message format follows the Anthropic Messages API because it is the
simplest one that has tool calls as first-class content blocks:

    messages = [
        {"role": "user", "content": "Which observables constrain model X?"},
        {"role": "assistant", "content": [
            {"type": "text", "text": "Let me look."},
            {"type": "tool_use", "id": "toolu_001", "name": "get_card_section",
             "input": {"model_id": "X", "section": "observables"}},
        ]},
        {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "toolu_001",
             "content": "...", "is_error": False},
        ]},
    ]

Every model in this module has the same method:

    model.complete(system, messages, tools, max_tokens) -> ModelResponse

`FakeModel` replays a script and needs no network or API key.
`AnthropicModel` and `OpenAIModel` translate to and from real providers;
their SDKs are imported only when you create one.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Callable, Protocol

# --------------------------------------------------------------------------
# Content blocks
# --------------------------------------------------------------------------


def text_block(text: str) -> dict:
    """A plain text block."""
    return {"type": "text", "text": text}


def tool_use_block(id: str, name: str, input: dict) -> dict:
    """A request from the model to run a tool."""
    return {"type": "tool_use", "id": id, "name": name, "input": input}


def tool_result_block(tool_use_id: str, content: str, is_error: bool = False) -> dict:
    """The result of running a tool, matched to its request by `tool_use_id`."""
    return {"type": "tool_result", "tool_use_id": tool_use_id,
            "content": content, "is_error": is_error}


def blocks_of(message: dict) -> list[dict]:
    """Return a message's content as a list of blocks (a string becomes one text block)."""
    content = message["content"]
    return [text_block(content)] if isinstance(content, str) else list(content)


# --------------------------------------------------------------------------
# Responses
# --------------------------------------------------------------------------


@dataclass
class Usage:
    """Token counts for one call, or summed over a run."""
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def __add__(self, other: "Usage") -> "Usage":
        return Usage(self.input_tokens + other.input_tokens,
                     self.output_tokens + other.output_tokens)


@dataclass
class ModelResponse:
    """What a model returns: content blocks, why it stopped, and token usage.

    stop_reason is "end_turn" (finished), "tool_use" (wants tools run) or
    "max_tokens" (cut off).
    """
    content: list[dict]
    stop_reason: str
    usage: Usage = field(default_factory=Usage)
    model: str = ""

    @property
    def text(self) -> str:
        """All text blocks joined together."""
        return "\n".join(b["text"] for b in self.content if b["type"] == "text")

    @property
    def tool_uses(self) -> list[dict]:
        """The tool_use blocks, in order."""
        return [b for b in self.content if b["type"] == "tool_use"]


class Model(Protocol):
    """Anything with this method can drive `run_agent`."""

    def complete(self, system: str, messages: list[dict], tools: list[dict],
                 max_tokens: int = 2048) -> ModelResponse: ...


def estimate_tokens(obj: Any) -> int:
    """Rough token count: about four characters of JSON per token."""
    text = obj if isinstance(obj, str) else json.dumps(obj, ensure_ascii=False)
    return max(1, len(text) // 4)


# --------------------------------------------------------------------------
# FakeModel: scripted and deterministic
# --------------------------------------------------------------------------


def call(name: str, **input: Any) -> dict:
    """Shorthand for a scripted tool call: call("get_gaps", model_id="X")."""
    return {"tool": name, "input": input}


class FakeModel:
    """A scripted model for offline examples and tests.

    `script` is either a list of turns, replayed in order, or a policy
    function `policy(messages) -> turn` that can read the tool results so far.
    A turn is one of:

      - a string: the final answer (stop_reason "end_turn");
      - a `call(...)` dict: one tool request;
      - a list mixing strings and `call(...)` dicts: text plus one or more
        tool requests in a single turn;
      - a ModelResponse, returned as is.

    Token usage is faked from the size of the request and reply, so budgets
    and cost plots behave plausibly. Every request is kept in `self.requests`.
    """

    def __init__(self, script: list | Callable[[list[dict]], Any], name: str = "fake"):
        self.script = script
        self.name = name
        self.requests: list[dict] = []
        self._turn = 0
        self._next_id = 0

    def complete(self, system: str, messages: list[dict], tools: list[dict],
                 max_tokens: int = 2048) -> ModelResponse:
        self.requests.append({"system": system, "messages": json.loads(json.dumps(messages)),
                              "tools": tools})
        turn = self._next_turn(messages)
        if isinstance(turn, ModelResponse):
            return turn
        content = self._to_blocks(turn)
        has_tools = any(b["type"] == "tool_use" for b in content)
        usage = Usage(estimate_tokens([system, messages, tools]), estimate_tokens(content))
        stop = "tool_use" if has_tools else "end_turn"
        if usage.output_tokens > max_tokens:
            stop = "max_tokens"
        return ModelResponse(content, stop, usage, self.name)

    def _next_turn(self, messages: list[dict]) -> Any:
        if callable(self.script):
            return self.script(messages)
        if self._turn >= len(self.script):
            raise RuntimeError(f"FakeModel script exhausted after {self._turn} turns")
        self._turn += 1
        return self.script[self._turn - 1]

    def _to_blocks(self, turn: Any) -> list[dict]:
        items = turn if isinstance(turn, list) else [turn]
        blocks = []
        for item in items:
            if isinstance(item, str):
                blocks.append(text_block(item))
            elif "tool" in item:
                self._next_id += 1
                blocks.append(tool_use_block(f"toolu_{self._next_id:03d}",
                                             item["tool"], item["input"]))
            else:
                blocks.append(item)  # already a content block
        return blocks


# --------------------------------------------------------------------------
# Anthropic adapter
# --------------------------------------------------------------------------

ANTHROPIC_STOP_REASONS = {"end_turn": "end_turn", "tool_use": "tool_use",
                          "max_tokens": "max_tokens", "stop_sequence": "end_turn",
                          "model_context_window_exceeded": "max_tokens"}


class AnthropicModel:
    """Claude through the Anthropic Messages API (`pip install anthropic`).

    Our message format is the Anthropic one, so the only translation is on
    the way back: SDK objects become plain dicts. Blocks other than text and
    tool_use (for example thinking blocks) are kept as dicts so they can be
    sent back unchanged on the next turn, as the API requires.
    """

    def __init__(self, model: str = "claude-opus-5-5", client: Any = None, **create_kwargs: Any):
        if client is None:
            import anthropic  # lazy: only needed for real runs
            client = anthropic.Anthropic()
        self.client = client
        self.model = model
        self.create_kwargs = create_kwargs  # e.g. thinking={"type": "adaptive"}

    def complete(self, system: str, messages: list[dict], tools: list[dict],
                 max_tokens: int = 2048) -> ModelResponse:
        response = self.client.messages.create(
            model=self.model, system=system, messages=messages,
            tools=tools, max_tokens=max_tokens, **self.create_kwargs)
        return from_anthropic_response(response)


def _get(obj: Any, key: str, default: Any = None) -> Any:
    """Read a field from an SDK object or a plain dict."""
    if isinstance(obj, dict):
        return obj.get(key, default)
    return getattr(obj, key, default)


def from_anthropic_response(response: Any) -> ModelResponse:
    """Convert an Anthropic Message (SDK object or dict) to a ModelResponse."""
    content = []
    for block in _get(response, "content", []):
        kind = _get(block, "type")
        if kind == "text":
            content.append(text_block(_get(block, "text")))
        elif kind == "tool_use":
            content.append(tool_use_block(_get(block, "id"), _get(block, "name"),
                                          dict(_get(block, "input") or {})))
        else:
            # exclude_none: null fields sent back on the next turn can be rejected
            content.append(block if isinstance(block, dict)
                           else block.model_dump(exclude_none=True))
    usage = _get(response, "usage")
    stop = ANTHROPIC_STOP_REASONS.get(_get(response, "stop_reason"), "end_turn")
    return ModelResponse(content, stop,
                         Usage(_get(usage, "input_tokens", 0), _get(usage, "output_tokens", 0)),
                         _get(response, "model", ""))


# --------------------------------------------------------------------------
# OpenAI-compatible adapter (OpenAI, vLLM, Ollama, llama.cpp server, ...)
# --------------------------------------------------------------------------


class OpenAIModel:
    """Any server that speaks OpenAI Chat Completions with tool calls
    (`pip install openai`).

    For an open model served locally, pass base_url, for example
    OpenAIModel("qwen3:32b", base_url="http://localhost:11434/v1", api_key="ollama").
    """

    def __init__(self, model: str, client: Any = None, base_url: str | None = None,
                 api_key: str | None = None, **create_kwargs: Any):
        if client is None:
            import openai  # lazy: only needed for real runs
            client = openai.OpenAI(base_url=base_url, api_key=api_key)
        self.client = client
        self.model = model
        # OpenAI's own API wants max_completion_tokens (reasoning models reject
        # max_tokens); local servers such as Ollama still expect max_tokens.
        self.max_tokens_param = "max_tokens" if base_url else "max_completion_tokens"
        self.create_kwargs = create_kwargs

    def complete(self, system: str, messages: list[dict], tools: list[dict],
                 max_tokens: int = 2048) -> ModelResponse:
        kwargs = dict(model=self.model, messages=to_openai_messages(system, messages),
                      **{self.max_tokens_param: max_tokens}, **self.create_kwargs)
        if tools:
            kwargs["tools"] = to_openai_tools(tools)
        return from_openai_response(self.client.chat.completions.create(**kwargs))


def to_openai_tools(tools: list[dict]) -> list[dict]:
    """Our tool specs -> Chat Completions `tools`."""
    return [{"type": "function",
             "function": {"name": t["name"], "description": t["description"],
                          "parameters": t["input_schema"]}}
            for t in tools]


def to_openai_messages(system: str, messages: list[dict]) -> list[dict]:
    """Our messages -> Chat Completions messages.

    Differences handled here: the system prompt is a message; tool calls
    live in `tool_calls` with JSON-string arguments; each tool result is its
    own message with role "tool". OpenAI has no is_error flag, so errors are
    marked in the text.
    """
    out = [{"role": "system", "content": system}] if system else []
    for message in messages:
        blocks = blocks_of(message)
        texts = [b["text"] for b in blocks if b["type"] == "text"]
        if message["role"] == "assistant":
            calls = [{"id": b["id"], "type": "function",
                      "function": {"name": b["name"], "arguments": json.dumps(b["input"])}}
                     for b in blocks if b["type"] == "tool_use"]
            entry = {"role": "assistant", "content": "\n".join(texts) or None}
            if calls:
                entry["tool_calls"] = calls
            out.append(entry)
            continue
        for b in blocks:
            if b["type"] == "tool_result":
                prefix = "ERROR: " if b.get("is_error") else ""
                out.append({"role": "tool", "tool_call_id": b["tool_use_id"],
                            "content": prefix + _result_text(b["content"])})
        if texts:
            out.append({"role": "user", "content": "\n".join(texts)})
    return out


def _result_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(b.get("text", "") for b in content)


OPENAI_STOP_REASONS = {"stop": "end_turn", "tool_calls": "tool_use",
                       "length": "max_tokens", "function_call": "tool_use"}


def from_openai_response(response: Any) -> ModelResponse:
    """Convert a Chat Completions response (SDK object or dict) to a ModelResponse.

    Arguments that are not valid JSON are passed on as {"_invalid_json": raw}
    so that schema validation fails and the model is told to fix them.
    """
    choice = _get(response, "choices")[0]
    message = _get(choice, "message")
    content = []
    if _get(message, "content"):
        content.append(text_block(_get(message, "content")))
    elif _get(message, "refusal"):  # a refusal has content=None and the reason here
        content.append(text_block(_get(message, "refusal")))
    for tc in _get(message, "tool_calls") or []:
        fn = _get(tc, "function")
        raw = _get(fn, "arguments") or "{}"
        try:
            args = json.loads(raw)
        except json.JSONDecodeError:
            args = {"_invalid_json": raw}
        content.append(tool_use_block(_get(tc, "id"), _get(fn, "name"), args))
    stop = OPENAI_STOP_REASONS.get(_get(choice, "finish_reason"), "end_turn")
    if any(b["type"] == "tool_use" for b in content):
        stop = "tool_use"
    usage = _get(response, "usage")
    return ModelResponse(content, stop,
                         Usage(_get(usage, "prompt_tokens", 0) or 0,
                               _get(usage, "completion_tokens", 0) or 0),
                         _get(response, "model", ""))
