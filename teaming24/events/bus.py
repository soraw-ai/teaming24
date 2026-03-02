"""
Typed EventBus — publish/subscribe with async and sync support.

This module provides the core event bus implementation. It replaces scattered
``_subscription_manager.broadcast()`` calls with a single, centralized bus
that supports:
- Typed events (:class:`EventType`)
- Async handlers on the asyncio loop
- Sync handlers on worker threads (via :class:`ThreadBridge`)
- Wildcard subscriptions (``*``) — subscribe to all events

Usage::

    from teaming24.events import EventBus, get_event_bus, EventType

    bus = get_event_bus()
    bus.set_loop(asyncio.get_event_loop())  # optional, enables publish_threadsafe

    # Subscribe (sync handler)
    def on_step(event_type, data):
        print(f"Step {data.get('step')} for task {data.get('task_id')}")

    sub_id = bus.subscribe(EventType.TASK_STEP, on_step)

    # Subscribe (async handler)
    async def on_completed(event_type, data):
        await save_result(data["task_id"], data["result"])

    bus.subscribe(EventType.TASK_COMPLETED, on_completed)

    # Wildcard — receive every event
    bus.subscribe("*", lambda et, d: logger.debug("Event: %s", et))

    # Publish from async code
    await bus.publish(EventType.TASK_STEP, {"task_id": "t1", "step": 1})

    bus.unsubscribe(sub_id)

Extension points:
- Handlers can be sync or async; the bus dispatches accordingly
- Use ``*`` as event_type for wildcard subscriptions
- Override EventBus for custom routing or filtering (advanced)
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from teaming24.events.types import EventType
from teaming24.utils.ids import random_hex

logger = logging.getLogger(__name__)

Handler = Callable[[EventType, dict], Any]


class _Subscription:
    __slots__ = ("id", "handler", "is_async")

    def __init__(self, handler: Handler, is_async: bool):
        self.id: str = random_hex(12)
        self.handler = handler
        self.is_async = is_async


class EventBus:
    """Centralized typed event bus for pub/sub with async and sync support.

    Full usage example::

        bus = get_event_bus()
        bus.set_loop(asyncio.get_running_loop())

        sub_id = bus.subscribe(EventType.TASK_COMPLETED, lambda et, d: print(d))
        await bus.publish(EventType.TASK_COMPLETED, {"task_id": "t1", "result": {}})
        bus.unsubscribe(sub_id)

    Thread-safety guarantees:
        - subscribe() and unsubscribe() are thread-safe (internal lock)
        - publish() must be called from the asyncio event loop
        - publish_threadsafe() can be called from any thread; schedules
          work on the bound loop or falls back to sync dispatch

    Extension points:
        - Handlers receive (event_type: EventType, data: dict)
        - Pass ``"*"`` to subscribe() for wildcard (all events)
        - set_loop() enables publish_threadsafe() to route to asyncio
    """

    def __init__(self) -> None:
        self._subs: dict[str, list[_Subscription]] = defaultdict(list)
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def set_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Bind the bus to an asyncio event loop (call once at startup).

        Required for publish_threadsafe() to schedule async handlers on the
        loop. If never set, publish_threadsafe() falls back to sync dispatch.

        :param loop: The asyncio event loop (e.g. from asyncio.get_event_loop())
        """
        self._loop = loop

    @property
    def loop(self) -> asyncio.AbstractEventLoop | None:
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug("EventBus loop not available in current thread")
                pass
        return self._loop

    # ------------------------------------------------------------------
    # Subscribe / Unsubscribe
    # ------------------------------------------------------------------

    def subscribe(
        self,
        event_type: EventType | str,
        handler: Handler,
    ) -> str:
        """Register a handler for an event type.

        :param event_type: EventType enum member or ``"*"`` for wildcard.
        :param handler: Callable(event_type, data) — sync or async.
        :return: Subscription ID for later unsubscribe().
        """
        key = event_type.value if isinstance(event_type, EventType) else str(event_type)
        is_async = asyncio.iscoroutinefunction(handler)
        sub = _Subscription(handler, is_async)
        with self._lock:
            self._subs[key].append(sub)
        return sub.id

    def unsubscribe(self, subscription_id: str) -> bool:
        """Remove a subscription by ID.

        :param subscription_id: The ID returned by subscribe().
        :return: True if the subscription was found and removed, False otherwise.
        """
        with self._lock:
            for _key, subs in self._subs.items():
                for i, sub in enumerate(subs):
                    if sub.id == subscription_id:
                        subs.pop(i)
                        return True
        return False

    # ------------------------------------------------------------------
    # Publish (async — preferred path when on the event loop)
    # ------------------------------------------------------------------

    async def publish(self, event_type: EventType, data: dict) -> None:
        """Publish an event to all matching handlers (async).

        Call from asyncio context. Dispatches to both typed and wildcard
        subscribers. Async handlers are awaited; sync handlers run inline.

        :param event_type: The event type (EventType enum).
        :param data: Payload dict; keys depend on event type (see types.py).
        """
        key = event_type.value
        with self._lock:
            handlers = list(self._subs.get(key, [])) + list(self._subs.get("*", []))
        for sub in handlers:
            try:
                if sub.is_async:
                    await sub.handler(event_type, data)
                else:
                    sub.handler(event_type, data)
            except Exception:
                logger.exception("EventBus handler error [%s] sub=%s", key, sub.id)

    # ------------------------------------------------------------------
    # Publish (thread-safe — for worker threads calling into asyncio)
    # ------------------------------------------------------------------

    def publish_threadsafe(self, event_type: EventType, data: dict) -> None:
        """Schedule an event publish on the bound asyncio loop.

        Safe to call from any thread (e.g. CrewAI worker threads). Uses
        asyncio.run_coroutine_threadsafe() when a loop is bound and running.
        Falls back to synchronous dispatch if no loop is available.

        :param event_type: The event type (EventType enum).
        :param data: Payload dict; keys depend on event type (see types.py).
        """
        loop = self.loop
        if loop is not None and loop.is_running():
            asyncio.run_coroutine_threadsafe(self.publish(event_type, data), loop)
        else:
            self._publish_sync(event_type, data)

    def _publish_sync(self, event_type: EventType, data: dict) -> None:
        """Synchronous fallback — call handlers directly (no awaiting).

        Used when publish_threadsafe() is called but no event loop is
        available. Async handlers are not awaited; a warning is logged.
        """
        key = event_type.value
        with self._lock:
            handlers = list(self._subs.get(key, [])) + list(self._subs.get("*", []))
        for sub in handlers:
            try:
                if sub.is_async:
                    logger.warning(
                        "Async handler %s called synchronously for %s",
                        sub.id, key,
                    )
                else:
                    sub.handler(event_type, data)
            except Exception:
                logger.exception("EventBus sync handler error [%s] sub=%s", key, sub.id)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_bus: EventBus | None = None
_bus_lock = threading.Lock()


def get_event_bus() -> EventBus:
    """Return the global EventBus singleton, creating it lazily (thread-safe).

    :return: The shared EventBus instance used across the application.
    """
    global _bus
    if _bus is None:
        with _bus_lock:
            if _bus is None:
                _bus = EventBus()
    return _bus
