"""
Session data types.

Framework-agnostic session and message models used by the SessionStore,
SessionManager, and channel adapters.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from teaming24.utils.ids import prefixed_id, random_hex


def _new_id() -> str:
    return prefixed_id("sess_", 12, separator="")


@dataclass
class Session:
    """A conversation session between a peer and an agent."""
    id: str = field(default_factory=_new_id)
    key: str = ""                       # routing key (channel:peer:agent)
    agent_id: str = "main"
    channel: str = "webchat"            # "webchat", "telegram", "slack", "discord"
    peer_id: str = ""                   # sender identifier
    peer_kind: str = "direct"           # "direct" | "group"
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def touch(self) -> None:
        """Update the last-activity timestamp."""
        self.updated_at = time.time()


@dataclass
class SessionMessage:
    """A single message within a session."""
    id: str = field(default_factory=lambda: random_hex(16))
    session_id: str = ""
    role: str = "user"                  # "user" | "assistant" | "system"
    content: str = ""
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)
