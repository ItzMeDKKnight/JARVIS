"""Shared audio type aliases."""

from __future__ import annotations

import numpy as np
import numpy.typing as npt

# Mono float32 samples in [-1, 1].
AudioArray = npt.NDArray[np.float32]
