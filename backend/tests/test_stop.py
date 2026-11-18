"""The Stop button / Esc: one stop path that halts everything, in every state.

Scenarios: mid-speech, mid-tool-call, during transcription, during a push-to-talk
recording, during the greeting, when idle, while muted, in text-only mode, and
the HUD acknowledgement. Each checks that nothing cancelled finishes or speaks later
and that Jarvis ends idle (or still muted) and ready again.
"""

from __future__ import annotations

import asyncio
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from jarvis.audio.playback import AudioPlayer
from jarvis.config import JarvisConfig
from jarvis.events import AssistantState, Interrupt
from jarvis.llm.types import LLMRequest, LLMResponse, TextCallback, ToolCall, Usage
from jarvis.search.base import SearchResult
from jarvis.stt.base import Transcription
from jarvis.voice import Mode
from tests.fakes import ScriptedTurn
from tests.test_hud import Rig as HudRig
from tests.test_voice import SILENCE, SPEECH, WAKE, FakeSTT, Rig, _config

S = AssistantState


class BlockingLLM:
    """Streams its text, then optionally calls tools; with ``hang`` it never finishes."""

    def __init__(self, chunks: list[str], *, tool_calls: list[ToolCall] | None = None) -> None:
        self._chunks = chunks
        self._tool_calls = tool_calls or []
        self.requests: list[LLMRequest] = []
        self.cancelled = False

    async def respond(self, request: LLMRequest, on_text: TextCallback) -> LLMResponse:
        self.requests.append(request)
        if len(self.requests) > 1:
            raise AssertionError("a stopped turn must not call Claude again")
        for chunk in self._chunks:
            await on_text(chunk)
        if self._tool_calls:
            return LLMResponse(
                stop_reason="tool_use",
                text="".join(self._chunks),
                tool_calls=list(self._tool_calls),
                assistant_content=[
                    {"type": "tool_use", "id": c.id, "name": c.name, "input": c.input}
                    for c in self._tool_calls
                ],
                model=request.route.model,
                usage=Usage(input_tokens=1, output_tokens=1),
            )
        try:
            await asyncio.Event().wait()  # the reply is still streaming
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class HangingSearch:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = False

    async def search(self, query: str) -> SearchResult:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise
        raise AssertionError("unreachable")


class BlockingSTT(FakeSTT):
    """Holds the transcription thread until released, like a slow Whisper pass."""

    def __init__(self, *texts: str) -> None:
        super().__init__(*texts)
        self.started = threading.Event()
        self.release = threading.Event()

    def transcribe(self, audio: np.ndarray) -> Transcription:
        self.started.set()
        self.release.wait(5)
        return super().transcribe(audio)


async def _until(predicate: Any, timeout_s: float = 3.0) -> None:
    deadline = time.monotonic() + timeout_s
    while not predicate():
        if time.monotonic() > deadline:
            raise AssertionError("condition not reached")
        await asyncio.sleep(0.005)


async def test_stop_mid_reply_silences_and_cancels_the_request(config: JarvisConfig) -> None:
    llm = BlockingLLM(["First sentence of a long story. ", "And then"])
    rig = Rig(_config(config), [], FakeSTT(), llm=llm)
    await rig.session.submit_text("tell me a story")
    await _until(lambda: rig.sink.played)  # the first sentence is being heard
    stops_before = rig.sink.stopped

    started = time.perf_counter()
    cancelled = await rig.session.interrupt()
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert "reply" in cancelled
    assert llm.cancelled  # the in-flight Claude request was cancelled, not left running
    assert rig.sink.stopped > stops_before
    assert elapsed_ms < 200
    await rig.settle()
    await asyncio.sleep(0.05)
    assert rig.spoken() == ["First sentence of a long story."]  # "And then" never spoken
    assert rig.session.mode is Mode.IDLE
    assert rig.states()[-1] == S.IDLE
    await rig.close()


async def test_stop_mid_tool_call_cancels_tool_and_follow_up(config: JarvisConfig) -> None:
    search = HangingSearch()
    llm = BlockingLLM(
        [], tool_calls=[ToolCall(id="t1", name="web_search", input={"query": "news today"})]
    )
    rig = Rig(_config(config), [], FakeSTT(), llm=llm, search=search)
    await rig.session.submit_text("what's the news")
    await asyncio.wait_for(search.started.wait(), 3)

    cancelled = await rig.session.interrupt()

    # "speech" too when the narration line was still queued at that moment.
    assert set(cancelled) - {"speech"} == {"reply", "tools"}
    assert search.cancelled
    await rig.settle()
    await asyncio.sleep(0.05)
    assert len(llm.requests) == 1  # no second round with the tool result
    assert rig.events.of_type("tool_finished") == []
    # The narration line is dropped too if it was still queued; nothing after the stop.
    assert rig.spoken() in ([], ["Searching for that now."])
    assert rig.session.mode is Mode.IDLE and rig.states()[-1] == S.IDLE
    await rig.close()


