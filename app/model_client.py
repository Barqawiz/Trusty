"""Thin client over llama.cpp's OpenAI-compatible /v1/chat/completions."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

import httpx

from .schemas import LedgerEntry, PlannerOutput, ToolResult

log = logging.getLogger(__name__)

# Gemma frequently wraps structured output in ```json ... ``` despite prompt
# instructions. Strip the fence (with or without language tag) before parsing.
_CODE_FENCE_RE = re.compile(
    r"^\s*```(?:json|JSON)?\s*\n?(.*?)\n?```\s*$",
    re.DOTALL,
)


def _strip_code_fence(content: str) -> str:
    """Strip ```json fences (symmetric, leading-only, or trailing-only)
    and close up to two unbalanced braces so json.loads can recover."""
    s = content.strip()
    m = _CODE_FENCE_RE.match(s)
    if m:
        s = m.group(1).strip()
    else:
        if s.endswith("```"):
            s = s[:-3].rstrip()
        if s.startswith("```"):
            nl = s.find("\n")
            s = s[nl + 1:] if nl != -1 else s[3:]
            s = s.strip()
    if s.startswith("{"):
        open_n = s.count("{")
        close_n = s.count("}")
        if open_n > close_n and (open_n - close_n) <= 2:
            s = s + ("}" * (open_n - close_n))
    return s


class LlamaClient:
    def __init__(
        self,
        base_url: str,
        planner_system_template: str,
        final_answer_template: str,
        timeout: float = 60.0,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.planner_template = planner_system_template
        self.final_answer_template = final_answer_template
        self._client = httpx.AsyncClient(timeout=timeout)

    async def aclose(self) -> None:
        await self._client.aclose()

    @classmethod
    def from_files(
        cls,
        base_url: str,
        prompts_dir: Path,
        timeout: float = 60.0,
        model_path: str = "",
    ) -> "LlamaClient":
        """Filename containing "trusty" loads the short tuned prompt;
        otherwise fall back to planner_system_long.md.bak (un-tuned)."""
        if "trusty" in str(model_path).lower():
            planner_path = prompts_dir / "planner_system.md"
            kind = "short (tuned)"
        else:
            long_path = prompts_dir / "planner_system_long.md.bak"
            planner_path = long_path if long_path.is_file() else prompts_dir / "planner_system.md"
            kind = "long (un-tuned)" if long_path.is_file() else "short (long fallback missing)"
        log.info("planner prompt: %s -> %s", kind, planner_path.name)
        return cls(
            base_url=base_url,
            planner_system_template=planner_path.read_text(),
            final_answer_template=(prompts_dir / "final_answer_system.md").read_text(),
            timeout=timeout,
        )

    def _render_planner(
        self,
        user_text: str,
        mode: str,
        tools_json: str,
        local_context: str,
        recent_turns: str,
    ) -> str:
        return (
            self.planner_template.replace("{{TOOLS_JSON}}", tools_json)
            .replace("{{MODE}}", mode)
            .replace("{{LOCAL_CONTEXT}}", local_context)
            .replace("{{RECENT_TURNS}}", recent_turns)
            .replace("{{USER_TEXT}}", user_text)
        )

    def _render_final(
        self,
        user_text: str,
        plan: PlannerOutput,
        tool_result: ToolResult,
        ledger: LedgerEntry,
    ) -> str:
        return (
            self.final_answer_template.replace("{{USER_TEXT}}", user_text)
            .replace("{{TOOL_CALL}}", plan.model_dump_json())
            .replace("{{TOOL_RESULT}}", tool_result.model_dump_json())
            .replace("{{PRIVACY_LEDGER}}", ledger.model_dump_json())
        )

    @staticmethod
    def _extract_message_text(message: dict[str, Any]) -> str:
        """Gemma 4 with thinking enabled puts the visible answer in `content`
        and the chain-of-thought in `reasoning_content`. With thinking disabled,
        `content` carries everything. Fall back to reasoning_content when the
        server still returns empty content (older builds, edge cases)."""
        content = (message.get("content") or "").strip()
        if content:
            return content
        return (message.get("reasoning_content") or "").strip()

    async def plan(
        self,
        user_text: str,
        mode: str,
        tools_json: str,
        local_context: str = "",
        recent_turns: str = "",
    ) -> PlannerOutput:
        system = self._render_planner(
            user_text,
            mode,
            tools_json,
            local_context,
            recent_turns,
        )
        body: dict[str, Any] = {
            "model": "gemma",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.0,
            # A 30+ word story + the JSON envelope can run
            # 150-300 tokens; we keep 1024 as a safety ceiling.
            "max_tokens": 1024,
            # response_format=json_object removed: it corrupts fine-tune output.
            # Disable Gemma 4's chain-of-thought for fast structured JSON.
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = await self._client.post(f"{self.base_url}/chat/completions", json=body)
        resp.raise_for_status()
        message = resp.json()["choices"][0]["message"]
        content = self._extract_message_text(message)
        log.debug("planner raw: %s", content)
        # Gemma sometimes wraps the JSON in a ```json fence even when the
        # prompt says no markdown. Strip the fence before parsing.
        cleaned = _strip_code_fence(content)
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as e:
            # Last-ditch: pull the first {...} block from the text.
            start = cleaned.find("{")
            end = cleaned.rfind("}")
            if start >= 0 and end > start:
                try:
                    data = json.loads(cleaned[start : end + 1])
                except json.JSONDecodeError:
                    raise ValueError(
                        f"Planner did not return valid JSON: {e!s} :: {content[:200]}"
                    ) from e
            else:
                raise ValueError(f"Planner returned no JSON: {content[:200]}") from e
        return PlannerOutput.model_validate(data)

    async def finalize(
        self,
        user_text: str,
        plan: PlannerOutput,
        tool_result: ToolResult,
        ledger: LedgerEntry,
    ) -> str:
        system = self._render_final(user_text, plan, tool_result, ledger)
        body: dict[str, Any] = {
            "model": "gemma",
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user_text},
            ],
            "temperature": 0.4,
            "max_tokens": 256,
            "chat_template_kwargs": {"enable_thinking": False},
        }
        resp = await self._client.post(f"{self.base_url}/chat/completions", json=body)
        resp.raise_for_status()
        return self._extract_message_text(resp.json()["choices"][0]["message"])
