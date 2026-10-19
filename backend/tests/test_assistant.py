"""End-to-end conversation tests with a scripted LLM: no network, fully deterministic."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from jarvis.assistant import Assistant, format_time_note
from jarvis.config import JarvisConfig
from jarvis.cost import CostTracker
from jarvis.events import AssistantState, EventBus
from jarvis.llm.types import LLMError, ToolCall, Usage
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore
from jarvis.search.base import SearchResult, SearchSource
from jarvis.state import StateMachine
from jarvis.tools.registry import build_default_registry
from tests.fakes import (
    EventRecorder,
    FakeBrowser,
    FakeConfirmer,
    FakeLauncher,
    FakeLLMClient,
    FakeSearch,
    ScriptedTurn,
)

S = AssistantState


class Harness:
    def __init__(
        self,
        config: JarvisConfig,
        turns: list[ScriptedTurn],
        *,
        memory: MemoryService | None = None,
    ) -> None:
        self.bus = EventBus()
        self.events = EventRecorder()
        self.bus.subscribe(self.events)
        self.launcher = FakeLauncher()
        self.llm = FakeLLMClient(turns)
        search = FakeSearch(
            SearchResult(
                summary="Mumbai is 31 degrees and sunny.",
                sources=(SearchSource(title="Weather Mumbai", url="https://w.example/mumbai"),),
            )
        )
        registry = build_default_registry(
            config, launcher=self.launcher, browser=FakeBrowser(), search=search, memory=memory
        )
        self.cost = CostTracker(config.pricing, config.budget)
        self.assistant = Assistant(
            config,
            llm=self.llm,
            registry=registry,
            bus=self.bus,
            state=StateMachine(self.bus),
            confirmer=FakeConfirmer(True),
            memory=memory,
            cost=self.cost,
        )

    def states(self) -> list[AssistantState]:
        return [e.state for e in self.events.of_type("state_changed")]

    def final_replies(self) -> list[str]:
        return [
            e.text for e in self.events.of_type("transcript") if e.role == "assistant" and e.final
        ]


async def test_plain_answer_streams_and_returns_to_idle(config: JarvisConfig) -> None:
    h = Harness(config, [ScriptedTurn(chunks=["Good evening, ", "Tony."])])

    await h.assistant.handle_text("hello jarvis")

    assert h.states() == [S.THINKING, S.SPEAKING, S.IDLE]
    partials = [e.text for e in h.events.of_type("transcript") if not e.final]
    assert partials == ["Good evening, ", "Good evening, Tony."]
    assert h.final_replies() == ["Good evening, Tony."]
    assert h.llm.requests[0].route_name == "routine"


async def test_open_app_turn_narrates_runs_tool_and_reports(config: JarvisConfig) -> None:
    h = Harness(
        config,
        [
            ScriptedTurn(
                tool_calls=[ToolCall(id="t1", name="open_app", input={"app_name": "vs code"})]
            ),
            ScriptedTurn(chunks=["VS Code is open, Tony."]),
        ],
    )

    await h.assistant.handle_text("open vs code")

    assert [s.target for s in h.launcher.launched] == ["code.exe"]
    types = [getattr(e, "type", "") for e in h.events.events]
    assert types.index("tool_started") < types.index("tool_finished")
    started = h.events.of_type("tool_started")[0]
    assert started.summary == "Opening VS Code."
    assert h.final_replies() == ["VS Code is open, Tony."]

    # The second request carries the tool result for the first request's tool call.
    second = h.llm.requests[1].messages
    assert second[-1]["role"] == "user"
    [result] = second[-1]["content"]
    assert result["tool_use_id"] == "t1" and "Opened VS Code" in result["content"]
    assert "is_error" not in result


async def test_coding_turn_uses_coding_route_and_shows_code(config: JarvisConfig) -> None:
    code = "def binary_search(xs, x):\n    ..."
    h = Harness(
        config,
        [
            ScriptedTurn(
                chunks=["This halves the search range each step, so it runs in log n time."],
                tool_calls=[
                    ToolCall(
                        id="c1",
                        name="show_code",
                        input={"title": "Binary search", "language": "python", "code": code},
                    )
                ],
            ),
            ScriptedTurn(chunks=["The code is on your screen."]),
        ],
    )

    await h.assistant.handle_text("write a python function for binary search")

    assert h.llm.requests[0].route_name == "coding"
    [block] = h.events.of_type("code_block")
    assert block.code == code
    # explanation -> tool starts -> narration line -> tool runs -> final reply -> done
    assert h.states() == [
        S.THINKING,
        S.SPEAKING,
        S.THINKING,
        S.SPEAKING,
        S.THINKING,
        S.SPEAKING,
        S.IDLE,
    ]


async def test_search_turn_publishes_sources(config: JarvisConfig) -> None:
    h = Harness(
        config,
        [
            ScriptedTurn(
                tool_calls=[ToolCall(id="s1", name="web_search", input={"query": "weather Mumbai"})]
            ),
            ScriptedTurn(chunks=["It's 31 degrees and sunny in Mumbai."]),
        ],
    )

    await h.assistant.handle_text("what is the weather in Mumbai")

    assert h.events.of_type("tool_started")[0].summary == "Searching for that now."
    assert h.events.of_type("sources")[0].items[0].title == "Weather Mumbai"


async def test_llm_failure_is_spoken_not_raised(config: JarvisConfig) -> None:
    h = Harness(config, [ScriptedTurn(error=LLMError("network"))])

    await h.assistant.handle_text("hello")

    [error] = h.events.of_type("error")
    assert error.code == "llm_network"
    assert h.final_replies() == ["I could not reach my brain right now, Tony."]
    assert h.states() == [S.THINKING, S.ERROR, S.SPEAKING, S.IDLE]


async def test_hindi_failure_message_is_hindi(config: JarvisConfig) -> None:
    h = Harness(config, [ScriptedTurn(error=LLMError("network"))])
    await h.assistant.handle_text("नमस्ते जार्विस")
    assert h.final_replies() == [config.messages.api_unreachable.hi]


async def test_refusal_is_spoken_and_not_kept_in_history(config: JarvisConfig) -> None:
    h = Harness(
        config,
        [ScriptedTurn(stop_reason="refusal"), ScriptedTurn(chunks=["Sure."])],
    )

    await h.assistant.handle_text("something declined")
    await h.assistant.handle_text("something fine")

    assert h.final_replies()[0] == config.messages.refusal.en
    roles = [m["role"] for m in h.llm.requests[1].messages]
    # The refused reply was not appended (its unanswered note was dropped too).
    assert roles == ["user", "user", "system"]


async def test_tool_round_limit_stops_runaway_loops(config: JarvisConfig) -> None:
    looping = ScriptedTurn(tool_calls=[ToolCall(id="x", name="open_url", input={"url": "a.com"})])
    limit = config.llm.max_tool_rounds
    h = Harness(config, [looping] * (limit + 1))

    await h.assistant.handle_text("keep opening things")

    assert len(h.llm.requests) == limit + 1
    assert h.final_replies()[-1] == config.messages.internal_error.en
    assert h.states()[-1] == S.IDLE


async def test_prefix_is_stable_across_turns_for_caching(config: JarvisConfig) -> None:
    h = Harness(config, [ScriptedTurn(chunks=["One."]), ScriptedTurn(chunks=["Two."])])

    await h.assistant.handle_text("first")
    await h.assistant.handle_text("second")

    first, second = h.llm.requests
    assert first.system == second.system and first.tools == second.tools
    # Append-only: the second request starts with exactly the first request's messages.
    assert second.messages[: len(first.messages)] == first.messages


# -- Phase 4: memory notes, per-turn budget, cost -------------------------------------


def _memory(config: JarvisConfig, tmp_path: Path) -> tuple[MemoryService, MemoryStore]:
    store = MemoryStore(tmp_path / "memory.db", max_facts=50, max_fact_chars=300)
    service = MemoryService(
        config.memory,
        store,
        None,
        user_name="Tony",
        assistant_name="JARVIS",
        history_max_turns=config.llm.history_max_turns,
    )
    return service, store


async def test_relevant_memory_is_added_as_system_note_after_user_turn(
    config: JarvisConfig, tmp_path: Path
) -> None:
    memory, store = _memory(config, tmp_path)
    store.add_fact(
        "Tony's sister is called Meera.", category="other", keywords=(), source="explicit"
    )
    h = Harness(
        config, [ScriptedTurn(chunks=["Meera."]), ScriptedTurn(chunks=["Hi."])], memory=memory
    )

    await h.assistant.handle_text("what's my sister called?")
    await h.assistant.handle_text("hello")
    store.close()

    first, second = h.llm.requests
    assert [m["role"] for m in first.messages] == ["user", "system"]
    assert "Tony's sister is called Meera." in first.messages[1]["content"]
    # The system prompt never changes, and the note stays put (append-only history).
    assert first.system == second.system
    assert second.messages[:2] == first.messages
    roles = [m["role"] for m in second.messages]
    assert roles == ["user", "system", "assistant", "user", "system"]
    # The sister fact was already sent: the second note carries only the time.
    assert "Memory notes" not in second.messages[4]["content"]


async def test_turn_without_relevant_memory_has_only_the_time(
    config: JarvisConfig, tmp_path: Path
) -> None:
    memory, store = _memory(config, tmp_path)
    h = Harness(config, [ScriptedTurn(chunks=["Done."])], memory=memory)
    await h.assistant.handle_text("open chrome")
    store.close()
    _user, note = h.llm.requests[0].messages
    assert note["role"] == "system"
    assert note["content"].startswith("Current local date and time:")
    assert "Memory notes" not in note["content"]


def test_time_note_format() -> None:
    now = datetime(2026, 10, 19, 14, 5, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    assert format_time_note(now) == (
        "Current local date and time: Monday, 19 October 2026, 14:05 (UTC+05:30)."
    )


async def test_time_note_can_be_switched_off(config: JarvisConfig) -> None:
    off = config.model_copy(update={"llm": config.llm.model_copy(update={"time_note": False})})
    h = Harness(off, [ScriptedTurn(chunks=["Hi."])])
    await h.assistant.handle_text("hello")
    assert [m["role"] for m in h.llm.requests[0].messages] == ["user"]


def _with_cap(config: JarvisConfig, cap: float) -> JarvisConfig:
    return config.model_copy(
        update={"budget": config.budget.model_copy(update={"session_cap_usd": cap})}
    )


async def test_budget_cap_blocks_calls_that_could_cross_it(config: JarvisConfig) -> None:
    # Haiku worst case for one call: 1024 output tokens x $0.50/M = ~$0.0005, plus input.
    h = Harness(_with_cap(config, 0.0001), [ScriptedTurn(chunks=["never"])])
    await h.assistant.handle_text("hello")
    assert h.llm.requests == []  # no call was made
    assert h.final_replies() == [
        "I've reached this session's 0.00 dollar spending limit, Tony. Restart me to reset it."
    ]


async def test_budget_warning_is_spoken_once(config: JarvisConfig) -> None:
    expensive = Usage(input_tokens=0, output_tokens=8000)  # 8000 x $0.50/M = $0.004
    turns = [ScriptedTurn(chunks=["One."], usage=expensive), ScriptedTurn(chunks=["Two."])]
    h = Harness(_with_cap(config, 0.0045), turns)
    await h.assistant.handle_text("first")
    warning = "Heads up, Tony: this session has used 0.00 of its 0.00 dollar budget."
    assert h.final_replies() == ["One.", warning]
    # Spent $0.004 of $0.0045: the next Haiku worst case (~$0.001) no longer fits.
    await h.assistant.handle_text("second")
    assert len(h.llm.requests) == 1
    assert h.final_replies()[-1].startswith("I've reached this session's")


async def test_usage_event_reports_model_and_spend(config: JarvisConfig) -> None:
    h = Harness(config, [ScriptedTurn(chunks=["Hi."])])
    await h.assistant.handle_text("hello")
    [usage] = h.events.of_type("usage")
    assert usage.model == "claude-haiku-5-5" and usage.route == "routine"
    assert usage.cap_usd == config.budget.session_cap_usd


async def test_output_budget_caps_max_tokens_and_stops_the_turn(config: JarvisConfig) -> None:
    small = config.model_copy(
        update={
            "llm": config.llm.model_copy(
                update={"max_output_tokens_per_turn": 1500, "min_tokens_per_round": 256}
            )
        }
    )
    tool_round = ScriptedTurn(
        tool_calls=[ToolCall(id="x", name="open_url", input={"url": "a.com"})],
        usage=Usage(output_tokens=1300),
    )
    h = Harness(small, [tool_round, ScriptedTurn(chunks=["never"])])

    await h.assistant.handle_text("keep going")

    assert len(h.llm.requests) == 1  # 200 tokens left < 256: no second round
    assert h.llm.requests[0].route.max_tokens == min(config.llm.routes["routine"].max_tokens, 1500)
    assert h.final_replies()[-1] == config.messages.turn_budget_exhausted.en


async def test_each_call_is_priced_into_the_session(config: JarvisConfig) -> None:
    h = Harness(
        config,
        [ScriptedTurn(chunks=["Hi."], usage=Usage(input_tokens=1_000_000, output_tokens=0))],
    )
    await h.assistant.handle_text("hello")
    assert h.cost.session_usd == config.pricing.models["claude-haiku-5-5"].input
