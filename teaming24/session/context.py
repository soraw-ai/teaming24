"""
Session-level context window tracking.

Wraps the token counting and compaction utilities from
``teaming24.agent.context`` with per-session state. Each ``SessionContext``
is bound to a single ``Session`` and tracks cumulative token usage
(input + output), context size, and compaction count.

How SessionContext wraps token counting
----------------------------------------
- Uses ``count_tokens`` and ``count_message_tokens`` from
  ``teaming24.agent.context`` for token estimation.
- Maintains ``TokenStats`` (input_tokens, output_tokens, context_tokens,
  compaction_count) per session.
- Persists stats in ``session.metadata["token_stats"]`` for durability
  and observability.

Usage pattern (create → record_input → guard → record_output)
------------------------------------------------------------
::

    ctx = SessionContext(session, model="gpt-4o")
    ctx.record_input(user_text)           # record user message tokens
    messages = await ctx.guard(messages)  # auto-compact if near limit
    # ... call LLM with messages ...
    ctx.record_output(assistant_text)     # record assistant response tokens

How auto-compaction works
-------------------------
- ``should_compact(messages)`` checks if the message list approaches the
  model's context limit (via ``needs_compaction`` from agent.context).
- ``guard(messages)``: if not over limit, returns messages unchanged and
  updates ``context_tokens``. If over limit, calls ``compact_messages``
  (summarize older messages into a system message), increments
  ``compaction_count``, and persists updated stats.

Integration with the agent execution loop
-----------------------------------------
The agent loop typically: (1) loads transcript, (2) converts to LLM
message format, (3) calls ``ctx.guard(messages)`` before the LLM call,
(4) records input/output tokens after each exchange. This keeps each
session within its model's context window without manual intervention.

How to extend (custom compaction strategies)
--------------------------------------------
- Override ``guard`` to use a different compaction strategy.
- Or subclass and override ``should_compact`` to change the threshold.
- The underlying ``compact_messages`` in ``teaming24.agent.context``
  can be swapped for a custom implementation if needed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from teaming24.agent.context import (
    compact_messages,
    count_message_tokens,
    count_tokens,
    get_context_limit,
    needs_compaction,
)
from teaming24.session.types import Session
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class TokenStats:
    """Running token counters for a session.

    Attributes:
        input_tokens: Cumulative tokens from user messages.
        output_tokens: Cumulative tokens from assistant messages.
        context_tokens: Current token count of the message list (after
            last guard/compaction). Used for utilization checks.
        compaction_count: Number of times auto-compaction has run for
            this session.
    """
    input_tokens: int = 0
    output_tokens: int = 0
    context_tokens: int = 0
    compaction_count: int = 0

    @property
    def total(self) -> int:
        return self.input_tokens + self.output_tokens

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "context_tokens": self.context_tokens,
            "compaction_count": self.compaction_count,
        }

    @classmethod
    def from_dict(cls, d: dict) -> TokenStats:
        return cls(
            input_tokens=d.get("input_tokens", 0),
            output_tokens=d.get("output_tokens", 0),
            context_tokens=d.get("context_tokens", 0),
            compaction_count=d.get("compaction_count", 0),
        )


class SessionContext:
    """Context-window manager bound to a single session.

    Tracks token usage, checks context limits, and auto-compacts
    message lists when approaching the model's context window.

    Usage example::

        session = manager.get_or_create(channel="webchat", peer_id="u1")
        ctx = SessionContext(session, model="gpt-4o")

        ctx.record_input("Hello, help me with X")
        messages = build_messages_from_transcript(session)
        messages = await ctx.guard(messages)  # compact if needed
        response = await llm.chat(messages)
        ctx.record_output(response)
    """

    def __init__(self, session: Session, model: str = "gpt-4o"):
        self.session = session
        self.model = model
        self.stats = TokenStats.from_dict(session.metadata.get("token_stats", {}))

    # ------------------------------------------------------------------
    # Token accounting
    # ------------------------------------------------------------------

    def record_input(self, text: str) -> int:
        """Record tokens for an incoming user message.

        Increments ``stats.input_tokens``, persists to session metadata,
        and returns the token count.
        """
        n = count_tokens(text)
        self.stats.input_tokens += n
        self._persist()
        return n

    def record_output(self, text: str) -> int:
        """Record tokens for an outgoing assistant message.

        Increments ``stats.output_tokens``, persists to session metadata,
        and returns the token count.
        """
        n = count_tokens(text)
        self.stats.output_tokens += n
        self._persist()
        return n

    # ------------------------------------------------------------------
    # Context guard
    # ------------------------------------------------------------------

    def should_compact(self, messages: list[dict[str, Any]]) -> bool:
        """Check whether the message list is approaching the context limit.

        Delegates to ``needs_compaction`` from ``teaming24.agent.context``,
        which compares total message tokens (plus response reserve) against
        the model's context limit.
        """
        return needs_compaction(messages, self.model)

    async def guard(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Auto-compact *messages* if they exceed the context threshold.

        If ``should_compact(messages)`` is False, returns messages unchanged
        and updates ``context_tokens``. Otherwise, calls ``compact_messages``
        to summarize older messages, increments ``compaction_count``, and
        returns the compacted list. Always persists updated stats.
        """
        if not self.should_compact(messages):
            self.stats.context_tokens = count_message_tokens(messages)
            self._persist()
            return messages

        logger.info(
            "[SessionContext] compacting session %s (%d messages)",
            self.session.id, len(messages),
        )
        compacted = await compact_messages(messages, self.model)
        self.stats.compaction_count += 1
        self.stats.context_tokens = count_message_tokens(compacted)
        self._persist()
        return compacted

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def get_limit(self) -> int:
        """Return the context window size (in tokens) for the configured model."""
        return get_context_limit(self.model)

    def usage_pct(self) -> float:
        """Return context utilization as a fraction (0..1).

        Computed as ``context_tokens / get_limit()``, capped at 1.0.
        Returns 0.0 if the model limit is unknown or zero.
        """
        limit = self.get_limit()
        if limit <= 0:
            return 0.0
        return min(self.stats.context_tokens / limit, 1.0)

    def _persist(self) -> None:
        """Write token stats back into session metadata."""
        self.session.metadata["token_stats"] = self.stats.to_dict()
