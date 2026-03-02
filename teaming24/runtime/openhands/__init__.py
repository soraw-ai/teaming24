"""
OpenHands Runtime Integration for Teaming24.

Provides OpenHands SDK integration for AI agent execution in isolated environments.

Installation:
    # Recommended (uv):
    uv pip install openhands-sdk openhands-tools openhands-workspace

    # Or with pip:
    pip install openhands-sdk openhands-tools openhands-workspace

    # Minimal:
    uv pip install openhands-sdk openhands-tools

Reference: https://docs.openhands.dev/sdk/getting-started

Usage:
    # Direct usage with context manager
    from teaming24.runtime.openhands import OpenHandsAdapter, OpenHandsConfig

    config = OpenHandsConfig(workspace_path="/workspace")
    async with OpenHandsAdapter(config) as adapter:
        result = await adapter.run_command("ls -la")
        print(result["output"])

    # Pooled usage for agents (recommended)
    from teaming24.runtime.openhands import allocate_openhands, release_openhands

    # Allocate persistent runtime for an agent
    runtime = await allocate_openhands("agent-123")

    # Use runtime...
    result = await runtime.run_command("python script.py")

    # Release when done (optional - auto-cleanup on exit)
    await release_openhands("agent-123")

Modes:
    - sdk_workspace: Full SDK with Docker workspace (best isolation)
    - workspace: Docker workspace only (direct execution)
    - sdk: SDK with local workspace (AI-assisted)
    - legacy: Legacy openhands-ai API
    - local: Local fallback (no isolation)
"""

from teaming24.runtime.openhands.adapter import (
    # Availability flags
    OPENHANDS_AVAILABLE,
    OPENHANDS_LEGACY_AVAILABLE,
    OPENHANDS_SDK_AVAILABLE,
    OPENHANDS_TOOLS_AVAILABLE,
    OPENHANDS_WORKSPACE_AVAILABLE,
    # Core classes
    OpenHandsAdapter,
    OpenHandsConfig,
    # Pool management (for agent-level allocation)
    OpenHandsPool,
    allocate_openhands,
    check_openhands_available,
    cleanup_all_openhands,
    # Factory functions
    create_openhands_runtime,
    get_openhands_mode,
    get_openhands_pool,
    release_openhands,
)

__all__ = [
    # Core classes
    "OpenHandsAdapter",
    "OpenHandsConfig",
    # Factory functions
    "create_openhands_runtime",
    "check_openhands_available",
    "get_openhands_mode",
    # Availability flags
    "OPENHANDS_AVAILABLE",
    "OPENHANDS_SDK_AVAILABLE",
    "OPENHANDS_TOOLS_AVAILABLE",
    "OPENHANDS_WORKSPACE_AVAILABLE",
    "OPENHANDS_LEGACY_AVAILABLE",
    # Pool management
    "OpenHandsPool",
    "get_openhands_pool",
    "allocate_openhands",
    "release_openhands",
    "cleanup_all_openhands",
]
