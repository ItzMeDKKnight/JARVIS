"""Measure how often "Hey Jarvis" fires when it shouldn't (and misses when it should).

Two sources:

* ``--source mic --minutes 10``: listen to the real room (TV, music, people
  talking) and log every detection with its time and score. Use it to tune
  ``wake_word.threshold``. Only scores are kept; no audio is stored.
* ``--source tts``: an automatic evaluation with the local Kokoro voices. It
  speaks "Hey Jarvis" in many voices and speeds (should detect), and many
  ordinary and near-miss sentences mixed with noise (should not detect).
  Synthetic speech isn't a real room, so treat it as a sanity check, not a
  guarantee.

    .venv/Scripts/python scripts/wakeword_soak.py --source tts
    .venv/Scripts/python scripts/wakeword_soak.py --source mic --minutes 10
"""

from __future__ import annotations

import argparse
import asyncio
import time
from typing import TYPE_CHECKING

import numpy as np

from jarvis.config import JarvisConfig, load_config
from jarvis.privacy import apply_offline_env, disable_onnxruntime_telemetry
from jarvis.utils.console import use_utf8_console

if TYPE_CHECKING:
    from jarvis.audio.wakeword import WakeWordDetector

RATE = 16_000
BLOCK = 512

POSITIVE = ["Hey Jarvis!", "Hey Jarvis.", "Hey, Jarvis!", "Hey Jarvis, what's the weather?"]
NEGATIVE = [
    "Hey, how are you doing today?",
    "The harvest service was nervous.",
    "Jars of jam are on the shelf.",
    "Hey Travis, pass me the salt.",
    "I watched Iron Man yesterday and Jarvis was brilliant.",
    "Hey buddy, have you seen my charger?",
    "Our service team will call you on Thursday.",
    "Paris is the capital of France.",
    "Let's order some pizza for dinner tonight.",
    "The weather is nice, let's go for a walk.",
    "Hey there, welcome back to the show.",
    "He drives a jeep through the jungle.",
    "Can you pass the remote control please?",
    "Turn up the volume, this song is great.",
    "The jury reached a verdict this afternoon.",
    "Hey Siri, set a timer for ten minutes.",
    "Hey Google, what's on my calendar?",
    "Alexa, play some music.",
]
NEGATIVE_HI = [
    "आज मौसम बहुत अच्छा है।",
    "चलो खाना खाने चलते हैं।",
    "मुझे कल सुबह जल्दी उठना है।",
    "यह गाना बहुत अच्छा है।",
]
VOICES_EN = ["bm_george", "bm_lewis", "am_michael", "am_fenrir", "af_heart", "bf_emma", "am_onyx"]


def _to_16k(audio: np.ndarray, rate: int) -> np.ndarray:
    n = round(audio.size * RATE / rate)
    positions = np.linspace(0, audio.size - 1, n)
    return np.interp(positions, np.arange(audio.size), audio).astype(np.float32)


