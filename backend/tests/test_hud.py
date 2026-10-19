"""Serve mode: HUD command routing, memory panel, text-only fallback, end-to-end over the socket."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from websockets.asyncio.client import connect

from jarvis.assistant import Assistant
from jarvis.config import JarvisConfig
from jarvis.control import TextSession
from jarvis.cost import CostTracker
from jarvis.events import (
    AssistantState,
    EventBus,
    MemoryDelete,
    MemoryForgetAll,
    MemoryList,
    MuteToggle,
    TextInput,
)
from jarvis.hud_app import READY_PREFIX, Hud, HudTokenError, read_token, run_hud
from jarvis.llm.types import ToolCall
from jarvis.memory.manager import MemoryService
from jarvis.memory.store import MemoryStore
from jarvis.state import StateMachine
from jarvis.tools.registry import build_default_registry
from tests.fakes import (
    EventRecorder,
    FakeBrowser,
    FakeLauncher,
    FakeLLMClient,
    FakeSearch,
    ScriptedTurn,
    shortcut,
)

S = AssistantState
TOKEN = "k" * 48


def test_token_is_required_and_long() -> None:
    assert read_token({"JARVIS_HUD_TOKEN": TOKEN}) == TOKEN
    with pytest.raises(HudTokenError):
        read_token({})
    with pytest.raises(HudTokenError):
        read_token({"JARVIS_HUD_TOKEN": "short"})


class Rig:
    def __init__(
        self,
        config: JarvisConfig,
        tmp_path: Path,
        turns: list[ScriptedTurn],
        launcher: FakeLauncher | None = None,
    ) -> None:
        self.bus = EventBus()
        self.events = EventRecorder()
        self.bus.subscribe(self.events)
        self.state = StateMachine(self.bus)
        self.store = MemoryStore(tmp_path / "memory.db", max_facts=50, max_fact_chars=300)
        self.memory = MemoryService(
            config.memory,
            self.store,
            None,
            user_name="Tony",
            assistant_name="JARVIS",
            history_max_turns=20,
        )
        self.launcher = launcher or FakeLauncher()
        self.llm = FakeLLMClient(turns)
        self.registry = build_default_registry(
            config,
            launcher=self.launcher,
            browser=FakeBrowser(),
            search=FakeSearch(),
            memory=self.memory,
        )
        self.session = TextSession(bus=self.bus, state=self.state)
        self.session.attach(
            Assistant(
                config,
                llm=self.llm,
                registry=self.registry,
                bus=self.bus,
                state=self.state,
                confirmer=self.session,
                memory=self.memory,
            )
        )
        self.hud = Hud(
            config, bus=self.bus, state=self.state, memory=self.memory, control=self.session
        )

    async def settle(self) -> None:
        for _ in range(100):
            await asyncio.sleep(0)
        turn = self.session._turn
        if turn is not None and not turn.done():
            await turn

    def states(self) -> list[AssistantState]:
        return [e.state for e in self.events.of_type("state_changed")]


async def test_typed_input_runs_a_turn(config: JarvisConfig, tmp_path: Path) -> None:
    rig = Rig(config, tmp_path, [ScriptedTurn(chunks=["Hi Tony."])])
    await rig.hud.handle(TextInput(text="hello"))
    await rig.settle()
    finals = [e.text for e in rig.events.of_type("transcript") if e.final]
    assert finals == ["hello", "Hi Tony."]
    assert rig.states() == [S.THINKING, S.SPEAKING, S.IDLE]
    rig.store.close()


async def test_text_session_asks_and_takes_typed_answer(
    config: JarvisConfig, tmp_path: Path
) -> None:
    launcher = FakeLauncher(shortcuts=[shortcut("Spotify")])
    turns = [
        ScriptedTurn(tool_calls=[ToolCall(id="t", name="open_app", input={"app_name": "spotify"})]),
        ScriptedTurn(chunks=["Opened."]),
    ]
    rig = Rig(config, tmp_path, turns, launcher)
    await rig.hud.handle(TextInput(text="open spotify"))
    for _ in range(500):  # memory recall hops to a worker thread: poll, don't count ticks
        if rig.session._answer is not None:
            break
        await asyncio.sleep(0.01)
    question = [e.text for e in rig.events.of_type("transcript") if e.role == "assistant"]
    assert "Shall I open it?" in question[-1]

    await rig.hud.handle(TextInput(text="haan"))
    await rig.settle()
    assert [s.name for s in launcher.launched_shortcuts] == ["Spotify"]
    rig.store.close()


async def test_mute_toggle_in_text_mode(config: JarvisConfig, tmp_path: Path) -> None:
    rig = Rig(config, tmp_path, [])
    await rig.hud.handle(MuteToggle())
    await rig.hud.handle(MuteToggle())
    assert rig.states() == [S.MUTED, S.IDLE]
    assert [e.muted for e in rig.events.of_type("status")] == [True, False]
    assert not rig.session.status().voice
    rig.store.close()


async def test_memory_panel_lists_and_deletes(config: JarvisConfig, tmp_path: Path) -> None:
    rig = Rig(config, tmp_path, [])
    rig.store.add_fact("Tony likes chess.", category="preference", keywords=(), source="explicit")
    rig.store.add_fact("Tony lives in Mumbai.", category="other", keywords=(), source="extracted")

    await rig.hud.handle(MemoryList())
    [snapshot] = rig.events.of_type("memory")
    assert snapshot.enabled
    assert [f.text for f in snapshot.facts] == ["Tony lives in Mumbai.", "Tony likes chess."]
    assert snapshot.facts[0].source == "extracted"

    chess_id = snapshot.facts[1].id
    await rig.hud.handle(MemoryDelete(ids=(chess_id,)))
    assert [f.text for f in rig.events.of_type("memory")[-1].facts] == ["Tony lives in Mumbai."]

    await rig.hud.handle(MemoryForgetAll())
    assert rig.events.of_type("memory")[-1].facts == ()
    assert rig.store.facts() == []
    rig.store.close()


async def test_snapshot_for_a_new_client(config: JarvisConfig, tmp_path: Path) -> None:
    rig = Rig(config, tmp_path, [])
    events = await rig.hud.snapshot()
    assert [e.type for e in events] == ["state_changed", "status", "memory"]  # type: ignore[attr-defined]
    rig.store.close()


async def test_serve_mode_end_to_end_with_voice_unavailable(
    config: JarvisConfig,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import jarvis.voice_app as voice_app

    async def no_voice(*args: object, **kwargs: object) -> None:
        raise voice_app.VoiceStartupError("no microphone")

    monkeypatch.setattr(voice_app, "build_voice", no_voice)
    rig = Rig(config, tmp_path, [ScriptedTurn(chunks=["Hello from the HUD."])])
    task = asyncio.create_task(
        run_hud(
            config,
            token=TOKEN,
            bus=rig.bus,
            state=rig.state,
            llm=rig.llm,
            registry=rig.registry,
            memory=rig.memory,
            cost=CostTracker(config.pricing, config.budget),
            watch_stdin=False,
        )
    )
    for _ in range(50):
        await asyncio.sleep(0.01)
        lines = capsys.readouterr().out.splitlines()
        ready = [line for line in lines if line.startswith(READY_PREFIX)]
        if ready:
            break
    info = json.loads(ready[0][len(READY_PREFIX) :])
    assert info["protocol"] == 2

    async with connect(f"ws://127.0.0.1:{info['port']}", origin="app://jarvis") as ws:  # type: ignore[arg-type]
        await ws.send(json.dumps({"v": 2, "type": "auth", "token": TOKEN}))
        await ws.send(json.dumps({"v": 2, "type": "text_input", "text": "hello"}))
        seen: list[dict[str, object]] = []
        while not any(m.get("text") == "Hello from the HUD." and m.get("final") for m in seen):
            seen.append(json.loads(await asyncio.wait_for(ws.recv(), 5)))
        types = [m["type"] for m in seen]
        assert types[:3] == ["state_changed", "status", "memory"]
        # Voice failed before we connected: the snapshot's status says so.
        assert seen[1]["voice"] is False
        await ws.send(json.dumps({"v": 2, "type": "shutdown"}))
        await asyncio.wait_for(task, 5)
    rig.store.close()
