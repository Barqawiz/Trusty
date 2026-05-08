"""In-process pub/sub for Eyes UI WebSocket state updates."""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import AsyncIterator

from .schemas import EyesState


class StateBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[EyesState]] = set()
        self._latest: EyesState = EyesState()

    @property
    def latest(self) -> EyesState:
        return self._latest

    async def publish(self, state: EyesState) -> None:
        self._latest = state
        for q in list(self._subscribers):
            try:
                q.put_nowait(state)
            except asyncio.QueueFull:
                pass

    @asynccontextmanager
    async def subscribe(self) -> AsyncIterator[asyncio.Queue[EyesState]]:
        q: asyncio.Queue[EyesState] = asyncio.Queue(maxsize=16)
        self._subscribers.add(q)
        # Send the latest snapshot immediately so a new client sees current state.
        try:
            q.put_nowait(self._latest)
        except asyncio.QueueFull:
            pass
        try:
            yield q
        finally:
            self._subscribers.discard(q)


bus = StateBus()
