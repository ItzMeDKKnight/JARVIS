"""Integration tests against the real local models. Skipped when models are not downloaded.

They guard behaviour that fakes cannot: Kokoro voice names in the installed
voices file, and the Whisper single-encoder-pass optimisation.
"""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.config import JarvisConfig
from jarvis.privacy import apply_offline_env

apply_offline_env()


def _require(config: JarvisConfig, *relative: str) -> None:
    root = config.paths.resolved_models_dir()
    missing = [r for r in relative if not (root / r).is_file()]
    if missing:
        pytest.skip(f"models not downloaded: {missing}")


@pytest.fixture(scope="module")
def kokoro(config: JarvisConfig):  # type: ignore[no-untyped-def]
    _require(config, config.tts.kokoro.model_file, config.tts.kokoro.voices_file)
    from jarvis.tts.kokoro_engine import KokoroEngine

    return KokoroEngine(config.tts.kokoro, config.paths.resolved_models_dir(), speed=1.0)


@pytest.fixture(scope="module")
def whisper(config: JarvisConfig):  # type: ignore[no-untyped-def]
    _require(config, f"whisper-{config.stt.model}/model.bin")
    from jarvis.stt.whisper_local import WhisperSTT

    return WhisperSTT(config.stt, config.paths.resolved_models_dir())


def _to_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    n = round(audio.size * 16_000 / rate)
    return np.interp(np.linspace(0, audio.size - 1, n), np.arange(audio.size), audio).astype(
        np.float32
    )


def test_configured_kokoro_voices_exist(kokoro, config: JarvisConfig) -> None:  # type: ignore[no-untyped-def]
    installed = set(kokoro.available_voices())
    assert set(config.tts.kokoro.voices.values()) <= installed


def test_silence_is_empty_without_running_the_encoder(whisper) -> None:  # type: ignore[no-untyped-def]
    result = whisper.transcribe(np.zeros(16_000, dtype=np.float32))
    assert result.text == ""


def test_english_round_trip_uses_one_encoder_pass(kokoro, whisper) -> None:  # type: ignore[no-untyped-def]
    audio = _to_16k(kokoro.synthesize("What is the capital of Australia?", "en"), 24_000)
    hits = whisper._model.cache_hits
    result = whisper.transcribe(audio)
    assert result.language == "en"
    assert "australia" in result.text.casefold()
    assert whisper._model.cache_hits == hits + 1  # decode reused detection's encoder output


def test_hindi_is_never_reported_as_urdu(kokoro, whisper) -> None:  # type: ignore[no-untyped-def]
    audio = _to_16k(kokoro.synthesize("भारत की राजधानी क्या है?", "hi"), 24_000)
    result = whisper.transcribe(audio)
    assert result.language == "hi"
    assert any("ऀ" <= ch <= "ॿ" for ch in result.text)


@pytest.fixture(scope="module")
def wake(config: JarvisConfig):  # type: ignore[no-untyped-def]
    wake_cfg = config.wake_word
    _require(config, wake_cfg.melspectrogram_file, wake_cfg.embedding_file, wake_cfg.model_file)
    from jarvis.audio.wakeword import WakeWordDetector

    return WakeWordDetector(wake_cfg, config.paths.resolved_models_dir())


def _padded(audio: np.ndarray) -> np.ndarray:
    silence = np.zeros(16_000, dtype=np.float32)
    return np.concatenate([silence, audio, silence])


def _best_score(wake, audio: np.ndarray) -> float:  # type: ignore[no-untyped-def]
    wake.reset()
    return max(wake.process(audio[i : i + 512]) for i in range(0, audio.size, 512))


@pytest.mark.parametrize("voice", ["bm_george", "af_heart", "hm_omega"])
def test_hey_jarvis_is_detected(kokoro, wake, config: JarvisConfig, voice: str) -> None:  # type: ignore[no-untyped-def]
    audio = _padded(_to_16k(kokoro.synthesize("Hey Jarvis!", "en", voice=voice), 24_000))
    assert _best_score(wake, audio) >= config.wake_word.threshold


@pytest.mark.parametrize(
    "text",
    ["Hey, how are you doing today?", "The harvest service was nervous.", "I like Iron Man."],
)
def test_other_speech_does_not_wake(kokoro, wake, config: JarvisConfig, text: str) -> None:  # type: ignore[no-untyped-def]
    audio = _padded(_to_16k(kokoro.synthesize(text, "en"), 24_000))
    assert _best_score(wake, audio) < config.wake_word.threshold


def test_streaming_vad_finds_speech_and_silence(kokoro) -> None:  # type: ignore[no-untyped-def]
    from jarvis.audio.vad import StreamingVAD

    speech = _to_16k(kokoro.synthesize("This is a test of the voice detector.", "en"), 24_000)
    vad = StreamingVAD()
    silence_probs = vad.process(np.zeros(16_000, dtype=np.float32))
    speech_probs = vad.process(speech)
    assert max(silence_probs) < 0.3
    assert np.mean(np.array(speech_probs) > 0.5) > 0.5  # most of the sentence is speech
