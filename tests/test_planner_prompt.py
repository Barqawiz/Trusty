"""Sanity checks on prompts/planner_system.md.

The planner prompt is the contract that steers Gemma. We can't unit-test
Gemma's actual outputs (live LLM call), but we CAN verify the prompt:
  - has all the routing rules the runtime depends on
  - covers entertainment/movies, creative tasks (story / joke / riddle)
  - keeps the worked-example anchors that downstream behavior expects
  - preserves the mishear vocabulary

If a future edit drops a rule or example by accident, these tests catch it
before deployment.
"""
from __future__ import annotations

from pathlib import Path

import pytest

PROMPT_PATH = Path(__file__).resolve().parents[1] / "prompts" / "planner_system.md"


@pytest.fixture(scope="module")
def prompt_text() -> str:
    return PROMPT_PATH.read_text()


# --- internet.search covers entertainment / live web data ------------------

INTERNET_SEARCH_KEYWORDS = [
    "movies",
    "shows",
    "news",
    "stock",
    "trending",
]


@pytest.mark.parametrize("kw", INTERNET_SEARCH_KEYWORDS)
def test_internet_search_keyword_covered(prompt_text: str, kw: str):
    assert kw in prompt_text, f"missing internet.search keyword {kw!r}"


def test_internet_search_movie_example_present(prompt_text: str):
    # Movies is a live-web-data trigger; the prompt explicitly lists it.
    assert "latest movies" in prompt_text
    assert "live web data" in prompt_text


# --- local.answer covers creative + stable-knowledge tasks ------------------

CREATIVE_TASK_TYPES = ["stories", "jokes", "poems", "riddles"]


@pytest.mark.parametrize("kw", CREATIVE_TASK_TYPES)
def test_local_answer_creative_keyword_covered(prompt_text: str, kw: str):
    assert kw in prompt_text, f"missing creative-task keyword {kw!r}"


def test_creative_examples_are_present(prompt_text: str):
    assert "tell me a short story for my kid" in prompt_text
    assert "creative — story" in prompt_text
    assert "tell me a joke" in prompt_text
    assert "creative — joke" in prompt_text


# --- Stable-knowledge + unit-conversion examples (rule 8) -------------------

def test_knowledge_and_conversion_examples_present(prompt_text: str):
    assert "what is 9 times 9" in prompt_text
    assert "convert 25 celsius to fahrenheit" in prompt_text
    assert "largest forest in the world" in prompt_text


# --- Routing rules: each tool category must be addressed --------------------

ROUTING_SECTIONS = [
    "Privacy violation",
    "Weather",
    "Vacuum",
    "TV",
    "Music",
    "Memory",
    "Live web data",
    "Stable knowledge",
]


@pytest.mark.parametrize("section", ROUTING_SECTIONS)
def test_routing_section_present(prompt_text: str, section: str):
    assert section in prompt_text, f"missing routing section: {section!r}"


# --- Anchored worked examples (downstream behavior expects these shapes) ---

ANCHORED_EXAMPLES = [
    "what is the weather in Dublin",
    "stop the vacuum",
    "play happy birthday",
    "set my location to Dublin",
    "search for the latest AI news",
    "send my voice recording to Google",
]


@pytest.mark.parametrize("phrase", ANCHORED_EXAMPLES)
def test_anchored_example_present(prompt_text: str, phrase: str):
    assert phrase in prompt_text, f"missing worked example: {phrase!r}"


# --- Mishear vocabulary -----------------------------------------------------

MISHEARS = ["wither", "dabble in", "vakyo", "roborok"]


@pytest.mark.parametrize("token", MISHEARS)
def test_mishear_token_present(prompt_text: str, token: str):
    assert token in prompt_text, f"missing mishear: {token!r}"


# --- Output contract: schema field names must be in the prompt -------------

SCHEMA_FIELDS = [
    "tool",
    "action",
    "arguments",
    "requires_internet",
    "external_payload",
    "privacy_risk",
    "reason",
    "final_response_required",
    "local_answer",
]


@pytest.mark.parametrize("field", SCHEMA_FIELDS)
def test_schema_field_documented(prompt_text: str, field: str):
    assert field in prompt_text, f"schema field missing from prompt: {field!r}"


def test_no_fence_no_prose_rule_present(prompt_text: str):
    """Prompt must instruct Gemma to emit JSON only — no prose / fences."""
    text = prompt_text.lower()
    assert "no prose" in text and ("no markdown" in text or "no code fences" in text)
    assert "first character of your reply must be `{`" in text


# --- Sanity: prompt loads via the model client ------------------------------

def test_planner_template_loads_via_model_client():
    from app.model_client import LlamaClient

    prompts_dir = PROMPT_PATH.parent
    client = LlamaClient.from_files(
        base_url="http://127.0.0.1:8080/v1", prompts_dir=prompts_dir,
    )
    text = client.planner_template.lower()
    assert "creative" in text
    assert "movies" in text
