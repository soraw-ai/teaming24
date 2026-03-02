"""
Memory system for Teaming24.

Provides persistent, searchable agent memory backed by SQLite with
optional vector search (chromadb / sentence-transformers).

Architecture:
  - Markdown files as durable source of truth (~/.teaming24/memory/).
  - SQLite FTS5 for keyword search (BM25).
  - Optional vector index for semantic similarity (chromadb).
  - Hybrid search combines both scores.

Usage:
    from teaming24.memory import MemoryManager
    mm = MemoryManager()
    mm.save("agent-1", "The user prefers dark mode.", tags=["preference"])
    results = mm.search("user preferences", top_k=5)
"""

from pathlib import Path

DEFAULT_MEMORY_DIR = Path.home() / ".teaming24" / "memory"
MEMORY_RECALL_SNIPPET_CHARS = 700
MEMORY_CHAT_USER_PREVIEW = 2_000
MEMORY_CHAT_ASSISTANT_PREVIEW = 8_000
MEMORY_RECALL_MAX_COMPACTION_BLOCKS = 1

from teaming24.memory.contracts import MemoryUsageStatus
from teaming24.memory.manager import MemoryManager
from teaming24.memory.search import hybrid_search
from teaming24.memory.store import MemoryEntry, MemoryStore

__all__ = [
    "DEFAULT_MEMORY_DIR",
    "MemoryUsageStatus",
    "MemoryEntry",
    "MemoryManager",
    "MemoryStore",
    "MEMORY_CHAT_ASSISTANT_PREVIEW",
    "MEMORY_CHAT_USER_PREVIEW",
    "MEMORY_RECALL_MAX_COMPACTION_BLOCKS",
    "MEMORY_RECALL_SNIPPET_CHARS",
    "hybrid_search",
]
