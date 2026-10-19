"""Benchmark Whisper model sizes for latency and accuracy, in English and Hindi.

Two sources of test speech:

* ``--source tts`` (default): sentences spoken by the local Kokoro voices.
  Fully automatic and repeatable, but synthetic speech is cleaner than a
  real voice, so accuracy here is an optimistic upper bound.
* ``--source mic``: you read the same sentences aloud (press Enter to start
  and stop each one). Audio stays in memory and is discarded afterwards.

    .venv/Scripts/python scripts/benchmark_stt.py
    .venv/Scripts/python scripts/benchmark_stt.py --models small large-v3-turbo --threads 4 8
    .venv/Scripts/python scripts/benchmark_stt.py --source mic --models small

Reports per model: load time, RAM, median/p90 latency for a short command,
real-time factor, CPU cores used, word error rate (English), character error
rate (Hindi) and how often the language came out right.
"""

from __future__ import annotations

import argparse
import gc
import statistics
import time
from dataclasses import dataclass

import numpy as np
import psutil

from jarvis.config import JarvisConfig, load_config
from jarvis.privacy import apply_offline_env, disable_onnxruntime_telemetry
from jarvis.text import normalize_name
from jarvis.utils.console import use_utf8_console

STT_RATE = 16_000

SENTENCES: dict[str, list[str]] = {
    "en": [
        "Open Visual Studio Code and show me my last project.",
        "What is the weather like in Mumbai today?",
        "Write a python function for binary search.",
        "What is the capital of Australia?",
        "Search the web for the latest news about electric cars.",
    ],
    "hi": [
        "आज भोपाल में मौसम कैसा है?",
        "कैलकुलेटर खोलो।",
        "मुझे पायथन में बाइनरी सर्च का कोड लिखकर दो।",
        "भारत की राजधानी क्या है?",
        "मेरे लिए एक अच्छा गाना चलाओ।",
    ],
}


@dataclass
class Sample:
    language: str
    reference: str
    audio: np.ndarray


@dataclass
class Result:
    language: str
    reference: str
    hypothesis: str
    detected: str
    whisper_guess: str
    seconds: float
    audio_seconds: float
    cpu_cores: float


def edit_distance(a: list[str], b: list[str]) -> int:
    previous = list(range(len(b) + 1))
    for i, x in enumerate(a, 1):
        current = [i]
        for j, y in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (x != y)))
        previous = current
    return previous[-1]


def error_rate(reference: str, hypothesis: str, language: str) -> float:
    """WER for English, CER (ignoring spaces) for Hindi, after normalisation."""
    ref, hyp = normalize_name(reference), normalize_name(hypothesis)
    if language == "hi":
        ref_units, hyp_units = list(ref.replace(" ", "")), list(hyp.replace(" ", ""))
    else:
        ref_units, hyp_units = ref.split(), hyp.split()
    return edit_distance(ref_units, hyp_units) / max(1, len(ref_units))


def resample(audio: np.ndarray, rate: int, target: int = STT_RATE) -> np.ndarray:
    if rate == target:
        return audio.astype(np.float32)
    n = round(audio.size * target / rate)
    positions = np.linspace(0, audio.size - 1, n)
    return np.interp(positions, np.arange(audio.size), audio).astype(np.float32)


def tts_samples(config: JarvisConfig) -> list[Sample]:
    from jarvis.tts.kokoro_engine import KokoroEngine

    engine = KokoroEngine(config.tts.kokoro, config.paths.resolved_models_dir(), speed=1.0)
    samples = []
    for language, sentences in SENTENCES.items():
        for sentence in sentences:
            audio = engine.synthesize(sentence, language)  # type: ignore[arg-type]
            samples.append(Sample(language, sentence, resample(audio, engine.sample_rate)))
    return samples


def mic_samples() -> list[Sample]:
    import sounddevice as sd

    samples = []
    print("You will read 10 sentences. Press Enter to start each one, Enter again to stop.\n")
    for language, sentences in SENTENCES.items():
        for sentence in sentences:
            input(f"Ready? [{language}] {sentence}\n  (Enter to start) ")
            blocks: list[np.ndarray] = []

            def collect(data: np.ndarray, *_: object, sink: list[np.ndarray] = blocks) -> None:
                sink.append(data[:, 0].copy())

            with sd.InputStream(samplerate=STT_RATE, channels=1, dtype="float32", callback=collect):
                input("  recording... (Enter to stop) ")
            samples.append(Sample(language, sentence, np.concatenate(blocks)))
    return samples


