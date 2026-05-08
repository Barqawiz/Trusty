"""Process-wide mutable runtime config. Reset on restart.

Lives separately from `Settings` (which is read once from .env) so the admin
panel can flip flags without restarting the service. The orchestrator and
internet_policy read from here for `mode`/`allow_internet`."""
from __future__ import annotations

import asyncio

from .schemas import RuntimeConfig
from .settings import Settings


class RuntimeState:
    def __init__(self, settings: Settings) -> None:
        self._lock = asyncio.Lock()
        self._config = RuntimeConfig(
            mode=settings.TRUSTY_MODE,
            paused=False,
            wakeword_threshold=settings.WAKEWORD_THRESHOLD,
        )

    @property
    def config(self) -> RuntimeConfig:
        return self._config

    async def update(self, **kwargs) -> RuntimeConfig:
        async with self._lock:
            data = self._config.model_dump()
            data.update({k: v for k, v in kwargs.items() if v is not None})
            self._config = RuntimeConfig.model_validate(data)
            return self._config
