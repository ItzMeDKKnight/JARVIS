"""Screen capture and input interfaces for screen vision mode (OS-specific code lives elsewhere).

Only :class:`jarvis.vision.gate.ScreenAccess` holds these, and it checks the screen vision
switch before every call. Nothing here writes a frame to disk: a :class:`Frame` is PNG bytes
in memory, and it is dropped as soon as the request that needed it ends.

Key names follow what the computer-use tool sends (xdotool style: ``ctrl+shift+t``,
``Return``, ``Page_Down``). :func:`parse_keys` normalises them and :func:`classify_keys`
decides which combinations are refused outright and which need a spoken yes first.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal, Protocol

MouseButton = Literal["left", "right", "middle"]
ScrollDirection = Literal["up", "down", "left", "right"]
KeyVerdict = Literal["ok", "confirm", "blocked"]


@dataclass(frozen=True)
class Frame:
    """One screenshot, scaled to fit the model's image limit. Lives only in memory."""

    data: bytes  # encoded image (JPEG: a fifth of the PNG size, which matters on slow uplinks)
    width: int  # size of the image sent to the model
    height: int
    scale: float  # screen pixels per image pixel (multiply model coordinates by this)
    media_type: str = "image/jpeg"


Region = tuple[int, int, int, int]  # x0, y0, x1, y1 in screen pixels


class ScreenCapturer(Protocol):
    def capture(self, max_long_edge: int, region: Region | None = None) -> Frame:
        """Grab the primary monitor, or one region of it (the HUD excludes itself)."""
        ...

    def screen_size(self) -> tuple[int, int]:
        """Primary monitor size in physical pixels."""
        ...


class InputController(Protocol):
    def click(self, x: int, y: int, button: MouseButton, count: int) -> None: ...
    def move(self, x: int, y: int) -> None: ...
    def scroll(
        self, x: int | None, y: int | None, direction: ScrollDirection, amount: int
    ) -> None: ...
    def type_text(self, text: str) -> None: ...
    def press(self, keys: tuple[str, ...], repeat: int) -> None: ...
    def focused_is_password(self) -> bool | None:
        """True if the focused field is a password box; None if that can't be told."""
        ...


class UnsupportedScreen:
    """Placeholder for operating systems without a screen adapter yet."""

    def capture(self, max_long_edge: int, region: Region | None = None) -> Frame:
        raise OSError(f"screen vision isn't supported on {sys.platform} yet")

    def screen_size(self) -> tuple[int, int]:
        return (0, 0)

    def click(self, x: int, y: int, button: MouseButton, count: int) -> None:
        raise OSError("unsupported")

    def move(self, x: int, y: int) -> None:
        raise OSError("unsupported")

    def scroll(self, x: int | None, y: int | None, direction: ScrollDirection, amount: int) -> None:
        raise OSError("unsupported")

    def type_text(self, text: str) -> None:
        raise OSError("unsupported")

    def press(self, keys: tuple[str, ...], repeat: int) -> None:
        raise OSError("unsupported")

    def focused_is_password(self) -> bool | None:
        return None


# -- keys ------------------------------------------------------------------------------------

_ALIASES = {
    "control": "ctrl",
    "ctl": "ctrl",
    "return": "enter",
    "kp_enter": "enter",
    "escape": "esc",
    "del": "delete",
    "page_up": "pageup",
    "prior": "pageup",
    "page_down": "pagedown",
    "next": "pagedown",
    "backspace": "backspace",
    "back_space": "backspace",
    "spacebar": "space",
    "cmd": "win",
    "super": "win",
    "super_l": "win",
    "super_r": "win",
    "meta": "win",
    "windows": "win",
    "option": "alt",
    "alt_l": "alt",
    "control_l": "ctrl",
    "shift_l": "shift",
    "arrowup": "up",
    "arrowdown": "down",
    "arrowleft": "left",
    "arrowright": "right",
}
MODIFIERS = frozenset({"ctrl", "alt", "shift", "win"})
NAMED_KEYS = frozenset(
    {
        "enter",
        "tab",
        "esc",
        "space",
        "backspace",
        "delete",
        "insert",
        "home",
        "end",
        "pageup",
        "pagedown",
        "up",
        "down",
        "left",
        "right",
        *(f"f{i}" for i in range(1, 13)),
    }
)


class UnknownKeyError(ValueError):
    """A key combination Jarvis doesn't understand (so it is not pressed)."""


def parse_keys(spec: str) -> tuple[str, ...]:
    """'ctrl+Shift+T' -> ('ctrl', 'shift', 't'). Raises UnknownKeyError for anything unknown."""
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts or len(parts) > 4:
        raise UnknownKeyError(f"not a key combination: {spec!r}")
    keys = []
    for part in parts:
        key = _ALIASES.get(part, part)
        if key in MODIFIERS or key in NAMED_KEYS or (len(key) == 1 and key.isprintable()):
            keys.append(key)
        else:
            raise UnknownKeyError(f"unknown key: {part!r}")
    return tuple(keys)


def classify_keys(keys: tuple[str, ...]) -> KeyVerdict:
    """Refuse system-level combinations; ask first for ones that close or delete things."""
    combo = set(keys)
    if "win" in combo:
        return "blocked"  # Win+R (run dialog), Win+X, Win+L, ... never by voice-driven automation
    if {"ctrl", "alt", "delete"} <= combo or {"ctrl", "shift", "esc"} <= combo:
        return "blocked"
    if {"alt", "f4"} <= combo or "delete" in combo or {"ctrl", "w"} <= combo:
        return "confirm"  # closes a window / tab, or deletes the selection
    return "ok"
