"""Task and chat persistence mixin extracted from the core Database class."""

from __future__ import annotations

import json
import time
from typing import Any

from teaming24.utils.ids import prefixed_id, random_hex
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class TaskChatMixin:
    """CRUD for tasks, task steps, chat sessions, and chat messages."""

    def save_task(self, task_data: dict[str, Any]):
        """Save or update a task."""
        task_id = task_data.get("id")
        if not task_id:
            return
        metadata = dict(task_data.get("metadata") or {})
        if "executing_agents" in task_data:
            metadata["executing_agents"] = task_data["executing_agents"]

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO tasks
                (id, name, description, status, task_type, assigned_to, delegated_agents,
                 steps, result, error, cost, output_dir, created_at, started_at, completed_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        COALESCE((SELECT created_at FROM tasks WHERE id = ?), ?),
                        ?, ?, ?)
                """,
                (
                    task_id,
                    task_data.get("name"),
                    task_data.get("description"),
                    task_data.get("status", "pending"),
                    task_data.get("task_type", "local"),
                    task_data.get("assigned_to"),
                    json.dumps(task_data.get("delegated_agents", [])),
                    json.dumps(task_data.get("steps", [])),
                    task_data.get("result"),
                    task_data.get("error"),
                    json.dumps(task_data.get("cost", {})),
                    task_data.get("output_dir"),
                    task_id,
                    task_data.get("created_at", time.time()),
                    task_data.get("started_at"),
                    task_data.get("completed_at"),
                    json.dumps(metadata),
                ),
            )

    def get_task(self, task_id: str) -> dict[str, Any] | None:
        """Get a task by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM tasks WHERE id = ?", (task_id,))
            row = cursor.fetchone()
            if row:
                return self._parse_task_row(dict(row))
            return None

    def _parse_task_row(self, row: dict[str, Any]) -> dict[str, Any]:
        """Parse JSON fields in task row."""
        for field in ["delegated_agents", "steps", "cost", "metadata"]:
            default = {} if field in ["cost", "metadata"] else []
            row[field] = type(self)._safe_json_loads(row.get(field), default)
        meta = row.get("metadata")
        if isinstance(meta, dict) and "executing_agents" in meta:
            row["executing_agents"] = meta.get("executing_agents", [])
        return row

    def list_tasks(self, status: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        """List tasks, optionally filtered by status."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            if status:
                cursor.execute(
                    "SELECT * FROM tasks WHERE status = ? ORDER BY created_at DESC LIMIT ?",
                    (status, limit),
                )
            else:
                cursor.execute(
                    "SELECT * FROM tasks ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                )
            return [self._parse_task_row(dict(row)) for row in cursor.fetchall()]

    def delete_task(self, task_id: str):
        """Delete a task."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM task_steps WHERE task_id = ?", (task_id,))
            cursor.execute("DELETE FROM tasks WHERE id = ?", (task_id,))

    def clear_all_tasks(self):
        """Delete all tasks and their steps."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM task_steps")
            cursor.execute("DELETE FROM tasks")
        logger.info("All tasks cleared")

    def clear_all_data(self):
        """Delete all user data from every table (full reset)."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            for table in [
                "task_steps",
                "tasks",
                "agent_skills",
                "skills",
                "agents",
                "chat_messages",
                "chat_sessions",
                "settings",
                "connection_history",
                "connection_sessions",
                "known_nodes",
                "marketplace_cache",
                "wallet_transactions",
                "sandbox_events",
                "custom_tools",
                "payment_records",
            ]:
                try:
                    cursor.execute(f"DELETE FROM {table}")
                except Exception as exc:
                    logger.warning("Failed to clear table %s: %s", table, exc)
        logger.info("All data cleared (full reset)")

    def save_task_step(self, task_id: str, step_data: dict[str, Any]):
        """Save a task step."""
        step_id = step_data.get("id")
        if not step_id:
            step_id = f"{task_id}_{random_hex(12)}"

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO task_steps
                (id, task_id, agent_id, agent_name, action, content, thought,
                 observation, status, started_at, completed_at, tokens_used)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    step_id,
                    task_id,
                    step_data.get("agent_id"),
                    step_data.get("agent_name"),
                    step_data.get("action"),
                    step_data.get("content"),
                    step_data.get("thought"),
                    step_data.get("observation"),
                    step_data.get("status", "pending"),
                    step_data.get("started_at"),
                    step_data.get("completed_at"),
                    step_data.get("tokens_used", 0),
                ),
            )

    def get_task_steps(self, task_id: str) -> list[dict[str, Any]]:
        """Get all steps for a task."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM task_steps WHERE task_id = ? ORDER BY started_at ASC",
                (task_id,),
            )
            return [dict(row) for row in cursor.fetchall()]

    def save_chat_session(self, session_data: dict[str, Any]):
        """Save or update a chat session."""
        session_id = session_data.get("id")
        if not session_id:
            return

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO chat_sessions
                (id, title, mode, created_at, updated_at, metadata)
                VALUES (?, ?, ?,
                        COALESCE((SELECT created_at FROM chat_sessions WHERE id = ?), ?),
                        ?, ?)
                """,
                (
                    session_id,
                    session_data.get("title", "New Chat"),
                    session_data.get("mode", "chat"),
                    session_id,
                    session_data.get("created_at", time.time()),
                    time.time(),
                    json.dumps(session_data.get("metadata", {})),
                ),
            )

    def get_chat_session(self, session_id: str) -> dict[str, Any] | None:
        """Get a chat session by ID."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM chat_sessions WHERE id = ?", (session_id,))
            row = cursor.fetchone()
            if row:
                result = dict(row)
                result["metadata"] = type(self)._safe_json_loads(result.get("metadata"), {})
                return result
            return None

    def list_chat_sessions(self, limit: int = 50) -> list[dict[str, Any]]:
        """List all chat sessions."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM chat_sessions ORDER BY updated_at DESC LIMIT ?",
                (limit,),
            )
            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry["metadata"] = type(self)._safe_json_loads(entry.get("metadata"), {})
                result.append(entry)
            return result

    def delete_chat_session(self, session_id: str):
        """Delete a chat session and its messages."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM chat_messages WHERE session_id = ?", (session_id,))
            cursor.execute("DELETE FROM chat_sessions WHERE id = ?", (session_id,))

    def save_chat_message(self, session_id: str, message_data: dict[str, Any]):
        """Save a chat message."""
        message_id = message_data.get("id")
        if not message_id:
            message_id = prefixed_id("msg_", 12, separator="")

        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO chat_messages
                (id, session_id, role, content, task_id, steps, cost, is_task, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    message_id,
                    session_id,
                    message_data.get("role", "user"),
                    message_data.get("content", ""),
                    message_data.get("task_id"),
                    json.dumps(message_data.get("steps", [])),
                    json.dumps(message_data.get("cost", {})),
                    1 if message_data.get("is_task") else 0,
                    message_data.get("created_at") or message_data.get("timestamp", time.time()),
                ),
            )
            cursor.execute(
                "UPDATE chat_sessions SET updated_at = ? WHERE id = ?",
                (time.time(), session_id),
            )

    def get_chat_messages(self, session_id: str) -> list[dict[str, Any]]:
        """Get all messages for a chat session."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY created_at ASC",
                (session_id,),
            )
            result = []
            for row in cursor.fetchall():
                entry = dict(row)
                entry["steps"] = type(self)._safe_json_loads(entry.get("steps"), [])
                entry["cost"] = type(self)._safe_json_loads(entry.get("cost"), {})
                entry["is_task"] = bool(entry.get("is_task"))
                result.append(entry)
            return result
