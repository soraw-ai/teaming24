"""
AgentEventBuffer — thread-safe circular buffer for SSE event replay.

Assigns monotonically increasing sequence numbers to each agent event
so the standard SSE `id:` / `Last-Event-ID` reconnect protocol works:
  • Browser disconnects → stores Last-Event-ID automatically
  • On reconnect browser sends `Last-Event-ID: N` header
  • Server replays all buffered events with seq > N
"""

import threading
from collections import deque


class AgentEventBuffer:
    """Circular buffer of raw SSE strings with monotonic sequence numbers."""

    def __init__(self, maxlen: int | None = None):
        if maxlen is None:
            from teaming24.config import get_config
            maxlen = get_config().api.event_buffer_capacity
        self._lock = threading.Lock()
        self._events: deque = deque(maxlen=maxlen)
        self._seq: int = 0

    def push(self, raw_sse: str) -> int:
        """Store a raw SSE string and return its sequence number."""
        with self._lock:
            self._seq += 1
            self._events.append((self._seq, raw_sse))
            return self._seq

    def get_since(self, since_seq: int) -> list[tuple[int, str]]:
        """Return all (seq, raw_sse) pairs whose seq > since_seq."""
        with self._lock:
            return [(s, r) for s, r in self._events if s > since_seq]

    @property
    def latest_seq(self) -> int:
        with self._lock:
            return self._seq


_buffer: AgentEventBuffer | None = None


def _get_buffer() -> AgentEventBuffer:
    global _buffer
    if _buffer is None:
        _buffer = AgentEventBuffer()
    return _buffer


def get_event_buffer() -> AgentEventBuffer:
    return _get_buffer()
