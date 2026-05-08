"""Sanity checks on prompts/planner_system.md.

The planner prompt is the contract that steers Gemma. We can't unit-test
Gemma's actual outputs (live LLM call), but we CAN verify the prompt:
  - still has all the existing rules and example shapes the runtime depends on
  - covers the new behavior: movies / shows / creative tasks (story / joke)
  - has not regressed on the preserved rules (weather / vacuum / music)

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


# --- New behavior: internet.search now covers entertainment / movies ------

INTERNET_SEARCH_KEYWORDS_NEW = [
    "movies",
    "shows",
    "release dates",
    "events",
    "concerts",
]


@pytest.mark.parametrize("kw", INTERNET_SEARCH_KEYWORDS_NEW)
def test_internet_search_keyword_covered(prompt_text: str, kw: str):
    """The internet.search rule (rule 12) must list the new keywords so the
    planner stops routing 'latest movies' to local.answer."""
    assert kw in prompt_text, (
        f"missing internet.search keyword {kw!r} in planner prompt"
    )


def test_internet_search_movie_example_present(prompt_text: str):
    """A worked example for movies anchors the planner."""
    assert "latest movies in theaters" in prompt_text
    assert "live web data — movies" in prompt_text


# --- New behavior: local.answer now covers creative tasks ------------------

CREATIVE_TASK_TYPES = ["stories", "jokes", "poems", "riddles"]


@pytest.mark.parametrize("kw", CREATIVE_TASK_TYPES)
def test_local_answer_creative_keyword_covered(prompt_text: str, kw: str):
    """Rule 13 must explicitly list creative tasks so the planner doesn't
    fall back to 'I can answer general knowledge questions'."""
    assert kw in prompt_text, (
        f"missing creative-task keyword {kw!r} in planner prompt"
    )


def test_creative_examples_are_present(prompt_text: str):
    """Worked examples seed the planner with the JSON shape for creative
    tasks. Without them Gemma reverts to refusal templates."""
    assert "tell me a short story for my kids" in prompt_text
    assert "creative — story" in prompt_text
    assert "tell me a joke" in prompt_text
    assert "creative — joke" in prompt_text


# --- Regression: existing rules / examples must still be intact ------------

PRESERVED_PHRASES = [
    # Core privacy rules
    "Never send microphone audio to the internet.",
    "Never send wake-word audio to the internet.",
    # Routing rule headers (the planner relies on these to find sections)
    "**Weather**",
    "**Vacuum / Roborock / floor cleaning**",
    "**Music**",
    "**LG TV control**",
    "**Memory**",
    "**Live / time-sensitive web data**",
    # Anchored examples that downstream behavior depends on
    "what is the weather in Dublin",
    "stop the vacuum",
    "play happy birthday",
    "Apple stock price",
    "look up the price of Bitcoin",
    "what is the capital of Country",
    # Mishear rules
    "wither",
    "dabble in",
    "vakyo",
    # Output schema fields
    '"tool": "local.answer|home.tv|home.vacuum|music|weather.live|internet.search|memory|none"',
]


@pytest.mark.parametrize("phrase", PRESERVED_PHRASES)
def test_preserved_phrase(prompt_text: str, phrase: str):
    assert phrase in prompt_text, (
        f"regression: phrase missing from planner prompt: {phrase!r}"
    )


# --- Sanity: prompt loads via the model client ----------------------------

def test_planner_template_loads_via_model_client():
    """`LlamaClient.from_files` reads the prompt at startup. Make sure the
    file is still where the client expects it."""
    from app.model_client import LlamaClient

    prompts_dir = PROMPT_PATH.parent
    client = LlamaClient.from_files(
        base_url="http://127.0.0.1:8080/v1", prompts_dir=prompts_dir,
    )
    # Confirm both rule sections are in the loaded text.
    assert "creative task" in client.planner_template.lower()
    assert "movies" in client.planner_template.lower()
