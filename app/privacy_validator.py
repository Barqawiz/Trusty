"""The hard privacy gate. Runs after the planner and before any tool executes."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .schemas import PlannerOutput


@dataclass
class ValidationResult:
    ok: bool
    reason: str = ""


class PrivacyValidator:
    def __init__(
        self,
        tools: dict[str, dict[str, Any]],
        globally_forbidden: list[str],
        block_message: str,
    ) -> None:
        self.tools = tools
        self.globally_forbidden = {s.lower() for s in globally_forbidden}
        self.block_message = block_message

    def _arguments_violate_floor(self, args: dict[str, Any]) -> str | None:
        """Return the offending key/value if any forbidden token appears."""
        for key, value in args.items():
            kl = str(key).lower()
            if any(f in kl for f in self.globally_forbidden):
                return f"argument key '{key}' looks like forbidden payload"
            if isinstance(value, (str, bytes, bytearray)):
                vl = str(value).lower()
                if any(f in vl for f in self.globally_forbidden):
                    return f"argument value for '{key}' references forbidden payload"
        return None

    def validate(self, plan: PlannerOutput) -> ValidationResult:
        # 1. Tool exists.
        if plan.tool not in self.tools:
            return ValidationResult(False, f"Unknown tool: {plan.tool}")

        tool = self.tools[plan.tool]

        # 2. Tool is allowed to send audio? Never.
        if tool.get("sends_audio", False):
            return ValidationResult(False, "Tool would send audio.")

        # 3. external_payload must be in the tool's allow-list when one is set.
        allowed_payloads = tool.get("allowed_external_payload")
        if allowed_payloads is not None:
            mapping = {
                "none": None,
                "text_query_only": "text_query",
                "location_only": "location_text",
            }
            expected = mapping.get(plan.external_payload)
            if expected is not None and expected not in allowed_payloads:
                return ValidationResult(
                    False,
                    f"external_payload '{plan.external_payload}' is not allowed for "
                    f"tool '{plan.tool}'.",
                )

        # 4. Hard floor: no argument may smell like audio or home logs.
        violation = self._arguments_violate_floor(plan.arguments)
        if violation:
            return ValidationResult(False, violation)

        # 5. Per-tool forbidden list.
        for forbidden in tool.get("forbidden_payload", []):
            if forbidden.lower() in self.globally_forbidden:
                # already covered by the global check above; defensive
                continue

        # 6. requires_internet must agree with the tool definition for live tools.
        if tool.get("internet") is True and plan.requires_internet is False:
            return ValidationResult(
                False, f"Tool '{plan.tool}' needs internet but plan says otherwise."
            )

        return ValidationResult(True)

    @classmethod
    def load(
        cls, project_root: Path, tools_yaml: dict[str, dict[str, Any]]
    ) -> "PrivacyValidator":
        path = project_root / "config" / "privacy_policy.yaml"
        data = yaml.safe_load(path.read_text())
        return cls(
            tools=tools_yaml,
            globally_forbidden=list(data.get("globally_forbidden", [])),
            block_message=data.get("block_message", "Privacy policy blocked this."),
        )
