"""Text-to-speech interface.

Engines are synchronous and CPU-bound; callers run them in a worker thread.
Each engine owns one voice per language and must be fully local: it may only
load model files from the models folder and must never download anything.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from jarvis.audio.types import AudioArray
from jarvis.config import TTSConfig
from jarvis.utils.language import Language

__all__ = ["AudioArray", "TTSEngine", "TTSError", "create_engine", "require_file"]


class TTSError(Exception):
    """Synthesis failed or the engine could not be loaded; message is user-safe."""


class TTSEngine(Protocol):
    @property
    def sample_rate(self) -> int:
        """Sample rate of the audio returned by :meth:`synthesize`."""
        ...

    def synthesize(self, text: str, language: Language) -> AudioArray:
        """Return mono float32 audio in [-1, 1] for ``text`` spoken in ``language``."""
        ...


def create_engine(config: TTSConfig, models_dir: Path) -> TTSEngine:
    """Build the configured engine. Imports are local so the unused engine never loads."""
    if config.engine == "kokoro":
        from jarvis.tts.kokoro_engine import KokoroEngine

        return KokoroEngine(config.kokoro, models_dir, speed=config.speed)
    from jarvis.tts.piper_engine import PiperEngine

    return PiperEngine(config.piper, models_dir, speed=config.speed)


def require_file(path: Path) -> Path:
    """Fail with a clear, actionable message instead of a deep library traceback."""
    if not path.is_file():
        raise TTSError(f"model file missing: {path}. Run scripts/download_models.py first.")
    return path
