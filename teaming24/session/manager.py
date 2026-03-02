"""
Session manager — owns the session lifecycle.

Resolves inbound messages to sessions using the configured ``dm_scope``,
handles reset triggers, and provides transcript access.
"""

from __future__ import annotations

import time
from pathlib import Path

from teaming24.config import SessionConfig, get_config
from teaming24.session.store import SessionStore
from teaming24.session.types import Session, SessionMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class SessionManager:
    """Manages conversation sessions with routing and isolation."""

    def __init__(
        self,
        store: SessionStore | None = None,
        session_config: SessionConfig | None = None,
    ):
        cfg = session_config or get_config().session
        if store is not None:
            self.store = store
        else:
            self.store = SessionStore(
                db_path=Path(cfg.store_path).expanduser() if cfg.store_path else None
            )
        self.dm_scope: str = cfg.dm_scope
        self.idle_minutes: int = cfg.idle_minutes
        self.max_history: int = max(0, cfg.max_history)
        self.reset_triggers: list[str] = list(cfg.reset_triggers)

    # ----- Public API -----------------------------------------------------

    def get_or_create(
        self,
        channel: str,
        peer_id: str,
        agent_id: str = "main",
        peer_kind: str = "direct",
    ) -> Session:
        """Resolve (or create) a session for the given routing coordinates."""
        key = self._build_key(channel, peer_id, agent_id, peer_kind)
        session = self.store.get_by_key(key)

        if session and self._is_expired(session):
            logger.info("[SessionManager] session expired, resetting: %s", key)
            self.store.clear_transcript(session.id)
            session.created_at = time.time()
            session.updated_at = time.time()
            self.store.save(session)
            return session

        if session is None:
            session = Session(
                key=key,
                agent_id=agent_id,
                channel=channel,
                peer_id=peer_id,
                peer_kind=peer_kind,
            )
            self.store.save(session)
            logger.info("[SessionManager] new session: %s  key=%s", session.id, key)

        return session

    def record_message(self, session_id: str, role: str, content: str, **meta) -> SessionMessage:
        """Append a message to the session transcript."""
        msg = SessionMessage(
            session_id=session_id,
            role=role,
            content=content,
            metadata=meta,
        )
        self.store.add_message(msg)

        session = self.store.get(session_id)
        if session:
            session.touch()
            self.store.save(session)

        if self.max_history > 0:
            trimmed = self.store.trim_transcript(session_id, self.max_history)
            if trimmed > 0:
                logger.debug(
                    "[SessionManager] trimmed %d old messages (session=%s, max_history=%d)",
                    trimmed,
                    session_id,
                    self.max_history,
                )
        return msg

    def get_transcript(self, session_id: str, limit: int = 100):
        return self.store.get_transcript(session_id, limit)

    def reset(self, session_id: str) -> Session | None:
        """Reset a session (clear transcript, keep key)."""
        session = self.store.get(session_id)
        if session is None:
            return None
        self.store.clear_transcript(session_id)
        session.created_at = time.time()
        session.updated_at = time.time()
        self.store.save(session)
        logger.info("[SessionManager] session reset: %s", session_id)
        return session

    def is_reset_trigger(self, text: str) -> bool:
        """Check if text is a reset command (e.g. /new, /reset)."""
        stripped = text.strip().split()[0] if text.strip() else ""
        return stripped.lower() in (t.lower() for t in self.reset_triggers)

    def cleanup(self) -> int:
        """Remove sessions idle longer than ``idle_minutes``."""
        return self.store.cleanup_idle(self.idle_minutes)

    # ----- Key building ---------------------------------------------------

    def _build_key(self, channel: str, peer_id: str, agent_id: str, peer_kind: str) -> str:
        """Build a session routing key based on ``dm_scope``."""
        if peer_kind == "group":
            return f"agent:{agent_id}:{channel}:group:{peer_id}"

        if self.dm_scope == "main":
            return f"agent:{agent_id}:main"
        if self.dm_scope == "per-peer":
            return f"agent:{agent_id}:dm:{peer_id}"
        # per-channel-peer (default)
        return f"agent:{agent_id}:{channel}:dm:{peer_id}"

    def _is_expired(self, session: Session) -> bool:
        if self.idle_minutes <= 0:
            return False
        cutoff = time.time() - (self.idle_minutes * 60)
        return session.updated_at < cutoff
