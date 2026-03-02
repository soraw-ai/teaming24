"""
Binding router — routes inbound messages to agents.

Implements a "most-specific-wins" binding model:
  - Each binding specifies match criteria (channel, account_id, peer).
  - The binding with the highest specificity score that matches the
    inbound message wins.
  - If no binding matches, the default agent ID is used.

Scoring (additive):
  +1 for matching channel
  +2 for matching account_id
  +4 for matching peer.kind
  +8 for matching peer.id
"""

from __future__ import annotations

from dataclasses import dataclass, field

from teaming24.channels.base import InboundMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class PeerMatch:
    kind: str = ""      # "direct" | "group" | "" (any)
    id: str = ""        # specific peer id or "" (any)


@dataclass
class BindingMatch:
    channel: str = ""
    account_id: str = ""
    peer: PeerMatch | None = None


@dataclass
class Binding:
    agent_id: str = "main"
    match: BindingMatch = field(default_factory=BindingMatch)


class BindingRouter:
    """Routes inbound messages to agents. Most-specific binding wins."""

    def __init__(
        self,
        bindings: list[Binding] | None = None,
        default_agent_id: str = "main",
    ):
        self._bindings = bindings or []
        self._default_agent_id = default_agent_id

    def set_bindings(self, bindings: list[Binding]) -> None:
        self._bindings = bindings

    def route(self, message: InboundMessage) -> str:
        """Determine which agent should handle *message*.

        Returns the ``agent_id`` of the best-matching binding, or
        ``default_agent_id`` if nothing matches.
        """
        best_score = 0
        best_agent = self._default_agent_id

        for binding in self._bindings:
            score = self._score(binding, message)
            if score > best_score:
                best_score = score
                best_agent = binding.agent_id

        logger.debug(
            "[BindingRouter] route ch=%s peer=%s → agent=%s (score=%d)",
            message.channel, message.peer_id, best_agent, best_score,
        )
        return best_agent

    @staticmethod
    def _score(binding: Binding, message: InboundMessage) -> int:
        m = binding.match
        score = 0

        # Channel match (required for any positive score)
        if m.channel:
            if m.channel != message.channel:
                return 0
            score += 1

        # Account match
        if m.account_id:
            if m.account_id != message.account_id:
                return 0
            score += 2

        # Peer match
        if m.peer:
            if m.peer.kind:
                if m.peer.kind != message.peer_kind:
                    return 0
                score += 4
            if m.peer.id:
                if m.peer.id != message.peer_id:
                    return 0
                score += 8

        return score
