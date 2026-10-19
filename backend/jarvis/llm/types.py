"""Provider-neutral types between the conversation loop and the LLM client.

The conversation loop (jarvis.assistant) depends only on :class:`LLMClient`,
so tests drive it with a scripted fake and never touch the network.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from jarvis.config import RouteConfig

TextCallback = Callable[[str], Awaitable[None]]


class LLMError(Exception):
    """The model could not be reached or rejected the request.

    ``code`` is a short machine-readable reason (``network``, ``auth``,
    ``rate_limited``, ``server``, ``bad_request``, ``bad_model``,
    ``bad_output``) that is safe to log and to show in the UI.
    """

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class LLMRequest:
    route_name: str
    route: RouteConfig
    system: str
    tools: list[dict[str, Any]]
    messages: list[Any]


@dataclass(frozen=True)
class ToolCall:
    id: str
    name: str
    input: object


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


@dataclass(frozen=True)
class LLMResponse:
    """One completed model response.

    ``assistant_content`` is the provider's content list, kept opaque and
    appended to history unchanged: thinking and fallback blocks must be sent
    back byte-for-byte, so the loop must never rebuild them from ``text``.
    """

    stop_reason: str
    text: str
    tool_calls: list[ToolCall]
    assistant_content: list[Any]
    model: str
    usage: Usage = field(default_factory=Usage)


class LLMClient(Protocol):
    async def respond(self, request: LLMRequest, on_text: TextCallback) -> LLMResponse:
        """Stream one response, calling ``on_text`` with each text fragment as it arrives."""
        ...
