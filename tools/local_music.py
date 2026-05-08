"""Offline music — plays from LOCAL_MUSIC_DIR using a system player.
This is a fallback when Music Assistant is unavailable. It runs locally; no
audio leaves the device.
"""
from __future__ import annotations

import logging
import platform
import random
import shutil
import subprocess
import threading
from pathlib import Path

log = logging.getLogger(__name__)

_proc_lock = threading.Lock()
_proc: subprocess.Popen | None = None


def _player_cmd(file_path: str) -> list[str] | None:
    system = platform.system()
    if system == "Darwin" and shutil.which("afplay"):
        return ["afplay", file_path]
    for candidate in ("mpg123", "mpv", "ffplay", "play"):
        if shutil.which(candidate):
            if candidate == "ffplay":
                return ["ffplay", "-nodisp", "-autoexit", "-loglevel", "error", file_path]
            if candidate == "mpv":
                return ["mpv", "--no-video", "--really-quiet", file_path]
            return [candidate, file_path]
    return None


def _stop_existing() -> None:
    global _proc
    with _proc_lock:
        if _proc and _proc.poll() is None:
            try:
                _proc.terminate()
            except Exception:
                pass
        _proc = None


def play_random_from_dir(directory: str) -> str | None:
    """Pick a random audio file under `directory` and start playback."""
    p = Path(directory)
    if not p.is_dir():
        log.warning("local music dir missing: %s", directory)
        return None
    candidates = [
        f for f in p.rglob("*")
        if f.is_file() and f.suffix.lower() in {".mp3", ".m4a", ".wav", ".flac", ".ogg"}
    ]
    if not candidates:
        return None
    pick = random.choice(candidates)
    cmd = _player_cmd(str(pick))
    if cmd is None:
        log.warning("no audio player found on system PATH")
        return None
    _stop_existing()
    global _proc
    with _proc_lock:
        _proc = subprocess.Popen(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    return str(pick)


def stop() -> None:
    _stop_existing()
