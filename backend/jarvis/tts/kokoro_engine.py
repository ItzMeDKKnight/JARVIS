"""Kokoro-82M via kokoro-onnx: the default local voice.

The ONNX session is created here rather than inside kokoro-onnx so we control
the thread count (leaving cores free for Whisper and the UI) and keep
execution on the CPU provider.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort
from kokoro_onnx import Kokoro

from jarvis.config import KokoroConfig
from jarvis.tts.base import AudioArray, TTSError, require_file
from jarvis.utils.language import Language


class KokoroEngine:
    def __init__(self, config: KokoroConfig, models_dir: Path, *, speed: float) -> None:
        model_path = require_file(models_dir / config.model_file)
        voices_path = require_file(models_dir / config.voices_file)

        options = ort.SessionOptions()
        options.intra_op_num_threads = config.threads
        options.inter_op_num_threads = 1
        session = ort.InferenceSession(
            str(model_path), sess_options=options, providers=["CPUExecutionProvider"]
        )
        self._kokoro = Kokoro.from_session(session, str(voices_path))
        self._voices = config.voices
        self._espeak = config.espeak_languages
        self._speed = speed

        available = set(self._kokoro.get_voices())
        missing = {v for v in self._voices.values() if v not in available}
        if missing:
            raise TTSError(f"Kokoro voices not found in {voices_path.name}: {sorted(missing)}")

    @property
    def sample_rate(self) -> int:
        return 24_000

    def available_voices(self) -> list[str]:
        return sorted(self._kokoro.get_voices())

    def synthesize(self, text: str, language: Language, voice: str | None = None) -> AudioArray:
        """Synthesize ``text``; ``voice`` overrides the configured voice (used by auditions)."""
        samples, rate = self._kokoro.create(
            text,
            voice=voice or self._voices[language],
            speed=self._speed,
            lang=self._espeak[language],
        )
        if rate != self.sample_rate:
            raise TTSError(f"unexpected Kokoro sample rate {rate}")
        return np.asarray(samples, dtype=np.float32)
