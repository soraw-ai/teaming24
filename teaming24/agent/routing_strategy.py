"""
Routing strategy for the Agentic Node Workforce Pool.

Encapsulates the algorithm that decides which pool member should handle
a given task or subtask.  Keeping this logic in a dedicated module makes
it easy to swap scoring functions, add ML-based ranking, or implement
auction-style selection without touching the pool or tools code.

Usage::

    from teaming24.agent.routing_strategy import RoutingStrategy
    strategy = RoutingStrategy()
    ranked = strategy.rank(entries, required_capabilities=["python", "fastapi"])
    best = strategy.select(entries, required_capabilities=["python", "fastapi"])
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from teaming24.utils.logger import get_logger

if TYPE_CHECKING:
    from teaming24.agent.workforce_pool import AgenticNodeEntry

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Scoring weights — tune these to change behaviour
# ---------------------------------------------------------------------------

@dataclass
class RoutingWeights:
    """Weights used by the default scoring function.

    Attributes:
        capability_match: Weight for the fraction of required capabilities
            that the entry satisfies (0.0 – 1.0 range after normalization).
        local_preference: Bonus score for the local Coordinator.  Set to 0
            so local and remote ANs are treated as equal peers — routing
            is purely capability-driven.
        cost_penalty: Penalty multiplier for entries that have a non-zero
            cost.  Higher values penalize expensive remote ANs more.
    """
    capability_match: float = 1.0
    local_preference: float = 0.0   # Equal priority: local = remote
    cost_penalty: float = 0.1


# Default weights instance — importable for override
DEFAULT_WEIGHTS = RoutingWeights()


# ---------------------------------------------------------------------------
# Strategy implementation
# ---------------------------------------------------------------------------

class RoutingStrategy:
    """Select the best pool member for a set of required capabilities.

    The default algorithm scores each entry as:

        score = (capability_overlap / total_required) * w_capability
              + local_bonus * w_local
              - cost_factor * w_cost

    Then entries are sorted descending by score.

    Args:
        weights: Optional ``RoutingWeights`` to customize scoring.
    """

    def __init__(self, weights: RoutingWeights = None):
        self.weights = weights or DEFAULT_WEIGHTS

    # -- public API --------------------------------------------------------

    def rank(
        self,
        entries: list[AgenticNodeEntry],
        required_capabilities: list[str] | None = None,
    ) -> list[AgenticNodeEntry]:
        """Return *entries* sorted best-first by score.

        Entries that are offline or whose status is not ``"online"`` are
        filtered out before scoring.
        """
        required = set(required_capabilities or [])
        online = [e for e in entries if e.status == "online"]

        if not online:
            return []

        scored = [(self._score(e, required), e) for e in online]
        scored.sort(key=lambda t: t[0], reverse=True)

        if logger.isEnabledFor(10):  # DEBUG
            for s, e in scored:
                logger.debug(f"  routing score {s:.3f} → {e.name} ({e.entry_type})")

        return [e for _, e in scored]

    def select(
        self,
        entries: list[AgenticNodeEntry],
        required_capabilities: list[str] | None = None,
    ) -> AgenticNodeEntry | None:
        """Return the single best entry, or ``None`` if nothing is online."""
        ranked = self.rank(entries, required_capabilities)
        return ranked[0] if ranked else None

    # -- internals ---------------------------------------------------------

    def _score(self, entry: AgenticNodeEntry, required: set) -> float:
        """Compute a numeric score for *entry* given *required* capabilities."""
        w = self.weights

        # 1. Capability overlap ratio (0.0 – 1.0)
        if required:
            entry_caps = set(entry.capabilities)
            overlap = len(required & entry_caps)
            cap_ratio = overlap / len(required)
        else:
            # No specific requirements → every entry is equally capable
            cap_ratio = 1.0

        # 2. Local preference bonus
        local_bonus = 1.0 if entry.entry_type == "local" else 0.0

        # 3. Cost penalty (parse simple numeric cost strings)
        cost_factor = 0.0
        if entry.cost:
            try:
                cost_factor = float(entry.cost.split()[0])
            except (ValueError, IndexError):
                logger.debug("Failed to parse entry cost %r for %s", entry.cost, entry.id)
                cost_factor = 0.0

        score = (
            cap_ratio * w.capability_match
            + local_bonus * w.local_preference
            - cost_factor * w.cost_penalty
        )
        return score
