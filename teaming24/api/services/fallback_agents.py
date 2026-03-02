"""Fallback singleton agent payload builders."""

from __future__ import annotations

from typing import Any

from teaming24.utils.ids import COORDINATOR_ID, LOCAL_COORDINATOR_NAME, ORGANIZER_ID


def build_fallback_organizer_agent_info(runtime_settings: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical fallback Organizer payload."""
    return {
        "id": ORGANIZER_ID,
        "name": "Organizer",
        "type": "organizer",
        "status": "online",
        "model": runtime_settings.get("organizerModel", ""),
        "goal": "Route and manage all incoming tasks",
        "backstory": "The Organizer is the entry point for all tasks in the system.",
        "capabilities": [
            {"name": "task_routing", "description": "Routes tasks to appropriate agents"},
            {"name": "network_delegation", "description": "Delegates to remote nodes"},
        ],
    }


def build_fallback_coordinator_agent_info(runtime_settings: dict[str, Any]) -> dict[str, Any]:
    """Return the canonical fallback local team coordinator payload."""
    return {
        "id": COORDINATOR_ID,
        "name": LOCAL_COORDINATOR_NAME,
        "type": "coordinator",
        "status": "online",
        "model": runtime_settings.get("coordinatorModel", ""),
        "goal": "Coordinate worker agents to execute tasks",
        "backstory": "The local team coordinator decomposes tasks and assigns them to workers.",
        "capabilities": [
            {"name": "task_decomposition", "description": "Breaks down complex tasks"},
            {"name": "worker_coordination", "description": "Coordinates worker agents"},
        ],
    }
