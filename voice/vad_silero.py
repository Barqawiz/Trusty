"""Silero VAD — neural speech / non-speech classifier on 16 kHz audio.

Why this exists
---------------
RMS thresholds are amplitude detectors, not speech detectors. They fire on
fan noise, TTS bleed-back, clapping, door slams — anything loud — and miss
quiet speech. The recorder's `speech_seen` flag was being driven off RMS
and routinely flipped True on non-speech. Result: 5 s of mostly-silence
handed to Moonshine, which returns ''.

Silero VAD (v5 ONNX) takes a 32 ms / 512-sample window of int16 audio and
returns P(speech). It runs in a few hundred microseconds on Pi 5 CPU, fits
in 2 MB, and is air-gappable (just an ONNX file).

API
---
    vad = SileroVAD(model_path)
    vad.reset()                       # clear streaming state
    p = vad.feed_frame(int16_frame)   # max P(speech) over chunks in frame
    probs = vad.score_buffer(int16_buffer)  # one-shot per-chunk probs

The streaming `feed_frame` accumulates a carryover so chunking is gap-free
across calls. `score_buffer` is stateless (used for trimming the final
buffer to its speech region).
"""
from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

# Silero v5 at 16 kHz expects 512 samples per chunk = 32 ms, prefixed
# with a 64-sample context tail from the previous chunk (zeros for the
# first chunk). The ONNX model's `input` is therefore 576 samples wide.
# Skipping the context prefix gives garbage probabilities — looks like
# the model was trained with this overlap.
CHUNK_SAMPLES = 512
CTX_SAMPLES = 64
SAMPLE_RATE = 16_000


def _model_path() -> Path:
    project_root = Path(__file__).resolve().parents[1]
    return project_root / "models" / "vad" / "silero_vad.onnx"


def have_vad() -> bool:
    """True iff the ONNX file exists. Importing onnxruntime is deferred."""
    return _model_path().is_file()


@lru_cache(maxsize=1)
def _session():
    import onnxruntime

    p = _model_path()
    if not p.is_file():
        raise FileNotFoundError(
            f"Silero VAD model missing at {p}. Run "
            f"`bash scripts/download_models.sh` to fetch it."
        )
    so = onnxruntime.SessionOptions()
    # VAD is single-frame work — extra threads cost more than they buy.
    so.intra_op_num_threads = 1
    so.inter_op_num_threads = 1
    log.info("Loading Silero VAD: %s", p)
    return onnxruntime.InferenceSession(
        str(p), sess_options=so, providers=["CPUExecutionProvider"],
    )


class SileroVAD:
    """Streaming VAD with carryover. One instance per recording session."""

    def __init__(self) -> None:
        self._session = _session()
        self._sr = np.array(SAMPLE_RATE, dtype=np.int64)
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        # 64-sample tail from the previous chunk, prepended to the next.
        # First chunk: zeros.
        self._context = np.zeros(CTX_SAMPLES, dtype=np.float32)
        # Float32 carryover for samples that don't yet make a full chunk.
        self._carry = np.zeros(0, dtype=np.float32)

    def reset(self) -> None:
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CTX_SAMPLES, dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)

    def _run(self, chunk_f32: np.ndarray) -> float:
        x = np.concatenate([self._context, chunk_f32]).reshape(1, -1)
        out, state = self._session.run(
            None, {"input": x, "state": self._state, "sr": self._sr}
        )
        self._state = state
        # Tail of THIS chunk (not the prepended context) becomes the next
        # chunk's context.
        self._context = chunk_f32[-CTX_SAMPLES:].copy()
        return float(out[0, 0])

    def feed_frame(self, frame_int16: np.ndarray) -> float:
        """Push the next captured frame, return max P(speech) over all
        complete 32 ms chunks produced. Carryover keeps chunking
        contiguous across calls. Returns 0.0 if no chunk completed yet
        (only possible on the very first sub-32 ms call)."""
        if frame_int16.size == 0:
            return 0.0
        f32 = frame_int16.astype(np.float32) / 32768.0
        buf = np.concatenate([self._carry, f32]) if self._carry.size else f32
        n_full = buf.size // CHUNK_SAMPLES
        if n_full == 0:
            self._carry = buf
            return 0.0
        peak = 0.0
        for i in range(n_full):
            start = i * CHUNK_SAMPLES
            peak = max(peak, self._run(buf[start:start + CHUNK_SAMPLES]))
        self._carry = buf[n_full * CHUNK_SAMPLES:].copy()
        return peak

    def score_buffer(self, buffer_int16: np.ndarray) -> np.ndarray:
        """One-shot mode: classify a complete recorded buffer. Resets
        state internally so the result is independent of streaming
        history. Returns a 1-D float32 array of per-chunk probabilities
        (one entry per 32 ms chunk, last partial chunk dropped)."""
        if buffer_int16.size < CHUNK_SAMPLES:
            return np.zeros(0, dtype=np.float32)
        # Use a fresh state — do NOT contaminate the streaming session.
        saved_state = self._state
        saved_context = self._context
        saved_carry = self._carry
        self._state = np.zeros((2, 1, 128), dtype=np.float32)
        self._context = np.zeros(CTX_SAMPLES, dtype=np.float32)
        self._carry = np.zeros(0, dtype=np.float32)
        try:
            f32 = buffer_int16.astype(np.float32) / 32768.0
            n = f32.size // CHUNK_SAMPLES
            probs = np.empty(n, dtype=np.float32)
            for i in range(n):
                start = i * CHUNK_SAMPLES
                probs[i] = self._run(f32[start:start + CHUNK_SAMPLES])
            return probs
        finally:
            self._state = saved_state
            self._context = saved_context
            self._carry = saved_carry


def trim_to_speech(
    audio_int16: np.ndarray,
    probs: np.ndarray,
    *,
    threshold: float = 0.5,
    pad_ms: int = 240,
) -> np.ndarray:
    """Return the slice of `audio_int16` covering the first → last chunk
    above `threshold`, padded by `pad_ms` on each side. Returns an empty
    array if no chunk crosses the threshold (caller treats as silence)."""
    if probs.size == 0:
        return np.zeros(0, dtype=np.int16)
    speech = np.where(probs >= threshold)[0]
    if speech.size == 0:
        return np.zeros(0, dtype=np.int16)
    pad_samples = int(pad_ms * SAMPLE_RATE / 1000)
    start = max(0, speech[0] * CHUNK_SAMPLES - pad_samples)
    end = min(audio_int16.size, (speech[-1] + 1) * CHUNK_SAMPLES + pad_samples)
    return audio_int16[start:end]
