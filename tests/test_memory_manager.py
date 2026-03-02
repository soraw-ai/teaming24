from pathlib import Path

from teaming24.config import get_config
from teaming24.memory.manager import MemoryManager
from teaming24.memory.store import MemoryStore


class _NoopVectorStore:
    available = False

    def add(self, *args, **kwargs):
        return None

    def search(self, *args, **kwargs):
        return []

    def delete(self, *args, **kwargs):
        return None


def _build_manager(tmp_path: Path) -> MemoryManager:
    return MemoryManager(
        store=MemoryStore(db_path=tmp_path / "memory.db"),
        vector_store=_NoopVectorStore(),
        memory_dir=tmp_path / "memory_md",
    )


def test_save_chat_turn_deduplicates_recent_entries(tmp_path: Path):
    mgr = _build_manager(tmp_path)

    first_id = mgr.save_chat_turn(
        agent_id="organizer-1",
        session_id="sess-1",
        task_id="task-1",
        user_message="Remember that the user prefers CSV exports.",
        assistant_message="I will default future exports to CSV.",
    )
    second_id = mgr.save_chat_turn(
        agent_id="organizer-1",
        session_id="sess-1",
        task_id="task-1",
        user_message="Remember that the user prefers CSV exports.",
        assistant_message="I will default future exports to CSV.",
    )

    assert first_id
    assert second_id == first_id
    entries = mgr.list_for_agent("organizer-1", limit=10)
    assert len(entries) == 1
    assert entries[0].source == "chat_turn"
    assert "CSV" in entries[0].content


def test_build_recall_context_returns_scoped_bundle(tmp_path: Path):
    mgr = _build_manager(tmp_path)
    mgr.save(
        agent_id="organizer-1",
        content="The user prefers CSV exports for monthly reports.",
        tags=["preference", "export"],
        source="manual",
    )
    mgr.save(
        agent_id="other-agent",
        content="Unrelated memory for another agent.",
        tags=["ignore"],
        source="manual",
    )

    recall = mgr.build_recall_context(
        "What export format does the user prefer?",
        agent_id="organizer-1",
        top_k=5,
        max_chars=2_000,
    )

    assert "Relevant long-term memory for this agent" in recall
    assert "CSV exports" in recall
    assert "other-agent" not in recall


def test_save_compacts_agent_memory_when_budget_is_exceeded(tmp_path: Path):
    mgr = _build_manager(tmp_path)

    for idx in range(12):
        mgr.save(
            agent_id="organizer-1",
            content=f"entry-{idx} " + ("x" * 22_000),
            tags=["bulk", f"idx:{idx}"],
            source="task",
        )

    entries = mgr.list_for_agent("organizer-1", limit=None)
    total_chars = sum(len(entry.content) for entry in entries)

    assert total_chars <= get_config().memory.persistent_max_chars
    assert any(entry.source == "compaction" for entry in entries)
    assert any("entry-11" in entry.content for entry in entries if entry.source != "compaction")


def test_build_recall_context_limits_compaction_results(tmp_path: Path):
    mgr = _build_manager(tmp_path)
    mgr.save(
        agent_id="organizer-1",
        content="The user prefers CSV exports for board reports and wants deterministic file names.",
        tags=["preference"],
        source="manual",
    )
    mgr.save(
        agent_id="organizer-1",
        content="[Compacted agent memory for organizer-1]\nOlder durable context summary:\n- Historical preference notes about CSV exports.",
        tags=["compaction_summary"],
        source="compaction",
        metadata={"compacted_count": 12, "compacted_sources": ["task", "chat_turn"]},
    )
    mgr.save(
        agent_id="organizer-1",
        content="[Compacted agent memory for organizer-1]\nOlder durable context summary:\n- Even older archive note about CSV exports.",
        tags=["compaction_summary"],
        source="compaction",
        metadata={"compacted_count": 24, "compacted_sources": ["task", "compaction"]},
    )

    recall = mgr.build_recall_context(
        "What export format does the user prefer?",
        agent_id="organizer-1",
        top_k=3,
        max_chars=4_000,
    )

    assert "CSV exports" in recall
    assert recall.count("source=compaction") <= 1


def test_get_usage_status_reports_budget_and_recent_compaction(tmp_path: Path):
    mgr = _build_manager(tmp_path)
    agent_id = "agent-status"

    mgr.save(agent_id=agent_id, content="x" * 120_000, source="manual")
    mgr.save(agent_id=agent_id, content="y" * 120_000, source="manual")

    status = mgr.get_usage_status(agent_id)

    assert status.agent_id == agent_id
    assert status.entry_count >= 1
    assert status.max_chars == int(get_config().memory.persistent_max_chars)
    assert status.max_tokens >= status.total_tokens
    assert status.remaining_chars >= 0
    assert 0.0 <= float(status.usage_ratio) <= 1.0
    assert status.last_compacted_at >= 0
