"""Request building and response parsing, checked without any network calls."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import anthropic
import httpx2
import pytest

from jarvis.config import JarvisConfig
from jarvis.llm.client import (
    FALLBACK_BETA,
    NOTE_CLOSE,
    NOTE_OPEN,
    AnthropicLLMClient,
    build_request_params,
)
from jarvis.llm.history import ConversationHistory
from jarvis.llm.types import LLMError, LLMRequest, LLMResponse
from jarvis.search.anthropic_search import parse_search_response
from jarvis.search.base import SearchError


def _request(config: JarvisConfig, route_name: str) -> LLMRequest:
    return LLMRequest(
        route_name=route_name,
        route=config.llm.routes[route_name],
        system="persona",
        tools=[{"name": "t"}],
        messages=[{"role": "user", "content": "hi"}],
    )


def test_routine_route_params(config: JarvisConfig) -> None:
    params = build_request_params(_request(config, "routine"))
    assert params["model"] == "claude-haiku-5-5"
    assert params["thinking"] == {"type": "disabled"}
    assert params["output_config"] == {"effort": "low"}
    assert params["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert params["cache_control"] == {"type": "ephemeral"}
    assert "fallbacks" not in params and "betas" not in params


def test_coding_route_enables_refusal_fallback(config: JarvisConfig) -> None:
    params = build_request_params(_request(config, "coding"))
    assert params["model"] == "claude-sonnet-5-5"
    assert params["fallbacks"] == "default"
    assert params["betas"] == [FALLBACK_BETA]


def _text(text: str, citations: list[Any] | None = None) -> Any:
    return SimpleNamespace(type="text", text=text, citations=citations)


def _cite(title: str, url: str) -> Any:
    return SimpleNamespace(type="web_search_result_location", title=title, url=url)


def _results(*pairs: tuple[str, str]) -> Any:
    items = [SimpleNamespace(title=t, url=u) for t, u in pairs]
    return SimpleNamespace(type="web_search_tool_result", content=items)


def test_search_parsing_prefers_cited_sources_and_dedupes() -> None:
    content = [
        SimpleNamespace(type="server_tool_use"),
        _results(("A", "https://a"), ("B", "https://b"), ("C", "https://c")),
        _text("It is sunny", [_cite("C", "https://c")]),
        _text(" today.", [_cite("C", "https://c")]),
    ]
    result = parse_search_response(content, max_sources=2)
    assert result.summary == "It is sunny today."
    assert [s.url for s in result.sources] == ["https://c", "https://a"]


def test_search_error_block_without_text_raises() -> None:
    error = SimpleNamespace(
        type="web_search_tool_result", content=SimpleNamespace(error_code="max_uses_exceeded")
    )
    with pytest.raises(SearchError, match="max_uses_exceeded"):
        parse_search_response([error], max_sources=3)


def test_history_trim_drops_oldest_turns_and_thinking_blocks() -> None:
    history = ConversationHistory(max_turns=2)
    for i in range(3):
        history.add_user_text(f"q{i}")
        history.add_assistant(
            [{"type": "thinking", "thinking": ""}, {"type": "text", "text": f"a{i}"}]
        )

    messages = history.messages
    assert messages[0] == {"role": "user", "content": "q1"}
    # Trimming happened when q2 arrived: the reply kept from before that lost its
    # thinking block; the reply added after the trim keeps its own.
    kept_before_trim, added_after_trim = (m for m in messages if m["role"] == "assistant")
    assert [b["type"] for b in kept_before_trim["content"]] == ["text"]
    assert [b["type"] for b in added_after_trim["content"]] == ["thinking", "text"]


def test_history_never_starts_with_tool_results() -> None:
    history = ConversationHistory(max_turns=1)
    history.add_user_text("open vs code")
    history.add_assistant([{"type": "tool_use", "id": "t", "name": "open_app", "input": {}}])
    history.add_tool_results([{"type": "tool_result", "tool_use_id": "t", "content": "ok"}])
    history.add_assistant([{"type": "text", "text": "done"}])
    history.add_user_text("thanks")
    assert history.messages == [{"role": "user", "content": "thanks"}]


# -- memory notes (mid-conversation system messages) -----------------------------------


def test_history_note_follows_user_turn_and_dangling_note_is_dropped() -> None:
    history = ConversationHistory(max_turns=5)
    history.add_user_text("my sister?")
    history.add_system_note("Things Tony has told you: ...")
    assert [m["role"] for m in history.messages] == ["user", "system"]
    # The turn failed before any reply: the next user turn must not follow the note.
    history.add_user_text("hello?")
    assert [m["role"] for m in history.messages] == ["user", "user"]


def test_note_must_follow_a_user_utterance() -> None:
    history = ConversationHistory(max_turns=5)
    with pytest.raises(ValueError, match="user utterance"):
        history.add_system_note("x")


def test_interrupted_turn_drops_unanswered_note() -> None:
    history = ConversationHistory(max_turns=5)
    history.add_user_text("hi")
    history.add_system_note("note")
    history.close_interrupted_turn()
    assert history.messages == [{"role": "user", "content": "hi"}]


def test_trim_keeps_notes_with_their_turn() -> None:
    history = ConversationHistory(max_turns=1)
    history.add_user_text("q0")
    history.add_system_note("n0")
    history.add_assistant([{"type": "text", "text": "a0"}])
    history.add_user_text("q1")
    history.add_system_note("n1")
    assert [m["content"] for m in history.messages] == ["q1", "n1"]


def test_inline_system_notes_fallback_folds_note_into_user_turn(config: JarvisConfig) -> None:
    request = LLMRequest(
        route_name="routine",
        route=config.llm.routes["routine"],
        system="persona",
        tools=[],
        messages=[
            {"role": "user", "content": "my sister?"},
            {"role": "system", "content": "Sister: Meera"},
        ],
    )
    native = build_request_params(request)
    assert native["messages"][1] == {"role": "system", "content": "Sister: Meera"}

    folded = build_request_params(request, system_notes_supported=False)["messages"]
    assert folded == [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "my sister?"},
                {"type": "text", "text": f"{NOTE_OPEN}\nSister: Meera\n{NOTE_CLOSE}"},
            ],
        }
    ]
    assert request.messages[0] == {"role": "user", "content": "my sister?"}  # not mutated


def _bad_request(message: str) -> anthropic.BadRequestError:
    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(
        message, response=httpx2.Response(400, request=request), body=None
    )


async def test_client_falls_back_once_when_system_role_is_rejected(
    config: JarvisConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    sent: list[list[Any]] = []
    rejected = False

    async def fake_stream(self: Any, params: dict[str, Any], on_text: Any) -> LLMResponse:
        nonlocal rejected
        sent.append(params["messages"])
        if not rejected:
            rejected = True
            raise _bad_request("role 'system' is not supported on this model")
        return LLMResponse(
            stop_reason="end_turn", text="", tool_calls=[], assistant_content=[], model="m"
        )

    monkeypatch.setattr(AnthropicLLMClient, "_stream_once", fake_stream)
    client = AnthropicLLMClient(cast(anthropic.AsyncAnthropic, None))
    request = LLMRequest(
        route_name="routine",
        route=config.llm.routes["routine"],
        system="persona",
        tools=[],
        messages=[{"role": "user", "content": "q"}, {"role": "system", "content": "n"}],
    )

    async def ignore(_: str) -> None:
        return None

    await client.respond(request, ignore)
    await client.respond(request, ignore)
    roles = [[m["role"] for m in messages] for messages in sent]
    assert roles == [["user", "system"], ["user"], ["user"]]


async def test_other_bad_requests_are_not_retried(
    config: JarvisConfig, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls = 0

    async def fake_stream(self: Any, params: dict[str, Any], on_text: Any) -> LLMResponse:
        nonlocal calls
        calls += 1
        raise _bad_request("max_tokens: too large")

    monkeypatch.setattr(AnthropicLLMClient, "_stream_once", fake_stream)
    client = AnthropicLLMClient(cast(anthropic.AsyncAnthropic, None))
    request = LLMRequest(
        route_name="routine",
        route=config.llm.routes["routine"],
        system="persona",
        tools=[],
        messages=[{"role": "user", "content": "q"}, {"role": "system", "content": "n"}],
    )

    async def ignore(_: str) -> None:
        return None

    with pytest.raises(LLMError, match="bad_request"):
        await client.respond(request, ignore)
    assert calls == 1
