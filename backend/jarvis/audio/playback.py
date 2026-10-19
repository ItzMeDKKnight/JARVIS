"""Speaker output with a queue, level metering and instant stop.

One output stream stays open for the whole voice session so a new sentence
starts playing without device-open latency. Synthesised chunks are queued
and played back to back. The PortAudio callback (its own thread) reports
three things to the asyncio loop: the TTS level for the HUD, "audio started"
(drives the SPEAKING state and the first-audio latency mark), and "queue
drained".
"""

from __future__ import annotations

import asyncio
import collections
import logging
import threading
from collections.abc import Callable
from typing import Protocol

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from jarvis.audio.capture import AudioDeviceError, resolve_device
from jarvis.audio.levels import rms_level
from jarvis.audio.types import AudioArray
from jarvis.config import AudioConfig

logger = logging.getLogger(__name__)

_OUTPUT_BLOCK_MS = 20


class AudioSink(Protocol):
    @property
    def is_playing(self) -> bool:
        """True while queued audio remains to be heard."""
        ...

    def enqueue(self, audio: AudioArray) -> None:
        """Queue audio (at the sink's sample rate) to play after what is already queued."""
        ...

    def stop(self) -> None:
        """Silence immediately and drop everything queued."""
        ...

    async def drain(self) -> None:
        """Wait until everything queued has been played (or stop() was called)."""
        ...


class AudioPlayer:
    def __init__(
        self,
        config: AudioConfig,
        sample_rate: int,
        loop: asyncio.AbstractEventLoop,
        *,
        on_level: Callable[[float], None],
        on_start: Callable[[], None],
        on_idle: Callable[[], None],
    ) -> None:
        self._config = config
        self._rate = sample_rate
        self._loop = loop
        self._on_level = on_level
        self._on_start = on_start
        self._on_idle = on_idle
        self._queue: collections.deque[AudioArray] = collections.deque()
        self._position = 0
        self._playing = False
        self._lock = threading.Lock()
        self._idle = asyncio.Event()
        self._idle.set()
        self._stream: sd.OutputStream | None = None

    def open(self) -> None:
        try:
            self._stream = sd.OutputStream(
                samplerate=self._rate,
                channels=1,
                dtype="float32",
                blocksize=self._rate * _OUTPUT_BLOCK_MS // 1000,
                device=resolve_device(self._config.output_device, "output"),
                callback=self._callback,
            )
            self._stream.start()
        except (sd.PortAudioError, ValueError) as exc:
            self._stream = None
            raise AudioDeviceError(f"could not open the speakers: {exc}") from exc

    def close(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()

    @property
    def sample_rate(self) -> int:
        return self._rate

    @property
    def is_playing(self) -> bool:
        return not self._idle.is_set()

    def enqueue(self, audio: AudioArray) -> None:
        if audio.size == 0:
            return
        with self._lock:
            self._queue.append(audio)
        self._idle.clear()

    def stop(self) -> None:
        with self._lock:
            self._queue.clear()
            self._position = 0
            was_playing = self._playing
            self._playing = False
        self._idle.set()
        if was_playing:
            self._on_level(0.0)
            self._on_idle()

    async def drain(self) -> None:
        await self._idle.wait()

    def _callback(
        self, outdata: npt.NDArray[np.float32], frames: int, time: object, status: sd.CallbackFlags
    ) -> None:
        out = np.zeros(frames, dtype=np.float32)
        filled = 0
        with self._lock:
            while filled < frames and self._queue:
                current = self._queue[0]
                take = min(frames - filled, current.size - self._position)
                out[filled : filled + take] = current[self._position : self._position + take]
                filled += take
                self._position += take
                if self._position >= current.size:
                    self._queue.popleft()
                    self._position = 0
            started = filled > 0 and not self._playing
            finished = filled == 0 and self._playing
            if started:
                self._playing = True
            if finished:
                self._playing = False
        outdata[:, 0] = out

        if started:
            self._loop.call_soon_threadsafe(self._on_start)
        if self._playing or finished:
            level = rms_level(out, self._config.level_floor_db, self._config.level_ceiling_db)
            self._loop.call_soon_threadsafe(self._on_level, level)
        if finished:
            self._loop.call_soon_threadsafe(self._finish)

    def _finish(self) -> None:
        # Runs on the event loop. Audio enqueued since the callback means we are not idle.
        with self._lock:
            if self._queue or self._playing:
                return
        self._idle.set()
        self._on_idle()
