"""OS adapter interfaces and portable helpers.

Everything that differs per operating system (launching apps, finding
shortcuts, where user data lives) sits behind these small interfaces, so the
rest of the code is portable and tests can use fakes instead of really
launching programs.

(The package is called ``os_adapters`` rather than ``platform`` so it can
never shadow Python's standard ``platform`` module if a script is run from
inside the package folder.)
"""

from __future__ import annotations

import os
import sys
import webbrowser
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from jarvis.config import LaunchSpec

APP_DIR_NAME = "Jarvis"


class LaunchError(Exception):
    """A launch failed; the message is safe to show to the user."""


@dataclass(frozen=True)
class Shortcut:
    """A launchable Start Menu (or equivalent) entry found on disk."""

    name: str
    path: Path


class AppLauncher(Protocol):
    def launch(self, spec: LaunchSpec) -> None:
        """Start the application described by ``spec``; raise LaunchError on failure."""
        ...

    def find_shortcuts(self, query: str, limit: int) -> list[Shortcut]:
        """Return installed-app shortcuts whose names best match ``query``."""
        ...

    def launch_shortcut(self, shortcut: Shortcut) -> None:
        """Start a shortcut previously returned by :meth:`find_shortcuts`."""
        ...


class KeyState(Protocol):
    """Read-only view of the keyboard, used for push-to-talk.

    Implementations only *query* key state when asked; they must not install
    keyboard hooks or record keystrokes.
    """

    def is_down(self, key: str) -> bool:
        """True while the named push-to-talk key is held."""
        ...

    def other_key_down(self, key: str) -> bool:
        """True if any key other than ``key`` (and its generic modifier) is held."""
        ...


class BrowserOpener(Protocol):
    def open(self, url: str) -> None:
        """Open an already-validated http(s) URL in the default browser."""
        ...


class DefaultBrowserOpener:
    """Opens URLs with the standard library, which uses the OS default browser."""

    def open(self, url: str) -> None:
        if not webbrowser.open(url, new=2):
            raise LaunchError("I couldn't find a web browser to open that with.")


class UnsupportedLauncher:
    """Placeholder for operating systems without an adapter yet."""

    def launch(self, spec: LaunchSpec) -> None:
        raise LaunchError(f"Opening apps isn't supported on {sys.platform} yet.")

    def find_shortcuts(self, query: str, limit: int) -> list[Shortcut]:
        return []

    def launch_shortcut(self, shortcut: Shortcut) -> None:
        raise LaunchError(f"Opening apps isn't supported on {sys.platform} yet.")


def default_launcher() -> AppLauncher:
    """Pick the launcher for the current OS."""
    if sys.platform == "win32":
        from jarvis.os_adapters.windows import WindowsLauncher

        return WindowsLauncher()
    return UnsupportedLauncher()


def default_key_state() -> KeyState:
    """Pick the keyboard reader for the current OS (push-to-talk is Windows-only for now)."""
    if sys.platform == "win32":
        from jarvis.os_adapters.windows import WindowsKeyState

        return WindowsKeyState()
    raise LaunchError(f"Push-to-talk isn't supported on {sys.platform} yet.")


def user_data_dir(override: Path | None = None) -> Path:
    """Folder for logs and (from Phase 4) the memory database. Never inside the repo."""
    if override is not None:
        return override.expanduser()
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_DIR_NAME
    xdg = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
    return Path(xdg) / APP_DIR_NAME.lower()
