r"""Windows app launching.

Launches go through ``os.startfile`` (Win32 ShellExecute), never through a
shell. ShellExecute resolves exe names registered under "App Paths"
(chrome.exe, code.exe), URIs such as ``ms-settings:``, Start Menu ``.lnk``
files, and Store apps via ``shell:AppsFolder\\<AppUserModelID>``.

Only two kinds of strings ever reach ``os.startfile``: targets from the
user-maintained apps.yaml, and ``.lnk`` files this module found itself inside
the Start Menu folders. Text from the language model is only ever used to
*look up* one of those, never launched directly.
"""

from __future__ import annotations

import ctypes
import difflib
import os
import re
import shutil
import winreg
from pathlib import Path
from typing import ClassVar

from jarvis.config import LaunchSpec
from jarvis.os_adapters.base import LaunchError, Shortcut
from jarvis.text import normalize_name

# Package family name + "!" + app id, e.g. Microsoft.WindowsCalculator_8wekyb3d8bbwe!App
_AUMID_PATTERN = re.compile(r"^[\w.\-]+_[a-z0-9]+![\w.\-]+$", re.IGNORECASE)
_SKIP_SHORTCUT_WORDS = ("uninstall", "readme", "help", "documentation", "release notes")
_FUZZY_CUTOFF = 0.75


def _start_menu_dirs() -> list[Path]:
    dirs = []
    for env_var in ("APPDATA", "PROGRAMDATA"):
        base = os.environ.get(env_var)
        if base:
            dirs.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return [d for d in dirs if d.is_dir()]


def _exe_is_available(exe: str) -> bool:
    """True if ShellExecute can find ``exe`` via the "App Paths" registry or PATH.

    Checking first avoids Windows' modal "cannot find chrome.exe" dialog and
    lets Jarvis simply say the app is not installed.
    """
    key = rf"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\{exe}"
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, key):
                return True
        except OSError:
            continue
    return shutil.which(exe) is not None


def _startfile(target: str) -> None:
    """Wrap os.startfile so every OS failure becomes a user-readable LaunchError."""
    try:
        os.startfile(target)  # noqa: S606 - ShellExecute without a shell; targets are vetted above
    except FileNotFoundError as exc:
        raise LaunchError("that app doesn't seem to be installed") from exc
    except OSError as exc:
        raise LaunchError(
            f"Windows refused to start it ({exc.strerror or 'unknown error'})"
        ) from exc


class WindowsKeyState:
    """Push-to-talk key reader using ``GetAsyncKeyState``.

    It polls the state of keys on request; no hook is installed and nothing
    is recorded, so it cannot act as a keylogger.
    """

    # Virtual-key codes: https://learn.microsoft.com/windows/win32/inputdev/virtual-key-codes
    _KEYS: ClassVar[dict[str, tuple[int, ...]]] = {
        # (the key itself, generic codes Windows reports alongside it)
        "left_ctrl": (0xA2, 0x11),
        "right_ctrl": (0xA3, 0x11),
        "f9": (0x78,),
        "caps_lock": (0x14,),
    }
    # Keyboard keys plus mouse buttons (0x01-0x06), so Ctrl+click also cancels push-to-talk.
    _SCAN_RANGE = range(0x01, 0xFF)

    def __init__(self) -> None:
        self._get = ctypes.windll.user32.GetAsyncKeyState

    def _down(self, vk: int) -> bool:
        return bool(self._get(vk) & 0x8000)

    def is_down(self, key: str) -> bool:
        return self._down(self._KEYS[key][0])

    def other_key_down(self, key: str) -> bool:
        ignored = set(self._KEYS[key])
        return any(self._down(vk) for vk in self._SCAN_RANGE if vk not in ignored)


class WindowsLauncher:
    """AppLauncher for Windows 10/11."""

    def launch(self, spec: LaunchSpec) -> None:
        if spec.kind == "appx":
            if not _AUMID_PATTERN.match(spec.target):
                raise LaunchError(f"'{spec.target}' is not a valid Store app id")
            _startfile(f"shell:AppsFolder\\{spec.target}")
        elif spec.kind == "path":
            path = Path(os.path.expandvars(spec.target))
            if not path.is_file():
                raise LaunchError("that app doesn't seem to be installed")
            _startfile(str(path))
        else:
            if spec.target.lower().endswith(".exe") and not _exe_is_available(spec.target):
                raise LaunchError("that app doesn't seem to be installed")
            _startfile(spec.target)

    def find_shortcuts(self, query: str, limit: int) -> list[Shortcut]:
        wanted = normalize_name(query)
        if not wanted:
            return []
        candidates: dict[str, Path] = {}
        for folder in _start_menu_dirs():
            for link in folder.rglob("*.lnk"):
                name = normalize_name(link.stem)
                if any(word in name for word in _SKIP_SHORTCUT_WORDS):
                    continue
                candidates.setdefault(name, link)

        # Prefer names that contain the query ("code" -> "visual studio code"),
        # then fall back to fuzzy matching for small mis-hearings.
        containing = sorted((n for n in candidates if f" {wanted} " in f" {n} "), key=len)
        fuzzy = difflib.get_close_matches(wanted, candidates.keys(), n=limit, cutoff=_FUZZY_CUTOFF)
        ordered = list(dict.fromkeys([*containing, *fuzzy]))[:limit]
        return [Shortcut(name=candidates[n].stem, path=candidates[n]) for n in ordered]

    def launch_shortcut(self, shortcut: Shortcut) -> None:
        resolved = shortcut.path.resolve()
        inside_start_menu = any(resolved.is_relative_to(d.resolve()) for d in _start_menu_dirs())
        if resolved.suffix.lower() != ".lnk" or not inside_start_menu:
            raise LaunchError("that shortcut is not in the Start Menu")
        _startfile(str(resolved))
