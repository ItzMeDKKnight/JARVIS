"""Voice pipeline tests with fake engine, sink, mic, detectors and STT: no devices, no models.

Microphone blocks are labelled by their sample value so the fake detectors can
score them: 0.0 = silence, 0.3 = speech, 0.9 = "Hey Jarvis". End-of-speech
decisions are made by the real Endpointer with the real config timings.
"""

from __future__ import annotations

import asyncio

import numpy as np
import pytest

from jarvis.assistant import Assistant
from jarvis.audio.vad import CHUNK_SAMPLES
from jarvis.config import JarvisConfig
from jarvis.events import AssistantState, EventBus
from jarvis.llm.history import ConversationHistory
from jarvis.llm.types import ToolCall
from jarvis.search.base import SearchResult
from jarvis.speech import VoiceSpeaker
from jarvis.state import StateMachine
from jarvis.stt.base import Transcription
from jarvis.tools.registry import build_default_registry
from jarvis.voice import EventPump, Mode, Purpose, VoiceConfirmer, VoiceSession, strip_wake_phrase
from tests.fakes import (
    EventRecorder,
    FakeBrowser,
    FakeLauncher,
    FakeLLMClient,
    FakeSearch,
    ScriptedTurn,
    shortcut,
)

S = AssistantState
SILENCE, SPEECH, WAKE = 0.0, 0.3, 0.9


# -- speech output ----------------------------------------------------------------------


class FakeEngine:
    sample_rate = 24_000

    def __init__(self, delay_s: float = 0.0) -> None:
        self.calls: list[tuple[str, str]] = []
        self._delay = delay_s

    def synthesize(self, text: str, language: str) -> np.ndarray:
        if self._delay:
            import time

            time.sleep(self._delay)
        self.calls.append((language, text))
        return np.full(240, 0.1, dtype=np.float32)


class FakeSink:
    """Plays instantly: each enqueue counts as heard."""

    def __init__(self) -> None:
        self.played: list[np.ndarray] = []
        self.stopped = 0
        self.speaker: VoiceSpeaker | None = None

    @property
    def is_playing(self) -> bool:
        return False

    def enqueue(self, audio: np.ndarray) -> None:
        self.played.append(audio)
        if self.speaker is not None:
            self.speaker.on_player_start()

    def stop(self) -> None:
        self.stopped += 1

    async def drain(self) -> None:
        await asyncio.sleep(0)


def _speaker(config: JarvisConfig, engine: FakeEngine, sink: FakeSink) -> VoiceSpeaker:
    postfx = config.tts.postfx.model_copy(update={"enabled": False})
    speaker = VoiceSpeaker(engine, sink, segmenter=config.tts.segmenter, postfx=postfx)
    sink.speaker = speaker
    speaker.start()
    return speaker


async def test_streamed_text_is_spoken_in_sentence_chunks(config: JarvisConfig) -> None:
    engine, sink = FakeEngine(), FakeSink()
    speaker = _speaker(config, engine, sink)

    for fragment in ["Good eve", "ning, Tony. ", "VS Code is ", "open."]:
        await speaker.feed(fragment, "en")
    await speaker.end_utterance()
    await speaker.drain()
    await speaker.close()

    assert engine.calls == [("en", "Good evening, Tony."), ("en", "VS Code is open.")]
    assert len(sink.played) == 2


async def test_hinglish_reply_switches_voice_per_script(config: JarvisConfig) -> None:
    engine, sink = FakeEngine(), FakeSink()
    speaker = _speaker(config, engine, sink)

    await speaker.say("मैंने VS Code खोल दिया है।", "hi")
    await speaker.drain()
    await speaker.close()

    assert engine.calls == [("hi", "मैंने"), ("en", "VS Code"), ("hi", "खोल दिया है।")]


async def test_stop_drops_queued_speech(config: JarvisConfig) -> None:
    engine, sink = FakeEngine(delay_s=0.05), FakeSink()
    speaker = _speaker(config, engine, sink)

    for i in range(5):
        await speaker.say(f"Sentence number {i}.", "en")
    await asyncio.sleep(0.02)  # the first one is being synthesised
    await speaker.stop()
    await speaker.drain()
    await speaker.close()

    assert len(sink.played) == 0  # the in-flight chunk is discarded too
    assert sink.stopped == 1


