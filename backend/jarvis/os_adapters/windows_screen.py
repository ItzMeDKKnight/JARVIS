r"""Windows screen capture and input for screen vision mode.

* Capture: Pillow's ``ImageGrab`` (GDI) grabs the primary monitor, which is scaled down
  and JPEG-encoded **in memory**. Nothing is written to disk. The HUD keeps itself out of
  the picture with ``setContentProtection`` (Windows' WDA_EXCLUDEFROMCAPTURE) while the
  mode is on.
* Input: ``SendInput`` with absolute mouse coordinates on the primary monitor, wheel
  events, Unicode keystrokes for typing (so Hindi and emoji type correctly whatever the
  keyboard layout), and virtual-key codes for shortcuts.
* Password fields: Windows UI Automation (through comtypes) reports whether the focused
  element is a password box, so typing into one always asks for a spoken yes first.

The process is made per-monitor DPI aware once, so screenshot pixels and click
coordinates are the same physical pixels.
"""

from __future__ import annotations

import ctypes
import io
import logging
import threading
import time
from ctypes import wintypes

from jarvis.os_adapters.screen import Frame, MouseButton, Region, ScrollDirection

logger = logging.getLogger(__name__)

_user32 = ctypes.WinDLL("user32", use_last_error=True)

_INPUT_MOUSE = 0
_INPUT_KEYBOARD = 1
_MOUSEEVENTF_MOVE = 0x0001
_MOUSEEVENTF_ABSOLUTE = 0x8000
_MOUSEEVENTF_WHEEL = 0x0800
_MOUSEEVENTF_HWHEEL = 0x1000
_BUTTON_FLAGS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
_KEYEVENTF_EXTENDEDKEY = 0x0001
_KEYEVENTF_KEYUP = 0x0002
_KEYEVENTF_UNICODE = 0x0004
_WHEEL_DELTA = 120
_CLICK_GAP_S = 0.04
_KEY_GAP_S = 0.008

_VK = {
    "ctrl": 0x11,
    "alt": 0x12,
    "shift": 0x10,
    "win": 0x5B,
    "enter": 0x0D,
    "tab": 0x09,
    "esc": 0x1B,
    "space": 0x20,
    "backspace": 0x08,
    "delete": 0x2E,
    "insert": 0x2D,
    "home": 0x24,
    "end": 0x23,
    "pageup": 0x21,
    "pagedown": 0x22,
    "left": 0x25,
    "up": 0x26,
    "right": 0x27,
    "down": 0x28,
    **{f"f{i}": 0x6F + i for i in range(1, 13)},
}
_EXTENDED = frozenset(
    {"delete", "insert", "home", "end", "pageup", "pagedown", "left", "up", "right", "down", "win"}
)


