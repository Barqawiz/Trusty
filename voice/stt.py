"""STT backend dispatcher.

Reads `STT_BACKEND` from the environment and routes to the right module.
No fallback — if the chosen backend can't load, the call raises.
Default when unset: `whisper` (the original whisper.cpp path) so existing
deployments keep working.

Allowed values:
    STT_BACKEND=whisper     (default) — whisper.cpp via subprocess
    STT_BACKEND=moonshine             — Moonshine ONNX, faster on Pi

Usage from voice/loop.py:

    from .stt import transcribe_pcm16, have_stt
"""
from __future__ import annotations

import os
from typing import Any

import numpy as np


def _backend() -> str:
    raw = os.environ.get("STT_BACKEND", "whisper").strip().lower()
    if raw not in ("whisper", "moonshine"):
        raise ValueError(
            f"Unsupported STT_BACKEND={raw!r}. Allowed: whisper, moonshine."
        )
    return raw


def have_stt(**kwargs: Any) -> bool:
    """True if the configured backend can actually run.

    For whisper, kwargs must include `whisper_bin` and `model_path`
    (matching the existing API). For moonshine, kwargs are ignored.
    """
    if _backend() == "moonshine":
        from .stt_moonshine import have_moonshine
        return have_moonshine()
    from .stt_whispercpp import have_whisper
    return have_whisper(**kwargs)


def transcribe_pcm16(
    audio: np.ndarray,
    sample_rate: int,
    **kwargs: Any,
) -> str:
    """Transcribe 16 kHz int16 mono audio. The kwargs are
    backend-specific; whisper expects `whisper_bin` + `model_path`,
    moonshine ignores them."""
    if _backend() == "moonshine":
        from .stt_moonshine import transcribe_pcm16 as _t
        return _t(audio, sample_rate)
    from .stt_whispercpp import transcribe_pcm16 as _t
    return _t(audio, sample_rate, **kwargs)
