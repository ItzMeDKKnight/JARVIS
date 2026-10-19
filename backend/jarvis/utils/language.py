"""Language detection by writing system.

Whisper (Phase 2) reports a language per utterance, but typed input and
mixed sentences need a cheap, deterministic answer too. Counting Devanagari
versus Latin letters is good enough to pick the reply language and narration
lines, and it never needs a model or the network.
"""

from __future__ import annotations

from typing import Literal

Language = Literal["en", "hi"]

_DEVANAGARI_START = 0x0900
_DEVANAGARI_END = 0x097F


def is_devanagari(char: str) -> bool:
    """True for characters in the Devanagari Unicode block."""
    return _DEVANAGARI_START <= ord(char) <= _DEVANAGARI_END


def detect_language(text: str) -> Language:
    """Guess the reply language: Hindi if any Devanagari appears, otherwise English.

    Any Devanagari counts because Hindi speech is usually transcribed with
    English technical words left in Latin script ("python में binary search
    लिखो"); a majority vote would wrongly call that English.

    Romanised Hindi ("kholo calculator") is reported as English on purpose:
    narration in English is always understandable, and the model itself decides
    the language of the actual reply from the full text.
    """
    return "hi" if any(is_devanagari(char) for char in text) else "en"
