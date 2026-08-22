"""Tiny in-process pub/sub used to feed the viewer's SSE stream."""

from __future__ import annotations

import asyncio
import json
from typing import Any


class EventBus:
    def __init__(self, maxsize: int = 512) -> None:
        self._subscribers: set[asyncio.Queue] = set()
        self._maxsize = maxsize

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subscribers.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subscribers.discard(q)

    def publish(self, kind: str, payload: Any = None) -> None:
        message = f"event: {kind}\ndata: {json.dumps(payload, default=str)}\n\n"
        for q in list(self._subscribers):
            try:
                q.put_nowait(message)
            except asyncio.QueueFull:
                # A stalled browser tab must never block capture or analysis.
                self._subscribers.discard(q)
