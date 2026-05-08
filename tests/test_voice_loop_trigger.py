"""Tests for the WAKEWORD_MODE=OFF trigger regex.

In transcript-scan mode the voice loop dispatches a chunk to /chat only if
the Whisper / Moonshine transcript matches `_WAKE_TRIGGER_RE`. The orchestrator
remains the source of truth for what each phrase actually does — this regex
is purely the "should we even forward this chunk?" gate, intentionally
permissive on common mishears so the loop never UNDER-dispatches.

These tests also cover `_handle_turn`'s `require_trigger` branch: it must
return (False, {}) without calling /chat when require_trigger=True and the
transcript has no trigger.
"""
from __future__ import annotations

import re

from voice.loop import _WAKE_TRIGGER_RE


# ---------------------------------------------------------------------------
# Should match — every utterance the loop must FORWARD to /chat in OFF mode.
# ---------------------------------------------------------------------------

MATCH_CASES = [
    # Direct address.
    "trusty what time is it",
    "Trusty, what time is it?",
    "TRUSTY play some jazz",
    "hey trusty",
    "Hey, Trusty.",
    # Wake verb forms.
    "wake up trusty",
    "wake up",
    "wake me up",
    "trusty wake up",
    "trusty wake up and search the web",
    "Trusty, wake up and look at the weather",
    # Sleep is still a trigger — it's a command we DO want to forward; the
    # orchestrator decides what to do with it. (The wake-paused regex on the
    # orchestrator side handles the unpause flow.)
    "trusty go to sleep",
    "trusty sleep",
    # Greetings (the orchestrator's wake-paused regex accepts these).
    "good morning trusty",
    "good afternoon",
    "good evening",
    # Whisper / Moonshine common mishears of "trusty".
    "trustly are you there",
    "rusty what time is it",
    "trust me what's the weather",   # `trust\w*` covers "trust me"
    "trust ye",
    # Generic openings — kept permissive on purpose.
    "hey what's up",
    "hi there",
]


# ---------------------------------------------------------------------------
# Should NOT match — chatter the loop must DROP silently in OFF mode.
# ---------------------------------------------------------------------------

NON_MATCH_CASES = [
    "i need to go to the park",
    "the weather is nice today",
    "play some music",                 # no trigger word at all
    "what time is it",                 # bare question, no name / wake verb
    "stop the music",                  # follow-up command without trigger
    "tell me a joke",
    "",                                # empty transcript
    "   ",                             # whitespace only
    ".",                               # punctuation
    "uh huh",                          # filler
    "ok thanks",
    "the kids are home",
    # "Trusty" embedded in a longer word should NOT trigger via the `trust\w*`
    # alternative — \b boundaries must hold. Test we don't false-positive on
    # words that look adjacent to "trust" but aren't.
    "i distrust this",                 # "distrust" — no leading word boundary
    "untrustworthy people",
]


def test_all_match_cases():
    failed = [t for t in MATCH_CASES if not _WAKE_TRIGGER_RE.search(t)]
    assert not failed, f"expected to match but didn't: {failed}"


def test_all_non_match_cases():
    failed = [t for t in NON_MATCH_CASES if _WAKE_TRIGGER_RE.search(t)]
    assert not failed, f"expected NOT to match but did: {failed}"


def test_match_object_returns_word():
    """The regex captures the trigger word so logs / telemetry can show
    which alternative fired. Sanity-check the capture exists."""
    m = _WAKE_TRIGGER_RE.search("trusty what time is it")
    assert m is not None
    assert m.group(0).lower().startswith("trust")


def test_regex_is_compiled_case_insensitive():
    """Whisper sometimes returns ALL CAPS for shouted speech. The regex
    must remain case-insensitive."""
    assert _WAKE_TRIGGER_RE.search("TRUSTY WAKE UP")
    assert _WAKE_TRIGGER_RE.search("Hey TRUSTY")
    assert _WAKE_TRIGGER_RE.flags & re.IGNORECASE


# ---------------------------------------------------------------------------
# `_handle_turn` integration: require_trigger discards non-trigger transcripts
# without making any /chat call. We monkeypatch the recording / transcribing
# helpers to simulate transcripts the recorder would have produced.
# ---------------------------------------------------------------------------


def _stub_audio():
    import numpy as np
    return np.ones(16000, dtype="int16")  # 1 s of nonzero audio


def _build_cfg(tmp_path):
    return {
        "trusty_url": "http://127.0.0.1:8090",
        "wakeword_name": "hey_trusty",
        "wakeword_model": "alexa",
        "wakeword_threshold": 0.5,
        "wakeword_threshold_builtin": 0.5,
        "wakeword_custom": "",
        "wakeword_enabled": False,
        "whisper_bin": "",
        "whisper_model": "",
    }


def test_handle_turn_drops_chunk_without_trigger(monkeypatch, tmp_path):
    """When require_trigger=True and transcript has no wake word, _handle_turn
    must return (False, {}) without calling /chat."""
    from voice import loop as loop_mod

    monkeypatch.setattr(loop_mod, "record_until_silence", lambda **kw: _stub_audio())
    monkeypatch.setattr(loop_mod, "transcribe_pcm16",
                        lambda *a, **kw: "tell me a joke")

    chat_calls = []

    def _no_chat(*a, **kw):  # any httpx.Client.post call would land here
        chat_calls.append((a, kw))
        raise AssertionError("/chat should not be called when trigger missing")

    monkeypatch.setattr("httpx.Client.post", _no_chat)
    monkeypatch.setattr(loop_mod, "speak", lambda *a, **kw: None)

    processed, plan = loop_mod._handle_turn(
        _build_cfg(tmp_path),
        "http://127.0.0.1:8090",
        require_trigger=True,
    )
    assert processed is False
    assert plan == {}
    assert chat_calls == []


def test_handle_turn_dispatches_when_trigger_present(monkeypatch, tmp_path):
    """When require_trigger=True and transcript contains a trigger, the
    chunk IS dispatched (we don't validate the full TTS path here, just that
    the request goes out)."""
    from voice import loop as loop_mod

    monkeypatch.setattr(loop_mod, "record_until_silence", lambda **kw: _stub_audio())
    monkeypatch.setattr(loop_mod, "transcribe_pcm16",
                        lambda *a, **kw: "trusty what time is it")
    monkeypatch.setattr(loop_mod, "speak", lambda *a, **kw: None)

    posted = []

    class _FakeResp:
        def raise_for_status(self): pass
        def json(self):
            return {"final_response": "It's 5 pm.", "plan": {"tool": "answer"}}

    class _FakeClient:
        def __init__(self, *a, **kw): pass
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def post(self, url, json=None):
            posted.append((url, json))
            return _FakeResp()

    monkeypatch.setattr("httpx.Client", _FakeClient)

    processed, plan = loop_mod._handle_turn(
        _build_cfg(tmp_path),
        "http://127.0.0.1:8090",
        require_trigger=True,
    )
    assert processed is True
    assert plan == {"tool": "answer"}
    assert len(posted) == 1
    assert posted[0][0].endswith("/chat")
    assert posted[0][1]["text"] == "trusty what time is it"
