"""LG webOS TV control via Home Assistant. Maps planner actions to HA services.

Mappings (HA + LG webOS integration):
- turn_on / turn_off            -> media_player.turn_on / turn_off
- open_app / select_source      -> media_player.select_source (source = app name)
- volume_up / volume_down       -> media_player.volume_up / volume_down
- mute                          -> media_player.volume_mute (is_volume_muted=true)
- show_notification             -> notify.<lg_tv_notify_service>  (best-effort)
- get_state                     -> /api/states/{entity_id}
"""
from __future__ import annotations

import logging
from typing import Any

from app.schemas import PlannerOutput, ToolResult
from app.settings import Settings
from app.tool_registry import ToolRegistry

from .home_assistant import HomeAssistantClient

log = logging.getLogger(__name__)


def register(registry: ToolRegistry, settings: Settings) -> None:
    entity_id = settings.LG_TV_ENTITY_ID

    def _client() -> HomeAssistantClient:
        return HomeAssistantClient(settings.HA_URL, settings.HA_TOKEN)

    async def handler(plan: PlannerOutput) -> ToolResult:
        action = plan.action
        args = plan.arguments or {}
        try:
            ha = _client()
            if action == "turn_on":
                await ha.call_service(
                    "media_player", "turn_on", {"entity_id": entity_id}
                )
                return ToolResult(ok=True, speak="TV is turning on.")
            if action == "turn_off":
                await ha.call_service(
                    "media_player", "turn_off", {"entity_id": entity_id}
                )
                return ToolResult(ok=True, speak="TV is off.")
            if action in ("open_app", "select_source"):
                source = args.get("app") or args.get("source") or ""
                if not source:
                    return ToolResult(ok=False, error="Missing app/source argument.")
                await ha.call_service(
                    "media_player",
                    "select_source",
                    {"entity_id": entity_id, "source": source},
                )
                return ToolResult(ok=True, speak=f"Opening {source} on the TV.")
            if action == "volume_up":
                await ha.call_service(
                    "media_player", "volume_up", {"entity_id": entity_id}
                )
                return ToolResult(ok=True, speak="Volume up.")
            if action == "volume_down":
                await ha.call_service(
                    "media_player", "volume_down", {"entity_id": entity_id}
                )
                return ToolResult(ok=True, speak="Volume down.")
            if action == "mute":
                muted = bool(args.get("muted", True))
                await ha.call_service(
                    "media_player",
                    "volume_mute",
                    {"entity_id": entity_id, "is_volume_muted": muted},
                )
                return ToolResult(
                    ok=True, speak=("TV muted." if muted else "TV unmuted.")
                )
            if action == "show_notification":
                message = args.get("message") or "Hi from Trusty."
                # Best-effort: rely on the user-configured notify service for the TV.
                service = args.get("service") or "notify.lg_webos_tv"
                domain, sname = service.split(".", 1) if "." in service else ("notify", service)
                await ha.call_service(domain, sname, {"message": message})
                return ToolResult(ok=True, speak="I sent the notification to the TV.")
            if action == "get_state":
                state = await ha.get_state(entity_id)
                return ToolResult(ok=True, data=state, speak=str(state.get("state")))
            return ToolResult(ok=False, error=f"Unknown action: {action}")
        except Exception as e:
            log.exception("LG TV action failed")
            return ToolResult(ok=False, error=str(e))

    registry.register("home.tv", handler)
