"""Loads tools.yaml and routes (tool, action) -> Python adapter callable."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Awaitable, Callable

import yaml

from .schemas import PlannerOutput, ToolResult

ToolHandler = Callable[[PlannerOutput], Awaitable[ToolResult]]


class ToolRegistry:
    def __init__(self, tools: dict[str, dict[str, Any]]) -> None:
        self._tools = tools
        self._handlers: dict[str, ToolHandler] = {}

    @property
    def tools(self) -> dict[str, dict[str, Any]]:
        return self._tools

    def register(self, name: str, handler: ToolHandler) -> None:
        self._handlers[name] = handler

    def has(self, name: str) -> bool:
        return name in self._tools

    async def dispatch(self, plan: PlannerOutput) -> ToolResult:
        if plan.tool == "none":
            # Used for clarification or blocked requests.
            return ToolResult(ok=True, data={"action": plan.action})
        handler = self._handlers.get(plan.tool)
        if handler is None:
            return ToolResult(ok=False, error=f"No handler for tool {plan.tool}")
        return await handler(plan)

    def tools_json(self) -> str:
        # Compact JSON for prompt embedding; only fields the planner needs.
        compact = {
            name: {
                "description": spec.get("description", ""),
                "internet": spec.get("internet", False),
                "actions": spec.get("actions", []),
                "allowed_external_payload": spec.get(
                    "allowed_external_payload", []
                ),
            }
            for name, spec in self._tools.items()
        }
        return json.dumps(compact)

    @classmethod
    def load(cls, project_root: Path) -> "ToolRegistry":
        path = project_root / "config" / "tools.yaml"
        data = yaml.safe_load(path.read_text())
        tools = data.get("tools", {})
        return cls(tools=tools)
