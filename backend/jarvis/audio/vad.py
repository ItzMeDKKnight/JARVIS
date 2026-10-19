"""Streaming voice-activity detection and end-of-speech detection.

Uses the Silero VAD v6 ONNX model that ships inside faster-whisper (MIT), so
there is nothing extra to download and no PyTorch. faster-whisper only runs
it over whole recordings; :class:`StreamingVAD` keeps the model's recurrent
state (``h``/``c``) and 64-sample context between calls so it can process
the live microphone 32 ms at a time.

:class:`Endpointer` turns per-chunk speech probabilities into "speech
started", "speech ended" and "nobody spoke" events. It is plain logic,
so it is easy to test.
"""

from __future__ import annotations

import os
from enum import Enum

import numpy as np
import onnxruntime as ort

from jarvis.audio.types import AudioArray
from jarvis.config import VADConfig

CHUNK_SAMPLES = 512  # 32 ms at 16 kHz: the size the model expects
_CONTEXT = 64
CHUNK_MS = 32


def bundled_model_path() -> str:
    from faster_whisper.utils import get_assets_path

    return os.path.join(get_assets_path(), "silero_vad_v6.onnx")


class StreamingVAD:
    def __init__(self, model_path: str | None = None) -> None:
        options = ort.SessionOptions()
        options.intra_op_num_threads = 1
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            model_path or bundled_model_path(),
            sess_options=options,
            providers=["CPUExecutionProvider"],
        )
        self.reset()

    def reset(self) -> None:
        self._h = np.zeros((1, 1, 128), dtype=np.float32)
        self._c = np.zeros((1, 1, 128), dtype=np.float32)
        self._context = np.zeros(_CONTEXT, dtype=np.float32)
        self._pending = np.zeros(0, dtype=np.float32)

    def process(self, audio: AudioArray) -> list[float]:
        """Add audio; return one speech probability per completed 32 ms chunk."""
        self._pending = np.concatenate([self._pending, audio.astype(np.float32)])
        probabilities = []
        while self._pending.size >= CHUNK_SAMPLES:
            chunk = self._pending[:CHUNK_SAMPLES]
            self._pending = self._pending[CHUNK_SAMPLES:]
            batch = np.concatenate([self._context, chunk])[None, :]
            output, self._h, self._c = self._session.run(
                None, {"input": batch, "h": self._h, "c": self._c}
            )
            self._context = chunk[-_CONTEXT:]
            probabilities.append(float(np.asarray(output).reshape(-1)[0]))
        return probabilities


class SpeechEvent(Enum):
    STARTED = "started"
    ENDED = "ended"
    NO_SPEECH = "no_speech"  # nothing was said before the start timeout


class Endpointer:
    """Decides when an utterance starts and ends from 32 ms speech probabilities.

    * Speech starts at the first chunk above ``threshold``.
    * It ends after ``end_silence_ms`` of chunks below ``neg_threshold``.
      Values between the two thresholds keep the current state (hysteresis).
    * A "speech" burst shorter than ``min_speech_ms`` followed by silence is
      treated as noise, and waiting resumes.
    * If no real speech starts within the start timeout, NO_SPEECH is reported.
    """

    def __init__(self, config: VADConfig) -> None:
        self._config = config
        self.reset(config.start_timeout_s)

    def reset(self, start_timeout_s: float) -> None:
        self._timeout_chunks = max(1, round(start_timeout_s * 1000 / CHUNK_MS))
        self._waited = 0
        self._in_speech = False
        self._speech_chunks = 0
        self._silence_chunks = 0
        self._started_reported = False
        self._done = False  # an end (or a timeout) was reported; ignore input until reset

    @property
    def in_speech(self) -> bool:
        return self._in_speech

    def feed(self, probability: float) -> SpeechEvent | None:
        """Process one 32 ms probability; each event is reported at most once."""
        if self._done:
            return None
        config = self._config
        if not self._in_speech:
            if probability >= config.threshold:
                self._in_speech = True
                self._speech_chunks = 1
                self._silence_chunks = 0
                return None
            self._waited += 1
            if not self._started_reported and self._waited >= self._timeout_chunks:
                self._done = True
                return SpeechEvent.NO_SPEECH
            return None

        if probability < config.neg_threshold:
            self._silence_chunks += 1
        elif probability >= config.threshold:
            self._speech_chunks += 1 + self._silence_chunks  # short pauses count as speech
            self._silence_chunks = 0

        long_enough = self._speech_chunks * CHUNK_MS >= config.min_speech_ms
        if long_enough and not self._started_reported:
            self._started_reported = True
            return SpeechEvent.STARTED
        if self._silence_chunks * CHUNK_MS >= config.end_silence_ms:
            if self._started_reported:
                self._done = True
                return SpeechEvent.ENDED
            # A blip (cough, click): forget it and keep waiting for real speech.
            self._in_speech = False
            self._waited += self._speech_chunks + self._silence_chunks
            self._speech_chunks = self._silence_chunks = 0
        return None
