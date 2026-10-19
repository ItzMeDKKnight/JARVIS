"""Piper TTS: the fallback local voice engine.

Voices are loaded from local .onnx files only. Piper's own download helpers
(``piper.download_voices``) are never used; scripts/download_models.py
fetches pinned, checksummed voice files instead.

Note: piper-tts is GPL-3.0-or-later and is an optional dependency
(``pip install -e ".[piper]"``).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

from jarvis.config import PiperConfig
from jarvis.tts.base import AudioArray, TTSError, require_file
from jarvis.utils.language import Language


class PiperEngine:
    def __init__(self, config: PiperConfig, models_dir: Path, *, speed: float) -> None:
        try:
            from piper import PiperVoice, SynthesisConfig
        except ImportError as exc:
            raise TTSError('Piper is not installed; run: pip install -e ".[piper]"') from exc

        self._voices = {
            language: PiperVoice.load(require_file(models_dir / file))
            for language, file in config.voices.items()
        }
        rates = {voice.config.sample_rate for voice in self._voices.values()}
        if len(rates) != 1:
            raise TTSError(f"Piper voices must share one sample rate, got {sorted(rates)}")
        self._rate = rates.pop()
        # Piper's length_scale is the inverse of speed: >1 is slower.
        self._syn_config = SynthesisConfig(length_scale=1.0 / speed)

    @property
    def sample_rate(self) -> int:
        return self._rate

    def synthesize(self, text: str, language: Language) -> AudioArray:
        voice = self._voices.get(language)
        if voice is None:
            raise TTSError(f"no Piper voice configured for '{language}'")
        chunks = [chunk.audio_float_array for chunk in voice.synthesize(text, self._syn_config)]
        if not chunks:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(chunks).astype(np.float32)
