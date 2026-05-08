"""Speech-to-text via whisper.cpp's whisper-cli (subprocess).

We picked the subprocess approach because the user already built whisper-cli
in `external/whisper.cpp/build/bin/whisper-cli`. No extra Python bindings.
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

log = logging.getLogger(__name__)


def transcribe_pcm16(
    audio: np.ndarray,
    sample_rate: int,
    whisper_bin: str,
    model_path: str,
    language: str = "en",
    threads: int | None = None,
) -> str:
    if audio.size == 0:
        return ""
    if threads is None:
        threads = max(1, (os.cpu_count() or 4) - 1)

    with tempfile.TemporaryDirectory() as td:
        wav_path = Path(td) / "in.wav"
        sf.write(str(wav_path), audio, sample_rate, subtype="PCM_16")
        out_prefix = Path(td) / "out"

        cmd = [
            whisper_bin,
            "-m", model_path,
            "-f", str(wav_path),
            "-l", language,
            "-t", str(threads),
            "--no-prints",
            "--no-timestamps",
            "-otxt",
            "-of", str(out_prefix),
        ]
        log.debug("whisper cmd: %s", cmd)
        result = subprocess.run(
            cmd, capture_output=True, text=True, check=False
        )
        if result.returncode != 0:
            log.warning("whisper-cli rc=%d stderr=%s", result.returncode, result.stderr.strip())
            return ""
        txt_path = out_prefix.with_suffix(".txt")
        if not txt_path.exists():
            return result.stdout.strip()
        return txt_path.read_text(encoding="utf-8").strip()


def have_whisper(whisper_bin: str, model_path: str) -> bool:
    return Path(whisper_bin).is_file() and Path(model_path).is_file()