class _MouseInput(ctypes.Structure):
    _fields_ = [
        ("dx", wintypes.LONG),
        ("dy", wintypes.LONG),
        ("mouseData", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _KeybdInput(ctypes.Structure):
    _fields_ = [
        ("wVk", wintypes.WORD),
        ("wScan", wintypes.WORD),
        ("dwFlags", wintypes.DWORD),
        ("time", wintypes.DWORD),
        ("dwExtraInfo", ctypes.c_size_t),
    ]


class _HardwareInput(ctypes.Structure):
    _fields_ = [
        ("uMsg", wintypes.DWORD),
        ("wParamL", wintypes.WORD),
        ("wParamH", wintypes.WORD),
    ]


class _InputUnion(ctypes.Union):
    _fields_ = [("mi", _MouseInput), ("ki", _KeybdInput), ("hi", _HardwareInput)]


class _Input(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _InputUnion)]


_user32.SendInput.argtypes = [wintypes.UINT, ctypes.POINTER(_Input), ctypes.c_int]
_user32.SendInput.restype = wintypes.UINT
_user32.VkKeyScanW.argtypes = [wintypes.WCHAR]
_user32.VkKeyScanW.restype = ctypes.c_short

_dpi_lock = threading.Lock()
_dpi_done = False


def _ensure_dpi_aware() -> None:
    """Physical pixels everywhere (screenshots and clicks agree on high-DPI screens)."""
    global _dpi_done
    with _dpi_lock:
        if _dpi_done:
            return
        try:
            _user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
        except (AttributeError, OSError):
            logger.info("could not set DPI awareness; coordinates may be scaled")
        _dpi_done = True


def _send(*inputs: _Input) -> None:
    array = (_Input * len(inputs))(*inputs)
    sent = _user32.SendInput(len(inputs), array, ctypes.sizeof(_Input))
    if sent != len(inputs):
        raise OSError(f"SendInput delivered {sent} of {len(inputs)} events")


def _mouse(flags: int, dx: int = 0, dy: int = 0, data: int = 0) -> _Input:
    event = _Input(type=_INPUT_MOUSE)
    event.u.mi = _MouseInput(dx, dy, ctypes.c_ulong(data & 0xFFFFFFFF).value, flags, 0, 0)
    return event


def _key(vk: int, *, up: bool = False, extended: bool = False) -> _Input:
    flags = (_KEYEVENTF_KEYUP if up else 0) | (_KEYEVENTF_EXTENDEDKEY if extended else 0)
    event = _Input(type=_INPUT_KEYBOARD)
    event.u.ki = _KeybdInput(vk, 0, flags, 0, 0)
    return event


def _unicode(unit: int, *, up: bool = False) -> _Input:
    event = _Input(type=_INPUT_KEYBOARD)
    event.u.ki = _KeybdInput(0, unit, _KEYEVENTF_UNICODE | (_KEYEVENTF_KEYUP if up else 0), 0, 0)
    return event


class WindowsScreen:
    """ScreenCapturer and InputController for the primary monitor."""

    def __init__(self, *, jpeg_quality: int = 80) -> None:
        _ensure_dpi_aware()
        self._jpeg_quality = jpeg_quality
        self._uia: object | None = None

    # -- capture --------------------------------------------------------------------------

    def screen_size(self) -> tuple[int, int]:
        return int(_user32.GetSystemMetrics(0)), int(_user32.GetSystemMetrics(1))

    def capture(self, max_long_edge: int, region: Region | None = None) -> Frame:
        from PIL import Image, ImageGrab

        # Primary monitor, physical pixels; a region is a zoom (cropped at full resolution).
        image = ImageGrab.grab(bbox=region, all_screens=False)
        try:
            width, height = image.size
            scale = (
                max(width, height) / max_long_edge if max(width, height) > max_long_edge else 1.0
            )
            if scale > 1.0:
                small = image.resize(
                    (round(width / scale), round(height / scale)), Image.Resampling.LANCZOS
                )
                image.close()
                image = small
            buffer = io.BytesIO()
            image.convert("RGB").save(buffer, format="JPEG", quality=self._jpeg_quality)
            return Frame(
                data=buffer.getvalue(), width=image.width, height=image.height, scale=scale
            )
        finally:
            image.close()

    # -- input ----------------------------------------------------------------------------

    def _absolute(self, x: int, y: int) -> tuple[int, int]:
        width, height = self.screen_size()
        x = min(max(x, 0), width - 1)
        y = min(max(y, 0), height - 1)
        return round(x * 65535 / max(width - 1, 1)), round(y * 65535 / max(height - 1, 1))

    def move(self, x: int, y: int) -> None:
        ax, ay = self._absolute(x, y)
        _send(_mouse(_MOUSEEVENTF_MOVE | _MOUSEEVENTF_ABSOLUTE, ax, ay))

    def click(self, x: int, y: int, button: MouseButton, count: int) -> None:
        self.move(x, y)
        down, up = _BUTTON_FLAGS[button]
        for i in range(max(1, min(count, 3))):
            if i:
                time.sleep(_CLICK_GAP_S)
            _send(_mouse(down), _mouse(up))

    def scroll(self, x: int | None, y: int | None, direction: ScrollDirection, amount: int) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        clicks = max(1, min(amount, 30))
        if direction in ("up", "down"):
            delta = _WHEEL_DELTA * clicks * (1 if direction == "up" else -1)
            _send(_mouse(_MOUSEEVENTF_WHEEL, data=delta))
        else:
            delta = _WHEEL_DELTA * clicks * (1 if direction == "right" else -1)
            _send(_mouse(_MOUSEEVENTF_HWHEEL, data=delta))

    def type_text(self, text: str) -> None:
        for char in text:
            if char == "\n":
                _send(_key(_VK["enter"]), _key(_VK["enter"], up=True))
            else:
                units = char.encode("utf-16-le")
                for i in range(0, len(units), 2):
                    unit = int.from_bytes(units[i : i + 2], "little")
                    _send(_unicode(unit), _unicode(unit, up=True))
            time.sleep(_KEY_GAP_S)

    def press(self, keys: tuple[str, ...], repeat: int) -> None:
        codes: list[tuple[int, bool]] = []
        shift_from_char = False
        for key in keys:
            if key in _VK:
                codes.append((_VK[key], key in _EXTENDED))
            else:
                scan = int(_user32.VkKeyScanW(key))
                if scan == -1:
                    raise OSError(f"no key for {key!r} on this keyboard layout")
                codes.append((scan & 0xFF, False))
                shift_from_char = shift_from_char or bool(scan & 0x100)
        if shift_from_char and _VK["shift"] not in [c for c, _ in codes]:
            codes.insert(0, (_VK["shift"], False))
        for _ in range(max(1, min(repeat, 100))):
            downs = [_key(vk, extended=ext) for vk, ext in codes]
            ups = [_key(vk, up=True, extended=ext) for vk, ext in reversed(codes)]
            _send(*downs, *ups)
            time.sleep(_KEY_GAP_S)

    def focused_is_password(self) -> bool | None:
        """Ask UI Automation about the focused element. None when it can't tell."""
        try:
            import comtypes
            import comtypes.client

            comtypes.CoInitialize()  # type: ignore[no-untyped-call]
            try:
                comtypes.client.GetModule("UIAutomationCore.dll")
                from comtypes.gen.UIAutomationClient import (
                    CUIAutomation,
                    IUIAutomation,
                )

                automation = comtypes.client.CreateObject(CUIAutomation, interface=IUIAutomation)
                element = automation.GetFocusedElement()
                return bool(element.CurrentIsPassword) if element is not None else None
            finally:
                comtypes.CoUninitialize()  # type: ignore[no-untyped-call]
        except Exception:
            logger.info("could not query the focused element (UI Automation)")
            return None
