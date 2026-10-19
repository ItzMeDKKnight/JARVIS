"""Speech-to-text interface."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from jarvis.audio.types import AudioArray
from jarvis.utils.language import Language


class STTError(Exception):
    """Transcription failed or the model could not be loaded; message is user-safe."""


@dataclass(frozen=True)
class Transcription:
    text: str
    language: Language
    # Whisper's confidence in the language it reported (before our en/hi restriction).
    language_probability: float
    # Whisper's own (unrestricted) guess, kept for diagnostics, e.g. "ur" for Hindi speech.
    detected_language: str


class SpeechToText(Protocol):
    def transcribe(self, audio: AudioArray) -> Transcription:
        """Transcribe 16 kHz mono float32 audio. Synchronous: run it in a worker thread."""
        ...
