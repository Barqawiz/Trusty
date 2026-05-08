"""Voice-driven sleep / wake / UI mode controls.

Reuses the existing `RuntimeState.paused` flag (the one wired to the
admin panel's *Pause Trusty* toggle). When `paused=True`, the orchestrator
short-circuits every `/chat` with "I'm asleep" — UNLESS the message
matches the wake-bypass pattern in orchestrator._handle_text, which
routes through here to flip `paused` back to False.

Also handles `mode_album` / `mode_eyes` which flip the Eyes UI between
the slideshow and the eyes face. The mode itself lives on the
orchestrator and is broadcast via the StateBus.

Privacy: nothing leaves the device. Runtime + UI flags are process-local
state. No audio, no network, no payload of any kind.
"""
from __future__ import annotations

import logging
import random

from app.runtime import RuntimeState
from app.schemas import PlannerOutput, ToolResult
from app.tool_registry import ToolRegistry

log = logging.getLogger(__name__)

# Picked at random per call so the assistant doesn't sound like a parrot.
_SLEEP_LINES = (
    "Going to sleep. Say trusty wake up when you need me.",
    "Resting now. Call trusty wake up to bring me back.",
    "Going quiet. Trusty wake up will rouse me.",
    "Lights out for me. Trusty wake up when you need me.",
    "Catching some shut-eye. Say trusty wake up to bring me back.",
)
_WAKE_LINES = (
    "I'm awake.",
    "Hey, I'm back.",
    "Back online.",
    "Hi, I'm here.",
    "All ears.",
    "Awake and listening.",
    "Right here, what do you need?",
    "Reporting in.",
    "Yep, I'm here.",
    "Good to be back.",
)


def register(registry: ToolRegistry, runtime: RuntimeState) -> None:
    async def handler(plan: PlannerOutput) -> ToolResult:
        action = plan.action

        if action == "sleep":
            await runtime.update(paused=True)
            log.info("Trusty asleep (paused=True via voice command)")
            return ToolResult(
                ok=True,
                data={"paused": True},
                speak=random.choice(_SLEEP_LINES),
            )

        if action == "wake":
            await runtime.update(paused=False)
            log.info("Trusty awake (paused=False via voice command)")
            return ToolResult(
                ok=True,
                data={"paused": False},
                speak=random.choice(_WAKE_LINES),
            )

        if action == "mode_album":
            log.info("Eyes UI -> album mode (voice command)")
            return ToolResult(
                ok=True,
                data={"ui_mode": "album"},
                speak="Showing the photo album.",
            )

        if action == "mode_eyes":
            log.info("Eyes UI -> eyes mode (voice command)")
            return ToolResult(
                ok=True,
                data={"ui_mode": "eyes"},
                speak="Back to eyes.",
            )

        return ToolResult(
            ok=False, error=f"Unknown system.power action: {action}",
        )

    registry.register("system.power", handler)
