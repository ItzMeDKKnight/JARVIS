"""Screen vision mode: off by default, enforced in the backend, instant off, safe actions."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.assistant import Assistant
from jarvis.config import JarvisConfig
from jarvis.cost import CostTracker
from jarvis.events import EventBus, Interrupt, VisionSet
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore
from jarvis.os_adapters.screen import Frame, UnknownKeyError, classify_keys, parse_keys
from jarvis.state import StateMachine
from jarvis.tools.base import ToolContext
from jarvis.tools.registry import build_default_registry
from jarvis.tools.screen_tools import (
    LookAtScreenTool,
    LookInput,
    SetScreenVisionTool,
    SetVisionInput,
)
from jarvis.vision.agent import ACT_MEMBERS, ALL_MEMBERS, LOOK_MEMBERS, ScreenAgent, toolset
from jarvis.vision.gate import ScreenAccess, ScreenGate, ScreenVisionOffError
from tests.fakes import (
    EventRecorder,
    FakeBrowser,
    FakeConfirmer,
    FakeLauncher,
    FakeLLMClient,
    FakeSearch,
)
from tests.test_hud import Rig as HudRig


class FakeScreen:
    """Records what it was asked to do; never touches the real screen."""

    def __init__(self, *, password: bool = False) -> None:
        self.captures = 0
        self.actions: list[tuple[Any, ...]] = []
        self.password = password

    def capture(self, max_long_edge: int, region: Any = None) -> Frame:
        self.captures += 1
        return Frame(data=b"\xff\xd8fakejpeg", width=1000, height=500, scale=2.0)

    def screen_size(self) -> tuple[int, int]:
        return (2000, 1000)

    def click(self, x: int, y: int, button: str, count: int) -> None:
        self.actions.append(("click", x, y, button, count))

    def move(self, x: int, y: int) -> None:
        self.actions.append(("move", x, y))

    def scroll(self, x: Any, y: Any, direction: str, amount: int) -> None:
        self.actions.append(("scroll", direction, amount))

    def type_text(self, text: str) -> None:
        self.actions.append(("type", text))

    def press(self, keys: tuple[str, ...], repeat: int) -> None:
        self.actions.append(("key", keys))

    def focused_is_password(self) -> bool | None:
        return self.password


# -- the gate: off by default, enforced in the backend ----------------------------------


async def test_capture_fails_when_the_mode_is_off() -> None:
    """The guarantee: with the mode off, no code path can capture the screen."""
    gate = ScreenGate(EventBus())
    screen = FakeScreen()
    access = ScreenAccess(gate, screen, screen)
    assert not gate.enabled  # off at every start
    with pytest.raises(ScreenVisionOffError):
        await access.capture(gate.generation, 1366)
    with pytest.raises(ScreenVisionOffError):
        await access.click(gate.generation, 1, 1, "left", 1)
    assert screen.captures == 0 and screen.actions == []


async def test_switching_off_invalidates_work_started_before() -> None:
    gate = ScreenGate(EventBus())
    screen = FakeScreen()
    access = ScreenAccess(gate, screen, screen)
    await gate.enable(source="test")
    generation = gate.check()
    await gate.disable(source="test")
    await gate.enable(source="test")  # on again: the old session's generation is still dead
    with pytest.raises(ScreenVisionOffError):
        await access.capture(generation, 1366)
    assert screen.captures == 0


async def test_switching_off_cancels_the_in_flight_request() -> None:
    gate = ScreenGate(EventBus())
    await gate.enable(source="test")
    started = asyncio.Event()

    async def vision_request() -> None:
        with gate.session():
            started.set()
            await asyncio.Event().wait()  # e.g. a Claude call carrying a screenshot

    task = asyncio.create_task(vision_request())
    await started.wait()
    await gate.disable(source="test")
    with pytest.raises(asyncio.CancelledError):
        await task


async def test_mode_changes_are_published(bus_and_events: tuple[EventBus, EventRecorder]) -> None:
    bus, events = bus_and_events
    gate = ScreenGate(bus)
    assert await gate.enable(source="t") and not await gate.enable(source="t")
    assert await gate.disable(source="t") and not await gate.disable(source="t")
    assert [e.enabled for e in events.of_type("vision")] == [True, False]


# -- keys ----------------------------------------------------------------------------------


def test_key_names_are_normalised_and_unknown_ones_refused() -> None:
    assert parse_keys("ctrl+Shift+T") == ("ctrl", "shift", "t")
    assert parse_keys("Return") == ("enter",)
    assert parse_keys("super+r") == ("win", "r")
    with pytest.raises(UnknownKeyError):
        parse_keys("ctrl+launch_missiles")


@pytest.mark.parametrize(
    ("spec", "verdict"),
    [
        ("space", "ok"),
        ("ctrl+f", "ok"),
        ("Return", "ok"),
        ("super+r", "blocked"),
        ("win", "blocked"),
        ("ctrl+alt+Delete", "blocked"),
        ("ctrl+shift+Escape", "blocked"),
        ("alt+F4", "confirm"),
        ("Delete", "confirm"),
        ("ctrl+w", "confirm"),
    ],
)
def test_dangerous_key_combinations(spec: str, verdict: str) -> None:
    assert classify_keys(parse_keys(spec)) == verdict


def test_look_mode_exposes_only_screenshot_and_zoom() -> None:
    disabled = set(toolset("look")["configs"])
    assert set(ALL_MEMBERS) - disabled == LOOK_MEMBERS
    act_enabled = set(ALL_MEMBERS) - set(toolset("act")["configs"])
    assert act_enabled == ACT_MEMBERS
    assert "left_click_drag" not in act_enabled and "hold_key" not in act_enabled


# -- the agent loop with a scripted model ---------------------------------------------------


def _tool_use(name: str, call_id: str, **args: Any) -> Any:
    from anthropic.types import ToolUseBlock

    return ToolUseBlock(type="tool_use", id=call_id, name=name, input=args)


def _text(text: str) -> Any:
    return SimpleNamespace(type="text", text=text)


class ScriptedClient:
    """Plays back responses; records the requests (tools, messages) it was sent."""

    def __init__(self, responses: list[list[Any]], gate_to_close: ScreenGate | None = None):
        self._responses = list(responses)
        self.requests: list[dict[str, Any]] = []
        self.messages = self
        self._gate = gate_to_close

    def with_options(self, **_: Any) -> ScriptedClient:
        return self

    async def create(self, **kwargs: Any) -> Any:
        # Snapshot: the agent clears its message list when it ends (releasing the frames).
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        self.live_messages = kwargs["messages"]
        content = self._responses.pop(0)
        stop = (
            "tool_use" if any(getattr(b, "type", "") == "tool_use" for b in content) else "end_turn"
        )
        usage = SimpleNamespace(
            input_tokens=100,
            output_tokens=20,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=0,
        )
        return SimpleNamespace(
            content=content, stop_reason=stop, usage=usage, model=kwargs["model"]
        )


async def _agent(
    config: JarvisConfig,
    responses: list[list[Any]],
    screen: FakeScreen,
    *,
    cost: CostTracker | None = None,
) -> tuple[ScreenAgent, ScreenGate, ScriptedClient]:
    gate = ScreenGate(EventBus())
    await gate.enable(source="test")
    client = ScriptedClient(responses)
    agent = ScreenAgent(
        client,  # type: ignore[arg-type]
        config.vision.model_copy(update={"settle_ms": 0}),
        ScreenAccess(gate, screen, screen),
        user_name="Tony",
        cost=cost,
        risk_labels=config.vision.risk_labels,
    )
    return agent, gate, client


async def _noop_narrate(step: str, target: str) -> None:
    return None


async def test_look_answers_from_a_screenshot_and_never_acts(config: JarvisConfig) -> None:
    screen = FakeScreen()
    agent, _, client = await _agent(
        config,
        [[_tool_use("left_click", "c1", coordinate=[10, 10])], [_text("A YouTube video is open.")]],
        screen,
    )
    said: list[str] = []

    async def confirm(category: str, target: str) -> bool:
        return True

    result = await agent.run(
        "look",
        task="what is open?",
        request="what's on my screen",
        language="en",
        narrate=_noop_narrate,
        confirm=confirm,
    )
    assert result.answer == "A YouTube video is open."
    assert screen.actions == []  # the click was refused in look mode
    refused = client.requests[1]["messages"][-1]["content"][0]
    assert refused["is_error"] and "not allowed in look mode" in refused["content"][0]["text"]
    assert refused["toolset_name"] == "computer"
    assert client.requests[0]["model"] == config.vision.model
    assert said == []


async def test_act_clicks_in_screen_pixels_and_narrates(config: JarvisConfig) -> None:
    screen = FakeScreen()
    agent, _, _ = await _agent(
        config,
        [
            [_tool_use("left_click", "c1", coordinate=[340, 165])],
            [_tool_use("key", "c2", text="space"), _tool_use("key", "c3", text="super+r")],
            [_text("The video is playing.")],
        ],
        screen,
    )
    lines: list[str] = []

    async def narrate(step: str, target: str) -> None:
        lines.append(step)

    async def confirm(category: str, target: str) -> bool:
        raise AssertionError("nothing risky here")

    result = await agent.run(
        "act",
        task="play the video",
        request="play the video that's open",
        language="en",
        narrate=narrate,
        confirm=confirm,
    )
    # Screenshot pixels x 2.0 scale = screen pixels; Win+R was refused, not pressed.
    assert screen.actions == [("click", 680, 330, "left", 1), ("key", ("space",))]
    assert lines == ["screen_click", "screen_key"]
    assert result.answer == "The video is playing." and result.actions == 2


async def test_risky_steps_need_a_spoken_yes(config: JarvisConfig) -> None:
    screen = FakeScreen(password=True)
    agent, _, client = await _agent(
        config,
        [
            [
                _tool_use(
                    "confirm_risky_action",
                    "r1",
                    category="send_message",
                    description="send it to Rahul",
                )
            ],
            [_tool_use("type", "t1", text="hunter2")],
            [_text("I stopped: you said no.")],
        ],
        screen,
    )
    asked: list[tuple[str, str]] = []

    async def confirm(category: str, target: str) -> bool:
        asked.append((category, target))
        return False

    await agent.run(
        "act",
        task="send the message",
        request="send this message to Rahul",
        language="en",
        narrate=_noop_narrate,
        confirm=confirm,
    )
    assert asked == [
        ("send_message", "send it to Rahul"),
        ("password_field", "type into a password field"),
    ]
    assert screen.actions == []  # declined twice: nothing typed, nothing sent
    first_result = client.requests[1]["messages"][-1]["content"][0]
    assert first_result["content"][0]["text"].startswith("declined")


async def test_switching_off_mid_run_stops_and_releases_frames(config: JarvisConfig) -> None:
    screen = FakeScreen()
    agent, gate, _ = await _agent(
        config, [[_tool_use("screenshot", "s1")], [_text("never reached")]], screen
    )

    async def confirm(category: str, target: str) -> bool:
        return True

    async def narrate(step: str, target: str) -> None:
        return None

    original = screen.capture

    def capture_then_switch_off(max_long_edge: int, region: Any = None) -> Frame:
        frame = original(max_long_edge, region)
        if screen.captures == 2:
            gate._enabled = False  # what disable() does first, mid-capture
        return frame

    screen.capture = capture_then_switch_off  # type: ignore[method-assign]
    with pytest.raises(ScreenVisionOffError):
        await agent.run(
            "look", task="look", request="look", language="en", narrate=narrate, confirm=confirm
        )


async def test_every_request_is_budget_checked(config: JarvisConfig) -> None:
    budget = config.budget.model_copy(update={"session_cap_usd": 0.001})
    cost = CostTracker(config.pricing, budget)
    screen = FakeScreen()
    agent, _, client = await _agent(config, [[_text("x")]], screen, cost=cost)

    async def confirm(category: str, target: str) -> bool:
        return True

    from jarvis.vision.agent import ScreenAgentError

    with pytest.raises(ScreenAgentError, match="spending limit"):
        await agent.run(
            "look", task="t", request="r", language="en", narrate=_noop_narrate, confirm=confirm
        )
    assert client.requests == []  # refused before sending the screenshot


# -- tools, voice commands, HUD ---------------------------------------------------------


def _ctx(confirm: bool = True) -> tuple[ToolContext, EventRecorder]:
    bus, events = EventBus(), EventRecorder()
    bus.subscribe(events)
    return ToolContext(bus=bus, confirmer=FakeConfirmer(confirm), language="en"), events


async def test_screen_tools_refuse_while_off(config: JarvisConfig) -> None:
    screen = FakeScreen()
    agent, gate, client = await _agent(config, [], screen)
    await gate.disable(source="test")
    tool = LookAtScreenTool(
        agent,
        gate,
        narration=config.narration,
        confirm_question=config.messages.confirm_screen_action,
        is_off_line=config.messages.screen_vision_is_off,
        confirm_timeout_s=5,
    )
    ctx, _ = _ctx()
    outcome = await tool.run(LookInput(question="what's on screen?"), ctx)
    assert not outcome.ok and "OFF" in outcome.content
    assert screen.captures == 0 and client.requests == []


async def test_turning_on_by_voice_asks_first(config: JarvisConfig) -> None:
    gate = ScreenGate(EventBus())
    tool = SetScreenVisionTool(
        gate,
        confirm_question=config.messages.confirm_screen_vision_on,
        on_line=config.messages.screen_vision_on,
        off_line=config.messages.screen_vision_off,
        confirm_timeout_s=5,
    )
    no_ctx, _ = _ctx(confirm=False)
    await tool.run(SetVisionInput(enabled=True), no_ctx)
    assert not gate.enabled
    yes_ctx, _ = _ctx(confirm=True)
    await tool.run(SetVisionInput(enabled=True), yes_ctx)
    assert gate.enabled
    confirmer = FakeConfirmer(True)
    off_ctx = ToolContext(bus=EventBus(), confirmer=confirmer, language="en")
    await tool.run(SetVisionInput(enabled=False), off_ctx)
    assert not gate.enabled and confirmer.questions == []  # off never asks


@pytest.mark.parametrize(
    "said",
    [
        "turn off screen vision",
        "Jarvis, stop looking at my screen please",
        "स्क्रीन विज़न बंद करो",
        "screen vision band karo",
    ],
)
async def test_spoken_off_command_needs_no_claude_call(config: JarvisConfig, said: str) -> None:
    bus = EventBus()
    gate = ScreenGate(bus)
    screen = FakeScreen()
    agent = ScreenAgent(
        SimpleNamespace(), config.vision, ScreenAccess(gate, screen, screen), user_name="T"
    )  # type: ignore[arg-type]
    registry = build_default_registry(
        config,
        launcher=FakeLauncher(),
        browser=FakeBrowser(),
        search=FakeSearch(),
        screen=(agent, gate),
    )
    llm = FakeLLMClient([])  # any call would fail: there are no scripted turns
    assistant = Assistant(
        config,
        llm=llm,
        registry=registry,
        bus=bus,
        state=StateMachine(bus),
        confirmer=FakeConfirmer(True),
    )
    await gate.enable(source="test")
    await assistant.handle_text(said)
    assert not gate.enabled and llm.requests == []


async def test_hud_button_and_stop_end_the_mode(config: JarvisConfig, tmp_path: Path) -> None:
    rig = HudRig(config, tmp_path, [])
    gate = ScreenGate(rig.bus)
    rig.hud._screen_gate = gate
    await rig.hud.handle(VisionSet(enabled=True))
    assert gate.enabled
    snapshot = await rig.hud.snapshot()
    assert any(getattr(e, "type", "") == "vision" and e.enabled for e in snapshot)
    await rig.hud.handle(Interrupt())  # Esc / Stop
    assert not gate.enabled
    assert rig.events.of_type("stopped")[-1].cancelled == ("vision",)
    rig.store.close()


async def test_turns_that_saw_the_screen_are_not_remembered(
    config: JarvisConfig, tmp_path: Path
) -> None:
    from jarvis.events import ToolStarted, Transcript

    store = MemoryStore(tmp_path / "m.db", max_facts=10, max_fact_chars=300)
    memory = MemoryService(
        config.memory, store, None, user_name="Tony", assistant_name="JARVIS", history_max_turns=20
    )
    say = lambda role, text: memory(  # noqa: E731
        Transcript(turn_id=text, role=role, text=text, language="en", final=True)
    )
    await say("user", "I like tea")
    await say("assistant", "Noted.")
    await say("user", "what's in this email on my screen?")
    await memory(ToolStarted(call_id="c", name="look_at_screen", summary=""))
    await say("assistant", "It says your bank PIN is due for renewal.")
    assert [line.text for line in memory._pending] == ["I like tea", "Noted."]
    store.close()


def test_every_risk_category_has_spoken_labels(config: JarvisConfig) -> None:
    from jarvis.vision.agent import RISK_CATEGORIES

    assert set(config.vision.risk_labels) == set(RISK_CATEGORIES)


def test_registry_adds_the_screen_tools(config: JarvisConfig) -> None:
    gate = ScreenGate(EventBus())
    screen = FakeScreen()
    agent = ScreenAgent(
        SimpleNamespace(), config.vision, ScreenAccess(gate, screen, screen), user_name="T"
    )  # type: ignore[arg-type]
    registry = build_default_registry(
        config,
        launcher=FakeLauncher(),
        browser=FakeBrowser(),
        search=FakeSearch(),
        screen=(agent, gate),
    )
    assert {"look_at_screen", "act_on_screen", "set_screen_vision"} <= set(registry.names)
    assert registry.screen_gate is gate
    for name in ("look_at_screen", "act_on_screen", "screen_click", "screen_key"):
        assert name in config.narration


async def test_frames_are_released_when_the_request_ends(config: JarvisConfig) -> None:
    screen = FakeScreen()
    agent, _, client = await _agent(config, [[_text("done")]], screen)

    async def confirm(category: str, target: str) -> bool:
        return True

    await agent.run(
        "look", task="t", request="r", language="en", narrate=_noop_narrate, confirm=confirm
    )
    # The list the agent sent (and that held the screenshot) is empty afterwards.
    assert client.live_messages == []
