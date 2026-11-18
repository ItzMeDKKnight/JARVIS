"""Deterministic fakes for hardware, OS and network boundaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from jarvis.config import LaunchSpec
from jarvis.llm.types import LLMError, LLMRequest, LLMResponse, TextCallback, ToolCall, Usage
from jarvis.os_adapters.base import LaunchError, Shortcut
from jarvis.search.base import SearchError, SearchResult, SearchSource


@dataclass
class ScriptedTurn:
    """One scripted model response: text streamed in chunks, then optional tool calls."""

    chunks: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    stop_reason: str | None = None
    error: LLMError | None = None
    usage: Usage = field(default_factory=lambda: Usage(input_tokens=10, output_tokens=5))


class FakeLLMClient:
    """Replays scripted turns in order and records every request it received."""

    def __init__(self, turns: list[ScriptedTurn]) -> None:
        self._turns = list(turns)
        self.requests: list[LLMRequest] = []

    async def respond(self, request: LLMRequest, on_text: TextCallback) -> LLMResponse:
        # Snapshot the messages: the real API sees them at call time, not later.
        self.requests.append(
            LLMRequest(
                route_name=request.route_name,
                route=request.route,
                system=request.system,
                tools=request.tools,
                messages=list(request.messages),
            )
        )
        turn = self._turns.pop(0)
        if turn.error is not None:
            raise turn.error
        for chunk in turn.chunks:
            await on_text(chunk)
        content: list[dict[str, Any]] = []
        if turn.chunks:
            content.append({"type": "text", "text": "".join(turn.chunks)})
        content.extend(
            {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
            for c in turn.tool_calls
        )
        stop = turn.stop_reason or ("tool_use" if turn.tool_calls else "end_turn")
        return LLMResponse(
            stop_reason=stop,
            text="".join(turn.chunks),
            tool_calls=list(turn.tool_calls),
            assistant_content=content,
            model=request.route.model,
            usage=turn.usage,
        )


class FakeLauncher:
    def __init__(self, shortcuts: list[Shortcut] | None = None, fail: str | None = None) -> None:
        self.launched: list[LaunchSpec] = []
        self.launched_shortcuts: list[Shortcut] = []
        self._shortcuts = shortcuts or []
        self._fail = fail

    def launch(self, spec: LaunchSpec) -> None:
        if self._fail:
            raise LaunchError(self._fail)
        self.launched.append(spec)

    def find_shortcuts(self, query: str, limit: int) -> list[Shortcut]:
        return self._shortcuts[:limit]

    def launch_shortcut(self, shortcut: Shortcut) -> None:
        self.launched_shortcuts.append(shortcut)


class FakeBrowser:
    def __init__(self) -> None:
        self.opened: list[str] = []

    def open(self, url: str) -> None:
        self.opened.append(url)


class FakeConfirmer:
    def __init__(self, answer: bool) -> None:
        self.answer = answer
        self.questions: list[str] = []

    async def confirm(self, question: str) -> bool:
        self.questions.append(question)
        return self.answer


class FakeSearch:
    def __init__(self, result: SearchResult | None = None, error: str | None = None) -> None:
        self._result = result
        self._error = error
        self.queries: list[str] = []

    async def search(self, query: str) -> SearchResult:
        self.queries.append(query)
        if self._error is not None:
            raise SearchError(self._error)
        assert self._result is not None
        return self._result


class FakeVideoFinder:
    """Returns scripted YouTube pages (or raises) and records each query."""

    def __init__(self, sources: tuple[SearchSource, ...] = (), error: str | None = None) -> None:
        self._sources = sources
        self._error = error
        self.queries: list[str] = []

    async def find_videos(self, query: str) -> tuple[SearchSource, ...]:
        self.queries.append(query)
        if self._error is not None:
            raise SearchError(self._error)
        return self._sources


class EventRecorder:
    """Event-bus subscriber that keeps every event for assertions."""

    def __init__(self) -> None:
        self.events: list[BaseModel] = []

    async def __call__(self, event: BaseModel) -> None:
        self.events.append(event)

    def of_type(self, event_type: str) -> list[Any]:
        return [e for e in self.events if getattr(e, "type", None) == event_type]


def shortcut(name: str) -> Shortcut:
    return Shortcut(name=name, path=Path(f"C:/fake/{name}.lnk"))


class FakeConsolidator:
    """Returns scripted consolidation results (or raises) and records each input."""

    def __init__(self, results: list[Any] | None = None) -> None:
        self._results = list(results or [])
        self.calls: list[tuple[str | None, list[Any], list[Any]]] = []

    async def consolidate(self, summary: str | None, facts: list[Any], lines: list[Any]) -> Any:
        self.calls.append((summary, list(facts), list(lines)))
        result = self._results.pop(0)
        if isinstance(result, Exception):
            raise result
        return result