async def test_stop_during_transcription_drops_the_result(config: JarvisConfig) -> None:
    stt = BlockingSTT("open calculator")
    rig = Rig(_config(config, greeting=""), [ScriptedTurn(chunks=["Never said."])], stt)
    await rig.feed(WAKE, 0.2)
    assert rig.session.mode is Mode.LISTENING
    listening = asyncio.create_task(rig.feed(SPEECH, 1.0, settle=False))
    await listening
    silence = asyncio.create_task(rig.feed(SILENCE, 1.0, settle=False))
    await _until(stt.started.is_set)

    cancelled = await rig.session.interrupt()

    assert cancelled == ("transcription",)
    await asyncio.wait_for(silence, 2)  # the audio loop is free again at once
    stt.release.set()
    await asyncio.sleep(0.1)
    await rig.settle()
    assert rig.llm.requests == []  # the transcript never became a request
    assert rig.session.mode is Mode.IDLE and rig.states()[-1] == S.IDLE
    await rig.close()


async def test_stop_during_push_to_talk_discards_the_recording(config: JarvisConfig) -> None:
    stt = FakeSTT("open calculator")
    rig = Rig(_config(config), [ScriptedTurn(chunks=["Never said."])], stt, hands_free=False)
    hud_ptt = rig.session._hud_ptt
    await rig.session.ptt_start()
    press = asyncio.create_task(rig.session._on_ptt_press(hud_ptt))
    await _until(lambda: rig.session.mode is Mode.PTT)
    rig.session._recording.append(np.full(16_000, SPEECH, dtype=np.float32))

    cancelled = await rig.session.interrupt()
    await rig.session.ptt_stop()
    await asyncio.wait_for(press, 2)

    assert cancelled == ("listening",)
    assert stt.calls == 0 and rig.llm.requests == []
    assert rig.session.mode is Mode.IDLE and rig.states()[-1] == S.IDLE
    await rig.close()


async def test_stop_during_greeting_does_not_start_listening(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [], FakeSTT())
    rig.engine._delay = 0.3  # the greeting is still being synthesised
    wake = asyncio.create_task(rig.feed(WAKE, 0.2, settle=False))
    await _until(lambda: rig.speaker.is_playing)

    cancelled = await rig.session.interrupt()
    await asyncio.wait_for(wake, 2)
    await asyncio.sleep(0.4)

    assert cancelled == ("speech",)
    assert rig.sink.played == []  # the greeting never played
    assert rig.session.mode is Mode.IDLE and rig.states()[-1] == S.IDLE
    await rig.close()


async def test_stop_when_idle_is_harmless(config: JarvisConfig) -> None:
    rig = Rig(_config(config), [ScriptedTurn(chunks=["Hello."])], FakeSTT("hi"))
    assert await rig.session.interrupt() == ()
    assert rig.states() == []  # no state change at all
    assert rig.session.mode is Mode.IDLE

    await rig.feed(WAKE, 0.2)  # still answers the wake word afterwards
    assert rig.session.mode is Mode.LISTENING
    await rig.close()


async def test_stop_keeps_the_microphone_muted_state(config: JarvisConfig) -> None:
    llm = BlockingLLM(["Working on it. "])
    rig = Rig(_config(config), [], FakeSTT(), llm=llm)
    await rig.session.set_muted(True)
    await rig.session.submit_text("do something")
    await _until(lambda: rig.sink.played)

    assert await rig.session.interrupt() == ("reply",)
    await rig.settle()
    assert rig.session.status().muted
    assert rig.session.mode is Mode.MUTED and rig.states()[-1] == S.MUTED

    # And unmuting afterwards still works normally.
    await rig.session.set_muted(False)
    assert rig.session.mode is Mode.IDLE
    await rig.close()


async def test_text_session_stop_and_hud_acknowledgement(
    config: JarvisConfig, tmp_path: Path
) -> None:
    rig = HudRig(config, tmp_path, [])
    llm = BlockingLLM(["Thinking out loud. "])
    assistant = rig.session._assistant
    assert assistant is not None
    assistant._llm = llm
    await rig.session.submit_text("hello")
    await _until(lambda: llm.requests)

    await rig.hud.handle(Interrupt())

    acks = rig.events.of_type("stopped")
    assert [a.cancelled for a in acks] == [("reply",)]
    assert llm.cancelled
    assert rig.states()[-1] == S.IDLE

    await rig.hud.handle(Interrupt())  # idle now: acknowledged, nothing cancelled
    assert [a.cancelled for a in rig.events.of_type("stopped")] == [("reply",), ()]
    rig.store.close()


class FakeStream:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def abort(self) -> None:
        self.calls.append("abort")

    def start(self) -> None:
        self.calls.append("start")


async def test_player_stop_discards_the_device_buffer(config: JarvisConfig) -> None:
    """Clearing our queue isn't enough: the driver still holds ~100-200 ms of sound."""
    idle: list[str] = []
    player = AudioPlayer(
        config.audio,
        24_000,
        asyncio.get_running_loop(),
        on_level=lambda _level: None,
        on_start=lambda: None,
        on_idle=lambda: idle.append("idle"),
    )
    stream = FakeStream()
    player._stream = stream  # type: ignore[assignment]
    player.enqueue(np.full(2400, 0.1, dtype=np.float32))
    player._playing = True

    player.stop()

    assert stream.calls == ["abort", "start"]
    assert not player.is_playing and idle == ["idle"]

    player.stop()  # nothing playing: the device is left alone
    assert stream.calls == ["abort", "start"]


@pytest.mark.skipif(sys.platform != "win32", reason="Windows power throttling only")
def test_power_throttling_opt_out_applies() -> None:
    from jarvis.os_adapters.base import opt_out_of_power_throttling

    assert opt_out_of_power_throttling() is True
