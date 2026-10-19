"""Anthropic implementation of LLMClient: streaming, prompt caching, refusal fallbacks.

Request layout (prompt caching is a prefix match over tools -> system -> messages):
* tools are sorted by the registry, so their bytes never change between turns;
* the system prompt carries a cache breakpoint, caching tools + persona together;
* top-level automatic caching adds a moving breakpoint on the latest message,
  so the growing conversation history is cached too.

Memory notes travel as mid-conversation ``role: "system"`` messages. The docs
list them for every model in config.yaml, but if the API ever rejects them
(another model swapped in, a platform without the feature) the client falls
back for the rest of the session to folding each note into the user turn
before it, as a tagged text block. Same content, same caching shape.

The SDK client is created with OpenTelemetry tracing and trace propagation
switched off. Nothing is exported without an OpenTelemetry SDK installed
anyway, but switching it off makes the guarantee explicit.
"""

from __future__ import annotations

import logging
from typing import Any

import anthropic

from jarvis.config import LLMConfig
from jarvis.llm.types import LLMError, LLMRequest, LLMResponse, TextCallback, ToolCall, Usage

logger = logging.getLogger(__name__)

# Beta header that enables `fallbacks: "default"` (server-side refusal fallback).
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Eager tool-input streaming skips server-side JSON validation; if the SDK cannot
# parse a tool input at all it raises ValueError and the turn is re-issued.
MAX_INVALID_JSON_RETRIES = 2
CONNECT_TIMEOUT_S = 10.0
NOTE_OPEN = "<system_note>"
NOTE_CLOSE = "</system_note>"


def create_async_client(api_key: str, config: LLMConfig) -> anthropic.AsyncAnthropic:
    """Build the one SDK client the whole process shares."""
    return anthropic.AsyncAnthropic(
        api_key=api_key,
        timeout=anthropic.Timeout(config.request_timeout_s, connect=CONNECT_TIMEOUT_S),
        max_retries=config.max_retries,
        open_telemetry={"traces": False, "propagation": False},
    )


def inline_system_notes(messages: list[Any]) -> list[Any]:
    """Fold each ``role: "system"`` note into the user message before it.

    Pure and deterministic, so every request in fallback mode renders the
    history identically and prompt caching still hits.
    """
    result: list[Any] = []
    for message in messages:
        if message["role"] != "system":
            result.append(message)
            continue
        note = {"type": "text", "text": f"{NOTE_OPEN}\n{message['content']}\n{NOTE_CLOSE}"}
        previous = result[-1]
        content = previous["content"]
        blocks = [{"type": "text", "text": content}] if isinstance(content, str) else list(content)
        result[-1] = {"role": previous["role"], "content": [*blocks, note]}
    return result


def _has_system_notes(messages: list[Any]) -> bool:
    return any(message["role"] == "system" for message in messages)


def _is_system_role_rejection(exc: anthropic.BadRequestError) -> bool:
    message = exc.message.casefold()
    return "role" in message and "system" in message


def build_request_params(
    request: LLMRequest, *, system_notes_supported: bool = True
) -> dict[str, Any]:
    """Translate a provider-neutral request into Messages API parameters.

    Kept as a pure function so tests can check caching and routing settings
    without a network call.
    """
    route = request.route
    messages = request.messages if system_notes_supported else inline_system_notes(request.messages)
    params: dict[str, Any] = {
        "model": route.model,
        "max_tokens": route.max_tokens,
        "system": [
            {"type": "text", "text": request.system, "cache_control": {"type": "ephemeral"}}
        ],
        "tools": request.tools,
        "messages": messages,
        "thinking": {"type": route.thinking},
        "output_config": {"effort": route.effort},
        "cache_control": {"type": "ephemeral"},
    }
    if route.fallbacks is not None:
        params["fallbacks"] = route.fallbacks
        params["betas"] = [FALLBACK_BETA]
    return params


def _map_api_error(exc: anthropic.APIError) -> LLMError:
    """Turn SDK exceptions into a small set of codes, most specific first."""
    if isinstance(exc, anthropic.APIConnectionError):  # includes APITimeoutError
        return LLMError("network", type(exc).__name__)
    if isinstance(exc, anthropic.AuthenticationError | anthropic.PermissionDeniedError):
        return LLMError("auth", "the API key was rejected")
    if isinstance(exc, anthropic.RateLimitError):
        return LLMError("rate_limited")
    if isinstance(exc, anthropic.NotFoundError):
        return LLMError("bad_model", "check the model names in config.yaml")
    if isinstance(exc, anthropic.BadRequestError):
        return LLMError("bad_request", exc.message)
    if isinstance(exc, anthropic.APIStatusError):
        return LLMError("server", str(exc.status_code))
    return LLMError("unknown", type(exc).__name__)


class AnthropicLLMClient:
    """LLMClient backed by the Anthropic Messages API (beta namespace for fallbacks)."""

    def __init__(self, client: anthropic.AsyncAnthropic) -> None:
        self._client = client
        self._system_notes_supported = True

    async def respond(self, request: LLMRequest, on_text: TextCallback) -> LLMResponse:
        try:
            return await self._respond(request, on_text)
        except anthropic.BadRequestError as exc:
            if not (
                self._system_notes_supported
                and _has_system_notes(request.messages)
                and _is_system_role_rejection(exc)
            ):
                raise _map_api_error(exc) from exc
            logger.warning("system notes rejected; folding them into user turns from now on")
            self._system_notes_supported = False
        try:
            return await self._respond(request, on_text)
        except anthropic.APIError as exc:
            raise _map_api_error(exc) from exc

    async def _respond(self, request: LLMRequest, on_text: TextCallback) -> LLMResponse:
        """Stream one response; BadRequestError propagates so respond() can fall back."""
        params = build_request_params(request, system_notes_supported=self._system_notes_supported)
        for attempt in range(MAX_INVALID_JSON_RETRIES + 1):
            try:
                return await self._stream_once(params, on_text)
            except ValueError as exc:
                logger.warning("unparseable tool input, retrying", extra={"attempt": attempt})
                if attempt == MAX_INVALID_JSON_RETRIES:
                    raise LLMError("bad_output", "tool input was not valid JSON") from exc
            except anthropic.BadRequestError:
                raise
            except anthropic.APIError as exc:
                raise _map_api_error(exc) from exc
        raise AssertionError("unreachable")  # pragma: no cover

    async def _stream_once(self, params: dict[str, Any], on_text: TextCallback) -> LLMResponse:
        async with self._client.beta.messages.stream(**params) as stream:
            async for event in stream:
                if event.type == "text":
                    await on_text(event.text)
            message = await stream.get_final_message()

        tool_calls = [
            ToolCall(id=block.id, name=block.name, input=block.input)
            for block in message.content
            if block.type == "tool_use"
        ]
        text = "".join(block.text for block in message.content if block.type == "text")
        usage = message.usage
        return LLMResponse(
            stop_reason=str(message.stop_reason),
            text=text,
            tool_calls=tool_calls,
            assistant_content=list(message.content),
            model=message.model,
            usage=Usage(
                input_tokens=usage.input_tokens,
                output_tokens=usage.output_tokens,
                cache_creation_input_tokens=usage.cache_creation_input_tokens or 0,
                cache_read_input_tokens=usage.cache_read_input_tokens or 0,
            ),
        )