def test_interrupted_tool_call_gets_a_result() -> None:
    history = ConversationHistory(max_turns=5)
    history.add_user_text("open vs code")
    history.add_assistant([{"type": "tool_use", "id": "t1", "name": "open_app", "input": {}}])

    history.close_interrupted_turn()

    last = history.messages[-1]
    assert last["role"] == "user"
    assert last["content"][0]["tool_use_id"] == "t1" and last["content"][0]["is_error"]


# -- voice session -------------------------------------------------------------------------


class FakeMic:
    def __init__(self) -> None:
        self.is_open = False
        self.flushes = 0

    def start(self) -> None:
        self.is_open = True

    def stop(self) -> None:
        self.is_open = False

    async def read(self) -> np.ndarray:
        raise AssertionError("tests feed blocks directly")

    def drain_nowait(self) -> list[np.ndarray]:
        return []

    def flush(self) -> None:
        self.flushes += 1

    def preroll(self) -> np.ndarray:
        return np.zeros(0, dtype=np.float32)


class FakeWake:
    def __init__(self) -> None:
        self.resets = 0

    def process(self, audio: np.ndarray) -> float:
        return 0.99 if float(audio.max(initial=0.0)) > 0.8 else 0.0

    def reset(self) -> None:
        self.resets += 1


