"""
Slack channel adapter.

Uses the ``slack-bolt`` library in async socket mode for receiving
events and the Slack Web API for sending messages.

Requires: ``pip install slack-bolt``
"""

from __future__ import annotations

import asyncio
from typing import Any

from teaming24.channels.base import ChannelAdapter, InboundMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class SlackAdapter(ChannelAdapter):
    """Slack bot channel adapter (Socket Mode)."""

    channel_type = "slack"

    def __init__(self, account_id: str = "default",
                 bot_token: str = "", app_token: str = ""):
        super().__init__()
        self.account_id = account_id
        self.bot_token = bot_token
        self.app_token = app_token
        self._handler = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.bot_token or not self.app_token:
            self._running = False
            logger.warning("[Slack] bot_token or app_token missing, skipping")
            return

        try:
            from slack_bolt.adapter.socket_mode.async_handler import AsyncSocketModeHandler
            from slack_bolt.async_app import AsyncApp
        except ImportError:
            self._running = False
            logger.error(
                "[Slack] slack-bolt not installed. "
                "Install with: pip install slack-bolt"
            )
            return

        app = AsyncApp(token=self.bot_token)

        @app.event("message")
        async def _handle_message(event, say):
            text = event.get("text", "")
            if not text:
                return

            channel_id = event.get("channel", "")
            channel_type = event.get("channel_type", "im")
            user_id = event.get("user", "")

            inbound = InboundMessage(
                channel="slack",
                account_id=self.account_id,
                peer_id=channel_id,
                peer_kind="group" if channel_type in ("channel", "group") else "direct",
                text=text,
                metadata={
                    "slack_user_id": user_id,
                    "slack_channel_type": channel_type,
                    "slack_ts": event.get("ts", ""),
                },
            )

            response = await self._dispatch(inbound)
            if response:
                await say(response)

        self._handler = AsyncSocketModeHandler(app, self.app_token)
        self._task = asyncio.create_task(self._handler.start_async())
        self._running = True
        logger.info("[Slack] adapter started (account=%s)", self.account_id)

    async def stop(self) -> None:
        if self._handler:
            await self._handler.close_async()
        if self._task:
            self._task.cancel()
        self._running = False
        logger.info("[Slack] adapter stopped")

    async def send_message(self, peer_id: str, text: str,
                           metadata: dict[str, Any] | None = None) -> None:
        try:
            from slack_sdk.web.async_client import AsyncWebClient
            client = AsyncWebClient(token=self.bot_token)
            await client.chat_postMessage(channel=peer_id, text=text)
        except Exception as exc:
            logger.error("[Slack] send_message failed: %s", exc)
