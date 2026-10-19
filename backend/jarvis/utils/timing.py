"""Latency measurement helpers.

The brief's latency target (first audio within about two seconds of the end of
speech) has to be measured, not guessed. A :class:`Stopwatch` records named
marks relative to one start time so each turn can log, for example,
``first_token_ms`` and ``total_ms`` as a single structured record.
"""

from __future__ import annotations

import time


class Stopwatch:
    """Monotonic stopwatch with named, first-write-wins marks."""

    def __init__(self) -> None:
        self._start = time.perf_counter()
        self._marks: dict[str, float] = {}

    def mark(self, name: str) -> None:
        """Record ``name`` the first time it happens; later calls are ignored.

        First-write-wins lets callers mark "first_token" inside a loop
        without tracking whether they already did.
        """
        self._marks.setdefault(name, time.perf_counter())

    def elapsed_ms(self) -> int:
        return round((time.perf_counter() - self._start) * 1000)

    def as_ms(self) -> dict[str, int]:
        """All marks as whole milliseconds since start, suitable for structured logging."""
        return {f"{name}_ms": round((t - self._start) * 1000) for name, t in self._marks.items()}
