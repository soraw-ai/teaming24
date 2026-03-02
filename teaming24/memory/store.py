"""
SQLite-backed memory store with FTS5 full-text search.

Each memory entry stores content, agent_id, tags, and a timestamp.
FTS5 provides efficient BM25-ranked keyword search without any
external dependency.
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from teaming24.utils.ids import prefixed_id
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_DB_PATH = Path.home() / ".teaming24" / "memory.db"


@dataclass
class MemoryEntry:
    """A single memory record."""
    id: str = ""
    agent_id: str = ""
    content: str = ""
    tags: list[str] = field(default_factory=list)
    source: str = ""            # "user", "task", "session", "manual"
    created_at: float = 0.0
    metadata: dict[str, Any] = field(default_factory=dict)
    score: float = 0.0          # search relevance score (populated by search)


class MemoryStore:
    """SQLite + FTS5 memory storage."""

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DEFAULT_DB_PATH
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
                CREATE TABLE IF NOT EXISTS memories (
                    id          TEXT PRIMARY KEY,
                    agent_id    TEXT NOT NULL,
                    content     TEXT NOT NULL,
                    tags        TEXT NOT NULL DEFAULT '[]',
                    source      TEXT NOT NULL DEFAULT '',
                    created_at  REAL NOT NULL,
                    metadata    TEXT NOT NULL DEFAULT '{}'
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_memories_agent
                ON memories(agent_id)
            """)
            # FTS5 virtual table for full-text search
            conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS memories_fts
                USING fts5(content, agent_id, tags, content=memories, content_rowid=rowid)
            """)
            # Triggers to keep FTS in sync
            conn.executescript("""
                CREATE TRIGGER IF NOT EXISTS memories_ai AFTER INSERT ON memories BEGIN
                    INSERT INTO memories_fts(rowid, content, agent_id, tags)
                    VALUES (new.rowid, new.content, new.agent_id, new.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_ad AFTER DELETE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, agent_id, tags)
                    VALUES ('delete', old.rowid, old.content, old.agent_id, old.tags);
                END;
                CREATE TRIGGER IF NOT EXISTS memories_au AFTER UPDATE ON memories BEGIN
                    INSERT INTO memories_fts(memories_fts, rowid, content, agent_id, tags)
                    VALUES ('delete', old.rowid, old.content, old.agent_id, old.tags);
                    INSERT INTO memories_fts(rowid, content, agent_id, tags)
                    VALUES (new.rowid, new.content, new.agent_id, new.tags);
                END;
            """)

    # ----- CRUD -----------------------------------------------------------

    def save(self, entry: MemoryEntry) -> str:
        if not entry.id:
            entry.id = prefixed_id("mem_", 12, separator="")
        if not entry.created_at:
            entry.created_at = time.time()
        with self._conn() as conn:
            conn.execute("""
                INSERT OR REPLACE INTO memories
                    (id, agent_id, content, tags, source, created_at, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (
                entry.id, entry.agent_id, entry.content,
                json.dumps(entry.tags), entry.source,
                entry.created_at, json.dumps(entry.metadata),
            ))
        return entry.id

    def get(self, memory_id: str) -> MemoryEntry | None:
        with self._conn() as conn:
            row = conn.execute(
                "SELECT * FROM memories WHERE id = ?", (memory_id,),
            ).fetchone()
            return self._row_to_entry(row) if row else None

    def list_by_agent(
        self,
        agent_id: str,
        limit: int | None = 50,
        *,
        ascending: bool = False,
    ) -> list[MemoryEntry]:
        order = "ASC" if ascending else "DESC"
        sql = f"SELECT * FROM memories WHERE agent_id = ? ORDER BY created_at {order}"
        params: list[Any] = [agent_id]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        with self._conn() as conn:
            rows = conn.execute(sql, tuple(params)).fetchall()
            return [self._row_to_entry(r) for r in rows]

    def delete(self, memory_id: str) -> None:
        with self._conn() as conn:
            conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))

    def clear_all(self, agent_id: str = "") -> int:
        """Delete all memories (optionally scoped to one agent)."""
        normalized_agent = str(agent_id or "").strip()
        with self._conn() as conn:
            if normalized_agent:
                count = int(
                    conn.execute(
                        "SELECT COUNT(*) FROM memories WHERE agent_id = ?",
                        (normalized_agent,),
                    ).fetchone()[0]
                )
                if count:
                    conn.execute("DELETE FROM memories WHERE agent_id = ?", (normalized_agent,))
                return count

            count = int(conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0])
            if count:
                conn.execute("DELETE FROM memories")
            return count

    # ----- FTS5 keyword search (BM25) ------------------------------------

    def search_fts(self, query: str, agent_id: str = "",
                   limit: int = 10) -> list[MemoryEntry]:
        """Full-text search using SQLite FTS5 BM25 ranking."""
        fts_query = self._build_fts_query(query)
        if not fts_query:
            return self._search_like(query, agent_id=agent_id, limit=limit)
        with self._conn() as conn:
            try:
                if agent_id:
                    rows = conn.execute("""
                        SELECT m.*, rank AS score
                        FROM memories_fts fts
                        JOIN memories m ON m.rowid = fts.rowid
                        WHERE memories_fts MATCH ? AND m.agent_id = ?
                        ORDER BY rank
                        LIMIT ?
                    """, (fts_query, agent_id, limit)).fetchall()
                else:
                    rows = conn.execute("""
                        SELECT m.*, rank AS score
                        FROM memories_fts fts
                        JOIN memories m ON m.rowid = fts.rowid
                        WHERE memories_fts MATCH ?
                        ORDER BY rank
                        LIMIT ?
                    """, (fts_query, limit)).fetchall()
            except sqlite3.OperationalError as exc:
                logger.warning("FTS query failed query=%r fts_query=%r: %s", query, fts_query, exc, exc_info=True)
                return self._search_like(query, agent_id=agent_id, limit=limit)
            entries = []
            for r in rows:
                e = self._row_to_entry(r)
                e.score = abs(float(r["score"])) if "score" in r.keys() else 0.0
                entries.append(e)
            return entries

    # ----- Helpers --------------------------------------------------------

    @staticmethod
    def _row_to_entry(row) -> MemoryEntry:
        return MemoryEntry(
            id=row["id"],
            agent_id=row["agent_id"],
            content=row["content"],
            tags=json.loads(row["tags"] or "[]"),
            source=row["source"],
            created_at=row["created_at"],
            metadata=json.loads(row["metadata"] or "{}"),
        )

    @staticmethod
    def _build_fts_query(query: str) -> str:
        tokens = [token for token in re.findall(r"[A-Za-z0-9_]+", str(query or "").lower()) if token]
        if not tokens:
            return ""
        unique_tokens: list[str] = []
        seen: set[str] = set()
        for token in tokens:
            if token in seen:
                continue
            seen.add(token)
            unique_tokens.append(f"{token}*")
        return " OR ".join(unique_tokens[:12])

    def _search_like(self, query: str, agent_id: str = "", limit: int = 10) -> list[MemoryEntry]:
        normalized = str(query or "").strip()
        if not normalized:
            return []
        pattern = f"%{normalized}%"
        with self._conn() as conn:
            if agent_id:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE agent_id = ? AND content LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (agent_id, pattern, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    """
                    SELECT * FROM memories
                    WHERE content LIKE ?
                    ORDER BY created_at DESC
                    LIMIT ?
                    """,
                    (pattern, limit),
                ).fetchall()
        entries = [self._row_to_entry(row) for row in rows]
        for index, entry in enumerate(entries):
            entry.score = max(0.0, 1.0 - (index * 0.05))
        return entries
