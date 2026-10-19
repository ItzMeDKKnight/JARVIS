"""Push-to-talk: hold one key, alone, to speak.

The key is polled (by default 50 times a second) instead of hooked. A press
only counts once it has been held *alone* for ``hold_threshold_ms``. That
matters for Left Ctrl, which is also half of Ctrl+C, Ctrl+V and Ctrl+click:
if any other key or mouse button goes down during the hold, the press is
treated as a shortcut and ignored.
"""

from __future__ import annotations

import asyncio
import time
from enum import Enum
from typing import Protocol

from jarvis.os_adapters.base import KeyState


class HoldResult(Enum):
    CONFIRMED = "confirmed"  # held alone long enough: start listening
    RELEASED = "released"  # let go too soon: a tap, ignore
    CHORD = "chord"  # another key joined: a shortcut, ignore


class PushToTalk:
    def __init__(self, keys: KeyState, key: str, *, hold_threshold_ms: int, poll_hz: int) -> None:
        self._keys = keys
        self._key = key
        self._threshold_s = hold_threshold_ms / 1000
        self._interval = 1 / poll_hz

    async def wait_for_press(self) -> None:
        """Return on the next up→down transition of the key (not while it is already held)."""
        while self._keys.is_down(self._key):
            await asyncio.sleep(self._interval)
        while not self._keys.is_down(self._key):
            await asyncio.sleep(self._interval)

    async def confirm_hold(self) -> HoldResult:
        """After a press, decide whether it is a real push-to-talk hold."""
        deadline = time.monotonic() + self._threshold_s
        while True:
            if not self._keys.is_down(self._key):
                return HoldResult.RELEASED
            if self._keys.other_key_down(self._key):
                await self._wait_until_released()
                return HoldResult.CHORD
            if time.monotonic() >= deadline:
                return HoldResult.CONFIRMED
            await asyncio.sleep(self._interval)

    async def wait_for_release(self, max_seconds: float) -> None:
        """Return when the key is released, or after ``max_seconds`` at most."""
        try:
            await asyncio.wait_for(self._wait_until_released(), max_seconds)
        except TimeoutError:
            return

    async def _wait_until_released(self) -> None:
        while self._keys.is_down(self._key):
            await asyncio.sleep(self._interval)


class PushToTalkSource(Protocol):
    """Anything that can drive a push-to-talk recording: a polled key or the HUD."""

    async def wait_for_press(self) -> None: ...
    async def confirm_hold(self) -> HoldResult: ...
    async def wait_for_release(self, max_seconds: float) -> None: ...


class CommandPushToTalk:
    """Push-to-talk driven by HUD commands (Space held in the HUD window).

    The HUD sends ``ptt_start`` on key-down and ``ptt_stop`` on key-up. The same
    hold threshold as the keyboard applies, so a quick tap of Space does nothing.
    The browser only reports keys while the HUD has focus, so there are no
    chords to worry about.
    """

    def __init__(self, *, hold_threshold_ms: int) -> None:
        self._threshold_s = hold_threshold_ms / 1000
        self._pressed = asyncio.Event()
        self._released = asyncio.Event()
        self._released.set()

    def press(self) -> None:
        if not self._released.is_set():
            return  # already held (key repeat)
        self._released.clear()
        self._pressed.set()

    def release(self) -> None:
        self._released.set()

    async def wait_for_press(self) -> None:
        await self._pressed.wait()
        self._pressed.clear()

    async def confirm_hold(self) -> HoldResult:
        try:
            await asyncio.wait_for(self._released.wait(), self._threshold_s)
        except TimeoutError:
            return HoldResult.CONFIRMED
        return HoldResult.RELEASED

    async def wait_for_release(self, max_seconds: float) -> None:
        try:
            await asyncio.wait_for(self._released.wait(), max_seconds)
        except TimeoutError:
            return
