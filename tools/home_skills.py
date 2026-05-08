"""Generic Home Assistant skill adapter.

Reads `config/home_skills.yaml` and registers one handler per declared
skill under `home.<skill>`. Each action either calls an HA service or
fetches entity state — all via the existing HomeAssistantClient. Adding a
new HA integration is a YAML edit + a matching `tools.yaml` entry; no
Python changes required.
"""
from __future__ import annotations

import logging
from typing import Any

import yaml

from app.schemas import PlannerOutput, ToolResult
from app.settings import Settings
from app.tool_registry import ToolRegistry

from .home_assistant import HomeAssistantClient

log = logging.getLogger(__name__)


def _safe_format(template: str, values: dict[str, Any]) -> str:
    try:
        return template.format(**values)
    except (KeyError, IndexError, ValueError):
        return template


def _build_handler(
    settings: Settings,
    skill_name: str,
    skill_spec: dict[str, Any],
):
    entity_env = skill_spec.get("entity_id_env")
    if not entity_env:
        raise ValueError(f"home skill '{skill_name}' missing entity_id_env")
    actions: dict[str, dict[str, Any]] = skill_spec.get("actions") or {}

    def _client() -> HomeAssistantClient:
        return HomeAssistantClient(settings.HA_URL, settings.HA_TOKEN)

    def _entity_id() -> str:
        value = getattr(settings, entity_env, "")
        if not value:
            raise RuntimeError(
                f"home skill '{skill_name}' needs Settings.{entity_env} set"
            )
        return value

    async def handler(plan: PlannerOutput) -> ToolResult:
        action = plan.action
        spec = actions.get(action)
        if spec is None:
            return ToolResult(
                ok=False,
                error=f"Unknown {skill_name} action: {action}",
            )
        args = plan.arguments or {}
        ha = _client()
        try:
            if spec.get("kind") == "state":
                state = await ha.get_state(_entity_id())
                template = spec.get("speak_template") or "{state}"
                values = {"state": state.get("state", "unknown")}
                attrs = state.get("attributes") or {}
                if isinstance(attrs, dict):
                    values.update(
                        {k: v for k, v in attrs.items() if k not in values}
                    )
                return ToolResult(
                    ok=True,
                    data=state,
                    speak=_safe_format(template, values),
                )

            domain = spec.get("domain")
            service = spec.get("service")
            if not domain or not service:
                return ToolResult(
                    ok=False,
                    error=f"{skill_name}.{action} is misconfigured",
                )
            payload: dict[str, Any] = {"entity_id": _entity_id()}
            argmap: dict[str, str] = spec.get("argmap") or {}
            for plan_key, ha_key in argmap.items():
                if plan_key in args and args[plan_key] not in (None, ""):
                    payload[ha_key] = args[plan_key]
            await ha.call_service(domain, service, payload)
            speak = _safe_format(spec.get("speak") or "Done.", args)
            return ToolResult(ok=True, speak=speak)
        except Exception as e:
            log.exception("%s.%s failed", skill_name, action)
            return ToolResult(ok=False, error=str(e))

    return handler


def register(registry: ToolRegistry, settings: Settings) -> None:
    path = settings.project_root / "config" / "home_skills.yaml"
    if not path.exists():
        log.info("home_skills.yaml not found at %s; skipping", path)
        return
    data = yaml.safe_load(path.read_text()) or {}
    skills = data.get("skills") or {}
    for skill_name, spec in skills.items():
        tool_name = spec.get("tool") or f"home.{skill_name}"
        if not registry.has(tool_name):
            log.warning(
                "home skill '%s' targets unknown tool '%s' (not in tools.yaml); "
                "skipping",
                skill_name,
                tool_name,
            )
            continue
        handler = _build_handler(settings, skill_name, spec)
        registry.register(tool_name, handler)
        log.info("registered home skill: %s -> %s", skill_name, tool_name)
