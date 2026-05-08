"""Microphone capture as 16 kHz mono int16 numpy frames.

Used by both wakeword and STT. On Mac and Pi the device discovery is the same;
the user can pin a device by index via the SOUNDDEVICE_INPUT env var.

Some USB mics (e.g. cheap C-Media dongles on Pi) only expose their native
sample rate (44.1 / 48 kHz) and PortAudio refuses 16 kHz outright. We
detect the mic's native rate, capture at THAT, and resample to 16 kHz in
the callback before handing frames off — wakeword + STT keep their
existing 16 kHz contract.
"""
from __future__ import annotations

import json
import logging
import os
import queue
from contextlib import contextmanager
from functools import lru_cache
from pathlib import Path
from typing import Iterator

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

SAMPLE_RATE = 16_000
CHANNELS = 1
DTYPE = "int16"
FRAME_MS = 80  # openWakeWord works on 80 ms windows
FRAME_SAMPLES = SAMPLE_RATE * FRAME_MS // 1000  # 1280


def _device() -> int | None:
    raw = os.environ.get("SOUNDDEVICE_INPUT")
    if raw is None or raw == "":
        return None
    try:
        return int(raw)
    except ValueError:
        return raw  # type: ignore[return-value]


@lru_cache(maxsize=4)
def _pick_capture_rate(device: int | str | None) -> int:
    """Return a sample rate the device actually accepts. Prefer 16 kHz; fall
    back to the device's native rate (44.1 / 48 kHz) for cheap USB mics
    that don't accept 16 kHz directly.

    Cached per device — `sd.query_devices` + `check_input_settings` calls
    are cheap but not free, and the device's accepted rates don't change
    while the device is plugged in. Cache key is the env-resolved device
    handle, so unplugging and choosing a different one (different
    SOUNDDEVICE_INPUT) gets a fresh probe."""
    candidates = [SAMPLE_RATE]
    try:
        info = sd.query_devices(device, "input")
        native = int(round(info.get("default_samplerate", 0) or 0))
        if native and native not in candidates:
            candidates.append(native)
    except Exception:
        pass
    for extra in (48_000, 44_100):
        if extra not in candidates:
            candidates.append(extra)
    for r in candidates:
        try:
            sd.check_input_settings(
                device=device, samplerate=r, channels=CHANNELS, dtype=DTYPE
            )
            return r
        except Exception:
            continue
    # Last-ditch: try the first option and let the OS error speak for itself.
    return candidates[0]


# Per-mic capture profile. Loaded from config/mic_profiles.json at import
# time and matched against the device name; unknown mics use the default
# profile. JSON edits picked up on next voice-loop restart. Built-in
# constants below act as a safety fallback if the JSON is missing/invalid.
_BUILTIN_DEFAULT = {
    "name": "default", "speech_rms": 350, "silence_rms": 200, "scale": 1.0,
}
_BUILTIN_PROFILES: list[dict] = [
    {"name": "razer", "match": ["razer", "seiren"],
     "speech_rms": 1000, "silence_rms": 750, "scale": 0.5},
]


def _load_profiles_from_json() -> tuple[dict, list[dict]]:
    """Return (default_profile, profiles_list) from config/mic_profiles.json.
    Falls back to the built-in constants if the file is missing or malformed."""
    path = Path(__file__).resolve().parents[1] / "config" / "mic_profiles.json"
    if not path.is_file():
        return _BUILTIN_DEFAULT, _BUILTIN_PROFILES
    try:
        data = json.loads(path.read_text())
    except Exception as e:
        log.warning("mic profiles JSON load failed (%s) — using built-in", e)
        return _BUILTIN_DEFAULT, _BUILTIN_PROFILES
    raw_default = data.get("default") or {}
    default = {**_BUILTIN_DEFAULT, **raw_default}
    default.setdefault("name", "default")
    profiles: list[dict] = []
    for p in (data.get("profiles") or []):
        try:
            profiles.append({
                "name": str(p.get("name", "?")),
                "match": [str(m).lower() for m in (p.get("match") or [])],
                "speech_rms": int(p.get("speech_rms", default["speech_rms"])),
                "silence_rms": int(p.get("silence_rms", default["silence_rms"])),
                "scale": float(p.get("scale", default["scale"])),
            })
        except Exception as e:
            log.warning("mic profile %r skipped (%s)", p.get("name"), e)
    return default, profiles


