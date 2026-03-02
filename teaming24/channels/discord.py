"""
Discord channel adapter.

Uses the ``discord.py`` library with intents for receiving messages
and sending responses.

Requires: ``pip install discord.py``
"""

from __future__ import annotations

import asyncio
from typing import Any

from teaming24.channels.base import ChannelAdapter, InboundMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class DiscordAdapter(ChannelAdapter):
    """Discord bot channel adapter."""

    channel_type = "discord"

    def __init__(self, account_id: str = "default", token: str = ""):
        super().__init__()
        self.account_id = account_id
        self.token = token
        self._client = None
        self._task: asyncio.Task | None = None

    async def start(self) -> None:
        if not self.token:
            self._running = False
            logger.warning("[Discord] no token configured, skipping")
            return

        try:
            import discord
        except ImportError:
            self._running = False
            logger.error(
                "[Discord] discord.py not installed. "
                "Install with: pip install discord.py"
            )
            return

        intents = discord.Intents.default()
        intents.message_content = True
        client = discord.Client(intents=intents)
        self._client = client

        adapter_self = self

        @client.event
        async def on_ready():
            logger.info("[Discord] logged in as %s", client.user)

        @client.event
        async def on_message(message):
            if message.author == client.user:
                return
            if not message.content:
                return

            is_dm = message.guild is None
            peer_id = str(message.channel.id)

            inbound = InboundMessage(
                channel="discord",
                account_id=adapter_self.account_id,
                peer_id=peer_id,
                peer_kind="direct" if is_dm else "group",
                text=message.content,
                metadata={
                    "discord_user_id": str(message.author.id),
                    "discord_username": str(message.author),
                    "guild_id": str(message.guild.id) if message.guild else "",
                    "guild_name": message.guild.name if message.guild else "",
                },
            )

            response = await adapter_self._dispatch(inbound)
            if response:
                # Split long messages (Discord limit: 2000 chars)
                for chunk in _split_message(response, 2000):
                    await message.channel.send(chunk)

        self._task = asyncio.create_task(client.start(self.token))
        self._running = True
        logger.info("[Discord] adapter started (account=%s)", self.account_id)

    async def stop(self) -> None:
        if self._client:
            await self._client.close()
        if self._task:
            self._task.cancel()
        self._running = False
        logger.info("[Discord] adapter stopped")

    async def send_message(self, peer_id: str, text: str,
                           metadata: dict[str, Any] | None = None) -> None:
        if not self._client:
            return
        try:
            channel = self._client.get_channel(int(peer_id))
            if channel is None:
                channel = await self._client.fetch_channel(int(peer_id))
            for chunk in _split_message(text, 2000):
                await channel.send(chunk)
        except Exception as exc:
            logger.error("[Discord] send_message failed: %s", exc)


def _split_message(text: str, max_len: int = 2000) -> list[str]:
    """Split text into chunks of at most *max_len* characters."""
    if len(text) <= max_len:
        return [text]
    chunks = []
    while text:
        chunks.append(text[:max_len])
        text = text[max_len:]
    return chunks
