"""
routers/sse — Server-Sent Events stream endpoint.
"""
from __future__ import annotations
import asyncio
from fastapi import APIRouter
from sse_starlette.sse import EventSourceResponse

from ..sse import event_bus

router = APIRouter(prefix="/api/v1", tags=["sse"])


@router.get("/events")
async def event_stream():
    queue = event_bus.subscribe()

    async def _generate():
        try:
            while True:
                event = await asyncio.wait_for(queue.get(), timeout=30.0)
                yield {"event": event.type, "data": event.to_sse()}
        except asyncio.TimeoutError:
            yield {"event": "ping", "data": "{}"}
        except asyncio.CancelledError:
            return
        finally:
            event_bus.unsubscribe(queue)

    return EventSourceResponse(_generate())