def run_model(
    config: JarvisConfig, model: str, threads: int, beam: int, samples: list[Sample]
) -> None:
    from jarvis.stt.whisper_local import WhisperSTT

    stt_config = config.stt.model_copy(update={"cpu_threads": threads, "beam_size": beam})
    process = psutil.Process()
    gc.collect()
    rss_before = process.memory_info().rss
    start = time.perf_counter()
    stt = WhisperSTT(stt_config, config.paths.resolved_models_dir(), model=model)
    load_s = time.perf_counter() - start
    stt.transcribe(samples[0].audio)  # warm-up: first call includes one-time setup
    stt.forget_cached_encoding()  # so the first measured sample is not a cache hit
    ram_mb = (process.memory_info().rss - rss_before) / 2**20

    results = []
    for sample in samples:
        cpu_before = sum(process.cpu_times()[:2])
        t0 = time.perf_counter()
        out = stt.transcribe(sample.audio)
        elapsed = time.perf_counter() - t0
        cpu = sum(process.cpu_times()[:2]) - cpu_before
        results.append(
            Result(
                sample.language,
                sample.reference,
                out.text,
                out.language,
                out.detected_language,
                elapsed,
                sample.audio.size / STT_RATE,
                cpu / elapsed if elapsed else 0.0,
            )
        )
    del stt
    gc.collect()
    report(model, threads, beam, load_s, ram_mb, results)


def report(
    model: str, threads: int, beam: int, load_s: float, ram_mb: float, results: list[Result]
) -> None:
    print(
        f"\n=== whisper-{model}  threads={threads} beam={beam}  "
        f"load {load_s:.1f}s  +RAM {ram_mb:.0f} MB"
    )
    for language in ("en", "hi"):
        rows = [r for r in results if r.language == language]
        if not rows:
            continue
        latencies = sorted(r.seconds for r in rows)
        p90 = latencies[min(len(latencies) - 1, int(0.9 * len(latencies)))]
        rtf = statistics.mean(r.seconds / r.audio_seconds for r in rows)
        err = statistics.mean(error_rate(r.reference, r.hypothesis, language) for r in rows)
        lang_ok = sum(r.detected == language for r in rows)
        raw_ok = sum(r.whisper_guess == language for r in rows)
        metric = "WER" if language == "en" else "CER"
        cores = statistics.mean(r.cpu_cores for r in rows)
        print(
            f"  [{language}] latency median {statistics.median(latencies) * 1000:.0f} ms, "
            f"p90 {p90 * 1000:.0f} ms, RTF {rtf:.2f}, CPU cores ~{cores:.1f}, "
            f"{metric} {err * 100:.1f}%, language ok {lang_ok}/{len(rows)} "
            f"(Whisper's own guess ok {raw_ok}/{len(rows)})"
        )
        for r in rows:
            print(
                f"      {r.seconds * 1000:5.0f} ms  {r.whisper_guess}->{r.detected}  {r.hypothesis}"
            )


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--source", choices=["tts", "mic"], default="tts")
    parser.add_argument("--models", nargs="+", default=["small", "medium", "large-v3-turbo"])
    parser.add_argument("--threads", nargs="+", type=int, default=None)
    parser.add_argument("--beam", type=int, default=None)
    args = parser.parse_args()

    apply_offline_env()
    disable_onnxruntime_telemetry()
    config = load_config()
    samples = tts_samples(config) if args.source == "tts" else mic_samples()
    print(
        f"{len(samples)} samples, {sum(s.audio.size for s in samples) / STT_RATE:.1f} s of audio, "
        f"CPU: {psutil.cpu_count(logical=False)} cores"
    )
    for model in args.models:
        for threads in args.threads or [config.stt.cpu_threads]:
            run_model(config, model, threads, args.beam or config.stt.beam_size, samples)


if __name__ == "__main__":
    main()
