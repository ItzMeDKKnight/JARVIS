"""Play sample lines in each candidate voice so you can pick Jarvis's voice by ear.

    .venv/Scripts/python scripts/audition_voices.py               # English + Hindi candidates
    .venv/Scripts/python scripts/audition_voices.py --lang en --voices bm_george bm_lewis
    .venv/Scripts/python scripts/audition_voices.py --postfx both # dry, then with FX
    .venv/Scripts/python scripts/audition_voices.py --engine piper
    .venv/Scripts/python scripts/audition_voices.py --list        # every installed voice

Then set tts.kokoro.voices (or tts.engine) in config/config.yaml.
Audio is played directly and never written to disk.
"""

from __future__ import annotations

import argparse
import time

import numpy as np
import sounddevice as sd

from jarvis.config import load_config
from jarvis.privacy import apply_offline_env, disable_onnxruntime_telemetry
from jarvis.tts.kokoro_engine import KokoroEngine
from jarvis.tts.postfx import apply_postfx
from jarvis.utils.console import use_utf8_console

SAMPLES = {
    "en": "Hello Tony, whatsup? All systems are online, and I've taken the liberty of "
    "checking the weather. Clear skies, thirty one degrees.",
    "hi": "नमस्ते टोनी, सभी सिस्टम ठीक से काम कर रहे हैं। आज मौसम साफ़ है।",
}
# Candidates: British and American male voices for English, the two Hindi male voices.
CANDIDATES = {
    "en": ["bm_george", "bm_lewis", "bm_fable", "bm_daniel", "am_michael", "am_fenrir", "am_onyx"],
    "hi": ["hm_omega", "hm_psi"],
}


def play(audio: np.ndarray, rate: int) -> None:
    sd.play(audio, rate)
    sd.wait()
    time.sleep(0.4)


def audition_kokoro(args: argparse.Namespace) -> None:
    config = load_config()
    engine = KokoroEngine(
        config.tts.kokoro, config.paths.resolved_models_dir(), speed=config.tts.speed
    )
    if args.list:
        print("\n".join(engine.available_voices()))
        return
    languages = [args.lang] if args.lang else ["en", "hi"]
    for language in languages:
        voices = args.voices or CANDIDATES[language]
        text = args.text or SAMPLES[language]
        for voice in voices:
            audio = engine.synthesize(text, language, voice=voice)
            for fx in ["off", "on"] if args.postfx == "both" else [args.postfx]:
                label = f"{voice} ({language}, postfx {fx})"
                print(f"Playing {label} ...", flush=True)
                out = (
                    apply_postfx(audio, engine.sample_rate, config.tts.postfx)
                    if fx == "on"
                    else audio
                )
                play(out, engine.sample_rate)


def audition_piper(args: argparse.Namespace) -> None:
    from jarvis.tts.piper_engine import PiperEngine

    config = load_config()
    engine = PiperEngine(
        config.tts.piper, config.paths.resolved_models_dir(), speed=config.tts.speed
    )
    for language in [args.lang] if args.lang else ["en", "hi"]:
        print(f"Playing Piper {config.tts.piper.voices[language]} ...", flush=True)
        play(engine.synthesize(args.text or SAMPLES[language], language), engine.sample_rate)


def main() -> None:
    use_utf8_console()
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--engine", choices=["kokoro", "piper"], default="kokoro")
    parser.add_argument("--lang", choices=["en", "hi"])
    parser.add_argument("--voices", nargs="+", help="Kokoro voice names to try")
    parser.add_argument("--text", help="custom line to speak")
    parser.add_argument("--postfx", choices=["off", "on", "both"], default="on")
    parser.add_argument("--list", action="store_true", help="list installed Kokoro voices")
    args = parser.parse_args()

    apply_offline_env()
    disable_onnxruntime_telemetry()
    if args.engine == "piper":
        audition_piper(args)
    else:
        audition_kokoro(args)


if __name__ == "__main__":
    main()
