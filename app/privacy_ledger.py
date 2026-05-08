"""Append-only JSONL privacy ledger. Every turn writes one line."""
from __future__ import annotations

import json
from pathlib import Path
from threading import Lock
from typing import Iterable

from .schemas import LedgerEntry


class PrivacyLedger:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()

    def append(self, entry: LedgerEntry) -> None:
        line = entry.model_dump_json()
        with self._lock, self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")

    def tail(self, limit: int = 20) -> list[dict]:
        if not self.path.exists():
            return []
        # Cheap tail — for a hackathon-scale ledger (likely <10k lines) this is fine.
        with self.path.open("r", encoding="utf-8") as f:
            lines = f.readlines()
        out: list[dict] = []
        for line in lines[-limit:]:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out
