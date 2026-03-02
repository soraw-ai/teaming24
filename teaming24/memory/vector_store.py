"""
Optional vector store for semantic memory search.

Uses chromadb when available; falls back to a no-op stub so the rest
of the memory system works without it.

Install: ``pip install chromadb``
"""

from __future__ import annotations

from pathlib import Path

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_PERSIST_DIR = Path.home() / ".teaming24" / "memory_vectors"

_HAS_CHROMA = False
try:
    import chromadb
    _HAS_CHROMA = True
except ImportError as e:
    logger.debug("ChromaDB not installed: %s", e)


class VectorStore:
    """Thin wrapper around ChromaDB for memory embeddings."""

    def __init__(self, persist_dir: Path | None = None, collection_name: str = "teaming24_memory"):
        self._persist_dir = persist_dir or DEFAULT_PERSIST_DIR
        self._collection_name = collection_name
        self._client = None
        self._collection = None

        if _HAS_CHROMA:
            try:
                self._persist_dir.mkdir(parents=True, exist_ok=True)
                self._client = chromadb.PersistentClient(
                    path=str(self._persist_dir),
                )
                self._collection = self._client.get_or_create_collection(
                    name=self._collection_name,
                    metadata={"hnsw:space": "cosine"},
                )
                logger.info("[VectorStore] ChromaDB initialised at %s", self._persist_dir)
            except Exception as exc:
                logger.warning("[VectorStore] ChromaDB init failed: %s — vector search disabled", exc)
                self._client = None
                self._collection = None
        else:
            logger.debug("[VectorStore] chromadb not installed — vector search disabled")

    @property
    def available(self) -> bool:
        return self._collection is not None

    def add(self, memory_id: str, content: str, agent_id: str = "",
            metadata: dict | None = None) -> None:
        """Add or update a memory in the vector index."""
        if not self.available:
            return
        try:
            meta = {"agent_id": agent_id}
            if metadata:
                meta.update(metadata)
            self._collection.upsert(
                ids=[memory_id],
                documents=[content],
                metadatas=[meta],
            )
        except Exception as exc:
            logger.warning("[VectorStore] add failed for %s: %s", memory_id, exc)

    def search(self, query: str, agent_id: str = "",
               top_k: int = 10) -> list[tuple[str, float]]:
        """Return (memory_id, distance) pairs sorted by similarity."""
        if not self.available:
            return []
        where = {"agent_id": agent_id} if agent_id else None
        try:
            results = self._collection.query(
                query_texts=[query],
                n_results=top_k,
                where=where,
            )
            ids = results.get("ids", [[]])[0]
            distances = results.get("distances", [[]])[0]
            return list(zip(ids, distances, strict=False))
        except Exception as exc:
            logger.warning("[VectorStore] search failed: %s", exc)
            return []

    def delete(self, memory_id: str) -> None:
        if self.available:
            try:
                self._collection.delete(ids=[memory_id])
            except Exception as e:
                logger.debug("VectorStore delete failed: %s", e)

    def clear_all(self) -> int:
        """Delete all indexed vectors in the current collection."""
        if not self.available:
            return 0
        try:
            rows = self._collection.get(include=[])
            ids = list(rows.get("ids", []) or [])
            if not ids:
                return 0
            self._collection.delete(ids=ids)
            return len(ids)
        except Exception as exc:
            logger.warning("[VectorStore] clear_all failed: %s", exc, exc_info=True)
            return 0
