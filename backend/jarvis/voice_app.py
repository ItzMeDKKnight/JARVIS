"""Assembles and runs voice mode (``python -m jarvis --voice``).

Kept apart from main.py so text mode never imports the heavy audio/ML stack
(faster-whisper, onnxruntime, sounddevice).
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from jarvis.assistant import Assistant
from jarvis.audio.capture import AudioDeviceError, MicStream
from jarvis.audio.playback import AudioPlayer
from jarvis.audio.ptt import PushToTalk
from jarvis.audio.vad import StreamingVAD
from jarvis.audio.wakeword import WakeWordDetector, WakeWordError
from jarvis.config import JarvisConfig
from jarvis.cost import CostTracker
from jarvis.events import EventBus
from jarvis.llm.types import LLMClient
from jarvis.memory.manager import MemoryService
from jarvis.os_adapters.base import default_key_state, user_data_dir
from jarvis.privacy import disable_onnxruntime_telemetry
from jarvis.speech import VoiceSpeaker
from jarvis.state import StateMachine
from jarvis.stt.base import STTError
from jarvis.tools.registry import ToolRegistry
from jarvis.tts.base import TTSError, create_engine
from jarvis.voice import EventPump, VoiceConfirmer, VoiceSession

logger = logging.getLogger(__name__)

_KEY_LABELS = {
    "left_ctrl": "Left Ctrl",
    "right_ctrl": "Right Ctrl",
    "f9": "F9",
    "caps_lock": "Caps Lock",
}


class VoiceStartupError(Exception):
    """Voice mode cannot start; the message tells the user what to do."""


def _instructions(config: JarvisConfig) -> str:
    lines = ["J.A.R.V.I.S. voice mode."]
    if "wake_word" in config.voice.triggers:
        lines.append(
            'Say "Hey Jarvis", pause, then speak. Say it again while Jarvis talks to interrupt.'
        )
    if "push_to_talk" in config.voice.triggers:
        key = _KEY_LABELS[config.voice.push_to_talk_key]
        lines.append(f"Or hold {key} (alone) while you speak; pressing it also interrupts.")
    lines.append("Ctrl+C to quit.\n")
    return "\n".join(lines)


@dataclass
class VoiceRuntime:
    """A ready voice session plus the audio objects that must be closed afterwards."""

    session: VoiceSession
    speaker: VoiceSpeaker
    player: AudioPlayer

    async def close(self) -> None:
        await self.speaker.close()
        self.player.close()


async def build_voice(
    config: JarvisConfig,
    *,
    bus: EventBus,
    state: StateMachine,
    llm: LLMClient,
    registry: ToolRegistry,
    memory: MemoryService | None = None,
    cost: CostTracker | None = None,
) -> VoiceRuntime:
    """Load the speech models, open the speakers and wire a VoiceSession with its Assistant.

    Raises VoiceStartupError with a user-readable message if anything is missing.
    The microphone is opened later, by ``session.run()``.
    """
    disable_onnxruntime_telemetry()
    from jarvis.stt.whisper_local import WhisperSTT  # heavy import, after offline env is set

    loop = asyncio.get_running_loop()
    pump = EventPump(bus)
    models_dir = config.paths.resolved_models_dir()
    hands_free = "wake_word" in config.voice.triggers

    print("Loading speech models (first start can take a few seconds)...", flush=True)
    try:
        engine = await asyncio.to_thread(create_engine, config.tts, models_dir)
        stt = await asyncio.to_thread(WhisperSTT, config.stt, models_dir)
        wake = WakeWordDetector(config.wake_word, models_dir) if hands_free else None
        vad = StreamingVAD() if hands_free else None
    except (TTSError, STTError, WakeWordError) as exc:
        raise VoiceStartupError(str(exc)) from exc

    speaker: VoiceSpeaker | None = None

    def on_start() -> None:
        if speaker is not None:
            speaker.on_player_start()

    def on_idle() -> None:
        if speaker is not None:
            speaker.on_player_idle()

    player = AudioPlayer(
        config.audio,
        engine.sample_rate,
        loop,
        on_level=pump.tts_level,
        on_start=on_start,
        on_idle=on_idle,
    )
    speaker = VoiceSpeaker(engine, player, segmenter=config.tts.segmenter, postfx=config.tts.postfx)
    try:
        player.open()
    except AudioDeviceError as exc:
        raise VoiceStartupError(str(exc)) from exc
    speaker.start()

    debug_dir = None
    if config.audio.debug_save_audio:
        debug_dir = user_data_dir(config.paths.data_dir) / "debug-audio"
        logger.warning(
            "debug_save_audio is ON: utterances are saved", extra={"dir": str(debug_dir)}
        )

    ptt = None
    if "push_to_talk" in config.voice.triggers:
        ptt = PushToTalk(
            default_key_state(),
            config.voice.push_to_talk_key,
            hold_threshold_ms=config.voice.hold_threshold_ms,
            poll_hz=config.voice.poll_hz,
        )

    confirmer = VoiceConfirmer()
    session = VoiceSession(
        config,
        state=state,
        mic=MicStream(config.audio, loop, pump.mic_level, preroll_ms=config.session.preroll_ms),
        speaker=speaker,
        stt=stt,
        pump=pump,
        wake=wake,
        vad=vad,
        ptt=ptt,
        debug_audio_dir=debug_dir,
    )
    confirmer.bind(session)
    session.attach(
        Assistant(
            config,
            llm=llm,
            registry=registry,
            bus=bus,
            state=state,
            confirmer=confirmer,
            speaker=speaker,
            memory=memory,
            cost=cost,
        )
    )
    return VoiceRuntime(session=session, speaker=speaker, player=player)


async def run_voice(
    config: JarvisConfig,
    *,
    bus: EventBus,
    state: StateMachine,
    llm: LLMClient,
    registry: ToolRegistry,
    memory: MemoryService | None = None,
    cost: CostTracker | None = None,
) -> None:
    """Terminal voice mode (``python -m jarvis --voice``)."""
    runtime = await build_voice(
        config, bus=bus, state=state, llm=llm, registry=registry, memory=memory, cost=cost
    )
    print(_instructions(config), flush=True)
    try:
        await runtime.session.run()
    except AudioDeviceError as exc:
        raise VoiceStartupError(str(exc)) from exc
    finally:
        await runtime.close()
