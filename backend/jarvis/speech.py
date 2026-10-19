"""Speech output: how the assistant's words become sound (or, in text mode, don't).

The conversation loop talks to a :class:`Speaker`. It feeds streamed text,
marks the end of each reply, and says fixed lines (narration, fallbacks).
The speaker tells the loop when audio actually starts and when it goes
quiet, which drives the SPEAKING state and the first-audio latency mark.

* :class:`TextSpeaker` (text mode) has no audio. It reports "started"
  immediately so the state machine behaves as it did in Phase 1.
* :class:`VoiceSpeaker` chunks text at sentence boundaries, splits each chunk
  by script (Latin → English voice, Devanagari → Hindi voice), synthesises on
  a worker thread, applies postfx, and queues audio on the player. Chunk n+1
  is synthesised while chunk n is playing.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Coroutine
from typing import Any, Protocol

from jarvis.audio.playback import AudioSink
from jarvis.audio.types import AudioArray
from jarvis.config import PostFXConfig, SegmenterConfig
from jarvis.tts.base import TTSEngine, TTSError
from jarvis.tts.postfx import apply_postfx
from jarvis.tts.segmenter import SentenceChunker, clean_for_speech, split_by_script
from jarvis.utils.language import Language

logger = logging.getLogger(__name__)


class SpeakerListener(Protocol):
    async def audio_started(self) -> None:
        """Sound (or text, in text mode) has started reaching the user."""
        ...

    async def audio_idle(self) -> None:
        """Everything queued so far has been heard."""
        ...


class Speaker(Protocol):
    def set_listener(self, listener: SpeakerListener) -> None: ...

    async def feed(self, fragment: str, language: Language) -> None:
        """Add a streamed fragment of the current reply."""
        ...

    async def end_utterance(self) -> None:
        """The current reply is complete; speak whatever is still buffered."""
        ...

    async def say(self, line: str, language: Language) -> None:
        """Queue a complete line (narration, a fallback, a question)."""
        ...

    @property
    def is_playing(self) -> bool:
        """True while there is queued or playing speech."""
        ...

    async def drain(self) -> None:
        """Wait until everything queued has been spoken."""
        ...

    async def stop(self) -> None:
        """Stop speaking immediately and drop everything queued."""
        ...


class TextSpeaker:
    """No audio: text appears instantly, so 'audio' starts as soon as text exists."""

    def __init__(self) -> None:
        self._listener: SpeakerListener | None = None

    def set_listener(self, listener: SpeakerListener) -> None:
        self._listener = listener

    async def feed(self, fragment: str, language: Language) -> None:
        await self._started()

    async def end_utterance(self) -> None:
        await self._idle()

    async def say(self, line: str, language: Language) -> None:
        # A printed line is "heard" instantly, so it starts and finishes at once.
        await self._started()
        await self._idle()

    @property
    def is_playing(self) -> bool:
        return False

    async def drain(self) -> None:
        return

    async def stop(self) -> None:
        return

    async def _started(self) -> None:
        if self._listener is not None:
            await self._listener.audio_started()

    async def _idle(self) -> None:
        if self._listener is not None:
            await self._listener.audio_idle()


class VoiceSpeaker:
    def __init__(
        self,
        engine: TTSEngine,
        sink: AudioSink,
        *,
        segmenter: SegmenterConfig,
        postfx: PostFXConfig,
    ) -> None:
        self._engine = engine
        self._sink = sink
        self._segmenter_config = segmenter
        self._postfx = postfx
        self._chunker = SentenceChunker(segmenter)
        self._language: Language = "en"
        self._queue: asyncio.Queue[tuple[int, str, Language]] = asyncio.Queue()
        self._generation = 0  # bumped by stop(); stale work from older generations is dropped
        self._synthesizing = False
        self._listener: SpeakerListener | None = None
        self._worker: asyncio.Task[None] | None = None
        # Strong references to fire-and-forget notification tasks (asyncio keeps only weak ones).
        self._notifications: set[asyncio.Task[None]] = set()

    def set_listener(self, listener: SpeakerListener) -> None:
        self._listener = listener

    def start(self) -> None:
        self._worker = asyncio.create_task(self._run(), name="tts-worker")

    async def close(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._worker

    # -- called by the player (via the event loop) -------------------------------

    def on_player_start(self) -> None:
        if self._listener is not None:
            self._notify(self._listener.audio_started())

    def on_player_idle(self) -> None:
        # A gap while the next chunk is still being synthesised is not "idle".
        if self._listener is not None and not self._pending():
            self._notify(self._listener.audio_idle())

    def _notify(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._notifications.add(task)
        task.add_done_callback(self._notifications.discard)

    # -- Speaker protocol ---------------------------------------------------------

    async def feed(self, fragment: str, language: Language) -> None:
        self._language = language
        for chunk in self._chunker.feed(fragment):
            self._queue.put_nowait((self._generation, chunk, language))

    async def end_utterance(self) -> None:
        for chunk in self._chunker.flush():
            self._queue.put_nowait((self._generation, chunk, self._language))
        self._chunker = SentenceChunker(self._segmenter_config)

    async def say(self, line: str, language: Language) -> None:
        cleaned = clean_for_speech(line)
        if cleaned:
            self._queue.put_nowait((self._generation, cleaned, language))

    @property
    def is_playing(self) -> bool:
        return self._pending() or self._sink.is_playing

    async def drain(self) -> None:
        await self._queue.join()
        await self._sink.drain()

    async def stop(self) -> None:
        self._generation += 1
        self._chunker = SentenceChunker(self._segmenter_config)
        while not self._queue.empty():
            self._queue.get_nowait()
            self._queue.task_done()
        self._sink.stop()

    # -- internals ------------------------------------------------------------------

    def _pending(self) -> bool:
        return self._synthesizing or not self._queue.empty()

    async def _run(self) -> None:
        while True:
            generation, text, language = await self._queue.get()
            self._synthesizing = True
            try:
                if generation == self._generation:
                    await self._speak_chunk(generation, text, language)
            finally:
                self._synthesizing = False
                self._queue.task_done()

    async def _speak_chunk(self, generation: int, text: str, language: Language) -> None:
        for segment in split_by_script(text, default=language):
            try:
                audio = await asyncio.to_thread(self._render, segment.text, segment.language)
            except TTSError:
                logger.warning("synthesis failed", extra={"chars": len(segment.text)})
                continue
            except Exception:
                logger.exception("unexpected synthesis error")
                continue
            if generation != self._generation:
                return  # stop() was called while we were synthesising
            self._sink.enqueue(audio)

    def _render(self, text: str, language: Language) -> AudioArray:
        audio = self._engine.synthesize(text, language)
        return apply_postfx(audio, self._engine.sample_rate, self._postfx)
