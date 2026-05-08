"""Online/offline gating. Read once at startup; cheap to call per turn."""
from __future__ import annotations

from pathlib import Path

import yaml


class InternetPolicy:
    def __init__(self, offline_blocklist: list[str], offline_message: str) -> None:
        self.offline_blocklist = set(offline_blocklist)
        self.offline_message = offline_message

    def allowed(self, tool_name: str, mode: str) -> bool:
        if mode == "offline" and tool_name in self.offline_blocklist:
            return False
        return True

    @classmethod
    def load(cls, project_root: Path) -> "InternetPolicy":
        path = project_root / "config" / "offline_mode.yaml"
        data = yaml.safe_load(path.read_text())
        return cls(
            offline_blocklist=list(data.get("blocked_when_offline", [])),
            offline_message=data.get(
                "offline_message", "Offline mode is on."
            ),
        )
