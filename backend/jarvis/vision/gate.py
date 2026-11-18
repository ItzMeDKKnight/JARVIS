"""The one switch for screen vision, enforced in the backend.

Every screenshot and every mouse or keyboard action goes through :class:`ScreenAccess`,
which asks the gate first. When the mode is off, the call raises :class:`ScreenVisionOffError`
before anything touches the screen, whatever asked for it (a tool, a bug, a confused model).
Turning the mode off:

* flips the switch, so no new capture or action can start;
* bumps a generation counter, so a capture or action that was already queued with the old
  generation refuses to run;
* cancels every task that is in the middle of a vision request (the in-flight Claude call
  carrying a screenshot, or an action sequence), so nothing continues in the background.

The state is held only in memory: off at every start, never saved.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TypeVar

from jarvis.events import EventBus, VisionMode
from jarvis.os_adapters.screen import (
    Frame,
    InputController,
    MouseButton,
    Region,
    ScreenCapturer,
    ScrollDirection,
)

logger = logging.getLogger(__name__)
T = TypeVar("T")


class ScreenVisionOffError(Exception):
    """Screen vision is off (or was switched off while this was waiting)."""


class ScreenGate:
    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._enabled = False  # off at every start, by design
        self._generation = 0
        self._tasks: set[asyncio.Task[object]] = set()

    @property
    def enabled(self) -> bool:
        return self._enabled

    @property
    def generation(self) -> int:
        return self._generation

    def event(self) -> VisionMode:
        return VisionMode(enabled=self._enabled)

    async def enable(self, *, source: str) -> bool:
        """Turn the mode on. Returns False if it was already on."""
        if self._enabled:
            return False
        self._enabled = True
        logger.info("screen vision on", extra={"source": source})
        await self._bus.publish(self.event())
        return True

    async def disable(self, *, source: str) -> bool:
        """Turn the mode off at once and stop everything in flight. False if already off."""
        if not self._enabled:
            return False
        self._enabled = False
        self._generation += 1
        current = asyncio.current_task()
        for task in list(self._tasks):
            if task is not current and not task.done():
                task.cancel()  # the in-flight request / action sequence
        logger.info("screen vision off", extra={"source": source})
        await self._bus.publish(self.event())
        return True

    def check(self, generation: int | None = None) -> int:
        """Raise unless the mode is on (and still the same session); return the generation."""
        if not self._enabled or (generation is not None and generation != self._generation):
            raise ScreenVisionOffError("screen vision is off")
        return self._generation

    @contextmanager
    def session(self) -> Iterator[int]:
        """Mark the current task as using the screen, so switching off can cancel it."""
        generation = self.check()
        task = asyncio.current_task()
        if task is not None:
            self._tasks.add(task)
        try:
            yield generation
        finally:
            if task is not None:
                self._tasks.discard(task)


class ScreenAccess:
    """The only way to the screen: every capture and action asks the gate first.

    The checks happen on the event loop *and* again inside the worker thread right before
    the OS call, so a switch-off between the two still stops it.
    """

    def __init__(
        self, gate: ScreenGate, capturer: ScreenCapturer, controller: InputController
    ) -> None:
        self.gate = gate
        self._capturer = capturer
        self._controller = controller

    async def capture(
        self, generation: int, max_long_edge: int, region: Region | None = None
    ) -> Frame:
        self.gate.check(generation)
        return await asyncio.to_thread(
            self._guarded, generation, self._capturer.capture, max_long_edge, region
        )

    async def click(self, generation: int, x: int, y: int, button: MouseButton, count: int) -> None:
        self.gate.check(generation)
        await asyncio.to_thread(
            self._guarded, generation, self._controller.click, x, y, button, count
        )

    async def move(self, generation: int, x: int, y: int) -> None:
        self.gate.check(generation)
        await asyncio.to_thread(self._guarded, generation, self._controller.move, x, y)

    async def scroll(
        self, generation: int, x: int | None, y: int | None, direction: ScrollDirection, amount: int
    ) -> None:
        self.gate.check(generation)
        await asyncio.to_thread(
            self._guarded, generation, self._controller.scroll, x, y, direction, amount
        )

    async def type_text(self, generation: int, text: str) -> None:
        self.gate.check(generation)
        await asyncio.to_thread(self._guarded, generation, self._controller.type_text, text)

    async def press(self, generation: int, keys: tuple[str, ...], repeat: int) -> None:
        self.gate.check(generation)
        await asyncio.to_thread(self._guarded, generation, self._controller.press, keys, repeat)

    async def focused_is_password(self, generation: int) -> bool | None:
        self.gate.check(generation)
        return await asyncio.to_thread(
            self._guarded, generation, self._controller.focused_is_password
        )

    def screen_size(self) -> tuple[int, int]:
        return self._capturer.screen_size()

    def _guarded(self, generation: int, fn: Callable[..., T], *args: object) -> T:
        self.gate.check(generation)  # last check, in the worker thread, right before the OS call
        return fn(*args)
