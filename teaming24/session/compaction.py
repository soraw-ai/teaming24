"""
JSONL transcript storage and session compaction helpers.

What JSONL transcripts are and why they're used
-----------------------------------------------
Transcripts are stored as append-only JSONL (one JSON object per line).
Each line is a self-contained record: either a message (id, role, content,
timestamp, metadata) or an event (event, timestamp, data). This format
enables reliable replay (read line-by-line, no parsing of nested structure),
crash safety (each append is atomic), and easy archival/streaming.

How compaction works
--------------------
``compact_transcript`` splits messages into "old" and "recent". Older
messages are summarized into a single text block (role + content preview);
recent messages are kept verbatim. The summary can be prepended as context
when rebuilding the message list for the LLM, reducing token usage while
preserving recent conversational context.

File format
-----------
- Default path: ``~/.teaming24/transcripts/{session_id}.jsonl``
- Message record: ``{"id", "session_id", "role", "content", "timestamp", "metadata"}``
- Event record: ``{"event": "<type>", "timestamp": <float>, "data": {...}}``

Usage examples
--------------
::

    # TranscriptWriter
    writer = TranscriptWriter(session_id="sess_abc123")
    writer.append(msg)                    # append SessionMessage
    writer.append_event("compaction", {"count": 5})
    messages = writer.read_messages()     # load only message records

    # compact_transcript
    summary, recent = compact_transcript(messages, keep_recent=4)
    # Use summary as system context, recent as full messages
"""

from __future__ import annotations

import json
import time
from pathlib import Path

from teaming24.session.types import SessionMessage
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_TRANSCRIPT_DIR = Path.home() / ".teaming24" / "transcripts"


class TranscriptWriter:
    """Append-only JSONL writer for a single session transcript.

    Writes to ``{base_dir}/{session_id}.jsonl``. Supports both message
    records (SessionMessage) and event records (compaction, reset, etc.).

    Usage example::

        writer = TranscriptWriter("sess_abc123")
        writer.append(SessionMessage(session_id="sess_abc123", role="user", content="Hi"))
        writer.append_event("compaction", {"messages_compacted": 10})
        records = writer.read_all()
    """

    def __init__(self, session_id: str, base_dir: Path | None = None):
        self.session_id = session_id
        self.dir = (base_dir or DEFAULT_TRANSCRIPT_DIR)
        self.dir.mkdir(parents=True, exist_ok=True)
        self.path = self.dir / f"{session_id}.jsonl"

    def append(self, msg: SessionMessage) -> None:
        """Append a single message to the JSONL transcript.

        Serializes the message as a JSON object (id, session_id, role,
        content, timestamp, metadata) and appends one line to the file.
        """
        record = {
            "id": msg.id,
            "session_id": msg.session_id,
            "role": msg.role,
            "content": msg.content,
            "timestamp": msg.timestamp,
            "metadata": msg.metadata,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def append_event(self, event_type: str, data: dict) -> None:
        """Append a non-message event (compaction, reset, etc.).

        Writes a record with keys ``event``, ``timestamp``, and ``data``.
        Used for audit trail and replay debugging.
        """
        record = {
            "event": event_type,
            "timestamp": time.time(),
            "data": data,
        }
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    def read_all(self) -> list[dict]:
        """Read all records from the transcript.

        Returns both message and event records in order. Skips malformed
        lines. Returns empty list if the file does not exist.
        """
        if not self.path.exists():
            return []
        records = []
        with open(self.path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        logger.warning("Invalid JSON line in compaction store %s; skipping line", self.path)
                        continue
        return records

    def read_messages(self) -> list[SessionMessage]:
        """Read only message records (skip events) and return as SessionMessage.

        Filters records that have both ``role`` and ``content`` keys,
        converting them to ``SessionMessage`` instances.
        """
        messages = []
        for rec in self.read_all():
            if "role" in rec and "content" in rec:
                messages.append(SessionMessage(
                    id=rec.get("id", ""),
                    session_id=rec.get("session_id", self.session_id),
                    role=rec["role"],
                    content=rec["content"],
                    timestamp=rec.get("timestamp", 0.0),
                    metadata=rec.get("metadata", {}),
                ))
        return messages

    @property
    def exists(self) -> bool:
        """True if the transcript file exists on disk."""
        return self.path.exists()

    @property
    def line_count(self) -> int:
        """Return the number of lines in the transcript file."""
        if not self.path.exists():
            return 0
        with open(self.path, encoding="utf-8") as f:
            return sum(1 for _ in f)


def compact_transcript(
    messages: list[SessionMessage],
    keep_recent: int = 4,
) -> tuple[str, list[SessionMessage]]:
    """Produce a text summary of older messages and return (summary, recent).

    Algorithm: Split messages into ``old = messages[:-keep_recent]`` and
    ``recent = messages[-keep_recent:]``. Build a summary string from old
    messages (role + first 300 chars of content per message, up to 50
    messages). Return ``(summary, recent)``. If there are too few messages
    to split (len <= keep_recent + 1), returns ``("", messages)`` unchanged.

    This is the session-level compaction — it operates on
    ``SessionMessage`` objects rather than raw LLM message dicts.

    Example::

        messages = [m1, m2, m3, m4, m5, m6]  # 6 messages
        summary, recent = compact_transcript(messages, keep_recent=2)
        # summary = "[Compacted — 4 earlier messages]\\n[user] ...\\n..."
        # recent = [m5, m6]
    """
    if len(messages) <= keep_recent + 1:
        return "", messages

    old = messages[:-keep_recent]
    recent = messages[-keep_recent:]

    lines = []
    for m in old:
        preview = m.content[:300].replace("\n", " ")
        lines.append(f"[{m.role}] {preview}")

    summary = (
        f"[Compacted — {len(old)} earlier messages]\n"
        + "\n".join(lines[:50])
    )

    logger.info(
        "[Compaction] %d messages → summary (%d chars) + %d recent",
        len(messages), len(summary), len(recent),
    )
    return summary, recent