def _scores(detector: WakeWordDetector, audio: np.ndarray) -> float:
    detector.reset()
    padded = np.concatenate([np.zeros(RATE, np.float32), audio, np.zeros(RATE // 2, np.float32)])
    return max(detector.process(padded[i : i + BLOCK]) for i in range(0, padded.size, BLOCK))


def tts_eval(config: JarvisConfig) -> None:
    from jarvis.audio.wakeword import WakeWordDetector
    from jarvis.tts.kokoro_engine import KokoroEngine

    models = config.paths.resolved_models_dir()
    detector = WakeWordDetector(config.wake_word, models)
    rng = np.random.default_rng(0)
    thresholds = (config.wake_word.threshold, config.wake_word.speaking_threshold)

    positives: list[tuple[str, float]] = []
    negatives: list[tuple[str, float]] = []
    negative_seconds = 0.0
    compute = 0.0
    for speed in (0.85, 1.0, 1.2):
        engine = KokoroEngine(config.tts.kokoro, models, speed=speed)
        for voice in VOICES_EN:
            for text in POSITIVE:
                audio = _to_16k(engine.synthesize(text, "en", voice=voice), engine.sample_rate)
                positives.append((f"{voice}@{speed} {text}", _scores(detector, audio)))
            for text in NEGATIVE:
                audio = _to_16k(engine.synthesize(text, "en", voice=voice), engine.sample_rate)
                noisy = audio + rng.normal(0, 0.01, audio.size).astype(np.float32)
                t0 = time.perf_counter()
                negatives.append((f"{voice}@{speed} {text}", _scores(detector, noisy)))
                compute += time.perf_counter() - t0
                negative_seconds += noisy.size / RATE + 1.5
        for voice in ("hm_omega", "hm_psi"):
            for text in NEGATIVE_HI:
                audio = _to_16k(engine.synthesize(text, "hi", voice=voice), engine.sample_rate)
                negatives.append((f"{voice}@{speed} {text}", _scores(detector, audio)))
                negative_seconds += audio.size / RATE + 1.5

    print(
        f"positives: {len(positives)} clips; negatives: {len(negatives)} clips "
        f"(~{negative_seconds / 60:.1f} min of speech + noise)"
    )
    for threshold in thresholds:
        hits = sum(score >= threshold for _, score in positives)
        false = [(name, score) for name, score in negatives if score >= threshold]
        print(
            f"threshold {threshold:.2f}: detected {hits}/{len(positives)} "
            f"({100 * hits / len(positives):.0f}%), false triggers {len(false)}/{len(negatives)}"
        )
        for name, score in false:
            print(f"    false trigger {score:.3f}  {name}")
    missed = sorted(positives, key=lambda item: item[1])[:5]
    print("weakest positives:", ", ".join(f"{s:.2f} {n}" for n, s in missed))
    near = sorted(negatives, key=lambda item: -item[1])[:5]
    print("highest negatives:", ", ".join(f"{s:.2f} {n}" for n, s in near))
    print(
        f"detector cost: {1000 * compute / (negative_seconds * 1000 / 80):.2f} ms per 80 ms frame "
        f"(one thread)"
    )


async def mic_soak(config: JarvisConfig, minutes: float) -> None:
    from jarvis.audio.capture import MicStream
    from jarvis.audio.wakeword import WakeWordDetector

    detector = WakeWordDetector(config.wake_word, config.paths.resolved_models_dir())
    loop = asyncio.get_running_loop()
    mic = MicStream(config.audio, loop, lambda _level: None, preroll_ms=0)
    mic.start()
    print(f"Listening for {minutes} min. Play TV/music or talk normally; don't say 'Hey Jarvis'.")
    start = time.monotonic()
    end = start + minutes * 60
    detections = 0
    best = 0.0
    loudest = -120.0
    try:
        while time.monotonic() < end:
            block = await mic.read()
            loudest = max(loudest, 20 * np.log10(max(float(np.sqrt(np.mean(block**2))), 1e-9)))
            score = detector.process(block)
            best = max(best, score)
            if score >= config.wake_word.threshold:
                detections += 1
                print(f"  {time.monotonic() - start:6.1f}s  detection, score {score:.3f}")
                detector.reset()
    finally:
        mic.stop()
    hours = minutes / 60
    print(
        f"{detections} false trigger(s) in {minutes} min ({detections / hours:.1f}/hour); "
        f"highest score {best:.3f}; loudest block {loudest:.1f} dBFS"
    )
    if loudest < -80:
        print("WARNING: the microphone delivered (near) silence. Is it muted?")


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", choices=["tts", "mic"], default="tts")
    parser.add_argument("--minutes", type=float, default=10)
    args = parser.parse_args()

    apply_offline_env()
    disable_onnxruntime_telemetry()
    config = load_config()
    if args.source == "tts":
        tts_eval(config)
    else:
        asyncio.run(mic_soak(config, args.minutes))


if __name__ == "__main__":
    main()
