"""Loudness metering for the HUD's reactive aura.

RMS is converted to dBFS and mapped linearly between a floor and a ceiling,
because human loudness perception is roughly logarithmic: a linear RMS meter
would sit near zero for normal speech and jump only on shouts.
"""

from __future__ import annotations

import math

import numpy as np
import numpy.typing as npt


def rms_level(block: npt.NDArray[np.float32], floor_db: float, ceiling_db: float) -> float:
    """Map a block's RMS loudness to 0..1 between ``floor_db`` and ``ceiling_db``."""
    if block.size == 0:
        return 0.0
    rms = float(np.sqrt(np.mean(np.square(block, dtype=np.float64))))
    if rms <= 0:
        return 0.0
    db = 20 * math.log10(rms)
    return min(1.0, max(0.0, (db - floor_db) / (ceiling_db - floor_db)))
