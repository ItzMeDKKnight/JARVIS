"""Optional voice colouring: a deeper, slightly "AI" Jarvis voice.

Three subtle effects, each easy to turn down to zero in config:

* a low-shelf boost for weight in the chest register,
* a faint doubled copy a few milliseconds late (a gentle chorus/metallic sheen),
* a very short synthetic room reverb for "presence in a space".

Implemented with numpy FFTs only, to avoid pulling in scipy (~40 MB) for
three filters. Each chunk is processed independently; sentence-sized chunks
are short enough that this costs a few milliseconds.
"""

from __future__ import annotations

import numpy as np

from jarvis.config import PostFXConfig
from jarvis.tts.base import AudioArray

_PEAK_LIMIT = 0.95
# Fixed seed: the reverb tail is noise-based but must sound identical every time.
_REVERB_SEED = 7


def _db_to_gain(db: float) -> float:
    return float(10 ** (db / 20))


def low_shelf(audio: AudioArray, sample_rate: int, cutoff_hz: float, gain_db: float) -> AudioArray:
    """Boost (or cut) frequencies below ``cutoff_hz`` with a smooth 4th-order transition."""
    if gain_db == 0 or audio.size == 0:
        return audio
    spectrum = np.fft.rfft(audio)
    freqs = np.fft.rfftfreq(audio.size, d=1 / sample_rate)
    low_weight = 1 / (1 + (freqs / cutoff_hz) ** 4)
    curve = 1 + (_db_to_gain(gain_db) - 1) * low_weight
    return np.fft.irfft(spectrum * curve, n=audio.size).astype(np.float32)


def doubled(audio: AudioArray, sample_rate: int, delay_ms: float, gain_db: float) -> AudioArray:
    """Mix in a quieter copy delayed by ``delay_ms``."""
    delay = round(sample_rate * delay_ms / 1000)
    if delay <= 0 or audio.size == 0:
        return audio
    out = np.concatenate([audio, np.zeros(delay, dtype=np.float32)])
    out[delay:] += audio * _db_to_gain(gain_db)
    return out


def reverb(audio: AudioArray, sample_rate: int, wet: float, decay_s: float) -> AudioArray:
    """Convolve with a short exponentially decaying noise burst (a tiny synthetic room)."""
    if wet <= 0 or audio.size == 0:
        return audio
    length = max(1, round(sample_rate * decay_s))
    rng = np.random.default_rng(_REVERB_SEED)
    t = np.arange(length) / sample_rate
    impulse = rng.standard_normal(length) * np.exp(-t / (decay_s / 5))
    impulse /= np.sqrt(np.sum(impulse**2))
    size = audio.size + length - 1
    n_fft = 1 << (size - 1).bit_length()
    tail = np.fft.irfft(np.fft.rfft(audio, n_fft) * np.fft.rfft(impulse, n_fft), n_fft)[:size]
    dry = np.concatenate([audio, np.zeros(length - 1, dtype=np.float32)])
    return ((1 - wet) * dry + wet * tail).astype(np.float32)


def apply_postfx(audio: AudioArray, sample_rate: int, config: PostFXConfig) -> AudioArray:
    """Run the enabled effects in order, then keep peaks below clipping."""
    if not config.enabled or audio.size == 0:
        return audio
    out = low_shelf(audio, sample_rate, config.low_shelf_hz, config.low_shelf_gain_db)
    out = doubled(out, sample_rate, config.doubling_delay_ms, config.doubling_gain_db)
    out = reverb(out, sample_rate, config.reverb_wet, config.reverb_decay_s)
    peak = float(np.max(np.abs(out)))
    if peak > _PEAK_LIMIT:
        out = out * (_PEAK_LIMIT / peak)
    return out.astype(np.float32)
