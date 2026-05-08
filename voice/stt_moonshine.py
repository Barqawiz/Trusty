"""Moonshine ONNX speech-to-text adapter.

Useful Sensors' Moonshine is a Whisper-class STT model rebuilt for edge
voice-assistant use: short utterances, low latency, small footprint.
On Pi 5 CPU it transcribes a 5-second clip in ~0.9 s using the `base`
variant (~120 MB ONNX), beating `whisper-base.en` on accent-robustness
while being roughly 3× faster.

Air-gapped by design
--------------------
The model files live inside the project at `models/moonshine/<size>/`
(`encoder_model.onnx` + `decoder_model_merged.onnx`). The tokenizer is
bundled with the `useful-moonshine-onnx` pip package. With `models_dir`
set explicitly, the loader never touches Hugging Face Hub — no GET, no
HEAD, no telemetry. Zero outbound calls at runtime.

To populate the local files initially, run `bash scripts/download_models.sh`
(it pulls from HF once and writes to the project tree, then the runtime
runs offline).

API contract — match `voice.stt_whispercpp` so the dispatcher can swap
between them based on the `STT_BACKEND` env var:

    transcribe_pcm16(audio: np.ndarray, sample_rate: int) -> str
    have_moonshine() -> bool

Audio comes in as 16 kHz int16 mono (the format `voice.audio_capture`
already produces). Moonshine wants float32 mono in [-1.0, +1.0], so we
just normalise before handing it off.
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


def _model_name() -> str:
    """Which Moonshine variant to load. `tiny` (~50 MB, fastest, slightly
    worse WER) or `base` (~120 MB, better accent handling). Defaults to
    `base` — the right answer for a voice assistant in most setups."""
    raw = os.environ.get("MOONSHINE_MODEL", "base").strip().lower()
    if "/" in raw:
        raw = raw.split("/")[-1]
    if raw not in ("tiny", "base"):
        raise ValueError(
            f"Unsupported MOONSHINE_MODEL={raw!r}. Allowed: tiny, base."
        )
    return raw


def _models_dir() -> Path:
    """Project-local directory holding `encoder_model.onnx` and
    `decoder_model_merged.onnx`. Looking in `models/moonshine/<size>/`
    relative to the project root."""
    name = _model_name()
    project_root = Path(__file__).resolve().parents[1]
    return project_root / "models" / "moonshine" / name


@lru_cache(maxsize=1)
def _model():
    """Load the Moonshine ONNX model from the project's local files.

    Passes `models_dir` to `MoonshineOnnxModel` so the loader bypasses
    Hugging Face Hub entirely — no HEAD checks, no downloads, no
    telemetry. Just `onnxruntime.InferenceSession` against the files on
    disk.
    """
    from moonshine_onnx import MoonshineOnnxModel

    name = _model_name()
    d = _models_dir()
    enc = d / "encoder_model.onnx"
    dec = d / "decoder_model_merged.onnx"
    if not enc.is_file() or not dec.is_file():
        raise FileNotFoundError(
            f"Moonshine ONNX files missing under {d}. Run "
            f"`bash scripts/download_models.sh` to populate them."
        )
    log.info("Loading Moonshine model: %s (offline, from %s)", name, d)
    return MoonshineOnnxModel(models_dir=str(d), model_name=name)


def have_moonshine() -> bool:
    """True if the package is importable AND the local model files exist.
    Reports False (without raising) when either check fails so the
    dispatcher can surface a clean error before the first transcription."""
    try:
        import moonshine_onnx  # noqa: F401
        d = _models_dir()
        ok = (d / "encoder_model.onnx").is_file() and (
            d / "decoder_model_merged.onnx"
        ).is_file()
        if not ok:
            log.warning(
                "moonshine model files missing at %s — run download_models.sh",
                d,
            )
        return ok
    except Exception as e:
        log.warning("moonshine not available: %s", e)
        return False


def transcribe_pcm16(audio: np.ndarray, sample_rate: int) -> str:
    """Transcribe 16 kHz mono int16 audio to text. Fully offline.

    Caller is expected to hand us a speech-tight buffer (Silero VAD has
    already trimmed leading/trailing non-speech). We peak-normalise to
    ~0.7 float so far-mic / quiet-voice inputs land in the loudness range
    Moonshine was trained on, then convert to float32 in [-1, 1].
    """
    if audio is None or audio.size == 0:
        return ""
    if sample_rate != 16_000:
        from math import gcd
        from scipy.signal import resample_poly

        g = gcd(sample_rate, 16_000)
        audio = resample_poly(audio.astype(np.float32), 16_000 // g, sample_rate // g)
        audio = np.clip(audio, -32768, 32767).astype(np.int16)

    samples = audio.astype(np.float32) / 32768.0
    # Peak-normalise the trimmed speech segment. Skip if already loud (avoid
    # touching clipped audio) or if the buffer is effectively silent (the
    # VAD shouldn't have handed us this, but defend against it).
    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if 0.02 < peak < 0.7:
        samples = samples * (0.7 / peak)
    samples = samples.reshape(1, -1)

    model = _model()
    tokens = model.generate(samples)
    from moonshine_onnx import load_tokenizer

    decoded = load_tokenizer().decode_batch(tokens)
    if not decoded:
        return ""
    text = decoded[0] if isinstance(decoded, list) else str(decoded)
    return text.strip()
