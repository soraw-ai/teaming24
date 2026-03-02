"""
Session management for Teaming24.

This package provides conversation session lifecycle, routing, isolation,
context window tracking, and JSONL transcript storage. It is the central
hub for managing multi-turn conversations between peers and agents.

What the session package provides
--------------------------------
- **Lifecycle**: Session creation, expiration, reset, and cleanup via
  ``SessionManager`` and ``SessionStore``.
- **Routing**: Session resolution by channel, peer, agent, and scope
  (main, per-peer, per-channel-peer).
- **Context tracking**: Per-session token accounting and auto-compaction
  via ``SessionContext`` when approaching model context limits.
- **Transcripts**: Append-only JSONL storage for reliable replay and
  archival via ``TranscriptWriter`` and ``compact_transcript``.

Quick start
-----------
::

    from teaming24.session import SessionManager, SessionContext, TranscriptWriter

    # Resolve or create a session for a peer
    manager = SessionManager()
    session = manager.get_or_create(channel="webchat", peer_id="user123")

    # Track context and guard before LLM calls
    ctx = SessionContext(session, model="gpt-4o")
    ctx.record_input(user_message)
    messages = await ctx.guard(messages)  # auto-compacts if needed
    ctx.record_output(assistant_reply)

    # Persist transcript for replay
    writer = TranscriptWriter(session.id)
    writer.append(msg)

How modules plug together
--------------------------
- ``SessionStore``: SQLite persistence for sessions and messages.
- ``SessionManager``: Uses ``SessionStore``; resolves sessions by routing
  key; handles reset triggers and idle cleanup.
- ``SessionContext``: Wraps ``teaming24.agent.context``; bound to a
  ``Session``; persists token stats in ``session.metadata``.
- ``TranscriptWriter``: Writes to ``~/.teaming24/transcripts/{session_id}.jsonl``.
- ``compact_transcript``: Session-level compaction (summarize old + keep
  recent); used when building messages from stored transcripts.

Exports
-------
- ``Session``: Dataclass for a conversation session (id, key, channel,
  peer_id, metadata, etc.).
- ``SessionMessage``: Dataclass for a single message (id, role, content,
  timestamp, metadata).
- ``SessionStore``: SQLite-backed session and message persistence.
- ``SessionManager``: Session lifecycle and routing.
- ``SessionContext``: Per-session context window and token tracking.
- ``TokenStats``: Running token counters (input, output, context,
  compaction_count).
- ``TranscriptWriter``: Append-only JSONL writer for session transcripts.
- ``compact_transcript``: Summarize old messages, return (summary, recent).
"""

from teaming24.session.compaction import TranscriptWriter, compact_transcript
from teaming24.session.context import SessionContext, TokenStats
from teaming24.session.manager import SessionManager
from teaming24.session.store import SessionStore
from teaming24.session.types import Session, SessionMessage

__all__ = [
    "Session",
    "SessionContext",
    "SessionManager",
    "SessionMessage",
    "SessionStore",
    "TokenStats",
    "TranscriptWriter",
    "compact_transcript",
]
