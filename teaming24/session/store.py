"""
SQLite-backed session store.

Reuses teaming24's existing Database pattern (context-managed connections,
auto-migration on init).
"""

from __future__ import annotations

import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

from teaming24.session.types import Session, SessionMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DB_PATH = Path.home() / ".teaming24" / "sessions.db"


class SessionStore:
    """SQLite persistence for sessions and session messages."""

    def __init__(self, db_path: Path | str | None = None):
        if db_path is None:
            self.db_path = DEFAULT_DB_PATH
        else:
            self.db_path = Path(db_path).expanduser()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _init_db(self):
        with self._conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS sessions (
                    id          TEXT PRIMARY KEY,
                    key         TEXT NOT NULL,
                    agent_id    TEXT NOT NULL DEFAULT 'main',
                    channel     TEXT NOT NULL DEFAULT 'webchat',
                    peer_id     TEXT NOT NULL DEFAULT '',
                    peer_kind   TEXT NOT NULL DEFAULT 'direct',
                    created_at  REAL NOT NULL,
                    updated_at  REAL NOT NULL,
                    metadata    TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_sessions_key
                ON sessions(key)
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS session_messages (
                    id          TEXT PRIMARY KEY,
                    session_id  TEXT NOT NULL,
                    role        TEXT NOT NULL DEFAULT 'user',
                    content     TEXT NOT NULL DEFAULT '',
                    timestamp   REAL NOT NULL,
                    metadata    TEXT NOT NULL DEFAULT '{}',
                    FOREIGN KEY (session_id) REFERENCES sessions(id)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_session_messages_session
                ON session_messages(session_id)
            """)

    # ----- Session CRUD ---------------------------------------------------

    def get_by_key(self, key: str) -> Session | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM sessions WHERE key = ? ORDER BY updated_at DESC LIMIT 1",
                (key,),
            ).fetchone()
            return self._row_to_session(row) if row else None

    def get(self, session_id: str) -> Session | None:
        with self._conn() as conn:
            row = conn.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
            return self._row_to_session(row) if row else None

    def save(self, session: Session) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO sessions
                    (id, key, agent_id, channel, peer_id, peer_kind,
                     created_at, updated_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                session.id, session.key, session.agent_id,
                session.channel, session.peer_id, session.peer_kind,
                session.created_at, session.updated_at,
                json.dumps(session.metadata),
            ))

    def list_sessions(self, limit: int = 50, channel: str | None = None) -> list[Session]:
        with self._conn() as conn:
            if channel:
                rows = conn.execute(
                    "SELECT * FROM sessions WHERE channel = ? ORDER BY updated_at DESC LIMIT ?",
                    (channel, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM sessions ORDER BY updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_session(r) for r in rows]

    def delete(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))
            conn.execute("DELETE FROM sessions WHERE id = ?", (session_id,))

    def clear_all(self) -> int:
        """Delete all sessions and transcript rows."""
        with self._conn() as conn:
            count = int(conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            if count:
                conn.execute("DELETE FROM session_messages")
                conn.execute("DELETE FROM sessions")
            return count

    # ----- Messages -------------------------------------------------------

    def add_message(self, msg: SessionMessage) -> None:
        with self._conn() as conn:
            conn.execute("""
                INSERT INTO session_messages (id, session_id, role, content, timestamp, metadata)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (
                msg.id, msg.session_id, msg.role,
                msg.content, msg.timestamp, json.dumps(msg.metadata),
            ))
            conn.execute(
                "UPDATE sessions SET updated_at = ? WHERE id = ?",
                (time.time(), msg.session_id),
            )

    def get_transcript(self, session_id: str, limit: int = 100) -> list[SessionMessage]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM session_messages WHERE session_id = ? ORDER BY timestamp ASC LIMIT ?",
                (session_id, limit),
            ).fetchall()
            return [self._row_to_message(r) for r in rows]

    def clear_transcript(self, session_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM session_messages WHERE session_id = ?", (session_id,))

    def trim_transcript(self, session_id: str, max_messages: int) -> int:
        """Keep only the latest ``max_messages`` messages for ``session_id``."""
        if max_messages <= 0:
            return 0
        with self._conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM session_messages WHERE session_id = ?",
                (session_id,),
            ).fetchone()[0]
            if total <= max_messages:
                return 0

            conn.execute(
                """
                DELETE FROM session_messages
                WHERE session_id = ?
                  AND id NOT IN (
                    SELECT id
                    FROM session_messages
                    WHERE session_id = ?
                    ORDER BY timestamp DESC, id DESC
                    LIMIT ?
                  )
                """,
                (session_id, session_id, max_messages),
            )
            return total - max_messages

    # ----- Cleanup --------------------------------------------------------

    def cleanup_idle(self, idle_minutes: int) -> int:
        """Delete sessions idle longer than *idle_minutes*. Returns count deleted."""
        if idle_minutes <= 0:
            return 0
        cutoff = time.time() - (idle_minutes * 60)
        with self._conn() as conn:
            count = conn.execute(
                "SELECT COUNT(*) FROM sessions WHERE updated_at < ?", (cutoff,),
            ).fetchone()[0]
            if count:
                conn.execute(
                    "DELETE FROM session_messages WHERE session_id IN "
                    "(SELECT id FROM sessions WHERE updated_at < ?)", (cutoff,))
                conn.execute(
                    "DELETE FROM sessions WHERE updated_at < ?", (cutoff,))
        return count

    # ----- Helpers --------------------------------------------------------

    @staticmethod
    def _safe_json(raw) -> dict:
        try:
            return json.loads(raw or "{}")
        except (json.JSONDecodeError, TypeError):
            logger.warning("Invalid JSON payload in session store row: %r", raw, exc_info=True)
            return {}

    @classmethod
    def _row_to_session(cls, row) -> Session:
        return Session(
            id=row["id"],
            key=row["key"],
            agent_id=row["agent_id"],
            channel=row["channel"],
            peer_id=row["peer_id"],
            peer_kind=row["peer_kind"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            metadata=cls._safe_json(row["metadata"]),
        )

    @classmethod
    def _row_to_message(cls, row) -> SessionMessage:
        return SessionMessage(
            id=row["id"],
            session_id=row["session_id"],
            role=row["role"],
            content=row["content"],
            timestamp=row["timestamp"],
            metadata=cls._safe_json(row["metadata"]),
        )
