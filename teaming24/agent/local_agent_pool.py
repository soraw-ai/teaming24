"""
Local Agent Workforce Pool — full set of all local Workers.

The pool contains ALL Workers (from the active scenario). Before each subtask,
the LocalAgentRouter selects a SUBSET from this pool. The Coordinator assigns
sub-subtasks only to the selected Workers.

This is the local counterpart to the AN Workforce Pool (AgenticNodeWorkforcePool).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class LocalAgentEntry:
    """One member of the Local Agent Workforce Pool (a Worker)."""

    id: str
    role: str
    capabilities: list[str] = field(default_factory=list)
    goal: str = ""
    status: str = "online"


# ---------------------------------------------------------------------------
# Pool implementation
# ---------------------------------------------------------------------------

class LocalAgentWorkforcePool:
    """Full pool of all local Workers.

    The pool contains ALL Workers. Before each subtask, the LocalAgentRouter
    selects a subset from this pool. get_pool() returns the full pool snapshot.

    Args:
        local_crew: The local ``LocalCrew`` instance (provides worker
            descriptions via get_worker_descriptions).
    """

    def __init__(self, local_crew: Any):
        self._local_crew = local_crew

    def get_pool(self) -> list[LocalAgentEntry]:
        """Build and return the current pool snapshot.

        Returns one entry per online Worker.
        """
        entries: list[LocalAgentEntry] = []

        if not self._local_crew:
            return entries

        worker_descs = []
        if hasattr(self._local_crew, "get_worker_descriptions"):
            worker_descs = self._local_crew.get_worker_descriptions()

        for wd in worker_descs:
            if not isinstance(wd, dict):
                continue
            role = wd.get("role", "Worker")
            caps = wd.get("capabilities", [])
            if not isinstance(caps, list):
                caps = [str(c) for c in caps] if caps else []
            goal = wd.get("goal", "")
            status = wd.get("status", "online")

            entries.append(LocalAgentEntry(
                id=role,
                role=role,
                capabilities=caps,
                goal=goal,
                status=status,
            ))

        return entries

    def describe(self) -> str:
        """Human-readable summary of the current pool.

        Suitable for injecting into the LocalAgentRouter prompt.
        """
        entries = self.get_pool()
        if not entries:
            return "Local Agent Workforce Pool is empty."

        lines = [f"Local Agent Workforce Pool ({len(entries)} Worker(s)):"]
        for i, e in enumerate(entries, 1):
            caps = ", ".join(e.capabilities) if e.capabilities else "general"
            goal_preview = (e.goal[:60] + "...") if len(e.goal) > 60 else e.goal
            lines.append(f"  {i}. {e.role}: {goal_preview} (capabilities: [{caps}])")
        return "\n".join(lines)
