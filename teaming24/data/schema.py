"""Database schema application helpers."""

from __future__ import annotations

from typing import Any

from teaming24.data import (
    AGENT_SCHEMA_MIGRATIONS,
    INDEX_STATEMENTS,
    SCHEMA_STATEMENTS,
    SKILL_SCHEMA_MIGRATIONS,
)


def apply_database_schema(conn: Any, *, logger: Any, db_path: Any) -> None:
    """Create tables, indexes, and run additive schema migrations."""
    cursor = conn.cursor()
    cursor.execute("PRAGMA journal_mode=WAL")

    for statement in SCHEMA_STATEMENTS:
        cursor.execute(statement)

    for statement in INDEX_STATEMENTS:
        cursor.execute(statement)

    existing_cols = {
        row[1] for row in cursor.execute("PRAGMA table_info(agents)").fetchall()
    }
    for col, typedef in AGENT_SCHEMA_MIGRATIONS:
        if col not in existing_cols:
            logger.debug("Schema migration: adding agents.%s", col)
            cursor.execute(f"ALTER TABLE agents ADD COLUMN {col} {typedef}")

    skill_cols = {
        row[1] for row in cursor.execute("PRAGMA table_info(skills)").fetchall()
    }
    for col, typedef in SKILL_SCHEMA_MIGRATIONS:
        if col not in skill_cols:
            logger.debug("Schema migration: adding skills.%s", col)
            cursor.execute(f"ALTER TABLE skills ADD COLUMN {col} {typedef}")

    logger.info("Database initialized at %s", db_path)
