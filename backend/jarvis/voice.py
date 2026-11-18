"""The hands-free voice loop: wake word, end-of-speech detection, follow-ups, barge-in.

One loop reads 32 ms microphone blocks and routes each by the current mode:

  IDLE       blocks feed the wake-word detector; "Hey Jarvis" wakes Jarvis
  LISTENING  blocks are recorded; the VAD decides when you stopped talking
  PTT        blocks are recorded while the push-to-talk key is held
  BUSY       Jarvis is transcribing, thinking or speaking; blocks feed only the
             wake-word detector (stricter threshold), so "Hey Jarvis" barges in.
             Nothing heard while Jarvis talks is ever transcribed (half-duplex).
  MUTED      the microphone is closed (Windows' mic indicator goes off); only
             typed input from the HUD works.

Session rules (Phase 3):
* Waking from IDLE plays the greeting only if the last conversation ended long
  enough ago (``session.greeting_after_idle_min``), then listens.
* After a reply, a follow-up window (``session.follow_up_s``) listens without the
  wake word; silence returns to IDLE.
* Push-to-talk still works alongside the wake word, and interrupts too.
* A tool's yes/no question listens for a spoken answer the same way.

HUD control (Phase 5): :meth:`VoiceSession.toggle_mute`, :meth:`interrupt`,
:meth:`submit_text` (typed input, which also answers a pending yes/no
question) and :meth:`ptt_start` / :meth:`ptt_stop` (Space held in the HUD).
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import Callable, Coroutine
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

import numpy as np
from pydantic import BaseModel

from jarvis.assistant import Assistant
from jarvis.audio.capture import AudioDeviceError
from jarvis.audio.debug import save_wav
from jarvis.audio.ptt import CommandPushToTalk, HoldResult, PushToTalkSource
from jarvis.audio.types import AudioArray
from jarvis.audio.vad import Endpointer, SpeechEvent
from jarvis.config import JarvisConfig, LocalizedText
from jarvis.confirm import is_yes
from jarvis.events import AssistantState, AudioLevel, EventBus, Status, StopTarget, Transcript
from jarvis.speech import Speaker
from jarvis.state import StateMachine
from jarvis.stt.base import SpeechToText, Transcription
from jarvis.utils.language import Language, detect_language
from jarvis.utils.logging import transcripts_enabled
from jarvis.utils.timing import Stopwatch

logger = logging.getLogger(__name__)

S = AssistantState

# The wake phrase often survives at the start of the recording; it is not part of the request.
_LEADING_WAKE_PHRASE = re.compile(
    r"^\s*(?:(?:hey|hi|okay|ok|हे)[\s,]*)?(?:jarvis|जार्विस|जारविस)[\s,.!?।]*", re.IGNORECASE
)


def strip_wake_phrase(text: str) -> str:
    return _LEADING_WAKE_PHRASE.sub("", text, count=1).strip()


class MicSource(Protocol):
    @property
    def is_open(self) -> bool: ...
    def start(self) -> None: ...
    def stop(self) -> None: ...
    async def read(self) -> AudioArray: ...
    def drain_nowait(self) -> list[AudioArray]: ...
    def flush(self) -> None: ...
    def preroll(self) -> AudioArray: ...


class WakeScorer(Protocol):
    def process(self, audio: AudioArray) -> float: ...
    def reset(self) -> None: ...


class SpeechProbabilities(Protocol):
    def process(self, audio: AudioArray) -> list[float]: ...
    def reset(self) -> None: ...


class Mode(Enum):
    IDLE = "idle"
    LISTENING = "listening"
    PTT = "ptt"
    BUSY = "busy"
    MUTED = "muted"


class Purpose(Enum):
    COMMAND = "command"  # after the wake word or push-to-talk
    FOLLOW_UP = "follow_up"  # the window after a reply
    ANSWER = "answer"  # a tool's yes/no question


class EventPump:
    """Publishes events from synchronous callbacks (audio threads hop to the loop first)."""

    def __init__(self, bus: EventBus) -> None:
        self._bus = bus
        self._tasks: set[asyncio.Task[None]] = set()

    def publish_soon(self, event: BaseModel) -> None:
        self.run_soon(self._bus.publish(event))

    def run_soon(self, coroutine: Coroutine[Any, Any, None]) -> None:
        task = asyncio.create_task(coroutine)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def mic_level(self, level: float) -> None:
        self.publish_soon(AudioLevel(source="mic", level=level))

    def tts_level(self, level: float) -> None:
        self.publish_soon(AudioLevel(source="tts", level=level))


class VoiceSession:
    def __init__(
        self,
        config: JarvisConfig,
        *,
        state: StateMachine,
        mic: MicSource,
        speaker: Speaker,
        stt: SpeechToText,
        pump: EventPump,
        wake: WakeScorer | None,
        vad: SpeechProbabilities | None,
        ptt: PushToTalkSource | None,
        debug_audio_dir: Path | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if wake is not None and vad is None:
            raise ValueError("the wake word needs a VAD to know when you stop talking")
        self._config = config
        self._state = state
        self._mic = mic
        self._speaker = speaker
        self._stt = stt
        self._pump = pump
        self._wake = wake
        self._vad = vad
        self._ptt = ptt
        self._debug_dir = debug_audio_dir
        self._clock = clock
        self._endpointer = Endpointer(config.vad)
        self._assistant: Assistant | None = None

        self._mode = Mode.IDLE
        self._purpose = Purpose.COMMAND
        self._recording: list[AudioArray] = []
        self._turn: asyncio.Task[None] | None = None
        self._answer: asyncio.Future[str] | None = None
        self._language: Language = "en"
        self._cooldown_until = 0.0
        self._session_ended_at: float | None = None  # None: no conversation yet, so greet
        self._hud_ptt = CommandPushToTalk(hold_threshold_ms=config.voice.hold_threshold_ms)
        self._muted = False
        self._follow_up_after_turn = False
        # Bumped by interrupt(): work started before a stop checks it and drops its result.
        self._stop_generation = 0
        self._stt_task: asyncio.Future[Transcription] | None = None

    def attach(self, assistant: Assistant) -> None:
        self._assistant = assistant

    @property
    def mode(self) -> Mode:
        return self._mode

    @property
    def language(self) -> Language:
        """Language of the last thing the user said."""
        return self._language

    @property
    def hands_free(self) -> bool:
        return self._wake is not None

    # -- main loops -------------------------------------------------------------------

    async def run(self) -> None:
        """Serve the microphone (and push-to-talk) until cancelled."""
        if self.hands_free:
            self._mic.start()  # AudioDeviceError propagates: voice mode can't work without it
        loops = [self._audio_loop(), self._ptt_loop(self._hud_ptt)]
        if self._ptt is not None:
            loops.append(self._ptt_loop(self._ptt))
        try:
            await asyncio.gather(*loops)
        finally:
            await self._interrupt_turn()
            self._mic.stop()

    async def _audio_loop(self) -> None:
        while True:
            await self.process_block(await self._mic.read())

    async def process_block(self, block: AudioArray) -> None:
        """Route one microphone block according to the current mode."""
        if self._mode is Mode.MUTED:
            return
        if self._mode is Mode.PTT:
            self._recording.append(block)
        elif self._mode is Mode.LISTENING:
            await self._listen(block)
        elif self._wake is not None:
            await self._score_wake_word(self._wake, block)

    async def _score_wake_word(self, wake: WakeScorer, block: AudioArray) -> None:
        wake_config = self._config.wake_word
        if self._mode is Mode.IDLE:
            threshold = wake_config.threshold
        elif wake_config.barge_in and self._speaker.is_playing:
            threshold = wake_config.speaking_threshold
        elif wake_config.barge_in and self._turn_running():
            threshold = wake_config.threshold
        else:
            return
        score = wake.process(block)
        if score < threshold or self._clock() < self._cooldown_until:
            return
        barge_in = self._mode is Mode.BUSY
        logger.info("wake word", extra={"score": round(score, 3), "barge_in": barge_in})
        wake.reset()
        self._cooldown_until = self._clock() + wake_config.cooldown_ms / 1000
        await self._on_wake(barge_in=barge_in)

    async def _ptt_loop(self, ptt: PushToTalkSource) -> None:
        while True:
            await ptt.wait_for_press()
            await self._on_ptt_press(ptt)

    # -- triggers ---------------------------------------------------------------------

    async def _on_wake(self, *, barge_in: bool) -> None:
        if barge_in:
            await self._interrupt_turn()
        self._mode = Mode.BUSY  # ignore audio while the greeting plays
        if not barge_in and self._should_greet():
            generation = self._stop_generation
            await self._require_assistant().announce(
                self._config.session.greeting, "en", then=S.LISTENING
            )
            if generation != self._stop_generation:
                await self._go_idle()  # stopped during the greeting: don't start listening
                return
        await self._start_listening(Purpose.COMMAND, self._config.vad.start_timeout_s)

    async def _on_ptt_press(self, ptt: PushToTalkSource) -> None:
        if self._muted:
            return
        was_playing = self._speaker.is_playing
        opened_here = not self._mic.is_open
        try:
            # Open right away so the first syllable isn't lost during the hold check.
            self._mic.start()
        except AudioDeviceError:
            logger.exception("microphone unavailable")
            await self._announce(self._config.messages.speech_unavailable)
            return

        if await ptt.confirm_hold() is not HoldResult.CONFIRMED:
            if opened_here:
                self._mic.stop()
            return

        answering = self._answer_pending()
        if not answering:
            await self._interrupt_turn()
        self._mic.drain_nowait()  # already in the pre-roll
        # While Jarvis was talking, the pre-roll holds its own voice: skip it.
        self._recording = [] if was_playing else [self._mic.preroll()]
        self._purpose = Purpose.ANSWER if answering else Purpose.COMMAND
        self._mode = Mode.PTT
        generation = self._stop_generation
        await self._state.transition(S.LISTENING)

        await ptt.wait_for_release(self._config.voice.max_utterance_s)
        self._recording.extend(self._mic.drain_nowait())
        if opened_here:
            self._mic.stop()
        self._pump.mic_level(0.0)
        if generation != self._stop_generation:
            self._recording = []  # stopped while recording: never transcribe it
            return
        await self._finish_recording()

    # -- listening with the VAD ----------------------------------------------------------

    async def _start_listening(self, purpose: Purpose, timeout_s: float) -> None:
        self._mic.flush()
        if self._vad is not None:
            self._vad.reset()
        self._endpointer.reset(timeout_s)
        self._recording = []
        self._purpose = purpose
        self._mode = Mode.LISTENING
        await self._state.transition(S.LISTENING)

    async def _listen(self, block: AudioArray) -> None:
        self._recording.append(block)
        limit = self._config.voice.max_utterance_s * self._config.audio.input_sample_rate
        if sum(b.size for b in self._recording) >= limit:
            await self._finish_recording()
            return
        if self._vad is None:
            return
        for probability in self._vad.process(block):
            event = self._endpointer.feed(probability)
            if event is SpeechEvent.ENDED:
                await self._finish_recording()
                return
            if event is SpeechEvent.NO_SPEECH:
                await self._nothing_said()
                return

    async def _nothing_said(self) -> None:
        if self._purpose is Purpose.ANSWER:
            self._mode = Mode.BUSY
            await self._state.transition(S.THINKING)
            self._deliver_answer("")
        else:
            await self._go_idle()

    async def _finish_recording(self) -> None:
        audio = np.concatenate(self._recording) if self._recording else np.zeros(0, np.float32)
        self._recording = []
        self._mode = Mode.BUSY
        await self._handle_recording(audio, self._purpose)

    async def _handle_recording(self, audio: AudioArray, purpose: Purpose) -> None:
        rate = self._config.audio.input_sample_rate
        if self._debug_dir is not None:
            save_wav(audio, rate, self._debug_dir)
        if audio.size < rate * self._config.voice.min_utterance_ms / 1000:
            await self._nothing_said()
            return

        timer = Stopwatch()  # starts at the end of speech: that is what the user waits from
        await self._state.transition(S.THINKING)
        generation = self._stop_generation
        # A separate future so a stop can abandon the wait at once. Whisper itself can't be
        # interrupted mid-pass; its result is simply dropped (and WhisperSTT serialises calls).
        transcription = asyncio.ensure_future(asyncio.to_thread(self._stt.transcribe, audio))
        self._stt_task = transcription
        try:
            result = await transcription
        except asyncio.CancelledError:
            current = asyncio.current_task()
            if current is not None and current.cancelling():
                raise  # the session itself is shutting down
            logger.info("transcription discarded by stop")
            return
        except Exception:
            logger.exception("transcription failed")
            if purpose is Purpose.ANSWER:
                self._deliver_answer("")
            else:
                await self._announce(self._config.messages.speech_unavailable)
            return
        finally:
            if self._stt_task is transcription:
                self._stt_task = None
        if generation != self._stop_generation:
            return  # stopped while transcribing
        timer.mark("stt")
        self._log_transcription(result, audio.size / rate, timer)
        if self._mode is not Mode.BUSY and purpose is not Purpose.ANSWER:
            return  # push-to-talk or the wake word took over while we were transcribing
        text = strip_wake_phrase(result.text)

        if purpose is Purpose.ANSWER:
            self._deliver_answer(text)
            return
        self._language = result.language
        if not text:
            if purpose is Purpose.FOLLOW_UP:
                await self._go_idle()  # a noise in the follow-up window: just stop listening
            else:
                await self._announce(self._config.messages.didnt_catch)
            return
        follow_up = self.hands_free and self._config.session.follow_up_s > 0
        self._start_turn(text, language=result.language, timer=timer, follow_up=follow_up)

    def _start_turn(
        self, text: str, *, language: Language, timer: Stopwatch | None, follow_up: bool
    ) -> None:
        self._follow_up_after_turn = follow_up
        turn = asyncio.create_task(
            self._require_assistant().handle_text(
                text,
                language=language,
                timer=timer,
                then=S.LISTENING if follow_up else S.IDLE,
            ),
            name="turn",
        )
        self._turn = turn
        turn.add_done_callback(self._on_turn_done)

    # -- after a turn -------------------------------------------------------------------

    def _on_turn_done(self, turn: asyncio.Task[None]) -> None:
        if turn.cancelled() or turn is not self._turn:
            return  # interrupted: whoever interrupted decides what happens next
        self._pump.run_soon(self._after_turn(turn))

    async def _after_turn(self, turn: asyncio.Task[None]) -> None:
        if self._mode not in (Mode.BUSY, Mode.MUTED) or self._turn is not turn:
            return  # push-to-talk or a barge-in already took over
        self._turn = None
        session = self._config.session
        if not self._follow_up_after_turn or self._muted:
            await self._go_idle()
            return
        await asyncio.sleep(session.follow_up_guard_ms / 1000)
        if self._mode is Mode.BUSY and self._turn is None:
            await self._start_listening(Purpose.FOLLOW_UP, session.follow_up_s)

    async def _go_idle(self) -> None:
        self._session_ended_at = self._clock()
        if self._muted:
            self._mode = Mode.MUTED
            await self._enter_muted_state()
            return
        self._mode = Mode.IDLE
        if self._wake is not None:
            self._wake.reset()
        self._mic.flush()
        await self._state.transition(S.IDLE)

    async def _enter_muted_state(self) -> None:
        # MUTED is only reachable from IDLE/LISTENING; settle in IDLE first.
        if self._state.state not in (S.IDLE, S.LISTENING, S.MUTED):
            await self._state.transition(S.IDLE)
        await self._state.transition(S.MUTED)

    # -- HUD control ---------------------------------------------------------------------

    def status(self) -> Status:
        return Status(
            muted=self._muted,
            voice=True,
            hands_free=self.hands_free,
            push_to_talk_key=self._config.voice.push_to_talk_key if self._ptt else None,
        )

    async def toggle_mute(self) -> None:
        await self.set_muted(not self._muted)

    async def set_muted(self, muted: bool) -> None:
        """Close (or reopen) the microphone. A running reply is stopped first."""
        if muted == self._muted:
            return
        self._muted = muted
        if muted:
            self._deliver_answer("")
            await self._interrupt_turn()
            self._recording = []
            self._mic.stop()
            self._mode = Mode.MUTED
            await self._enter_muted_state()
        else:
            self._mode = Mode.IDLE
            await self._state.transition(S.IDLE)
            if self.hands_free:
                try:
                    self._mic.start()
                except AudioDeviceError:
                    logger.exception("microphone unavailable after unmute")
                    await self._announce(self._config.messages.speech_unavailable)
        logger.info("mute changed", extra={"muted": muted})
        self._pump.publish_soon(self.status())

    async def interrupt(self) -> tuple[StopTarget, ...]:
        """Stop everything Jarvis is doing: the HUD Stop button and Esc both end up here.

        Speech is silenced first (the part you hear), then recording, transcription,
        the Claude request and any running or queued tools are cancelled. Work that
        can't be interrupted mid-call (a Whisper pass, an app launch already handed
        to Windows) finishes in the background and its result is dropped. Jarvis then
        waits for the wake word or push-to-talk again; mute is left as it was.
        Returns what was actually stopped (empty when Jarvis was idle).
        """
        cancelled: list[StopTarget] = []
        self._stop_generation += 1
        if self._speaker.is_playing:
            cancelled.append("speech")
        await self._speaker.stop()
        if self._mode in (Mode.LISTENING, Mode.PTT):
            cancelled.append("listening")
        self._recording = []
        transcription, self._stt_task = self._stt_task, None
        if transcription is not None and not transcription.done():
            cancelled.append("transcription")
            transcription.cancel()
        if self._turn_running():
            cancelled.append("reply")
            if self._assistant is not None and self._assistant.tools_running:
                cancelled.append("tools")
        await self._interrupt_turn()
        self._deliver_answer("")
        if self._mode in (Mode.LISTENING, Mode.PTT, Mode.BUSY):
            await self._go_idle()
        elif self._muted and cancelled:
            await self._enter_muted_state()  # a typed request ran while muted: stay muted
        return tuple(cancelled)

    async def submit_text(self, text: str) -> None:
        """Typed input from the HUD: a new request, or the answer to a pending question."""
        text = text.strip()
        if not text:
            return
        if self._answer_pending():
            self._pump.publish_soon(
                Transcript(
                    turn_id="answer",
                    role="user",
                    text=text,
                    language=detect_language(text),
                    final=True,
                )
            )
            self._deliver_answer(text)
            return
        await self._interrupt_turn()
        self._recording = []
        if not self._muted:
            self._mode = Mode.BUSY
        self._start_turn(text, language=detect_language(text), timer=None, follow_up=False)

    async def ptt_start(self) -> None:
        self._hud_ptt.press()

    async def ptt_stop(self) -> None:
        self._hud_ptt.release()

    def _should_greet(self) -> bool:
        if not self._config.session.greeting:
            return False
        if self._session_ended_at is None:
            return True
        idle_s = self._clock() - self._session_ended_at
        return idle_s >= self._config.session.greeting_after_idle_min * 60

    # -- yes/no questions from tools ---------------------------------------------------

    async def ask(self, question: str) -> str:
        """Speak ``question`` and return the transcribed spoken answer ("" if none)."""
        await self._speaker.say(question, self._language)
        await self._speaker.drain()
        self._answer = asyncio.get_running_loop().create_future()
        voice = self._config.voice
        try:
            if self.hands_free:
                await self._start_listening(Purpose.ANSWER, voice.confirm_listen_timeout_s)
            # Upper bound: time to start, the longest utterance, and transcription.
            limit = voice.confirm_listen_timeout_s + voice.max_utterance_s + 15
            return await asyncio.wait_for(self._answer, limit)
        except TimeoutError:
            return ""
        finally:
            self._answer = None
            if self._mode is Mode.LISTENING and self._purpose is Purpose.ANSWER:
                self._mode = Mode.BUSY

    def _answer_pending(self) -> bool:
        return self._answer is not None and not self._answer.done()

    def _deliver_answer(self, text: str) -> None:
        if self._answer is not None and not self._answer.done():
            self._answer.set_result(text)

    # -- helpers ------------------------------------------------------------------------

    def _turn_running(self) -> bool:
        return self._turn is not None and not self._turn.done()

    async def _interrupt_turn(self) -> None:
        turn, self._turn = self._turn, None
        await self._speaker.stop()
        if turn is not None and not turn.done():
            turn.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await turn

    async def _announce(self, line: LocalizedText) -> None:
        await self._require_assistant().announce(line.for_language(self._language), self._language)
        await self._go_idle()

    def _require_assistant(self) -> Assistant:
        if self._assistant is None:
            raise RuntimeError("VoiceSession.attach() was not called")
        return self._assistant

    def _log_transcription(self, result: Transcription, seconds: float, timer: Stopwatch) -> None:
        extra: dict[str, Any] = {
            "audio_ms": round(seconds * 1000),
            "stt_ms": timer.as_ms().get("stt_ms"),
            "language": result.language,
            "whisper_language": result.detected_language,
            "language_probability": round(result.language_probability, 3),
            "chars": len(result.text),
            "purpose": self._purpose.value,
        }
        if transcripts_enabled():
            extra["text"] = result.text
        logger.info("transcribed", extra=extra)


class VoiceConfirmer:
    """Confirmer that asks out loud and listens for a spoken yes/no."""

    def __init__(self) -> None:
        self._session: VoiceSession | None = None

    def bind(self, session: VoiceSession) -> None:
        self._session = session

    async def confirm(self, question: str) -> bool:
        if self._session is None:
            return False
        return is_yes(await self._session.ask(question))
