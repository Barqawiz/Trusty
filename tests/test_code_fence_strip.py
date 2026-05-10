"""Tests for the planner JSON code-fence stripper.

Gemma 4 frequently wraps structured output in ```json ... ``` even when the
system prompt says "no markdown". Without stripping, json.loads fails and
the orchestrator falls back to a refusal template. _strip_code_fence is
the safety net that turns the fenced output back into plain JSON.
"""
from __future__ import annotations

import pytest

from app.model_client import _strip_code_fence


CASES_FENCED = [
    # (input, expected_after_strip)
    (
        '```json\n{"tool":"local.answer"}\n```',
        '{"tool":"local.answer"}',
    ),
    (
        '```JSON\n{"tool":"local.answer"}\n```',
        '{"tool":"local.answer"}',
    ),
    (
        '```\n{"tool":"local.answer"}\n```',
        '{"tool":"local.answer"}',
    ),
    # Extra surrounding whitespace
    (
        '   ```json\n  {"tool":"local.answer"}  \n```   ',
        '{"tool":"local.answer"}',
    ),
    # Multi-line JSON inside fence
    (
        '```json\n{\n  "tool": "local.answer",\n  "action": "answer"\n}\n```',
        '{\n  "tool": "local.answer",\n  "action": "answer"\n}',
    ),
]


@pytest.mark.parametrize("raw,expected", CASES_FENCED)
def test_strips_code_fence(raw, expected):
    assert _strip_code_fence(raw) == expected


CASES_PASSTHROUGH = [
    # Plain JSON — no fence, return unchanged.
    '{"tool":"local.answer"}',
    '{"tool":"local.answer","action":"answer"}',
    # Empty / whitespace
    "",
    "   ",
    # Non-JSON garbage — stripper doesn't try to fix it.
    "Some prose without JSON.",
]


@pytest.mark.parametrize("raw", CASES_PASSTHROUGH)
def test_passthrough_when_no_fence(raw):
    assert _strip_code_fence(raw) == raw


def test_fenced_real_planner_output():
    """The exact shape we saw in the Pi log when the bug fired."""
    raw = (
        '```json\n'
        '{\n'
        '  "tool": "local.answer",\n'
        '  "action": "answer",\n'
        '  "arguments": {},\n'
        '  "requires_internet": false,\n'
        '  "external_payload": "none",\n'
        '  "privacy_risk": "low",\n'
        '  "reason": "creative task — short story",\n'
        '  "final_response_required": true,\n'
        '  "local_answer": "Once upon a time..."\n'
        '}\n'
        '```'
    )
    cleaned = _strip_code_fence(raw)
    # Must round-trip through json.loads
    import json
    data = json.loads(cleaned)
    assert data["tool"] == "local.answer"
    assert data["local_answer"].startswith("Once upon a time")


def test_planner_prompt_has_explicit_no_fence_rule():
    """Belt-and-braces: even with the stripper, the prompt should explicitly
    forbid code fences so Gemma is more likely to comply in the first place."""
    from pathlib import Path
    text = (Path(__file__).resolve().parents[1] / "prompts" / "planner_system.md").read_text()
    forbids_fences = (
        "```json fences" in text
        or "no ```json" in text.lower()
        or "no code fences" in text.lower()
        or "no markdown" in text.lower()
    )
    assert forbids_fences, "planner prompt should explicitly forbid markdown / code fences"
    assert "first character of your reply must be `{`" in text.lower()
