"""Typed contracts for durable agent memory status."""

from __future__ import annotations

from dataclasses import asdict, dataclass


@dataclass(frozen=True)
class MemoryUsageStatus:
    """Stable schema for backend memory-budget telemetry."""

    agent_id: str
    entry_count: int
    session_entry_count: int
    total_chars: int
    max_chars: int
    total_tokens: int
    max_tokens: int
    remaining_chars: int
    usage_ratio: float
    is_compacting: bool
    recently_compacted: bool
    last_compacted_at: float
    last_compaction_deleted_count: int
    last_compaction_before_chars: int
    last_compaction_after_chars: int
    last_compaction_summary_id: str
    last_saved_at: float

    def to_dict(self) -> dict[str, object]:
        """Serialize into a JSON-safe dict."""
        return asdict(self)
