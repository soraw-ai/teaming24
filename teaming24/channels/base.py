"""
Channel adapter base interface and InboundMessage type.

Every messaging platform (Telegram, Slack, Discord, WebChat) implements
the ``ChannelAdapter`` ABC.  The adapter normalises platform-specific
messages into ``InboundMessage`` objects and forwards them to a callback.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Callable, Coroutine
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# InboundMessage — the universal inbound message format
# ---------------------------------------------------------------------------

@dataclass
class InboundMessage:
    """A message received from any channel, normalised to a common shape."""
    channel: str                        # "telegram", "slack", "discord", "webchat"
    account_id: str = "default"         # which bot account received this
    peer_id: str = ""                   # sender identifier (platform-specific)
    peer_kind: str = "direct"           # "direct" | "group"
    text: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# Type for the inbound message callback.
OnMessage = Callable[[InboundMessage], Coroutine[Any, Any, str | None]]


# ---------------------------------------------------------------------------
# ChannelAdapter — ABC for messaging platform integrations
# ---------------------------------------------------------------------------

class ChannelAdapter(ABC):
    """Abstract base for a messaging channel.

    Subclasses implement ``start()`` / ``stop()`` to manage the
    platform connection (webhook, polling, etc.) and ``send_message()``
    to deliver responses.  Inbound messages are forwarded via the
    ``on_message`` callback.
    """

    channel_type: str = ""

    def __init__(self):
        self._on_message: OnMessage | None = None
        self._running: bool = False

    def on_message(self, callback: OnMessage) -> None:
        """Register the inbound message handler (set by ChannelManager)."""
        self._on_message = callback

    async def _dispatch(self, message: InboundMessage) -> str | None:
        """Forward an inbound message to the registered handler."""
        if self._on_message:
            return await self._on_message(message)
        return None

    @abstractmethod
    async def start(self) -> None:
        """Start listening for messages."""

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully shut down the channel."""

    @abstractmethod
    async def send_message(
        self,
        peer_id: str,
        text: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Send a message back to a peer on this channel."""
