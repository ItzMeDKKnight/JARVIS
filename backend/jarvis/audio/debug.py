"""Opt-in saving of recorded utterances, for troubleshooting only.

Only used when ``audio.debug_save_audio`` is true (off by default). Files go to
the user data folder, never the repository, and are plain 16-bit WAV so they
can be inspected with any audio tool and deleted by hand.
"""

from __future__ import annotations

import time
import wave
from pathlib import Path

import numpy as np

from jarvis.audio.types import AudioArray


def save_wav(audio: AudioArray, sample_rate: int, folder: Path) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = (
        folder / f"utterance-{time.strftime('%Y%m%d-%H%M%S')}-{time.monotonic_ns() % 1000:03d}.wav"
    )
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(sample_rate)
        handle.writeframes(pcm.tobytes())
    return path
