"""Process-wide privacy guards, applied at startup before heavy imports.

* Hugging Face Hub: forced offline with telemetry disabled. faster-whisper
  imports it, and models are loaded from local folders only, so it never
  needs the network at runtime.
* ONNX Runtime: Windows builds can emit telemetry events; we switch them off
  explicitly (``disable_telemetry_events``).
* ``DO_NOT_TRACK`` is the community-wide opt-out honoured by many tools.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "HF_HUB_DISABLE_IMPLICIT_TOKEN": "1",
    "DO_NOT_TRACK": "1",
}


def apply_offline_env() -> None:
    """Set offline/telemetry-off variables. Must run before faster-whisper is imported."""
    for name, value in OFFLINE_ENV.items():
        os.environ[name] = value


def disable_onnxruntime_telemetry() -> None:
    """Turn off ONNX Runtime's telemetry events (Windows builds)."""
    import onnxruntime

    onnxruntime.disable_telemetry_events()
    logger.info("onnxruntime telemetry events disabled")