class FakeVAD:
    def process(self, audio: np.ndarray) -> list[float]:
        value = float(audio.max(initial=0.0))
        probability = 0.95 if 0.2 < value < 0.8 else 0.02
        return [probability] * (audio.size // CHUNK_SAMPLES)

    def reset(self) -> None:
        return


class FakeSTT:
    def __init__(self, *texts: str, language: str = "en") -> None:
        self._texts = list(texts)
        self.language = language
        self.calls = 0

    def transcribe(self, audio: np.ndarray) -> Transcription:
        self.calls += 1
        text = self._texts.pop(0) if self._texts else ""
        return Transcription(
            text=text,
            language=self.language,  # type: ignore[arg-type]
            language_probability=0.9,
            detected_language=self.language,
        )


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _config(config: JarvisConfig, **session: object) -> JarvisConfig:
    updates = {"follow_up_guard_ms": 0, "follow_up_s": 1.0, **session}
    return config.model_copy(update={"session": config.session.model_copy(update=updates)})


class Rig:
    def __init__(
        self,
        config: JarvisConfig,
        turns: list[ScriptedTurn],
        stt: FakeSTT,
        *,
        hands_free: bool = True,
        launcher: FakeLauncher | None = None,
    ) -> None:
        self.config = config
        self.bus = EventBus()
        self.events = EventRecorder()
        self.bus.subscribe(self.events)
        self.state = StateMachine(self.bus)
        self.engine, self.sink = FakeEngine(), FakeSink()
        self.speaker = _speaker(config, self.engine, self.sink)
        self.llm = FakeLLMClient(turns)
        self.stt = stt
        self.clock = Clock()
        self.wake = FakeWake() if hands_free else None
        registry = build_default_registry(
            config,
            launcher=launcher or FakeLauncher(),
            browser=FakeBrowser(),
            search=FakeSearch(SearchResult(summary="ok", sources=())),
        )
        confirmer = VoiceConfirmer()
        self.session = VoiceSession(
            config,
            state=self.state,
            mic=FakeMic(),
            speaker=self.speaker,
            stt=stt,
            pump=EventPump(self.bus),
            wake=self.wake,
            vad=FakeVAD() if hands_free else None,
            ptt=None,
            clock=self.clock,
        )
        confirmer.bind(self.session)
        self.session.attach(
            Assistant(
                config,
                llm=self.llm,
                registry=registry,
                bus=self.bus,
                state=self.state,
                confirmer=confirmer,
                speaker=self.speaker,
            )
        )

    async def feed(self, value: float, seconds: float, *, settle: bool = True) -> None:
        for _ in range(round(seconds * 16_000 / CHUNK_SAMPLES)):
            await self.session.process_block(np.full(CHUNK_SAMPLES, value, dtype=np.float32))
        if settle:
            await self.settle()

    async def say(self, seconds: float = 1.0) -> None:
        """Speak, then stay quiet long enough for the end-of-speech timeout."""
        await self.feed(SPEECH, seconds)
        await self.feed(SILENCE, 1.0)

    async def settle(self) -> None:
        for _ in range(50):
            await asyncio.sleep(0)
        if self.session._turn is not None and not self.session._turn.done():
            await self.session._turn
        for _ in range(50):
            await asyncio.sleep(0)

    def states(self) -> list[AssistantState]:
        return [e.state for e in self.events.of_type("state_changed")]

    def spoken(self) -> list[str]:
        return [text for _, text in self.engine.calls]

    async def close(self) -> None:
        await self.speaker.close()


async def test_wake_greets_answers_and_takes_a_follow_up(config: JarvisConfig) -> None:
    rig = Rig(
        _config(config),
        [ScriptedTurn(chunks=["It is sunny."]), ScriptedTurn(chunks=["Thirty one degrees."])],
        FakeSTT("what is the weather", "and the temperature"),
    )

    await rig.feed(WAKE, 0.2)
    assert rig.spoken() == ["Hello Tony, whatsup?"]
    assert rig.session.mode is Mode.LISTENING

    await rig.say()  # first request, then the follow-up window opens
    assert rig.session.mode is Mode.LISTENING and rig.session._purpose is Purpose.FOLLOW_UP

    await rig.say()  # follow-up without the wake word, and no second greeting
    assert rig.spoken() == ["Hello Tony, whatsup?", "It is sunny.", "Thirty one degrees."]
    assert [m["content"] for m in rig.llm.requests[1].messages if m["role"] == "user"] == [
        "what is the weather",
        "and the temperature",
    ]

    await rig.feed(SILENCE, 1.5)  # nothing more said: back to idle
    assert rig.session.mode is Mode.IDLE
    assert rig.states()[-1] == S.IDLE
    assert rig.states()[:4] == [S.THINKING, S.SPEAKING, S.LISTENING, S.THINKING]
    await rig.close()


async def test_greeting_only_after_a_long_idle(config: JarvisConfig) -> None:
    rig = Rig(
        _config(config, greeting_after_idle_min=10),
        [ScriptedTurn(chunks=["Done."])] * 3,
        FakeSTT("one", "two", "three"),
    )

    await rig.feed(WAKE, 0.2)
    await rig.say()
    await rig.feed(SILENCE, 1.5)  # session ends
    assert rig.spoken().count("Hello Tony, whatsup?") == 1

    rig.clock.now += 5 * 60  # five minutes later: no greeting
    await rig.feed(WAKE, 0.2)
    assert rig.session.mode is Mode.LISTENING
    assert rig.spoken().count("Hello Tony, whatsup?") == 1
    await rig.say()
    await rig.feed(SILENCE, 1.5)

    rig.clock.now += 11 * 60  # eleven minutes later: greet again
    await rig.feed(WAKE, 0.2)
    assert rig.spoken().count("Hello Tony, whatsup?") == 2
    await rig.close()


async def test_wake_then_silence_returns_to_idle(config: JarvisConfig) -> None:
    timed = config.model_copy(update={"vad": config.vad.model_copy(update={"start_timeout_s": 1})})
    rig = Rig(_config(timed), [], FakeSTT())

    await rig.feed(WAKE, 0.2)
    await rig.feed(SILENCE, 1.5)

    assert rig.session.mode is Mode.IDLE
    assert rig.stt.calls == 0 and rig.llm.requests == []
    await rig.close()


async def test_wake_phrase_is_ignored_inside_a_listening_window(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [], FakeSTT())
    await rig.feed(WAKE, 0.2)
    resets = rig.wake.resets if rig.wake else 0
    await rig.feed(WAKE, 0.2)  # while listening, blocks are recorded, not scored
    assert rig.wake is not None and rig.wake.resets == resets
    await rig.close()


async def test_barge_in_stops_the_reply_and_listens(config: JarvisConfig) -> None:
    gate = asyncio.Event()

    class SlowLLM(FakeLLMClient):
        async def respond(self, request, on_text):  # type: ignore[no-untyped-def]
            await gate.wait()
            return await super().respond(request, on_text)

    rig = Rig(_config(config), [], FakeSTT("tell me a long story"))
    rig.session._assistant._llm = SlowLLM([ScriptedTurn(chunks=["Once upon a time."])])  # type: ignore[union-attr]

    await rig.feed(WAKE, 0.2)
    await rig.feed(SPEECH, 1.0)
    for _ in range(round(16_000 / CHUNK_SAMPLES)):  # silence ends the request; the turn starts
        await rig.session.process_block(np.zeros(CHUNK_SAMPLES, dtype=np.float32))
    await asyncio.sleep(0.01)
    assert rig.session.mode is Mode.BUSY

    rig.clock.now += 5  # past the wake word cooldown
    await rig.session.process_block(np.full(CHUNK_SAMPLES, WAKE, dtype=np.float32))
    await asyncio.sleep(0.01)

    assert rig.session.mode is Mode.LISTENING
    assert rig.sink.stopped >= 1
    assert "Once upon a time." not in rig.spoken()
    assert rig.spoken().count("Hello Tony, whatsup?") == 1  # no greeting on barge-in
    await rig.close()


async def test_spoken_yes_confirms_an_unknown_app(config: JarvisConfig) -> None:
    launcher = FakeLauncher(shortcuts=[shortcut("Spotify")])
    rig = Rig(
        _config(config),
        [
            ScriptedTurn(
                tool_calls=[ToolCall(id="t1", name="open_app", input={"app_name": "spotify"})]
            ),
            ScriptedTurn(chunks=["Spotify is open."]),
        ],
        FakeSTT("open spotify", "haan, khol do"),
        launcher=launcher,
    )

    await rig.feed(WAKE, 0.2)
    await rig.feed(SPEECH, 1.0)
    for _ in range(round(16_000 / CHUNK_SAMPLES)):
        await rig.session.process_block(np.zeros(CHUNK_SAMPLES, dtype=np.float32))
    # The tool asks its question and listens for the answer.
    for _ in range(200):
        await asyncio.sleep(0.005)
        if rig.session._purpose is Purpose.ANSWER and rig.session.mode is Mode.LISTENING:
            break
    assert any("Spotify" in s and "Start Menu" in s for s in rig.spoken())

    # The turn is waiting for this answer, so don't wait for the turn while answering.
    await rig.feed(SPEECH, 1.0, settle=False)
    await rig.feed(SILENCE, 1.0, settle=False)
    await rig.settle()

    assert [s.name for s in launcher.launched_shortcuts] == ["Spotify"]
    assert "Spotify is open." in rig.spoken()
    await rig.close()


async def test_push_to_talk_only_mode_has_no_follow_up(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [ScriptedTurn(chunks=["Hello."])], FakeSTT("hi"), hands_free=False)

    rig.session._mode = Mode.PTT  # what a confirmed push-to-talk press sets up
    await rig.state.transition(S.LISTENING)
    for _ in range(30):
        await rig.session.process_block(np.full(CHUNK_SAMPLES, SPEECH, dtype=np.float32))
    await rig.session._finish_recording()
    await rig.settle()

    assert rig.spoken() == ["Hello."]
    assert rig.session.mode is Mode.IDLE
    assert rig.states() == [S.LISTENING, S.THINKING, S.SPEAKING, S.IDLE]
    await rig.close()


async def test_empty_transcription_after_wake_says_didnt_catch(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [], FakeSTT(""))
    await rig.feed(WAKE, 0.2)
    await rig.say()
    assert rig.spoken()[-1] == config.messages.didnt_catch.en
    assert rig.session.mode is Mode.IDLE
    await rig.close()


@pytest.mark.parametrize(
    ("heard", "request_text"),
    [
        ("Hey Jarvis, what's the weather?", "what's the weather?"),
        ("Jarvis open VS Code", "open VS Code"),
        ("jarvis.", ""),
        ("जार्विस, कैलकुलेटर खोलो", "कैलकुलेटर खोलो"),
        ("What does Jarvis mean?", "What does Jarvis mean?"),
    ],
)
def test_strip_wake_phrase(heard: str, request_text: str) -> None:
    assert strip_wake_phrase(heard) == request_text


# -- HUD control (Phase 5) -------------------------------------------------------------


async def test_mute_closes_the_mic_and_ignores_the_wake_word(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [], FakeSTT())
    mic = rig.session._mic
    mic.start()

    await rig.session.toggle_mute()
    await rig.settle()
    assert rig.states()[-1] == S.MUTED
    assert not mic.is_open
    assert rig.session.status().muted

    await rig.feed(WAKE, 0.2)  # "Hey Jarvis" while muted: nothing happens
    assert rig.spoken() == [] and rig.session.mode is Mode.MUTED

    await rig.session.toggle_mute()
    await rig.settle()
    assert rig.states()[-1] == S.IDLE and mic.is_open
    assert [e.muted for e in rig.events.of_type("status")] == [True, False]
    await rig.close()


async def test_mute_stops_a_reply_in_progress(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [ScriptedTurn(chunks=["A long answer."])], FakeSTT())
    await rig.session.submit_text("tell me a story")
    await rig.session.set_muted(True)
    await rig.settle()
    assert rig.states()[-1] == S.MUTED
    await rig.close()


async def test_typed_input_runs_a_turn_without_follow_up(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [ScriptedTurn(chunks=["Hello Tony."])], FakeSTT())
    await rig.session.submit_text("  hello  ")
    await rig.settle()
    assert rig.spoken() == ["Hello Tony."]
    assert rig.session.mode is Mode.IDLE  # typed turns don't open a listening window
    assert rig.states() == [S.THINKING, S.SPEAKING, S.IDLE]
    await rig.close()


async def test_typed_input_while_muted_returns_to_muted(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [ScriptedTurn(chunks=["Done."])], FakeSTT())
    await rig.session.set_muted(True)
    await rig.session.submit_text("hello")
    await rig.settle()
    assert rig.states() == [S.MUTED, S.THINKING, S.SPEAKING, S.IDLE, S.MUTED]
    await rig.close()


async def test_typed_answer_settles_a_spoken_question(config: JarvisConfig) -> None:
    launcher = FakeLauncher(shortcuts=[shortcut("Spotify")])
    rig = Rig(
        _config(config),
        [
            ScriptedTurn(
                tool_calls=[ToolCall(id="t1", name="open_app", input={"app_name": "spotify"})]
            ),
            ScriptedTurn(chunks=["Spotify is open."]),
        ],
        FakeSTT(),
        launcher=launcher,
    )
    await rig.session.submit_text("open spotify")
    for _ in range(500):
        if rig.session._answer_pending():
            break
        await asyncio.sleep(0.01)
    assert rig.session._answer_pending()  # Jarvis asked and is listening for the answer

    await rig.session.submit_text("yes")
    await rig.settle()
    assert [s.name for s in launcher.launched_shortcuts] == ["Spotify"]
    assert rig.spoken()[-1] == "Spotify is open."
    await rig.close()


async def test_interrupt_stops_listening(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [], FakeSTT())
    await rig.feed(WAKE, 0.2)
    assert rig.session.mode is Mode.LISTENING
    await rig.session.interrupt()
    assert rig.session.mode is Mode.IDLE and rig.states()[-1] == S.IDLE
    await rig.close()


async def test_command_push_to_talk_needs_a_real_hold() -> None:
    from jarvis.audio.ptt import CommandPushToTalk, HoldResult

    ptt = CommandPushToTalk(hold_threshold_ms=30)
    ptt.press()
    await ptt.wait_for_press()
    ptt.release()
    assert await ptt.confirm_hold() is HoldResult.RELEASED  # a tap

    ptt.press()
    ptt.press()  # key repeat is ignored
    await ptt.wait_for_press()
    assert await ptt.confirm_hold() is HoldResult.CONFIRMED
    ptt.release()
    await ptt.wait_for_release(1.0)
