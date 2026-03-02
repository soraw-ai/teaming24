import asyncio
import json
import time
from typing import TYPE_CHECKING, Any

from teaming24.utils.logger import get_logger

if TYPE_CHECKING:
    from teaming24.config import SubscriptionConfig

logger = get_logger(__name__)

class SubscriptionManager:
    """Manages SSE subscriptions for network events."""

    def __init__(self, config: 'SubscriptionConfig'):
        """
        Initialize Subscription Manager.

        Args:
            config: Subscription configuration from agentanet.yaml
        """
        self.config = config
        self.subscribers: list[asyncio.Queue] = []
        self._shutdown_event: asyncio.Event | None = None

    def set_shutdown_event(self, event: asyncio.Event) -> None:
        """Wire a global shutdown event so subscribe() loops exit promptly."""
        self._shutdown_event = event

    async def subscribe(self):
        """Yield SSE events for a new subscriber."""
        # Prevent too many subscribers
        if len(self.subscribers) >= self.config.max_subscribers:
            logger.warning(f"Max subscribers ({self.config.max_subscribers}) reached, rejecting new connection")
            yield f"data: {json.dumps({'type': 'error', 'message': 'Too many connections', 'timestamp': time.time()})}\n\n"
            return

        # Use bounded queue to prevent memory exhaustion
        queue: asyncio.Queue = asyncio.Queue(maxsize=self.config.max_queue_size)
        self.subscribers.append(queue)
        try:
            # Initial connection message
            yield f"data: {json.dumps({'type': 'connected', 'timestamp': time.time()})}\n\n"

            while not (self._shutdown_event and self._shutdown_event.is_set()):
                try:
                    # Wait for message or timeout for keep-alive
                    msg = await asyncio.wait_for(queue.get(), timeout=self.config.keepalive_interval)
                    yield msg
                except TimeoutError:
                    logger.debug("Subscription keepalive timeout; sending ping")
                    # Send keep-alive ping
                    yield f"data: {json.dumps({'type': 'ping', 'timestamp': time.time()})}\n\n"
        except (asyncio.CancelledError, GeneratorExit):
            logger.debug("Subscription cancelled")
            pass
        finally:
            if queue in self.subscribers:
                self.subscribers.remove(queue)

    def close_all(self) -> None:
        """Drain all subscriber queues with a close sentinel so generators exit."""
        for queue in list(self.subscribers):
            try:
                queue.put_nowait(None)
            except Exception as e:
                logger.debug(f"Error sending close sentinel to subscriber queue: {e}")
        self.subscribers.clear()

    # Event types that are ephemeral and should NOT be stored in the replay buffer.
    _EPHEMERAL_TYPES = frozenset(('ping', 'keepalive', 'heartbeat', 'connected'))

    async def broadcast(self, event_type: str, data: Any):
        """Broadcast an event to all SSE subscribers AND WebSocket clients.

        Non-ephemeral events are pushed to the AgentEventBuffer exactly once
        (regardless of how many SSE connections are open) and the assigned
        sequence number is embedded as an SSE ``id:`` field so browsers can
        automatically send ``Last-Event-ID`` on reconnect.
        """
        raw_data = json.dumps({'type': event_type, 'data': data, 'timestamp': time.time()})
        raw_sse = f"data: {raw_data}\n\n"

        if event_type not in self._EPHEMERAL_TYPES:
            try:
                from teaming24.api.event_buffer import get_event_buffer
                seq = get_event_buffer().push(raw_sse)
                message = f"id: {seq}\n{raw_sse}"
            except Exception as exc:
                logger.warning("Failed to persist SSE event to replay buffer: %s", exc, exc_info=True)
                message = raw_sse
        else:
            message = raw_sse

        # Send to all SSE subscribers (remove dead/slow ones)
        dead_subscribers = []
        for queue in self.subscribers:
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.warning("Subscriber queue full, dropping slow subscriber")
                dead_subscribers.append(queue)
            except Exception as e:
                logger.debug(f"Error broadcasting to subscriber: {e}")
                dead_subscribers.append(queue)

        for queue in dead_subscribers:
            if queue in self.subscribers:
                self.subscribers.remove(queue)

        # Also push to WebSocket hub (if available)
        try:
            from teaming24.communication.websocket import get_ws_hub
            hub = get_ws_hub()
            if hub.client_count > 0:
                await hub.broadcast(event_type, data if isinstance(data, dict) else {"data": data})
        except Exception as e:
            logger.warning(f"Failed to broadcast to subscriber: {e}")
            pass
