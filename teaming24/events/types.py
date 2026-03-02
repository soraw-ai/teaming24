"""
Typed event definitions for Teaming24.

This module defines all event types used by the event bus. Each event type
has an associated payload schema (data dict keys). Publishers should include
the documented keys; subscribers may safely use .get() for optional fields.

Event categories:
    - Task lifecycle: task creation, execution, completion, failure
    - Agent execution: agent start/complete, token streaming
    - Human-in-the-loop: approval requests and resolutions
    - Sandbox: container lifecycle and output
    - Network/peer: connection status and peer events
    - Chat/streaming: messages and stream chunks
    - System: status and configuration changes

How to use::

    from teaming24.events.types import EventType

    # Publish with expected payload
    await bus.publish(EventType.TASK_COMPLETED, {
        "task_id": "t1",
        "result": {...},
    })

Extending: Add new enum members to EventType and document payload keys
in this module's docstring or inline comments.
"""

from __future__ import annotations

from enum import Enum


class EventType(str, Enum):
    """All event types emitted by the Teaming24 system."""

    # -------------------------------------------------------------------------
    # Task lifecycle — fired as tasks progress through execution
    # -------------------------------------------------------------------------

    # task_id, optional: name, description
    TASK_CREATED = "task_created"

    # task_id, optional: agent_id
    TASK_STARTED = "task_started"

    # task_id, step (int), optional: output, agent_id
    TASK_STEP = "task_step"

    # task_id, result (any), optional: output
    TASK_COMPLETED = "task_completed"

    # task_id, error (str or Exception), optional: traceback
    TASK_FAILED = "task_failed"

    # task_id, optional: reason
    TASK_CANCELLED = "task_cancelled"

    # -------------------------------------------------------------------------
    # Agent execution — fired during CrewAI/agent runs
    # -------------------------------------------------------------------------

    # task_id, agent_id, optional: agent_name
    AGENT_STARTED = "agent_started"

    # task_id, agent_id, optional: output, result
    AGENT_COMPLETED = "agent_completed"

    # task_id, agent_id, token (str) — streaming token from LLM
    AGENT_TOKEN = "agent_token"

    # -------------------------------------------------------------------------
    # Human-in-the-loop approval — fired when user input is required
    # -------------------------------------------------------------------------

    # task_id, request_id, message, options (list), optional: context
    APPROVAL_REQUEST = "approval_request"

    # task_id, request_id, approved (bool), optional: response, user_id
    APPROVAL_RESOLVED = "approval_resolved"

    # -------------------------------------------------------------------------
    # Sandbox events — fired for containerized execution environments
    # -------------------------------------------------------------------------

    # sandbox_id, optional: image, config
    SANDBOX_CREATED = "sandbox_created"

    # sandbox_id, optional: reason
    SANDBOX_DESTROYED = "sandbox_destroyed"

    # sandbox_id, event_type (str), optional: payload
    SANDBOX_EVENT = "sandbox_event"

    # sandbox_id, output (str), optional: stream (stdout/stderr)
    SANDBOX_OUTPUT = "sandbox_output"

    # -------------------------------------------------------------------------
    # Network / peer events — fired for WebRTC or peer connections
    # -------------------------------------------------------------------------

    # status (str), optional: peers (list), error
    NETWORK_STATUS = "network_status"

    # peer_id, optional: metadata
    PEER_CONNECTED = "peer_connected"

    # peer_id, optional: reason
    PEER_DISCONNECTED = "peer_disconnected"

    # -------------------------------------------------------------------------
    # Chat / streaming — fired for chat UI and SSE/WebSocket streams
    # -------------------------------------------------------------------------

    # message (str), optional: role, sender_id, timestamp
    CHAT_MESSAGE = "chat_message"

    # chunk (str), optional: task_id, stream_id
    STREAM_CHUNK = "stream_chunk"

    # optional: task_id, stream_id
    STREAM_END = "stream_end"

    # -------------------------------------------------------------------------
    # System — fired for app-wide status and config
    # -------------------------------------------------------------------------

    # status (str), optional: details (dict)
    SYSTEM_STATUS = "system_status"

    # key (str), value (any), optional: section
    CONFIG_CHANGED = "config_changed"
