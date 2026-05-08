"""Home Assistant REST client. Used by both home.tv (LG TV) and any future
home control. Reads HA_URL and HA_TOKEN from settings. No payload leaves the
LAN — Home Assistant handles devices locally.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

from app.schemas import PlannerOutput, ToolResult
from app.settings import Settings
from app.tool_registry import ToolRegistry

log = logging.getLogger(__name__)


class HomeAssistantClient:
    def __init__(self, base_url: str, token: str, timeout: float = 8.0) -> None:
        self.base_url = base_url.rstrip("/")
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = timeout

    async def call_service(
        self, domain: str, service: str, data: dict[str, Any]
    ) -> dict[str, Any]:
        url = f"{self.base_url}/api/services/{domain}/{service}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.post(url, json=data, headers=self._headers)
            r.raise_for_status()
            return {"ok": True, "raw": r.json() if r.content else None}

    async def get_state(self, entity_id: str) -> dict[str, Any]:
        url = f"{self.base_url}/api/states/{entity_id}"
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            r = await client.get(url, headers=self._headers)
            r.raise_for_status()
            return r.json()


def register(registry: ToolRegistry, settings: Settings) -> None:
    """Register a generic 'home.control' tool entry if you add it later."""
    # No `home.control` tool in the MVP; this module is imported by lg_tv.
    return
