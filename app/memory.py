"""Lightweight key/value memory persisted to data/memory.json.

Used to give Gemma context about the user — name, default location, etc. —
across turns. Kept deliberately small: a flat dict of strings + a recents
list. Never leaves the device. Edit by hand or via Trusty's own update path.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from threading import Lock
from typing import Any

log = logging.getLogger(__name__)

_DEFAULTS: dict[str, Any] = {
    "user_name": "",
    "default_location": "",
    "preferences": {},
    "recents": {
        # last successful values per slot, e.g. {"location": "Dublin"}
    },
}


class Memory:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.exists():
            return dict(_DEFAULTS)
        try:
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            # Fill in any missing defaults so callers can rely on shape.
            merged = dict(_DEFAULTS)
            merged.update(data or {})
            return merged
        except (json.JSONDecodeError, OSError) as e:
            log.warning("memory.json corrupt (%s) — starting fresh", e)
            return dict(_DEFAULTS)

    def _save(self) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, sort_keys=True)
        tmp.replace(self.path)

    @property
    def data(self) -> dict[str, Any]:
        return dict(self._data)

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._save()

    def update(self, **kwargs: Any) -> None:
        with self._lock:
            for k, v in kwargs.items():
                if v is None:
                    continue
                self._data[k] = v
            self._save()

    def remember_recent(self, slot: str, value: str) -> None:
        with self._lock:
            recents = dict(self._data.get("recents") or {})
            recents[slot] = value
            self._data["recents"] = recents
            self._save()

    def clear(self) -> None:
        """Reset to defaults. Used by the admin panel's 'Clear memory' button."""
        with self._lock:
            self._data = dict(_DEFAULTS)
            self._save()

    # ---- Prompt helpers --------------------------------------------------

    def as_planner_context(self) -> str:
        """One-paragraph summary for the {{LOCAL_CONTEXT}} placeholder."""
        bits: list[str] = []
        name = (self._data.get("user_name") or "").strip()
        loc = (self._data.get("default_location") or "").strip()
        recents = self._data.get("recents") or {}

        if name:
            bits.append(f"The user's name is {name}.")
        if loc:
            bits.append(f"Default location is {loc}.")
        if recents.get("location"):
            bits.append(f"Last weather location used: {recents['location']}.")
        if not bits:
            return "No persisted user facts."
        return " ".join(bits)
