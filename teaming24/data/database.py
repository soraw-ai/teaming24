"""
SQLite Database for persistent storage.

Stores:
- Network settings
- Connection history
- Node configurations
- User preferences

TODO(refactor): Schema bootstrapping, settings, tasks, chat, and
    agent/skill CRUD have been extracted. The remaining network, wallet,
    and sandbox persistence should still be split into domain mixins.
"""

import json
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from teaming24.data import DEFAULT_DB_PATH
from teaming24.data.agent_skill_mixin import AgentSkillMixin
from teaming24.data.schema import apply_database_schema
from teaming24.data.settings_mixin import SettingsMixin
from teaming24.data.task_chat_mixin import TaskChatMixin
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

class Database(AgentSkillMixin, TaskChatMixin, SettingsMixin):
    """SQLite database for persistent storage."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    @contextmanager
    def _get_conn(self):
        """Context manager for database connections."""
        conn = sqlite3.connect(str(self.db_path))
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except Exception as e:
            logger.debug("Database transaction failed, rolling back: %s", e)
            conn.rollback()
            raise e
        finally:
            conn.close()

    def _init_db(self):
        """Initialize database schema."""
        with self._get_conn() as conn:
            apply_database_schema(conn, logger=logger, db_path=self.db_path)

    @staticmethod
    def _safe_json_loads(val: Any, default: Any = None) -> Any:
        """Parse JSON string with fallback to default on parse error or None."""
        if val is None:
            return default
        if isinstance(val, (dict, list)):
            return val
        try:
            return json.loads(val)
        except (json.JSONDecodeError, TypeError) as e:
            logger.debug("JSON decode fallback: %s", e)
            return default

    # ==================== Payment Records (AN re-payment skip) ====================

    def is_payment_recorded(self, parent_task_id: str, requester_id: str) -> bool:
        """Check if (parent_task_id, requester_id) was already paid (skip re-payment)."""
        if not parent_task_id or not requester_id:
            return False
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM payment_records WHERE parent_task_id = ? AND requester_id = ? LIMIT 1",
                (parent_task_id, requester_id),
            )
            return cursor.fetchone() is not None

    def save_payment_record(self, parent_task_id: str, requester_id: str):
        """Persist that (parent_task_id, requester_id) has been paid."""
        if not parent_task_id or not requester_id:
            return
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO payment_records (parent_task_id, requester_id, paid_at)
                VALUES (?, ?, ?)
                """,
                (parent_task_id, requester_id, time.time()),
            )
        logger.debug(f"Payment record saved: parent_task_id={parent_task_id}, requester={requester_id}")

    def is_expense_recorded(self, task_id: str, target_an: str) -> bool:
        """Check if we already recorded wallet expense for (task_id, target_an)."""
        if not task_id or not target_an:
            return False
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT 1 FROM wallet_expense_records WHERE task_id = ? AND target_an = ? LIMIT 1",
                (task_id, target_an),
            )
            return cursor.fetchone() is not None

    def save_expense_record(self, task_id: str, target_an: str, amount: float):
        """Record that we charged expense for (task_id, target_an)."""
        if not task_id or not target_an:
            return
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO wallet_expense_records (task_id, target_an, amount, recorded_at)
                VALUES (?, ?, ?, ?)
                """,
                (task_id, target_an, amount, time.time()),
            )
        logger.debug(f"Expense record saved: task_id={task_id}, target_an={target_an}, amount={amount}")

    # ==================== Connection History ====================

    def add_connection_history(self, node_data: dict[str, Any]):
        """Add or update connection history entry."""
        node_id = node_data.get('id')
        if not node_id:
            return

        now = time.time()
        caps_json = json.dumps(node_data.get('capabilities', []))
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO connection_history
                (id, name, alias, ip, port, wallet_address, agent_id, capability,
                 description, capabilities, last_connected, connect_count, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(id) DO UPDATE SET
                    name = excluded.name,
                    alias = excluded.alias,
                    ip = excluded.ip,
                    port = excluded.port,
                    wallet_address = excluded.wallet_address,
                    agent_id = excluded.agent_id,
                    capability = excluded.capability,
                    description = excluded.description,
                    capabilities = excluded.capabilities,
                    last_connected = excluded.last_connected,
                    connect_count = connect_count + 1
            """, (
                node_id,
                node_data.get('name'),
                node_data.get('alias'),
                node_data.get('ip'),
                node_data.get('port'),
                node_data.get('wallet_address'),
                node_data.get('agent_id'),
                node_data.get('capability'),
                node_data.get('description'),
                caps_json,
                now,
                now,
            ))

    def get_connection_history(self, limit: int = 20) -> list[dict[str, Any]]:
        """Get connection history ordered by last connected."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT * FROM connection_history
                ORDER BY last_connected DESC
                LIMIT ?
            """, (limit,))

            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry['capabilities'] = Database._safe_json_loads(entry.get('capabilities'), [])
                result.append(entry)
            return result

    def remove_connection_history(self, node_id: str):
        """Remove a connection history entry."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM connection_history WHERE id = ?", (node_id,))

    def clear_connection_history(self):
        """Clear all connection history."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM connection_history")

    # ==================== Connection Sessions ====================

    def add_connection_session(self, session_data: dict[str, Any]):
        """Append a connection session record."""
        session_id = session_data.get("session_id")
        if not session_id:
            return
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO connection_sessions
                (session_id, node_id, name, alias, ip, port, direction, started_at, ended_at,
                 duration_seconds, reason, metadata, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    session_id,
                    session_data.get("node_id"),
                    session_data.get("name"),
                    session_data.get("alias"),
                    session_data.get("ip"),
                    session_data.get("port"),
                    session_data.get("direction"),
                    session_data.get("started_at"),
                    session_data.get("ended_at"),
                    session_data.get("duration_seconds"),
                    session_data.get("reason"),
                    json.dumps(session_data.get("metadata") or {}),
                    session_data.get("created_at") or time.time(),
                ),
            )

    def get_connection_sessions(self, limit: int = 200, node_id: str | None = None) -> list[dict[str, Any]]:
        """Get connection sessions ordered by ended_at/started_at desc."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if node_id:
                cursor.execute(
                    """
                    SELECT * FROM connection_sessions
                    WHERE node_id = ?
                    ORDER BY COALESCE(ended_at, started_at) DESC
                    LIMIT ?
                    """,
                    (node_id, limit),
                )
            else:
                cursor.execute(
                    """
                    SELECT * FROM connection_sessions
                    ORDER BY COALESCE(ended_at, started_at) DESC
                    LIMIT ?
                    """,
                    (limit,),
                )
            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry["metadata"] = Database._safe_json_loads(entry.get("metadata"), {})
                result.append(entry)
            return result

    def clear_connection_sessions(self):
        """Clear all connection sessions."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM connection_sessions")

    # ==================== Known Nodes ====================

    def upsert_node(self, node_data: dict[str, Any]):
        """Insert or update a known node."""
        node_id = node_data.get('id')
        if not node_id:
            return

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR REPLACE INTO known_nodes
                (id, name, ip, port, type, wallet_address, agent_id, capability,
                 description, capabilities, price, region, status, last_seen, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM known_nodes WHERE id = ?), ?), ?)
            """, (
                node_id,
                node_data.get('name'),
                node_data.get('ip'),
                node_data.get('port'),
                node_data.get('type', 'wan'),
                node_data.get('wallet_address'),
                node_data.get('agent_id'),
                node_data.get('capability'),
                node_data.get('description'),
                json.dumps(node_data.get('capabilities', [])),
                node_data.get('price'),
                node_data.get('region'),
                node_data.get('status', 'offline'),
                time.time(),
                node_id,
                time.time(),
                json.dumps(node_data.get('metadata', {}))
            ))

    def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get a known node by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM known_nodes WHERE id = ?", (node_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result['capabilities'] = Database._safe_json_loads(result.get('capabilities'), [])
                result['metadata'] = Database._safe_json_loads(result.get('metadata'), {})
                return result
            return None

    def get_all_nodes(self, node_type: str | None = None) -> list[dict[str, Any]]:
        """Get all known nodes, optionally filtered by type."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if node_type:
                cursor.execute(
                    "SELECT * FROM known_nodes WHERE type = ? ORDER BY last_seen DESC",
                    (node_type,)
                )
            else:
                cursor.execute("SELECT * FROM known_nodes ORDER BY last_seen DESC")

            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry['capabilities'] = Database._safe_json_loads(entry.get('capabilities'), [])
                entry['metadata'] = Database._safe_json_loads(entry.get('metadata'), {})
                result.append(entry)
            return result

    def remove_node(self, node_id: str):
        """Remove a known node."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM known_nodes WHERE id = ?", (node_id,))

    # ==================== Marketplace Cache ====================

    def upsert_marketplace_cache_nodes(self, nodes: list[dict[str, Any]]):
        """Upsert marketplace node snapshots by node id."""
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            for node in nodes or []:
                node_id = str(node.get("id", "")).strip()
                if not node_id:
                    continue
                cursor.execute(
                    """
                    INSERT OR REPLACE INTO marketplace_cache (id, data, fetched_at)
                    VALUES (?, ?, ?)
                    """,
                    (node_id, json.dumps(node), now),
                )

    def get_marketplace_cache_nodes(
        self,
        max_age_seconds: float | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        """Load cached marketplace nodes, newest first."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if max_age_seconds is not None:
                min_fetched_at = time.time() - max(0.0, float(max_age_seconds))
                cursor.execute(
                    """
                    SELECT data FROM marketplace_cache
                    WHERE fetched_at >= ?
                    ORDER BY fetched_at DESC
                    LIMIT ?
                    """,
                    (min_fetched_at, max(1, int(limit))),
                )
            else:
                cursor.execute(
                    """
                    SELECT data FROM marketplace_cache
                    ORDER BY fetched_at DESC
                    LIMIT ?
                    """,
                    (max(1, int(limit)),),
                )

            result: list[dict[str, Any]] = []
            for row in cursor.fetchall():
                parsed = Database._safe_json_loads(row["data"], None)
                if isinstance(parsed, dict):
                    result.append(parsed)
            return result

    def remove_marketplace_cache_node(self, node_id: str):
        """Delete one marketplace cache row by node id."""
        if not node_id:
            return
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM marketplace_cache WHERE id = ?", (node_id,))

    def clear_marketplace_cache(self):
        """Delete all cached marketplace nodes."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM marketplace_cache")

    # ==================== Wallet Transactions ====================

    def save_wallet_transaction(self, tx: dict[str, Any]):
        """Persist a wallet transaction (snake_case fields)."""
        tx_id = tx.get("id")
        if not tx_id:
            return
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT OR IGNORE INTO wallet_transactions
                (id, timestamp, type, amount, task_id, task_name, description,
                 tx_hash, payer, payee, mode, network, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                tx_id,
                tx.get("timestamp"),
                tx.get("type"),
                tx.get("amount"),
                tx.get("task_id"),
                tx.get("task_name"),
                tx.get("description"),
                tx.get("tx_hash"),
                tx.get("payer"),
                tx.get("payee"),
                tx.get("mode"),
                tx.get("network"),
                time.time(),
            ))

    def get_wallet_transactions(self, limit: int = 1000) -> list[dict[str, Any]]:
        """Load wallet transactions ordered oldest-first (snake_case)."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM wallet_transactions ORDER BY timestamp ASC LIMIT ?",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]

    def clear_wallet_transactions(self):
        """Delete all wallet transactions."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM wallet_transactions")

    # ==================== Sandbox Events ====================

    def save_sandbox_event(self, sandbox_id: str, event: dict[str, Any]):
        """Persist a sandbox event."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                INSERT INTO sandbox_events
                (sandbox_id, event_type, event_data, timestamp, created_at)
                VALUES (?, ?, ?, ?, ?)
            """, (
                sandbox_id,
                event.get("type", "info"),
                json.dumps(event),
                event.get("timestamp", time.time()),
                time.time(),
            ))

    def get_sandbox_events(self, sandbox_id: str, limit: int = 500) -> list[dict[str, Any]]:
        """Load persisted events for a sandbox, oldest-first."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT event_data FROM sandbox_events WHERE sandbox_id = ? ORDER BY timestamp ASC LIMIT ?",
                (sandbox_id, limit)
            )
            result = []
            for row in cursor.fetchall():
                parsed = Database._safe_json_loads(row["event_data"], None)
                if parsed is not None:
                    result.append(parsed)
            return result

    def clear_sandbox_events(self, sandbox_id: str):
        """Delete all persisted events for a sandbox."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM sandbox_events WHERE sandbox_id = ?", (sandbox_id,))


# Global database instance
_db: Database | None = None
_db_lock = threading.Lock()


def get_database() -> Database:
    """Get or create the global database instance (thread-safe)."""
    global _db
    if _db is None:
        with _db_lock:
            if _db is None:
                _db = Database()
    return _db
