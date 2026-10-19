"""Audio-adjacent logic that needs no devices: levels, postfx, push-to-talk, yes/no."""

from __future__ import annotations

import numpy as np
import pytest

from jarvis.audio.levels import rms_level
from jarvis.audio.ptt import HoldResult, PushToTalk
from jarvis.config import PostFXConfig
from jarvis.confirm import is_yes
from jarvis.stt.whisper_local import _best_allowed
from jarvis.tts.postfx import apply_postfx, doubled, low_shelf, reverb

RATE = 24_000


def _tone(freq: float, seconds: float = 0.5, amp: float = 0.5) -> np.ndarray:
    t = np.arange(int(RATE * seconds)) / RATE
    return (amp * np.sin(2 * np.pi * freq * t)).astype(np.float32)


def _rms(x: np.ndarray) -> float:
    return float(np.sqrt(np.mean(x.astype(np.float64) ** 2)))


# -- levels ---------------------------------------------------------------------


def test_level_meter_maps_db_range() -> None:
    assert rms_level(np.zeros(512, dtype=np.float32), -60, -10) == 0.0
    loud = np.full(512, 0.9, dtype=np.float32)
    assert rms_level(loud, -60, -10) == 1.0
    # -35 dBFS sits halfway between -60 and -10.
    mid = np.full(512, 10 ** (-35 / 20), dtype=np.float32)
    assert rms_level(mid, -60, -10) == pytest.approx(0.5, abs=0.01)


# -- postfx ---------------------------------------------------------------------


def test_low_shelf_boosts_bass_but_not_treble() -> None:
    bass, treble = _tone(80), _tone(4000)
    assert _rms(low_shelf(bass, RATE, 220, 6)) / _rms(bass) == pytest.approx(2.0, rel=0.1)
    assert _rms(low_shelf(treble, RATE, 220, 6)) / _rms(treble) == pytest.approx(1.0, abs=0.02)


def test_doubling_and_reverb_extend_the_tail() -> None:
    audio = _tone(440, 0.2)
    assert doubled(audio, RATE, 14, -12).size == audio.size + round(RATE * 0.014)
    assert reverb(audio, RATE, 0.1, 0.2).size > audio.size


def test_reverb_is_deterministic() -> None:
    audio = _tone(440, 0.2)
    assert np.array_equal(reverb(audio, RATE, 0.1, 0.2), reverb(audio, RATE, 0.1, 0.2))


def test_postfx_never_clips_and_can_be_disabled() -> None:
    config = PostFXConfig(
        enabled=True,
        low_shelf_hz=220,
        low_shelf_gain_db=12,
        reverb_wet=0.3,
        reverb_decay_s=0.2,
        doubling_delay_ms=14,
        doubling_gain_db=0,
    )
    hot = _tone(100, amp=0.99)
    assert np.max(np.abs(apply_postfx(hot, RATE, config))) <= 0.95 + 1e-6
    off = config.model_copy(update={"enabled": False})
    assert apply_postfx(hot, RATE, off) is hot


# -- push-to-talk ---------------------------------------------------------------


class ScriptedKeys:
    """Key state that changes on a schedule of poll counts."""

    def __init__(self, ptt_down: list[bool], other_down: list[bool] | None = None) -> None:
        self._ptt = ptt_down
        self._other = other_down or [False] * len(ptt_down)
        self._i = 0

    def is_down(self, key: str) -> bool:
        value = self._ptt[min(self._i, len(self._ptt) - 1)]
        self._i += 1
        return value

    def other_key_down(self, key: str) -> bool:
        return self._other[min(self._i, len(self._other) - 1)]


def _ptt(keys: ScriptedKeys, threshold_ms: int = 0) -> PushToTalk:
    return PushToTalk(keys, "left_ctrl", hold_threshold_ms=threshold_ms, poll_hz=200)


async def test_hold_alone_is_confirmed() -> None:
    assert await _ptt(ScriptedKeys([True] * 50), threshold_ms=20).confirm_hold() is (
        HoldResult.CONFIRMED
    )


