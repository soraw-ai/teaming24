"""
Telegram channel adapter.

Uses the ``python-telegram-bot`` library for receiving and sending messages.
The adapter runs the bot in polling mode and normalises Telegram messages
into ``InboundMessage`` objects.

Requires: ``pip install python-telegram-bot``
"""

from __future__ import annotations

from typing import Any

from teaming24.channels.base import ChannelAdapter, InboundMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class TelegramAdapter(ChannelAdapter):
    """Telegram bot channel adapter."""

    channel_type = "telegram"

    def __init__(self, account_id: str = "default", bot_token: str = ""):
        super().__init__()
        self.account_id = account_id
        self.bot_token = bot_token
        self._app = None

    async def start(self) -> None:
        if not self.bot_token:
            self._running = False
            logger.warning("[Telegram] no bot_token configured, skipping")
            return

        try:
            from telegram.ext import ApplicationBuilder, MessageHandler, filters
        except ImportError:
            self._running = False
            logger.error(
                "[Telegram] python-telegram-bot not installed. "
                "Install with: pip install python-telegram-bot"
            )
            return

        self._app = ApplicationBuilder().token(self.bot_token).build()

        async def _handle_message(update, context):
            message = update.effective_message
            if not message or not message.text:
                return

            chat = update.effective_chat
            is_group = chat.type in ("group", "supergroup")
            peer_id = str(chat.id)

            inbound = InboundMessage(
                channel="telegram",
                account_id=self.account_id,
                peer_id=peer_id,
                peer_kind="group" if is_group else "direct",
                text=message.text,
                metadata={
                    "telegram_user_id": str(update.effective_user.id) if update.effective_user else "",
                    "telegram_username": (update.effective_user.username or "") if update.effective_user else "",
                    "chat_title": chat.title or "",
                    "message_id": message.message_id,
                },
            )

            response = await self._dispatch(inbound)
            if response:
                await message.reply_text(response)

        self._app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, _handle_message))
        await self._app.initialize()
        await self._app.start()
        await self._app.updater.start_polling()
        self._running = True
        logger.info("[Telegram] adapter started (account=%s)", self.account_id)

    async def stop(self) -> None:
        if self._app:
            await self._app.updater.stop()
            await self._app.stop()
            await self._app.shutdown()
        self._running = False
        logger.info("[Telegram] adapter stopped")

    async def send_message(self, peer_id: str, text: str,
                           metadata: dict[str, Any] | None = None) -> None:
        if self._app and self._app.bot:
            await self._app.bot.send_message(chat_id=int(peer_id), text=text)
