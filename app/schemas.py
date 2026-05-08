from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Literal

from pydantic import BaseModel, Field

ToolName = Literal[
    "local.answer",
    "home.tv",
    "home.vacuum",
    "music",
    "weather.live",
    "internet.search",
    "system.power",
    "memory",
    "none",
]
ExternalPayload = Literal["none", "text_query_only", "location_only"]
PrivacyRisk = Literal["low", "medium", "high"]


class PlannerOutput(BaseModel):
    tool: ToolName
    action: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    requires_internet: bool = False
    external_payload: ExternalPayload = "none"
    privacy_risk: PrivacyRisk = "low"
    reason: str = ""
    final_response_required: bool = True
    # When tool == "local.answer" the planner is asked to fill this with the
    # spoken reply directly. Lets the orchestrator skip the second LLM call
    # for general-knowledge questions. Empty / null for every other tool.
    local_answer: str | None = None


class ToolResult(BaseModel):
    ok: bool
    data: dict[str, Any] = Field(default_factory=dict)
    speak: str | None = None  # short human-readable summary if the tool produced one
    error: str | None = None


class LedgerEntry(BaseModel):
    assistant: str = "Trusty"
    hardware: str = "Raspberry Pi 5 plus AI HAT"
    runtime: str = "llama.cpp"
    model: str = "Gemma 4 E2B IT GGUF"
    mode: Literal["online", "offline"]
    user_text: str
    tool: ToolName
    action: str = ""
    internet_used: bool = False
    external_payload: ExternalPayload = "none"
    audio_left_device: bool = False
    home_logs_left_device: bool = False
    blocked: bool = False
    block_reason: str | None = None
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )


EyesStateName = Literal[
    "idle", "listening", "thinking", "speaking",
    "searching", "weather", "tv", "music", "vacuum",
    "offline", "blocked", "error",
]
EyesUiMode = Literal["eyes", "album"]


class EyesState(BaseModel):
    state: EyesStateName = "idle"
    caption: str = "Hey Trusty"
    # Visual mode the Eyes UI is in. Flipped by voice commands ("trusty
    # show album", "trusty go back to eyes") and the toggle button in the
    # Eyes header. Persistent across state changes — only mode-switching
    # commands change it.
    mode: EyesUiMode = "eyes"
    privacy: dict[str, Any] = Field(
        default_factory=lambda: {
            "audio_left_device": False,
            "internet_used": False,
            "external_payload": "none",
        }
    )
    # Carries the user's transcribed text when a turn starts. The Mac wrapper
    # UI shows it briefly above the eyes; Pi clients ignore unknown fields.
    user_text: str | None = None
    # True between the first VAD-detected speech frame and the moment the
    # full transcript is broadcast. Mac wrapper shows a "Listening..." bubble
    # so the user gets feedback while STT is still running.
    user_speaking: bool = False


class RuntimeConfig(BaseModel):
    """Mutable runtime knobs exposed to the admin panel. Persisted only in
    process memory — restart resets to .env defaults.

    Note: audio-upload and home-log-upload are intentionally absent. They are
    permanently false — enforced by `privacy_validator.py` and a 403 in
    `app.main.patch_runtime`. They are not toggles, period."""

    mode: Literal["online", "offline"] = "online"
    paused: bool = False
    wakeword_threshold: float = 0.5


class ServiceHealth(BaseModel):
    name: str
    url: str
    ok: bool
    latency_ms: float | None = None
    error: str | None = None


class ChatRequest(BaseModel):
    text: str
    speak: bool = False  # if true, also synthesise audio to local speaker


class ChatResponse(BaseModel):
    plan: PlannerOutput
    tool_result: ToolResult
    final_response: str
    ledger: LedgerEntry