_DEFAULT_PROFILE, _MIC_PROFILES = _load_profiles_from_json()


@lru_cache(maxsize=4)
def _pick_mic_profile(device: int | str | None) -> dict:
    """One-time mic profile lookup by device name. Returns the default
    profile for unrecognised mics so existing setups keep their behaviour."""
    try:
        info = sd.query_devices(device, "input")
        name = str(info.get("name", "")).lower()
    except Exception:
        return _DEFAULT_PROFILE
    for profile in _MIC_PROFILES:
        for needle in profile.get("match", []):
            if needle in name:
                log.info(
                    "mic profile: %r matched %r — speech_rms=%d silence_rms=%d",
                    name, profile["name"], profile["speech_rms"], profile["silence_rms"],
                )
                return profile
    log.info(
        "mic profile: %r — using default thresholds (speech_rms=%d, silence_rms=%d)",
        name, _DEFAULT_PROFILE["speech_rms"], _DEFAULT_PROFILE["silence_rms"],
    )
    return _DEFAULT_PROFILE


def _resample_to_16k(samples: np.ndarray, src_rate: int) -> np.ndarray:
    if src_rate == SAMPLE_RATE:
        return samples
    # Integer up/down ratio when possible (44100 → 16000 = 160 / 441).
    from math import gcd

    g = gcd(SAMPLE_RATE, src_rate)
    up = SAMPLE_RATE // g
    down = src_rate // g
    from scipy.signal import resample_poly

    out = resample_poly(samples.astype(np.float32), up, down)
    return np.clip(out, -32768, 32767).astype(np.int16)


@contextmanager
def open_input_stream() -> Iterator[queue.Queue[np.ndarray]]:
    """Open a sounddevice InputStream and yield a queue of 16 kHz frames.

    If the mic doesn't natively accept 16 kHz, we capture at its native
    rate and resample on the way out so callers always see 16 kHz int16.
    """
    q: queue.Queue[np.ndarray] = queue.Queue()

    device = _device()
    capture_rate = _pick_capture_rate(device)
    if capture_rate != SAMPLE_RATE:
        log.info(
            "mic does not accept %d Hz; capturing at %d Hz and resampling",
            SAMPLE_RATE, capture_rate,
        )
    # Block size is chosen so that AFTER resampling we deliver FRAME_SAMPLES
    # 16 kHz samples per callback (the wakeword's 80 ms frame).
    capture_block = capture_rate * FRAME_MS // 1000

    # Per-mic capture-side software attenuation (e.g. Razer scale=0.5).
    # Resolved once at stream open; default 1.0 leaves cheap-mic signal
    # paths unchanged.
    scale = float(_pick_mic_profile(device).get("scale", 1.0))
    if scale != 1.0:
        log.info("mic profile: applying capture scale %.2f", scale)

    def callback(indata, frames, time_info, status):  # noqa: ARG001
        if status:
            return
        raw = indata.reshape(-1).astype(np.int16)
        resampled = _resample_to_16k(raw, capture_rate)
        if scale != 1.0:
            resampled = np.clip(
                resampled.astype(np.float32) * scale, -32768, 32767,
            ).astype(np.int16)
        q.put(resampled)

    stream = sd.InputStream(
        samplerate=capture_rate,
        channels=CHANNELS,
        dtype=DTYPE,
        blocksize=capture_block,
        callback=callback,
        device=device,
    )
    with stream:
        yield q


def _rms(frame: np.ndarray) -> int:
    if frame.size == 0:
        return 0
    return int(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))


# VAD probability thresholds. Hysteresis: a chunk above SPEECH_P flips
# speech_seen on; chunks below SILENCE_P count toward the silence
# terminator. Anything between is ambiguous and ignored. Values match
# the silero-vad PyPI defaults (0.5 / 0.35) and have been stable across
# every benchmark I've seen.
_VAD_SPEECH_P = 0.5
_VAD_SILENCE_P = 0.35


