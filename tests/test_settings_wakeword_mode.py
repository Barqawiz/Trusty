"""Tests for the WAKEWORD_MODE setting.

The flag exists so the assistant can run without openWakeWord — instead the
voice loop scans Whisper / Moonshine transcripts for a trigger word. We need
the parser to fail SAFE: anything other than the literal "OFF" must keep the
legacy openWakeWord-driven behavior. Empty / missing / typos / garbage all
default to ON.
"""
from __future__ import annotations

import importlib

import pytest

import app.settings as settings_module


def _fresh_settings(**env: str):
    """Build a Settings instance from a clean env mapping.

    Pydantic BaseSettings reads `os.environ` at construction. We can't rely
    on the cached singleton (`get_settings()`) because every test would then
    see whatever the first test loaded. Instead we instantiate `Settings`
    directly with `_env_file=None` so it ignores the on-disk .env.
    """
    return settings_module.Settings(_env_file=None, **env)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    """Strip WAKEWORD_MODE from the OS env so tests can set it explicitly.

    The repo's .env sets WAKEWORD_MODE=ON, which would leak through pydantic's
    case-insensitive match if we didn't clear it.
    """
    for key in ("WAKEWORD_MODE", "wakeword_mode", "Wakeword_Mode"):
        monkeypatch.delenv(key, raising=False)


def test_default_is_enabled():
    """Unset WAKEWORD_MODE → openWakeWord stays active (current behavior)."""
    s = _fresh_settings()
    assert s.WAKEWORD_MODE == "ON"
    assert s.wakeword_enabled is True


def test_explicit_on():
    s = _fresh_settings(WAKEWORD_MODE="ON")
    assert s.wakeword_enabled is True


def test_lowercase_on():
    s = _fresh_settings(WAKEWORD_MODE="on")
    assert s.wakeword_enabled is True


def test_explicit_off_uppercase():
    s = _fresh_settings(WAKEWORD_MODE="OFF")
    assert s.wakeword_enabled is False


def test_explicit_off_mixed_case():
    s = _fresh_settings(WAKEWORD_MODE="Off")
    assert s.wakeword_enabled is False


def test_explicit_off_lowercase():
    s = _fresh_settings(WAKEWORD_MODE="off")
    assert s.wakeword_enabled is False


def test_off_with_whitespace():
    s = _fresh_settings(WAKEWORD_MODE="  OFF  ")
    assert s.wakeword_enabled is False


def test_empty_value_defaults_to_enabled():
    """Empty string is the most common .env mistake (`WAKEWORD_MODE=`).
    Must NOT silently disable the wake word."""
    s = _fresh_settings(WAKEWORD_MODE="")
    assert s.wakeword_enabled is True


def test_garbage_value_defaults_to_enabled():
    """Typo or invalid value → safe default (legacy behavior)."""
    s = _fresh_settings(WAKEWORD_MODE="enabled")
    assert s.wakeword_enabled is True


def test_yes_does_not_disable():
    """We deliberately accept ONLY the literal "OFF" — `NO` / `FALSE` / `0`
    are not synonyms. Documented in .env.example."""
    s = _fresh_settings(WAKEWORD_MODE="NO")
    assert s.wakeword_enabled is True


def test_property_is_recomputed_each_call():
    """`wakeword_enabled` is a property, not a cached bool. The same Settings
    instance returns the value for whatever WAKEWORD_MODE it was built with."""
    s_on = _fresh_settings(WAKEWORD_MODE="ON")
    s_off = _fresh_settings(WAKEWORD_MODE="OFF")
    assert s_on.wakeword_enabled is True
    assert s_off.wakeword_enabled is False
