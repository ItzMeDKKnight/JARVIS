"""Switchable personalities: Normal, Witty, Evil, Joyful, Lusty, with an intensity from 0 to 100.

config/persona.md has a *core* (who Jarvis is, language and speech rules, every safety,
confirmation and memory rule, and the rule that personality changes only how Jarvis talks)
followed by one section per mode, each with four intensity bands, marked like this::

    <!-- personality: witty -->
    <!-- band: 0-25 -->
    ...text...

Prompt layout, for prompt caching: the core is the first system block (cached together with
the tools and byte-identical in every mode); the active mode and band are a second, small
system block after it. Changing personality therefore changes only that block: one cache
miss for the conversation after it, never for the core.

The choice is saved in ``<data_dir>/settings.json`` (next to memory.db, never in the repo),
restored at start, and applies from the next reply.
"""

from __future__ import annotations

import json
import logging
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, get_args

from jarvis.events import EventBus, PersonalityEvent

logger = logging.getLogger(__name__)

Mode = Literal["normal", "witty", "evil", "joyful", "lusty"]
MODES: tuple[Mode, ...] = get_args(Mode)
BANDS: tuple[tuple[int, int], ...] = ((0, 25), (26, 50), (51, 75), (76, 100))
DEFAULT_INTENSITY = 50
LABELS: dict[Mode, str] = {
    "normal": "Normal",
    "witty": "Witty",
    "evil": "Evil",
    "joyful": "Joyful",
    "lusty": "Lusty",
}

_SECTION = re.compile(r"<!--\s*personality:\s*([a-z]+)\s*-->")
_BAND = re.compile(r"<!--\s*band:\s*(\d{1,3})\s*-\s*(\d{1,3})\s*-->")


class PersonaFormatError(ValueError):
    """persona.md's personality sections are missing or malformed (caught at startup)."""


@dataclass(frozen=True)
class Personality:
    mode: Mode = "normal"
    intensity: int = DEFAULT_INTENSITY

    def __post_init__(self) -> None:
        if self.mode not in MODES:
            raise ValueError(f"unknown personality mode {self.mode!r}")
        if not 0 <= self.intensity <= 100:
            raise ValueError("intensity must be between 0 and 100")

    @property
    def label(self) -> str:
        if self.mode == "normal":
            return LABELS["normal"]
        return f"{LABELS[self.mode]} {self.intensity}%"


@dataclass(frozen=True)
class PersonaSections:
    core: str
    normal: str
    bands: dict[Mode, dict[tuple[int, int], str]]


def split_persona(template: str) -> PersonaSections:
    """Split persona.md into the core and the per-mode, per-band texts. Strict on purpose."""
    first = _SECTION.search(template)
    if first is None:
        raise PersonaFormatError("persona.md has no '<!-- personality: ... -->' sections")
    core = template[: first.start()].strip()
    parts = _SECTION.split(template[first.start() :])
    # parts = ["", mode1, body1, mode2, body2, ...]
    texts: dict[str, str] = {}
    for mode, body in zip(parts[1::2], parts[2::2], strict=True):
        if mode in texts:
            raise PersonaFormatError(f"personality '{mode}' appears twice")
        texts[mode] = body
    if set(texts) != set(MODES):
        raise PersonaFormatError(f"persona.md needs exactly these modes: {', '.join(MODES)}")
    normal = texts["normal"].strip()
    bands: dict[Mode, dict[tuple[int, int], str]] = {}
    for mode in MODES:
        if mode == "normal":
            continue
        pieces = _BAND.split(texts[mode])
        found: dict[tuple[int, int], str] = {}
        for low, high, text in zip(pieces[1::3], pieces[2::3], pieces[3::3], strict=True):
            found[(int(low), int(high))] = text.strip()
        if tuple(sorted(found)) != BANDS or not all(found.values()):
            raise PersonaFormatError(f"'{mode}' needs four bands: 0-25, 26-50, 51-75, 76-100")
        bands[mode] = found
    if not normal:
        raise PersonaFormatError("the normal personality needs a short description")
    return PersonaSections(core=core, normal=normal, bands=bands)


def band_of(intensity: int) -> tuple[int, int]:
    for band in BANDS:
        if band[0] <= intensity <= band[1]:
            return band
    raise ValueError("intensity must be between 0 and 100")


def personality_prompt(sections: PersonaSections, personality: Personality) -> str:
    """The small second system block: the active mode and band (after the cached core)."""
    if personality.mode == "normal":
        body = sections.normal
        heading = "# Active personality: Normal"
    else:
        low, high = band_of(personality.intensity)
        body = sections.bands[personality.mode][(low, high)]
        heading = (
            f"# Active personality: {LABELS[personality.mode]}, intensity "
            f"{personality.intensity}% (band {low}-{high})"
        )
    reminder = (
        "This changes only how you talk. Tools, confirmations, the allowlist, the spending cap, "
        "memory rules and the accuracy of every answer stay exactly as in normal mode."
    )
    return f"{heading}\n{body}\n{reminder}"


class PersonalitySettings:
    """Reads and writes the saved personality in <data_dir>/settings.json (atomically)."""

    KEY = "personality"

    def __init__(self, path: Path) -> None:
        self._path = path

    def load(self) -> Personality:
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            raw = data.get(self.KEY, {}) if isinstance(data, dict) else {}
            return Personality(
                mode=raw.get("mode", "normal"),
                intensity=int(raw.get("intensity", DEFAULT_INTENSITY)),
            )
        except FileNotFoundError:
            return Personality()
        except (ValueError, TypeError, OSError, AttributeError):
            logger.warning("settings.json is unreadable; using the normal personality")
            return Personality()

    def save(self, personality: Personality) -> None:
        data: dict[str, object] = {}
        try:
            loaded = json.loads(self._path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (FileNotFoundError, ValueError, OSError):
            pass
        data[self.KEY] = {"mode": personality.mode, "intensity": personality.intensity}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=self._path.parent, prefix=".settings-", suffix=".json")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(data, handle, indent=2)
            os.replace(tmp, self._path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


class PersonalityState:
    """The current personality: read before every Claude request, changed by voice or the HUD."""

    def __init__(
        self,
        sections: PersonaSections,
        *,
        settings: PersonalitySettings | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._sections = sections
        self._settings = settings
        self._bus = bus
        self._current = settings.load() if settings is not None else Personality()

    @property
    def current(self) -> Personality:
        return self._current

    def prompt(self) -> str:
        return personality_prompt(self._sections, self._current)

    def event(self) -> PersonalityEvent:
        return PersonalityEvent(mode=self._current.mode, intensity=self._current.intensity)

    async def set(self, mode: Mode, intensity: int | None = None) -> Personality:
        """Switch mode and/or intensity; saved at once, used from the next reply."""
        if intensity is None:
            intensity = self._current.intensity
        new = Personality(mode=mode, intensity=max(0, min(100, int(intensity))))
        self._current = new
        if self._settings is not None:
            try:
                self._settings.save(new)
            except OSError:
                logger.exception("could not save the personality setting")
        logger.info("personality changed", extra={"mode": new.mode, "intensity": new.intensity})
        if self._bus is not None:
            await self._bus.publish(self.event())
        return new
