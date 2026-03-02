"""
Hybrid search combining FTS5 (BM25) and vector similarity.

The final score for each memory is:
    score = alpha * bm25_norm + (1 - alpha) * vector_norm

Where ``alpha`` controls the keyword vs. semantic balance (default 0.5).
"""

from __future__ import annotations

from teaming24.memory.store import MemoryEntry, MemoryStore
from teaming24.memory.vector_store import VectorStore


def hybrid_search(
    query: str,
    store: MemoryStore,
    vector_store: VectorStore | None = None,
    agent_id: str = "",
    top_k: int = 10,
    alpha: float = 0.5,
) -> list[MemoryEntry]:
    """Run hybrid BM25 + vector search and return merged results.

    Args:
        query: Search query string.
        store: The SQLite memory store (always available).
        vector_store: Optional vector store for semantic search.
        agent_id: Filter to a specific agent (empty = all).
        top_k: Maximum results to return.
        alpha: Weight for keyword vs. vector (0 = pure vector, 1 = pure keyword).

    Returns:
        List of MemoryEntry objects with ``.score`` populated, sorted descending.
    """
    # 1. Keyword search (FTS5 / BM25)
    fts_results = store.search_fts(query, agent_id=agent_id, limit=top_k * 2)
    fts_map: dict[str, float] = {}
    fts_max = max((e.score for e in fts_results), default=1.0) or 1.0
    for e in fts_results:
        fts_map[e.id] = e.score / fts_max  # normalise to 0-1

    # 2. Vector search (if available)
    vec_map: dict[str, float] = {}
    if vector_store and vector_store.available:
        vec_results = vector_store.search(query, agent_id=agent_id, top_k=top_k * 2)
        if vec_results:
            max_dist = max(d for _, d in vec_results) or 1.0
            for mid, dist in vec_results:
                vec_map[mid] = 1.0 - (dist / max_dist)  # convert distance to similarity

    # 3. Merge scores
    all_ids = set(fts_map) | set(vec_map)
    scored: dict[str, float] = {}
    for mid in all_ids:
        kw = fts_map.get(mid, 0.0)
        vs = vec_map.get(mid, 0.0)
        scored[mid] = alpha * kw + (1.0 - alpha) * vs

    # 4. Build result list
    entry_cache: dict[str, MemoryEntry] = {e.id: e for e in fts_results}
    results: list[MemoryEntry] = []
    for mid, score in sorted(scored.items(), key=lambda x: x[1], reverse=True)[:top_k]:
        entry = entry_cache.get(mid)
        if entry is None:
            entry = store.get(mid)
        if entry:
            entry.score = score
            results.append(entry)

    return results
