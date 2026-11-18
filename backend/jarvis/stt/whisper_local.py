"""faster-whisper (CTranslate2) speech-to-text, fully local, int8 on the CPU.

Models are loaded only from ``models/whisper-<size>`` with
``local_files_only=True``, so faster-whisper never contacts the Hugging Face
Hub. scripts/download_models.py fills that folder once, with checksums.

Latency: Whisper's encoder always processes a padded 30-second window, so one
encoder pass costs about a second on this class of CPU, however short the
utterance. faster-whisper's ``transcribe(language=None)`` encodes twice
(once to detect the language, once to decode), and restricting the language
afterwards could add a third pass. Instead we:

1. run the VAD once ourselves, returning immediately when there is no
   speech (this also avoids Whisper's habit of inventing text for silence);
2. detect the language, keeping only the allowed ones (en/hi). Whisper often
   labels Hindi speech as Urdu, since the spoken languages are nearly
   identical but the scripts differ;
3. transcribe with that language, and the model subclass below reuses the
   encoder output from step 2 when the input window is byte-identical.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from pathlib import Path

import numpy as np
from faster_whisper import WhisperModel
from faster_whisper.vad import VadOptions, collect_chunks, get_speech_timestamps

from jarvis.audio.types import AudioArray
from jarvis.config import STTConfig
from jarvis.stt.base import STTError, Transcription
from jarvis.utils.language import Language

logger = logging.getLogger(__name__)

SAMPLE_RATE = 16_000


def model_dir(models_root: Path, size: str) -> Path:
    return models_root / f"whisper-{size}"


class _EncoderCachingModel(WhisperModel):  # type: ignore[misc]
    """WhisperModel that remembers its last encoder result.

    Language detection and decoding encode the same padded window; caching
    by content hash saves a full encoder pass. Any difference in the input
    simply misses the cache, so correctness never depends on it.
    """

    _cache_key: bytes | None = None
    _cache_value: object = None
    cache_hits = 0

    def encode(self, features: np.ndarray) -> object:
        key = hashlib.blake2b(np.ascontiguousarray(features).tobytes(), digest_size=16).digest()
        if key == self._cache_key:
            self.cache_hits += 1
            return self._cache_value
        value = super().encode(features)
        self._cache_key, self._cache_value = key, value
        return value


class WhisperSTT:
    def __init__(self, config: STTConfig, models_root: Path, *, model: str | None = None) -> None:
        """Load the model. ``model`` overrides config.stt.model (used by the benchmark)."""
        path = model_dir(models_root, model or config.model)
        if not (path / "model.bin").is_file():
            raise STTError(f"Whisper model missing in {path}. Run scripts/download_models.py.")
        self._config = config
        self._vad = VadOptions()
        # One pass at a time: a stop can abandon a transcription while Whisper is still
        # running it, and the encoder cache below is not safe for concurrent calls.
        self._lock = threading.Lock()
        self._model = _EncoderCachingModel(
            str(path),
            device="cpu",
            compute_type=config.compute_type,
            cpu_threads=config.cpu_threads,
            local_files_only=True,
        )

    def transcribe(self, audio: AudioArray) -> Transcription:
        with self._lock:
            return self._transcribe(audio)

    def _transcribe(self, audio: AudioArray) -> Transcription:
        speech = self._speech_only(audio)
        if speech.size == 0:
            return Transcription(
                text="",
                language=self._config.allowed_languages[0],
                language_probability=0.0,
                detected_language="",
            )

        # transcribe() encodes features[..., :-1] (it drops the final frame before padding),
        # so detect on exactly those frames to make the encoder inputs byte-identical.
        features = self._model.feature_extractor(speech)[..., :-1]
        guess, probability, all_probs = self._model.detect_language(features=features)
        chosen = _best_allowed(all_probs, self._config.allowed_languages)
        segments, _ = self._model.transcribe(
            speech,
            language=chosen,
            beam_size=self._config.beam_size,
            vad_filter=False,  # already applied above
            condition_on_previous_text=False,
            without_timestamps=True,
        )
        text = " ".join(segment.text.strip() for segment in segments).strip()
        return Transcription(
            text=text,
            language=chosen,
            language_probability=float(probability),
            detected_language=str(guess),
        )

    def forget_cached_encoding(self) -> None:
        """Drop the cached encoder output (benchmarks use this so warm-up can't skew timings)."""
        self._model._cache_key = None
        self._model._cache_value = None

    def _speech_only(self, audio: AudioArray) -> AudioArray:
        if not self._config.vad_filter:
            return audio
        timestamps = get_speech_timestamps(audio, self._vad)
        if not timestamps:
            return np.zeros(0, dtype=np.float32)
        chunks, _ = collect_chunks(audio, timestamps)
        return np.concatenate(chunks).astype(np.float32)


def _best_allowed(
    probabilities: list[tuple[str, float]] | None, allowed: tuple[Language, ...]
) -> Language:
    """Most probable language among ``allowed``; the first allowed one if none has a score."""
    scores = dict(probabilities or [])
    return max(allowed, key=lambda language: scores.get(language, 0.0))
