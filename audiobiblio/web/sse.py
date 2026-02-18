"""
sse — In-process event bus for Server-Sent Events.
"""
from __future__ import annotations
import asyncio
import json
import time
from dataclasses import dataclass, field
from typing import Any
import structlog

log = structlog.get_logger()


@dataclass
class Event:
    type: str
    data: dict[str, Any]
    timestamp: float = field(default_factory=time.time)

    def to_sse(self) -> str:
        return json.dumps({"type": self.type, "data": self.data, "ts": self.timestamp})


class EventBus:
    """Broadcast events to all connected SSE clients."""

    def __init__(self):
        self._subscribers: list[asyncio.Queue] = []

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue):
        try:
            self._subscribers.remove(q)
        except ValueError:
            pass

    async def publish(self, event: Event):
        dead = []
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                dead.append(q)
        for q in dead:
            self._subscribers.remove(q)

    def publish_sync(self, event_type: str, data: dict[str, Any]):
        """Publish from a sync context (e.g. scheduler thread)."""
        event = Event(type=event_type, data=data)
        try:
            loop = asyncio.get_running_loop()
            asyncio.run_coroutine_threadsafe(self.publish(event), loop)
        except RuntimeError:
            pass  # no loop running yet


# Singleton
event_bus = EventBus()