def record_until_silence(
    max_seconds: float = 12.0,
    silence_seconds: float = 1.6,
    min_seconds: float = 2.0,
    initial_speech_timeout: float | None = None,
    q: queue.Queue[np.ndarray] | None = None,
    on_speech_start=None,
) -> np.ndarray:
    """Record until `silence_seconds` of non-speech AFTER real speech, or max_seconds.

    Returns a *speech-tight* buffer trimmed to the user's actual utterance
    (with ~240 ms of padding on each side). Empty array means we never
    detected speech — caller should treat that as silence.

    Speech detection runs through Silero VAD, not RMS. RMS thresholds
    routinely fired on TTS bleed-back, fan noise and mic preamp hiss,
    handing Moonshine a buffer of mostly-silence and getting '' back.
    Silero classifies speech vs non-speech directly, ignores environmental
    energy, and works the same on every mic.

    `q` is the live wake-word stream queue; we drain that instead of
    opening a second InputStream (USB mics on Linux are ALSA-exclusive).
    """
    from .vad_silero import SileroVAD, trim_to_speech, CHUNK_SAMPLES, SAMPLE_RATE as VAD_SR

    silent_target = int(silence_seconds * 1000 / FRAME_MS)
    min_frames = int(min_seconds * 1000 / FRAME_MS)
    max_frames = int(max_seconds * 1000 / FRAME_MS)
    initial_timeout_frames = (
        int(initial_speech_timeout * 1000 / FRAME_MS)
        if initial_speech_timeout else None
    )

    def _loop(qq: queue.Queue[np.ndarray]) -> np.ndarray:
        vad = SileroVAD()
        frames: list[np.ndarray] = []
        silent_frames = 0
        speech_seen = False
        peak_rms = 0
        peak_p = 0.0
        for i in range(max_frames):
            frame = qq.get()
            frames.append(frame)
            peak_rms = max(peak_rms, _rms(frame))
            p = vad.feed_frame(frame)
            peak_p = max(peak_p, p)

            if p >= _VAD_SPEECH_P:
                if not speech_seen and on_speech_start is not None:
                    # Fire ONCE, on first speech frame — used by the Mac
                    # wrapper to flip the transcript bubble to "Listening…".
                    try: on_speech_start()
                    except Exception: pass
                speech_seen = True
                silent_frames = 0
                continue

            # Initial-timeout guard for conversation follow-ups: if we
            # haven't heard speech within the first N seconds, return
            # empty so the caller can exit follow-up mode quickly.
            if (initial_timeout_frames is not None
                    and not speech_seen
                    and i + 1 >= initial_timeout_frames):
                log.info(
                    "record: no speech within %.1fs follow-up window — "
                    "exiting early", initial_speech_timeout,
                )
                return np.zeros(0, dtype=np.int16)

            if speech_seen and p < _VAD_SILENCE_P:
                silent_frames += 1
                if silent_frames >= silent_target and i + 1 >= min_frames:
                    break
            else:
                silent_frames = 0

        if not frames:
            log.info("record: frames=0 (no audio)")
            return np.zeros(0, dtype=np.int16)
        full = np.concatenate(frames)
        full_duration = full.size / VAD_SR

        if not speech_seen:
            log.info(
                "record: frames=%d duration=%.2fs peak_rms=%d peak_p=%.2f "
                "speech_seen=False (returning empty)",
                len(frames), full_duration, peak_rms, peak_p,
            )
            return np.zeros(0, dtype=np.int16)

        # Stateless second pass over the full buffer to find the speech
        # region precisely. Trims away leading hesitation, trailing
        # silence, and the silence_seconds tail the terminator added.
        probs = vad.score_buffer(full)
        trimmed = trim_to_speech(full, probs, threshold=_VAD_SPEECH_P)
        trimmed_duration = trimmed.size / VAD_SR if trimmed.size else 0.0
        speech_chunks = int((probs >= _VAD_SPEECH_P).sum())
        log.info(
            "record: frames=%d duration=%.2fs peak_rms=%d peak_p=%.2f "
            "speech_chunks=%d trimmed=%.2fs",
            len(frames), full_duration, peak_rms, peak_p,
            speech_chunks, trimmed_duration,
        )
        return trimmed

    if q is not None:
        # Drain leftover wake-word frames first so the recording doesn't
        # start with stale audio.
        while not q.empty():
            try:
                q.get_nowait()
            except queue.Empty:
                break
        return _loop(q)

    with open_input_stream() as new_q:
        return _loop(new_q)
