"""
WebChat channel adapter.

Wraps the existing GUI WebSocket / REST connection as a channel so that
messages from the frontend can flow through the same binding router and
session manager as messages from external platforms.

This adapter does not start a separate listener — it provides a
``handle_webchat_message()`` entry point that the API server calls
when a chat message arrives via ``POST /api/chat/agent`` or the
WebSocket ``send`` method.
"""

from __future__ import annotations

from typing import Any

from teaming24.channels.base import ChannelAdapter, InboundMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class WebChatAdapter(ChannelAdapter):
    """Channel adapter for the built-in web GUI."""

    channel_type = "webchat"

    def __init__(self, account_id: str = "default"):
        super().__init__()
        self.account_id = account_id

    async def start(self) -> None:
        self._running = True
        logger.info("[WebChat] adapter ready (messages arrive via REST/WS)")

    async def stop(self) -> None:
        self._running = False

    async def send_message(self, peer_id: str, text: str,
                           metadata: dict[str, Any] | None = None) -> None:
        """Send a response back to the web GUI.

        In practice, webchat responses are streamed via SSE / WebSocket
        events.  This method is a no-op since delivery happens through
        the existing event pipeline.
        """
        pass

    async def handle_webchat_message(
        self,
        text: str,
        peer_id: str = "gui-user",
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """Entry point for messages coming from the web GUI.

        Called by the API server to route webchat messages through the
        channel / session pipeline.
        """
        inbound = InboundMessage(
            channel="webchat",
            account_id=self.account_id,
            peer_id=peer_id,
            peer_kind="direct",
            text=text,
            metadata=metadata or {},
        )
        return await self._dispatch(inbound)
