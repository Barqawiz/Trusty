"""Tool: answer general knowledge from local Gemma. No tool data; the
finalize step (in orchestrator) drafts the spoken answer. We just signal
that the call succeeded with empty payload."""
from __future__ import annotations

from app.model_client import LlamaClient
from app.schemas import PlannerOutput, ToolResult
from app.tool_registry import ToolRegistry


def register(registry: ToolRegistry, client: LlamaClient) -> None:
    async def handler(plan: PlannerOutput) -> ToolResult:
        return ToolResult(ok=True, data={"question": plan.arguments.get("question", "")})

    registry.register("local.answer", handler)
