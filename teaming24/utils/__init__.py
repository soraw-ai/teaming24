"""Teaming24 Utilities.

Common utilities used across the Teaming24 project, including logging,
singleton patterns, HTTP client factories, and config helpers.
"""

from .ids import (
    COORDINATOR_ID,
    # Prefixes & helpers
    DEMO_PREFIX,
    OPENHANDS_PREFIX,
    # Well-known IDs
    ORGANIZER_ID,
    SYSTEM_ID,
    WORKER_PREFIX,
    agentic_node_id,
    build_worker_lookup,
    extract_agent_id_from_openhands_sandbox,
    generic_id,
    # Node UID
    get_node_uid,
    is_demo_id,
    is_openhands_sandbox,
    resolve_agent_id,
    sandbox_id_demo,
    sandbox_id_for_openhands,
    sandbox_id_for_task,
    sandbox_id_generic,
    # Agent ID generators
    worker_id,
)
from .ids import (
    session_id as generate_session_id,
)
from .ids import (
    step_id as generate_step_id,
)
from .ids import (
    # Task / Sandbox / Misc ID generators
    task_id as generate_task_id,
)
from .logger import (
    # Config
    LogConfig,
    # Source categories
    LogSource,
    clear_context,
    critical,
    # Convenience functions
    debug,
    error,
    exception,
    get_agent_logger,
    get_context,
    # Logger access
    get_logger,
    info,
    # Context management
    set_context,
    setup_logging,
    warning,
)
from .shared import (
    SingletonMixin,
    config_to_dict,
    create_http_client,
    sync_async_cleanup,
)

__all__ = [
    # Logger
    "LogSource",
    "LogConfig",
    "setup_logging",
    "get_logger",
    "get_agent_logger",
    "set_context",
    "clear_context",
    "get_context",
    "debug",
    "info",
    "warning",
    "error",
    "critical",
    "exception",
    # Shared utilities
    "SingletonMixin",
    "sync_async_cleanup",
    "create_http_client",
    "config_to_dict",
    # IDs
    "ORGANIZER_ID",
    "COORDINATOR_ID",
    "SYSTEM_ID",
    "get_node_uid",
    "worker_id",
    "agentic_node_id",
    "resolve_agent_id",
    "build_worker_lookup",
    "generate_task_id",
    "sandbox_id_for_task",
    "sandbox_id_for_openhands",
    "sandbox_id_generic",
    "sandbox_id_demo",
    "generate_step_id",
    "generate_session_id",
    "generic_id",
    "DEMO_PREFIX",
    "OPENHANDS_PREFIX",
    "WORKER_PREFIX",
    "is_demo_id",
    "is_openhands_sandbox",
    "extract_agent_id_from_openhands_sandbox",
]
