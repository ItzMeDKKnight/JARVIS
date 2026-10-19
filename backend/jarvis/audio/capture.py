"""Microphone input with sounddevice.

:class:`MicStream` delivers 32 ms blocks into an asyncio queue. In wake-word
mode it stays open for the whole session, so "Hey Jarvis" can be heard. In
push-to-talk-only mode it is opened only while the key is held, so the
Windows "microphone in use" indicator shows exactly when you are recorded.
Audio is kept in memory only; nothing is written to disk here.

PortAudio calls the stream callback on its own thread. The callback only
copies the block and computes a level; everything else is handed to the
asyncio loop with ``call_soon_threadsafe``.
"""

from __future__ import annotations

import asyncio
import collections
import logging
from collections.abc import Callable

import numpy as np
import numpy.typing as npt
import sounddevice as sd

from jarvis.audio.levels import rms_level
from jarvis.audio.types import AudioArray
from jarvis.config import AudioConfig

logger = logging.getLogger(__name__)

LevelCallback = Callable[[float], None]
# ~6 s of 32 ms blocks; if the consumer stalls, the oldest audio is dropped.
_QUEUE_BLOCKS = 200


class AudioDeviceError(Exception):
    """The audio device could not be opened; message is user-safe."""


def resolve_device(device: str | int | None, kind: str) -> int | None:
    """Turn a config value (index, name substring, or None) into a PortAudio index."""
    if device is None or isinstance(device, int):
        return device
    channels_key = "max_input_channels" if kind == "input" else "max_output_channels"
    for index, info in enumerate(sd.query_devices()):
        if device.casefold() in str(info["name"]).casefold() and info[channels_key] > 0:
            return index
    raise AudioDeviceError(f"no {kind} device matching '{device}'")


class MicStream:
    """Mono float32 microphone blocks, a level meter, and a short pre-roll memory."""

    def __init__(
        self,
        config: AudioConfig,
        loop: asyncio.AbstractEventLoop,
        on_level: LevelCallback,
        *,
        preroll_ms: int,
    ) -> None:
        self._config = config
        self._loop = loop
        self._on_level = on_level
        self._block = config.input_sample_rate * config.block_ms // 1000
        self._queue: asyncio.Queue[AudioArray] = asyncio.Queue(maxsize=_QUEUE_BLOCKS)
        preroll_blocks = max(1, preroll_ms // config.block_ms)
        self._preroll: collections.deque[AudioArray] = collections.deque(maxlen=preroll_blocks)
        self._stream: sd.InputStream | None = None

    @property
    def is_open(self) -> bool:
        return self._stream is not None

    def start(self) -> None:
        if self._stream is not None:
            return
        self.flush()
        try:
            self._stream = sd.InputStream(
                samplerate=self._config.input_sample_rate,
                channels=1,
                dtype="float32",
                blocksize=self._block,
                device=resolve_device(self._config.input_device, "input"),
                callback=self._callback,
            )
            self._stream.start()
        except (sd.PortAudioError, ValueError) as exc:
            self._stream = None
            raise AudioDeviceError(f"could not open the microphone: {exc}") from exc

    def stop(self) -> None:
        stream, self._stream = self._stream, None
        if stream is not None:
            stream.stop()
            stream.close()
        self._on_level(0.0)

    async def read(self) -> AudioArray:
        """Next block, waiting if necessary."""
        return await self._queue.get()

    def drain_nowait(self) -> list[AudioArray]:
        """Everything queued right now, without waiting."""
        blocks = []
        while not self._queue.empty():
            blocks.append(self._queue.get_nowait())
        return blocks

    def flush(self) -> None:
        """Drop queued and pre-roll audio (stale audio from before a state change)."""
        self.drain_nowait()
        self._preroll.clear()

    def preroll(self) -> AudioArray:
        """The last ``preroll_ms`` of audio, for speech that began just before a trigger."""
        if not self._preroll:
            return np.zeros(0, dtype=np.float32)
        return np.concatenate(list(self._preroll))

    def _callback(
        self, indata: npt.NDArray[np.float32], frames: int, time: object, status: sd.CallbackFlags
    ) -> None:
        if status:
            logger.debug("mic status", extra={"status": str(status)})
        block = np.array(indata[:, 0], dtype=np.float32)
        level = rms_level(block, self._config.level_floor_db, self._config.level_ceiling_db)
        self._loop.call_soon_threadsafe(self._push, block, level)

    def _push(self, block: AudioArray, level: float) -> None:
        if self._queue.full():
            self._queue.get_nowait()
        self._queue.put_nowait(block)
        self._preroll.append(block)
        self._on_level(level)
