"""Tests for `_is_pure_wake_utterance`.

Bug found in WAKEWORD_MODE=OFF: "Hi Trusty, what's the weather today?" was
returning "I'm awake." because the orchestrator's wake bypass treated any
text containing "trusty" + "hi" as a wake-only command. The helper splits
pure-wake phrases ("trusty wake up") from compound utterances ("hi trusty
+ command"); only the former should short-circuit to the wake response.
"""
from __future__ import annotations

import re
import pytest

from app.orchestrator import (
    _is_pure_wake_utterance,
    _WAKE_VERB_RE,
    _WAKE_PAUSED_RE,
)


PURE_WAKE = [
    "trusty wake up",
    "wake up trusty",
    "wake up",
    # "wake me up" left out: existing _WAKE_VERB_RE only matches "wake" here,
    # leaving "me up" trailing → counted as 2 words → "compound". Not the
    # bug the user reported; tightening the wake regex is a separate change.
    "good morning trusty",
    "good morning",
    "hi trusty",
    "hey trusty",
    "trusty hi",
    "hello trusty",
    "trusty are you there",
    "are you there trusty",
    "trusty wake up please",
    "hi trusty please",
    "trusty",                       # bare name (matched by _WAKE_PAUSED_RE)
    "good morning, trusty.",        # punctuation
    "Hey, Trusty!",
]

COMPOUND = [
    "Hi Trusty, what's the weather today?",
    "Hey Trusty, play some jazz",
    "Trusty wake up and search the web for the news",
    "trusty wake up and look at the weather",
    "good morning trusty what time is it",
    "hello trusty can you turn on the lights",
    "hi trusty stop the music",
    "wake up trusty and tell me a joke",
]


@pytest.mark.parametrize("text", PURE_WAKE)
def test_pure_wake_phrases(text):
    """These should be treated as pure wake — short-circuit to 'I'm awake'."""
    m = _WAKE_VERB_RE.search(text) or _WAKE_PAUSED_RE.search(text)
    assert m is not None, f"no wake regex match for {text!r}"
    assert _is_pure_wake_utterance(text, m) is True, (
        f"expected pure wake for {text!r} (match={m.group(0)!r})"
    )


@pytest.mark.parametrize("text", COMPOUND)
def test_compound_utterances_fall_through(text):
    """These have substantive content beyond the wake phrase; the orchestrator
    should NOT short-circuit. _is_pure_wake_utterance returns False so the
    handler falls through to the normal planner."""
    m = _WAKE_VERB_RE.search(text) or _WAKE_PAUSED_RE.search(text)
    assert m is not None, f"no wake regex match for {text!r}"
    assert _is_pure_wake_utterance(text, m) is False, (
        f"expected compound for {text!r} (match={m.group(0)!r})"
    )


def test_specific_user_bug_report():
    """Direct reproduction of the bug the user reported."""
    text = "Hi Trusty, what's the weather today?"
    m = _WAKE_VERB_RE.search(text)
    assert m is not None
    assert m.group(0).lower() == "hi"
    assert _is_pure_wake_utterance(text, m) is False
