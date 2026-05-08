from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _expand(path: str | None) -> str | None:
    if path is None:
        return None
    return os.path.expandvars(os.path.expanduser(path))


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    TRUSTY_HOME: str = "."
    TRUSTY_HOST: str = "0.0.0.0"
    TRUSTY_PORT: int = 8090
    TRUSTY_MODE: Literal["online", "offline"] = "online"

    LLAMA_CPP_DIR: str = ""
    LLAMA_HOST: str = "127.0.0.1"
    LLAMA_PORT: int = 8080
    LLAMA_BASE_URL: str = "http://127.0.0.1:8080/v1"
    GEMMA_MODEL_PATH: str = ""

    HF_TOKEN: str = ""

    WAKEWORD_NAME: str = "hey_trusty"
    WAKEWORD_MODEL_NAME: str = "hey_jarvis"
    WAKEWORD_MODEL_PATH: str = ""
    WAKEWORD_THRESHOLD: float = 0.5
    # ON (default): openWakeWord gates STT. OFF: continuous STT + transcript-scan
    # for "trusty"/"wake up"; openWakeWord not loaded. Sleep/wake/music unchanged.
    WAKEWORD_MODE: str = "ON"

    WHISPER_CPP_DIR: str = ""
    WHISPER_BIN: str = ""
    WHISPER_MODEL_PATH: str = ""

    KOKORO_MODEL_PATH: str = ""
    KOKORO_VOICES_PATH: str = ""
    KOKORO_VOICE: str = "af_heart"
    KOKORO_LANG: str = "en-us"
    KOKORO_SPEED: float = 1.0

    HA_URL: str = "http://localhost:8123"
    HA_TOKEN: str = ""
    LG_TV_ENTITY_ID: str = "media_player.lg_webos_tv"
    VACUUM_ENTITY_ID: str = "vacuum.s6_pure"

    MUSIC_ASSISTANT_URL: str = "http://localhost:8095"
    MUSIC_PLAYER_ID: str = "media_player.lg_webos_tv"
    # API token from MA UI → Settings → API Tokens. When set, Trusty plays
    # directly via Music Assistant's WebSocket (no Home Assistant needed).
    # When empty, falls back to the HA service `music_assistant.play_media`.
    MUSIC_ASSISTANT_TOKEN: str = ""
    # Optional override: which MA player to send audio to. If empty, the
    # tool picks the first available MA player it finds.
    MUSIC_ASSISTANT_PLAYER_ID: str = ""
    LOCAL_MUSIC_DIR: str = ""

    SEARXNG_URL: str = "http://localhost:8088"

    EYES_ENABLED: bool = True
    EYES_PORT: int = 8091

    def model_post_init(self, _ctx) -> None:  # noqa: D401
        # Expand ${TRUSTY_HOME} and ~ in path-like fields.
        for field in (
            "LLAMA_CPP_DIR", "GEMMA_MODEL_PATH",
            "WAKEWORD_MODEL_PATH",
            "WHISPER_CPP_DIR", "WHISPER_BIN", "WHISPER_MODEL_PATH",
            "KOKORO_MODEL_PATH", "KOKORO_VOICES_PATH",
            "LOCAL_MUSIC_DIR",
        ):
            value = getattr(self, field)
            if isinstance(value, str) and value:
                # Two-pass expansion so ${TRUSTY_HOME} resolves even if it
                # was loaded before TRUSTY_HOME itself.
                os.environ.setdefault("TRUSTY_HOME", self.TRUSTY_HOME)
                expanded = _expand(value)
                object.__setattr__(self, field, expanded)

    @property
    def project_root(self) -> Path:
        return Path(self.TRUSTY_HOME).resolve()

    @property
    def wakeword_enabled(self) -> bool:
        """True when openWakeWord drives wake detection. Only literal "OFF"
        (case-insensitive) disables it; typos / empty / missing fail safe to ON."""
        return str(self.WAKEWORD_MODE).strip().upper() != "OFF"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
