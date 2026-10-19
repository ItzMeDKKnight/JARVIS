"""'Hey Jarvis' wake word detection with openWakeWord's ONNX models.

This is a small port of the streaming inference in openWakeWord 0.6.0
(https://github.com/dscripka/openWakeWord, Apache-2.0, (c) David Scripka),
written against onnxruntime directly. We don't depend on the package: it
pulls in scipy, scikit-learn and requests for features we don't use, and has
not been updated since 2024. The pretrained models are CC BY-NC-SA 4.0 and are
downloaded by scripts/download_models.py, never committed.

Pipeline, once per 80 ms frame of 16 kHz audio (1280 samples):

  last 1280 + 480 samples ─► melspectrogram.onnx ─► x/10 + 2 ─► mel buffer
  last 76 mel frames      ─► embedding_model.onnx ─► one 96-dim embedding
  last 16 embeddings      ─► hey_jarvis.onnx      ─► score in [0, 1]

Everything runs on one CPU thread.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import onnxruntime as ort

from jarvis.audio.types import AudioArray
from jarvis.config import WakeWordConfig

FRAME_SAMPLES = 1280  # 80 ms at 16 kHz
_MEL_CONTEXT = 160 * 3  # extra samples the melspectrogram window needs
_MEL_WINDOW = 76
_MEL_BINS = 32
_MEL_BUFFER_MAX = 10 * 97  # ~10 s of mel frames
_FEATURE_BUFFER_MAX = 120  # ~10 s of embeddings
_WARMUP_FRAMES = 5  # openWakeWord reports 0 for the first five predictions
_SEED = 1234  # initial "noise" history must be deterministic


class WakeWordError(Exception):
    """The wake word models could not be loaded; message is user-safe."""


def _session(path: Path) -> ort.InferenceSession:
    if not path.is_file():
        raise WakeWordError(f"wake word model missing: {path}. Run scripts/download_models.py.")
    options = ort.SessionOptions()
    options.intra_op_num_threads = 1
    options.inter_op_num_threads = 1
    return ort.InferenceSession(str(path), sess_options=options, providers=["CPUExecutionProvider"])


class WakeWordDetector:
    """Streaming detector: feed any-sized blocks, get the best score among new frames."""

    def __init__(self, config: WakeWordConfig, models_dir: Path) -> None:
        self._mel = _session(models_dir / config.melspectrogram_file)
        self._embedding = _session(models_dir / config.embedding_file)
        self._wake = _session(models_dir / config.model_file)
        self._wake_input = self._wake.get_inputs()[0].name
        self._window = int(self._wake.get_inputs()[0].shape[1])
        self.reset()

    def reset(self) -> None:
        """Forget all history (after a detection, so one utterance can't trigger twice)."""
        self._pending = np.zeros(0, dtype=np.float32)
        self._raw = np.zeros(0, dtype=np.float32)
        self._mel_buffer = np.ones((_MEL_WINDOW, _MEL_BINS), dtype=np.float32)
        noise = np.random.default_rng(_SEED).integers(-1000, 1000, 16000 * 4).astype(np.float32)
        self._features = self._embed_clip(noise)
        self._frames = 0

    def process(self, audio: AudioArray) -> float:
        """Add float audio in [-1, 1]; return the highest score of the frames it completed."""
        # The models were trained on 16-bit PCM values, passed as floats.
        pcm = np.clip(np.round(audio * 32767), -32768, 32767).astype(np.float32)
        self._pending = np.concatenate([self._pending, pcm])
        best = 0.0
        while self._pending.size >= FRAME_SAMPLES:
            frame, self._pending = self._pending[:FRAME_SAMPLES], self._pending[FRAME_SAMPLES:]
            best = max(best, self._process_frame(frame))
        return best

    def _process_frame(self, frame: np.ndarray) -> float:
        self._raw = np.concatenate([self._raw, frame])[-(FRAME_SAMPLES + _MEL_CONTEXT) :]
        self._mel_buffer = np.vstack([self._mel_buffer, self._melspectrogram(self._raw)])[
            -_MEL_BUFFER_MAX:
        ]
        window = self._mel_buffer[-_MEL_WINDOW:][None, :, :, None]
        embedding = self._embedding.run(None, {"input_1": window})[0].reshape(1, -1)
        self._features = np.vstack([self._features, embedding])[-_FEATURE_BUFFER_MAX:]

        features = self._features[-self._window :][None].astype(np.float32)
        score = float(self._wake.run(None, {self._wake_input: features})[0].reshape(-1)[0])
        self._frames += 1
        return 0.0 if self._frames <= _WARMUP_FRAMES else score

    def _melspectrogram(self, samples: np.ndarray) -> np.ndarray:
        output = np.asarray(self._mel.run(None, {"input": samples[None, :].astype(np.float32)})[0])
        # The same rescaling openWakeWord applies to match Google's speech_embedding inputs.
        spec: np.ndarray = (output.reshape(-1, _MEL_BINS) / 10 + 2).astype(np.float32)
        return spec

    def _embed_clip(self, samples: np.ndarray) -> np.ndarray:
        """Embeddings for a whole clip (used to seed the history, as openWakeWord does)."""
        spec = self._melspectrogram(samples)
        windows = [
            spec[i : i + _MEL_WINDOW]
            for i in range(0, spec.shape[0], 8)
            if spec[i : i + _MEL_WINDOW].shape[0] == _MEL_WINDOW
        ]
        batch = np.array(windows, dtype=np.float32)[..., None]
        embeddings = np.asarray(self._embedding.run(None, {"input_1": batch})[0])
        return embeddings.reshape(len(windows), -1)
