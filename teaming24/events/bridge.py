"""
Thread-safe bridge: worker thread → asyncio event loop.

Problem: CrewAI and other agents run in worker threads (ThreadPoolExecutor),
but SSE, WebSocket, and UI updates need to run on the asyncio event loop.
Publishing events directly from a worker thread would bypass the loop and
fail to reach async subscribers.

Solution: ThreadBridge provides an ``emit()`` method that safely routes
events from any thread into the bus's publish_threadsafe(), which schedules
the actual publish on the asyncio loop.

Usage::

    # At startup (in async context)
    loop = asyncio.get_running_loop()
    bridge = ThreadBridge(loop=loop)

    # Pass bridge.emit to a worker (e.g. CrewAI callback)
    def crew_callback(output):
        bridge.emit(EventType.TASK_STEP, {"task_id": "t1", "output": output})

    # Or use emit_async when already on the loop
    await bridge.emit_async(EventType.CHAT_MESSAGE, {"message": "Hello"})

When to use ThreadBridge vs direct bus.publish_threadsafe():
    - Use ThreadBridge when you need a long-lived, configured bridge (e.g.
      one per server instance) that you pass to multiple workers. It also
      ensures the bus's loop is set via set_loop().
    - Use bus.publish_threadsafe() directly when you have a one-off call
      from a thread and already have get_event_bus() available. No need
      for a bridge instance.
"""

from __future__ import annotations

import asyncio
import logging

from teaming24.events.bus import EventBus, get_event_bus
from teaming24.events.types import EventType

logger = logging.getLogger(__name__)


class ThreadBridge:
    """Convenience wrapper for emitting events from worker threads to asyncio.

    Provides a sync ``emit()`` callable suitable for use in CrewAI worker
    threads that need to push events back to the async event bus. Internally
    delegates to bus.publish_threadsafe() and ensures the bus is bound to
    the given loop.

    Usage::

        bridge = ThreadBridge(loop=asyncio.get_event_loop())
        # Pass bridge.emit to a worker thread
        bridge.emit(EventType.TASK_STEP, {"task_id": "...", "step": 3})
    """

    def __init__(
        self,
        loop: asyncio.AbstractEventLoop | None = None,
        bus: EventBus | None = None,
    ) -> None:
        self._loop = loop
        self._bus = bus or get_event_bus()
        if loop is not None:
            self._bus.set_loop(loop)

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        return self._loop or self._bus.loop

    def emit(self, event_type: EventType, data: dict) -> None:
        """Emit an event from any thread.

        Safe to call from worker threads (e.g. CrewAI callbacks). Routes
        through the bus's publish_threadsafe() to schedule on the asyncio loop.
        """
        self._bus.publish_threadsafe(event_type, data)

    async def emit_async(self, event_type: EventType, data: dict) -> None:
        """Emit directly on the event loop (when you're already in async context).

        Use this instead of emit() when calling from async code to avoid
        the thread-safe indirection.
        """
        await self._bus.publish(event_type, data)