async def test_tap_is_ignored() -> None:
    keys = ScriptedKeys([True, False])
    assert await _ptt(keys, threshold_ms=200).confirm_hold() is HoldResult.RELEASED


async def test_ctrl_c_is_a_chord_not_push_to_talk() -> None:
    keys = ScriptedKeys([True, True, True, False], other_down=[False, True, True, False])
    assert await _ptt(keys, threshold_ms=500).confirm_hold() is HoldResult.CHORD


async def test_wait_for_press_needs_a_fresh_press() -> None:
    # Key already held at start (e.g. leftover from a shortcut): must see up, then down.
    keys = ScriptedKeys([True, True, False, False, True])
    await _ptt(keys).wait_for_press()
    assert keys._i == 5


# -- yes / no ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "answer", ["yes", "Yes please.", "haan", "haan ji, khol do", "हाँ", "जी हाँ", "ठीक है", "okay"]
)
def test_yes_answers(answer: str) -> None:
    assert is_yes(answer)


@pytest.mark.parametrize(
    "answer",
    [
        "no",
        "No, don't open it.",
        "nahi",
        "नहीं",
        "मत खोलो",
        "",
        None,
        "hmm",
        "yes no",
        "okay, cancel",
    ],
)
def test_no_or_unclear_answers(answer: str | None) -> None:
    assert not is_yes(answer)


# -- whisper language restriction ---------------------------------------------------


def test_urdu_guess_becomes_hindi() -> None:
    probs = [("ur", 0.62), ("hi", 0.30), ("en", 0.05)]
    assert _best_allowed(probs, ("en", "hi")) == "hi"


def test_missing_probabilities_fall_back_to_first_allowed() -> None:
    assert _best_allowed(None, ("en", "hi")) == "en"


# -- end-of-speech detection ----------------------------------------------------------

from jarvis.audio.vad import CHUNK_MS, Endpointer, SpeechEvent  # noqa: E402
from jarvis.config import VADConfig  # noqa: E402

VAD = VADConfig(
    threshold=0.5, neg_threshold=0.35, min_speech_ms=250, end_silence_ms=800, start_timeout_s=2
)


def _run(endpointer: Endpointer, probabilities: list[float]) -> list[tuple[int, SpeechEvent]]:
    events = []
    for i, p in enumerate(probabilities):
        event = endpointer.feed(p)
        if event is not None:
            events.append((i, event))
    return events


def _chunks(ms: float) -> int:
    return round(ms / CHUNK_MS)


def test_utterance_starts_and_ends_after_the_silence_timeout() -> None:
    probs = [0.0] * 10 + [0.9] * _chunks(1000) + [0.1] * _chunks(1000)
    events = _run(Endpointer(VAD), probs)
    assert [e for _, e in events] == [SpeechEvent.STARTED, SpeechEvent.ENDED]
    end_index = events[1][0]
    assert end_index == 10 + _chunks(1000) + _chunks(800) - 1


def test_short_pauses_inside_speech_do_not_end_it() -> None:
    probs = [0.9] * _chunks(500) + [0.1] * _chunks(400) + [0.9] * _chunks(500) + [0.1] * 40
    events = _run(Endpointer(VAD), probs)
    assert [e for _, e in events] == [SpeechEvent.STARTED, SpeechEvent.ENDED]


def test_values_between_thresholds_keep_speaking() -> None:
    probs = [0.9] * _chunks(500) + [0.4] * _chunks(2000)
    assert [e for _, e in _run(Endpointer(VAD), probs)] == [SpeechEvent.STARTED]


def test_a_cough_is_not_an_utterance() -> None:
    probs = [0.9] * _chunks(100) + [0.1] * _chunks(900) + [0.9] * _chunks(600) + [0.1] * 30
    events = [e for _, e in _run(Endpointer(VAD), probs)]
    assert events == [SpeechEvent.STARTED, SpeechEvent.ENDED]  # only the real speech counts


def test_nobody_speaks_within_the_timeout() -> None:
    events = _run(Endpointer(VAD), [0.1] * _chunks(2500))
    assert events[0] == (_chunks(2000) - 1, SpeechEvent.NO_SPEECH)
