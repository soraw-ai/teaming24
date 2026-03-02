from __future__ import annotations

import json
import time
from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class SettingsMixin:
    """Settings CRUD extracted from Database to keep the main DB class smaller."""

    def get_setting(self, key: str, default: Any = None) -> Any:
        """Get a setting value."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT value FROM settings WHERE key = ?", (key,))
            row = cursor.fetchone()
            if row:
                return type(self)._safe_json_loads(row["value"], row["value"])
            return default

    def set_setting(self, key: str, value: Any):
        """Set a setting value."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                (key, json.dumps(value), time.time()),
            )

    def set_settings(self, updates: dict[str, Any]):
        """Set multiple settings atomically in a single transaction."""
        if not updates:
            return
        now = time.time()
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT OR REPLACE INTO settings (key, value, updated_at)
                VALUES (?, ?, ?)
                """,
                [(str(key), json.dumps(value), now) for key, value in updates.items()],
            )

    def get_all_settings(self) -> dict[str, Any]:
        """Get all settings."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT key, value FROM settings")
            result = {}
            for row in cursor.fetchall():
                result[row["key"]] = type(self)._safe_json_loads(row["value"], row["value"])
            return result

    def delete_setting(self, key: str):
        """Delete a setting."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM settings WHERE key = ?", (key,))

    def clear_all_settings(self):
        """Clear all settings (for reset to defaults)."""
        with self._get_conn() as conn:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM settings")
        logger.info("All settings cleared")
