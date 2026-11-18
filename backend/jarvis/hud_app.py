"""Serve mode (``python -m jarvis --serve``): the backend the Electron HUD launches.

Start-up order is chosen so the HUD is useful as early as possible:

1. the WebSocket server starts and ``JARVIS_READY {"port": ..}`` is printed on
   stdout (Electron waits for that line);
2. a text session answers typed input right away;
3. the speech models load (a few seconds); once ready, the voice session
   takes over. If voice can't start (no mic, missing models), Jarvis says so
   and the HUD keeps working with typed input.

Shutdown: the HUD sends ``shutdown``; and if Electron dies, its end of our
stdin closes, which we treat the same way. Either way the normal exit path
runs, so the last memory consolidation and the session cost log still happen.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import sys
import threading
import time

from pydantic import BaseModel

from jarvis.assistant import Assistant
from jarvis.audio.capture import AudioDeviceError
from jarvis.config import JarvisConfig
from jarvis.control import SessionControl, TextSession
from jarvis.cost import CostTracker
from jarvis.events import (
    PROTOCOL_VERSION,
    ClientCommand,
    ErrorEvent,
    EventBus,
    Interrupt,
    MemoryDelete,
    MemoryFactItem,
    MemoryForgetAll,
    MemoryList,
    MemorySnapshot,
    MuteToggle,
    PersonalitySet,
    PttStart,
    PttStop,
    Shutdown,
    StateChanged,
    Stopped,
    TextInput,
    Transcript,
    UsageUpdate,
    VisionSet,
)
from jarvis.llm.types import LLMClient
from jarvis.memory.manager import MemoryService
from jarvis.personality import PersonalityState
from jarvis.server import HudServer
from jarvis.state import StateMachine
from jarvis.tools.registry import ToolRegistry
from jarvis.vision.gate import ScreenGate

logger = logging.getLogger(__name__)

READY_PREFIX = "JARVIS_READY "
TOKEN_ENV_VAR = "JARVIS_HUD_TOKEN"  # noqa: S105 (a variable name, not a secret)
MIN_TOKEN_CHARS = 32


class HudTokenError(Exception):
    """The launch token is missing or too short; serve mode refuses to start."""


def read_token(environ: dict[str, str]) -> str:
    token = environ.get(TOKEN_ENV_VAR, "").strip()
    if len(token) < MIN_TOKEN_CHARS:
        raise HudTokenError(
            f"{TOKEN_ENV_VAR} must hold a random token of at least {MIN_TOKEN_CHARS} characters. "
            "Serve mode is meant to be started by the HUD (cd ui && npm start)."
        )
    return token


class Hud:
    """Routes HUD commands to the current session and memory; builds connect snapshots."""

    def __init__(
        self,
        config: JarvisConfig,
        *,
        bus: EventBus,
        state: StateMachine,
        memory: MemoryService | None,
        control: SessionControl,
        screen_gate: ScreenGate | None = None,
        personality: PersonalityState | None = None,
    ) -> None:
        self._config = config
        self._screen_gate = screen_gate
        self._personality = personality
        self._bus = bus
        self._state = state
        self._memory = memory
        self.control = control
        self.shutdown = asyncio.Event()
        self._last_usage: UsageUpdate | None = None
        bus.subscribe(self._remember_usage)

    async def _remember_usage(self, event: BaseModel) -> None:
        if isinstance(event, UsageUpdate):
            self._last_usage = event

    async def snapshot(self) -> list[BaseModel]:
        """Everything a newly connected HUD needs to draw the current picture."""
        events: list[BaseModel] = [StateChanged(state=self._state.state), self.control.status()]
        if self._screen_gate is not None:
            events.append(self._screen_gate.event())
        if self._personality is not None:
            events.append(self._personality.event())
        if self._last_usage is not None:
            events.append(self._last_usage)
        events.append(await self.memory_snapshot())
        return events

    async def memory_snapshot(self) -> MemorySnapshot:
        if self._memory is None:
            return MemorySnapshot(enabled=False, facts=(), summary=None)
        facts, summary = await self._memory.snapshot()
        items = tuple(
            MemoryFactItem(
                id=f.id,
                text=f.text,
                category=f.category,
                source="explicit" if f.source == "explicit" else "extracted",
                created_at=f.created_at,
            )
            for f in facts
        )
        return MemorySnapshot(enabled=True, facts=items, summary=summary)

    async def handle(self, command: ClientCommand) -> None:
        control = self.control
        if isinstance(command, TextInput):
            await control.submit_text(command.text)
        elif isinstance(command, MuteToggle):
            await control.toggle_mute()
        elif isinstance(command, Interrupt):
            await self._stop(control)
        elif isinstance(command, PttStart):
            await control.ptt_start()
        elif isinstance(command, PttStop):
            await control.ptt_stop()
        elif isinstance(command, Shutdown):
            logger.info("shutdown requested by the hud")
            self.shutdown.set()
        elif isinstance(command, MemoryList | MemoryDelete | MemoryForgetAll):
            await self._memory_command(command)
        elif isinstance(command, VisionSet):
            await self._vision_set(command.enabled)
        elif isinstance(command, PersonalitySet) and self._personality is not None:
            await self._personality.set(command.mode, command.intensity)

    async def _vision_set(self, enabled: bool) -> None:
        """The eye button / tray item. Pressing the button is itself the consent to turn on."""
        gate = self._screen_gate
        if gate is None:
            await self._bus.publish(
                ErrorEvent(code="vision_unavailable", message="Screen vision is not available")
            )
            return
        if enabled:
            await gate.enable(source="hud")
        else:
            await gate.disable(source="hud")

    async def _stop(self, control: SessionControl) -> None:
        """The one stop path (HUD Stop button and Esc): cancel, then tell the HUD what stopped."""
        started = time.perf_counter()
        # Esc / Stop also ends screen vision, first, so no capture can start meanwhile.
        gate = self._screen_gate
        vision_was_on = gate is not None and await gate.disable(source="stop")
        cancelled = await control.interrupt()
        if vision_was_on:
            cancelled = (*cancelled, "vision")
        await self._bus.publish(Stopped(cancelled=cancelled))
        logger.info(
            "stop handled",
            extra={
                "cancelled": list(cancelled),
                "ms": round((time.perf_counter() - started) * 1000),
            },
        )

    async def _memory_command(self, command: MemoryList | MemoryDelete | MemoryForgetAll) -> None:
        if self._memory is not None:
            if isinstance(command, MemoryDelete):
                await self._memory.forget_ids(list(command.ids))
            elif isinstance(command, MemoryForgetAll):
                await self._memory.forget_everything()
        await self._bus.publish(await self.memory_snapshot())


def _watch_stdin(loop: asyncio.AbstractEventLoop, shutdown: asyncio.Event) -> None:
    """End serve mode when the parent process goes away (our stdin hits end-of-file).

    A daemon thread, not an executor job: a blocked read must never keep the
    process alive at exit.
    """

    def watch() -> None:
        while sys.stdin.readline():
            pass
        loop.call_soon_threadsafe(shutdown.set)

    threading.Thread(target=watch, name="stdin-watch", daemon=True).start()


async def run_hud(
    config: JarvisConfig,
    *,
    token: str,
    bus: EventBus,
    state: StateMachine,
    llm: LLMClient,
    registry: ToolRegistry,
    memory: MemoryService | None,
    cost: CostTracker,
    watch_stdin: bool = True,
) -> None:
    text_session = TextSession(bus=bus, state=state)
    text_session.attach(
        Assistant(
            config,
            llm=llm,
            registry=registry,
            bus=bus,
            state=state,
            confirmer=text_session,
            memory=memory,
            cost=cost,
        )
    )
    hud = Hud(
        config,
        bus=bus,
        state=state,
        memory=memory,
        control=text_session,
        screen_gate=registry.screen_gate,
        personality=registry.personality,
    )
    server = HudServer(
        config.server, token=token, bus=bus, on_command=hud.handle, snapshot=hud.snapshot
    )
    port = await server.start()
    print(READY_PREFIX + json.dumps({"port": port, "protocol": PROTOCOL_VERSION}), flush=True)
    if watch_stdin:
        _watch_stdin(asyncio.get_running_loop(), hud.shutdown)

    sessions = asyncio.create_task(
        _run_sessions(
            config,
            hud=hud,
            text_session=text_session,
            bus=bus,
            state=state,
            llm=llm,
            registry=registry,
            memory=memory,
            cost=cost,
        ),
        name="sessions",
    )
    try:
        await hud.shutdown.wait()
    finally:
        sessions.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sessions
        await server.close()


async def _run_sessions(
    config: JarvisConfig,
    *,
    hud: Hud,
    text_session: TextSession,
    bus: EventBus,
    state: StateMachine,
    llm: LLMClient,
    registry: ToolRegistry,
    memory: MemoryService | None,
    cost: CostTracker,
) -> None:
    """Typed input at once; voice as soon as it loads; back to text if voice fails."""
    text_task = asyncio.create_task(text_session.run(), name="text-session")
    try:
        # Imported lazily: loading the audio/ML stack is the slow part.
        from jarvis.voice_app import VoiceStartupError, build_voice

        try:
            runtime = await build_voice(
                config, bus=bus, state=state, llm=llm, registry=registry, memory=memory, cost=cost
            )
        except VoiceStartupError as exc:
            await _voice_failed(config, bus, str(exc))
            await text_task
            return
        await text_session.interrupt()
        hud.control = runtime.session
        await bus.publish(runtime.session.status())
        logger.info("voice session ready")
        try:
            await runtime.session.run()
        except AudioDeviceError as exc:
            hud.control = text_session
            await bus.publish(text_session.status())
            await _voice_failed(config, bus, str(exc))
            await text_task
        finally:
            await runtime.close()
    finally:
        text_task.cancel()


async def _voice_failed(config: JarvisConfig, bus: EventBus, reason: str) -> None:
    logger.warning("voice unavailable; typed input only", extra={"reason": reason})
    line = config.messages.voice_unavailable.en
    await bus.publish(ErrorEvent(code="voice_unavailable", message=reason))
    await bus.publish(
        Transcript(
            turn_id="voice-unavailable", role="assistant", text=line, language="en", final=True
        )
    )
