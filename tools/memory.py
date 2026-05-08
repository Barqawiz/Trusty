"""Tool: local memory store.

Lets the planner write three classes of facts the user explicitly states
about themselves:

  - `set_location` — default city for weather queries.
  - `set_name`     — what the assistant should call them.
  - `clear`        — wipe everything.

Privacy: writes go to `data/memory.json` on this device. No network,
no tool dispatch beyond the local file. The planner's `arguments.value`
is the raw user phrase; we sanitize lightly (Title-case, trim trailing
punctuation) before saving.
"""
from __future__ import annotations

import logging

from app.memory import Memory
from app.schemas import PlannerOutput, ToolResult
from app.tool_registry import ToolRegistry

log = logging.getLogger(__name__)


_NAME_STOPWORDS = frozenset({
    "going", "doing", "trying", "thinking", "leaving", "working",
    "talking", "telling", "asking",
    "fine", "ok", "okay", "good", "great", "tired", "busy",
    "ready", "back", "here", "there", "home", "late", "early",
    "sorry", "sure", "right", "wrong", "yes", "no", "yeah", "nope",
})


def _sanitise(value: str) -> str:
    return (value or "").strip(" .,?!\"'").title()


def register(registry: ToolRegistry, memory: Memory) -> None:
    async def handler(plan: PlannerOutput) -> ToolResult:
        action = plan.action
        args = plan.arguments or {}
        value = _sanitise(str(args.get("value", "")))

        if action == "set_location":
            if not value:
                return ToolResult(
                    ok=False, error="missing value",
                    speak="Which city should I remember?",
                )
            memory.set("default_location", value)
            # Also overwrite `recents.location` so the next planner turn
            # doesn't cling to the previous weather city via recent-context
            # ("the user already asked about Tokyo" gets stale fast when
            # the user has explicitly moved). Without this, follow-up
            # weather queries with no location keep returning the old city.
            memory.remember_recent("location", value)
            log.info("memory.set_location -> %s", value)
            return ToolResult(
                ok=True,
                data={"default_location": value},
                speak=f"Got it, I'll remember your location as {value}.",
            )

        if action == "set_name":
            if not value or value.lower() in _NAME_STOPWORDS:
                return ToolResult(
                    ok=False, error="bad name",
                    speak="That doesn't sound like a name I should save.",
                )
            memory.set("user_name", value)
            log.info("memory.set_name -> %s", value)
            return ToolResult(
                ok=True,
                data={"user_name": value},
                speak=f"Got it, I'll call you {value}.",
            )

        if action == "clear":
            memory.clear()
            log.info("memory.clear")
            return ToolResult(
                ok=True,
                data={},
                speak="Okay, I cleared my memory.",
            )

        return ToolResult(
            ok=False, error=f"Unknown memory action: {action}",
        )

    registry.register("memory", handler)
