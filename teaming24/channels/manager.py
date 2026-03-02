"""
Channel manager — orchestrates all channel adapters and routes messages.

Lifecycle:
  1. ``ChannelManager.start()`` starts all enabled channel adapters.
  2. Each adapter calls ``_handle_inbound()`` when a message arrives.
  3. The manager resolves a session, records the message, executes
     the task, and sends the response back through the originating
     adapter.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from teaming24.channels.base import ChannelAdapter, InboundMessage
from teaming24.channels.router import Binding, BindingMatch, BindingRouter, PeerMatch
from teaming24.config import (
    BindingConfig,
    get_config,
)
from teaming24.session.manager import SessionManager
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# Type for the execute callback (provided by the API layer).
ExecuteFn = Callable[[str, str, str], Coroutine[Any, Any, str]]
# Signature: async (session_id, agent_id, text) -> result


class ChannelManager:
    """Manages all channel adapters, routing, and session resolution."""

    def __init__(
        self,
        session_manager: SessionManager | None = None,
        execute_fn: ExecuteFn | None = None,
    ):
        self.session_manager = session_manager or SessionManager()
        self._execute_fn = execute_fn
        self._adapters: dict[str, ChannelAdapter] = {}
        self._binding_router = BindingRouter()

    # ----- Configuration --------------------------------------------------

    def configure_from_config(self) -> None:
        """Load channels, bindings, and adapters from teaming24 config."""
        cfg = get_config()

        # Build binding router
        bindings = self._build_bindings(cfg.bindings)
        self._binding_router.set_bindings(bindings)

        # Create channel adapters
        ch_cfg = cfg.channels

        # Telegram
        if ch_cfg.telegram.enabled:
            for acc_name, acc in (ch_cfg.telegram.accounts or {}).items():
                from teaming24.channels.telegram import TelegramAdapter
                token = getattr(acc, "bot_token", "") if not isinstance(acc, dict) else acc.get("bot_token", "")
                adapter = TelegramAdapter(account_id=acc_name, bot_token=token)
                self._adapters[f"telegram:{acc_name}"] = adapter

        # Slack
        if ch_cfg.slack.enabled:
            for acc_name, acc in (ch_cfg.slack.accounts or {}).items():
                from teaming24.channels.slack import SlackAdapter
                bot_token = getattr(acc, "bot_token", "") if not isinstance(acc, dict) else acc.get("bot_token", "")
                app_token = getattr(acc, "app_token", "") if not isinstance(acc, dict) else acc.get("app_token", "")
                adapter = SlackAdapter(account_id=acc_name, bot_token=bot_token, app_token=app_token)
                self._adapters[f"slack:{acc_name}"] = adapter

        # Discord
        if ch_cfg.discord.enabled:
            for acc_name, acc in (ch_cfg.discord.accounts or {}).items():
                from teaming24.channels.discord import DiscordAdapter
                token = getattr(acc, "token", "") if not isinstance(acc, dict) else acc.get("token", "")
                adapter = DiscordAdapter(account_id=acc_name, token=token)
                self._adapters[f"discord:{acc_name}"] = adapter

        # WebChat is required for the built-in GUI. Keep it enabled even if
        # config sets channels.webchat.enabled=false.
        webchat_cfg = getattr(ch_cfg, "webchat", None)
        if webchat_cfg and not getattr(webchat_cfg, "enabled", True):
            logger.warning(
                "[ChannelManager] channels.webchat.enabled=false ignored; "
                "WebChat is required for the GUI and remains enabled"
            )

        from teaming24.channels.webchat import WebChatAdapter
        self._adapters["webchat:default"] = WebChatAdapter()

        logger.info(
            "[ChannelManager] configured %d adapters: %s",
            len(self._adapters), list(self._adapters.keys()),
        )

    def set_execute_fn(self, fn: ExecuteFn) -> None:
        """Set the callback that executes agent tasks."""
        self._execute_fn = fn

    # ----- Lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start all channel adapters."""
        for key, adapter in self._adapters.items():
            adapter.on_message(self._handle_inbound)
            try:
                await adapter.start()
                if getattr(adapter, "_running", False):
                    logger.info("[ChannelManager] started adapter: %s", key)
                else:
                    logger.info(
                        "[ChannelManager] adapter not active (check config/deps): %s",
                        key,
                    )
            except Exception as exc:
                adapter._running = False
                logger.error("[ChannelManager] failed to start %s: %s", key, exc)

    async def stop(self) -> None:
        """Stop all channel adapters."""
        for key, adapter in self._adapters.items():
            try:
                await adapter.stop()
            except Exception as exc:
                logger.debug("[ChannelManager] error stopping %s: %s", key, exc)
            finally:
                adapter._running = False

    # ----- Inbound message pipeline ---------------------------------------

    async def _handle_inbound(self, message: InboundMessage) -> str | None:
        """Pipeline: route → session → execute → respond."""
        # 1. Route message to an agent
        agent_id = self._binding_router.route(message)

        # 2. Resolve (or create) a session
        session = self.session_manager.get_or_create(
            channel=message.channel,
            peer_id=message.peer_id,
            agent_id=agent_id,
            peer_kind=message.peer_kind,
        )

        # 3. Check for reset triggers
        if self.session_manager.is_reset_trigger(message.text):
            self.session_manager.reset(session.id)
            return "Session reset. Send a new message to start fresh."

        # 4. Record the inbound message
        self.session_manager.record_message(session.id, "user", message.text)

        # 5. Execute via the configured agent
        result = ""
        if self._execute_fn:
            try:
                result = await self._execute_fn(session.id, agent_id, message.text)
            except Exception as exc:
                logger.error("[ChannelManager] execute_fn error: %s", exc)
                result = f"Sorry, an error occurred: {exc}"

        # 6. Record the response
        if result:
            self.session_manager.record_message(session.id, "assistant", result)

        return result

    # ----- Helpers --------------------------------------------------------

    def get_webchat_adapter(self):
        """Return the WebChat adapter (for direct API integration)."""
        return self._adapters.get("webchat:default")

    @staticmethod
    def _build_bindings(binding_configs: list) -> list[Binding]:
        bindings = []
        for bc in (binding_configs or []):
            if isinstance(bc, BindingConfig):
                m = bc.match
                peer = None
                if m.peer:
                    peer = PeerMatch(kind=m.peer.kind, id=m.peer.id)
                bindings.append(Binding(
                    agent_id=bc.agent_id,
                    match=BindingMatch(
                        channel=m.channel,
                        account_id=m.account_id,
                        peer=peer,
                    ),
                ))
            elif isinstance(bc, dict):
                raw_match = bc.get("match")
                if isinstance(raw_match, dict):
                    match_data = raw_match
                else:
                    match_data = {
                        "channel": bc.get("channel", ""),
                        "account_id": bc.get("account_id", ""),
                        "peer": bc.get("peer"),
                    }

                peer_data = match_data.get("peer")
                if isinstance(peer_data, dict):
                    peer = PeerMatch(
                        kind=str(peer_data.get("kind", "")),
                        id=str(peer_data.get("id", "")),
                    )
                elif isinstance(peer_data, (str, int, float)):
                    peer = PeerMatch(id=str(peer_data))
                else:
                    peer = None

                bindings.append(Binding(
                    agent_id=bc.get("agent_id", "main"),
                    match=BindingMatch(
                        channel=match_data.get("channel", ""),
                        account_id=match_data.get("account_id", ""),
                        peer=peer,
                    ),
                ))
        return bindings
