"""
Shared dependencies, helpers, and singleton accessors for the API layer.

This module serves as the central import hub for the API. All route
modules and services should import shared dependencies from here
instead of reaching into application internals directly. This keeps
imports DRY, avoids circular dependencies, and makes it easy to swap
implementations for testing.

Role in the API layer
---------------------
- **Shared singletons**: Config, logger, database, task manager.
- **Import hub**: Re-exports from config, task, agent, communication,
  and data modules so routes need only ``from teaming24.api.deps import X``.
- **Path constants**: BASE_DIR, DOCS_DIR, GUI_DIR, GUI_DIST_DIR for
  static files and docs.

How route modules should import from deps
-----------------------------------------
::

    from teaming24.api.deps import config, logger, get_database
    from teaming24.api.deps import BASE_DIR, DOCS_DIR

Do not import directly from ``teaming24.config``, ``teaming24.task``,
etc. in route modules — use deps as the single source.

How to add a new shared dependency
----------------------------------
1. Import the dependency at module level.
2. Re-export it (it will be available as ``deps.<name>``).
3. Document it in this docstring under Exports.

Exports
-------
- ``config``: Unified app config (from ``teaming24.config``).
- ``logger``: Module logger for the API layer.
- ``BASE_DIR``, ``DOCS_DIR``, ``GUI_DIR``, ``GUI_DIST_DIR``: Path constants.
- ``CONFIG_DIR``, ``UNIFIED_CONFIG_FILE``: Config paths.
- ``get_database``: Database connection accessor.
- ``get_task_manager``: Factory for task manager (from ``teaming24.task``).
- ``get_task_manager_instance``: Lazy singleton for the global task manager.
- ``get_network_manager``: Lazy accessor for the global network manager.
- ``get_output_manager``: Output manager accessor.
- ``check_agent_framework_available``, ``check_crewai_available``,
  ``create_local_crew``: Agent setup helpers.
- ``TaskStatus``: Task status enum.
- ``NetworkManager``, ``SubscriptionManager``, ``NodeInfo``: Communication types.
"""
from __future__ import annotations

from pathlib import Path

from teaming24.config import CONFIG_DIR, get_config
from teaming24.config import UNIFIED_CONFIG_FILE as _UNIFIED_CONFIG_FILE
from teaming24.data.database import get_database as get_database
from teaming24.task import get_task_manager
from teaming24.task.output import get_output_manager as get_output_manager
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

BASE_DIR = Path(__file__).parent.parent.parent
DOCS_DIR = BASE_DIR / "docs"
GUI_DIR = BASE_DIR / "teaming24" / "gui"
GUI_DIST_DIR = GUI_DIR / "dist"
_unified_path = Path(_UNIFIED_CONFIG_FILE)
UNIFIED_CONFIG_FILE = str(_unified_path if _unified_path.is_absolute() else Path(CONFIG_DIR) / _unified_path)

config = get_config()


_task_manager = None


def get_task_manager_instance():
    """Get or create the global task manager singleton."""
    global _task_manager
    if _task_manager is None:
        agent_id = config.local_node.name if config.local_node else "local"
        _task_manager = get_task_manager(agent_id)
    return _task_manager


def get_network_manager():
    """Get the global network manager singleton (lazily via api.server)."""
    from teaming24.api.server import get_network_manager as _get_network_manager

    return _get_network_manager()
