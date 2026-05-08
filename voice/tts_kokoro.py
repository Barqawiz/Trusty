"""Kokoro ONNX text-to-speech. Plays via sounddevice.

A single Kokoro instance is loaded lazily on first use and cached. ONNX model
is the int8 build (~80 MB) per blueprint. Voice / language / speed read from
.env via app.settings.

Kokoro emits audio at 24 kHz. Some USB DACs (e.g. the cheap CD002 / TI
PCM2902 found on Pi setups) only accept 48 kHz on their hw device, so we
detect the speaker's accepted rate once and resample on the way out.
"""
from __future__ import annotations

import logging
import os
import threading
from functools import lru_cache
from math import gcd
from pathlib import Path

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)
_play_lock = threading.Lock()


@lru_cache(maxsize=4)
def _accepted_output_rate(source_rate: int) -> int:
    """Return a sample rate the default output device accepts. Prefer the
    source's native rate; fall back to 48 / 44.1 / 16 kHz; otherwise the
    device's `default_samplerate`."""
    candidates = [source_rate, 48_000, 44_100, 22_050, 16_000]
    for r in candidates:
        try:
            sd.check_output_settings(samplerate=r, channels=1, dtype="float32")
            return r
        except Exception:
            continue
    try:
        info = sd.query_devices(None, "output")
        return int(round(info.get("default_samplerate") or 48_000))
    except Exception:
        return 48_000


def _resample(samples: np.ndarray, src_rate: int, dst_rate: int) -> np.ndarray:
    if src_rate == dst_rate:
        return samples
    g = gcd(src_rate, dst_rate)
    up = dst_rate // g
    down = src_rate // g
    from scipy.signal import resample_poly

    return resample_poly(samples.astype(np.float32), up, down)


@lru_cache(maxsize=1)
def _instance():
    from kokoro_onnx import Kokoro  # heavy import, deferred

    model_path = os.environ["KOKORO_MODEL_PATH"]
    voices_path = os.environ["KOKORO_VOICES_PATH"]
    if not Path(model_path).is_file() or not Path(voices_path).is_file():
        raise FileNotFoundError(
            f"Kokoro files missing: {model_path} / {voices_path}"
        )
    log.info("Loading Kokoro: %s", model_path)
    return Kokoro(model_path, voices_path)


def synthesize(text: str) -> tuple[np.ndarray, int]:
    voice = os.environ.get("KOKORO_VOICE", "af_heart")
    lang = os.environ.get("KOKORO_LANG", "en-us")
    speed = float(os.environ.get("KOKORO_SPEED", "1.0"))
    kokoro = _instance()
    samples, sample_rate = kokoro.create(
        text=text, voice=voice, speed=speed, lang=lang
    )
    return samples, sample_rate


def speak(text: str) -> None:
    """Synthesize and play through the default audio device."""
    text = (text or "").strip()
    if not text:
        return
    try:
        samples, sample_rate = synthesize(text)
    except Exception as e:
        log.warning("TTS synth failed: %s", e)
        return
    target_rate = _accepted_output_rate(int(sample_rate))
    if target_rate != sample_rate:
        log.debug(
            "TTS resample %d Hz -> %d Hz for output device", sample_rate, target_rate
        )
        try:
            samples = _resample(samples, int(sample_rate), target_rate)
        except Exception as e:
            log.warning("TTS resample failed: %s — playing at native rate", e)
            target_rate = int(sample_rate)
    with _play_lock:
        try:
            sd.play(samples, target_rate)
            sd.wait()
        except Exception as e:
            log.warning("TTS playback failed: %s", e)


def synthesize_to_wav(text: str, out_path: str) -> str:
    """Useful for headless/CI testing — writes a wav and returns the path."""
    import soundfile as sf

    samples, sample_rate = synthesize(text)
    sf.write(out_path, samples, sample_rate)
    return out_path
