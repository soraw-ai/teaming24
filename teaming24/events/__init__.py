"""
Typed event system for Teaming24 — plug-and-play pub/sub.

This package provides a centralized, typed event bus for decoupled
communication between modules. Any component can publish or subscribe
to events without knowing who else is listening.

Architecture::

    ┌──────────┐  publish()   ┌──────────┐  handler()  ┌──────────┐
    │ Producer │ ──────────▶  │ EventBus │ ──────────▶  │ Consumer │
    └──────────┘              └──────────┘              └──────────┘
    (any module)              (singleton)               (any module)

Quick start::

    from teaming24.events import EventType, get_event_bus

    bus = get_event_bus()

    # Subscribe (sync or async handler)
    def on_task_done(event_type, data):
        print(f"Task {data['task_id']} completed!")

    sub_id = bus.subscribe(EventType.TASK_COMPLETED, on_task_done)

    # Publish (from async context)
    await bus.publish(EventType.TASK_COMPLETED, {"task_id": "t1"})

    # Publish (from worker thread)
    bus.publish_threadsafe(EventType.TASK_COMPLETED, {"task_id": "t1"})

    # Unsubscribe
    bus.unsubscribe(sub_id)

Extending with new event types::

    # Add to teaming24/events/types.py:
    class EventType(str, Enum):
        ...
        MY_CUSTOM_EVENT = "my_custom_event"

Thread safety:
    - subscribe/unsubscribe are thread-safe (internal lock)
    - publish_threadsafe() can be called from any thread
    - publish() must be called from the asyncio event loop

Exports:
    EventType       — Enum of all typed event names
    EventBus        — The pub/sub bus class
    get_event_bus   — Singleton accessor (lazy init)
    ThreadBridge    — Convenience wrapper for worker threads
"""

from teaming24.events.bridge import ThreadBridge
from teaming24.events.bus import EventBus, get_event_bus
from teaming24.events.types import EventType

__all__ = [
    "EventType",
    "EventBus",
    "get_event_bus",
    "ThreadBridge",
]
