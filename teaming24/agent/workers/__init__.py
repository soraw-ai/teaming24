"""
Worker Agent Registry
=====================

All worker agent blueprints are defined in Python files inside this package.
The YAML configuration file references them **by name** to select which ones
to load at runtime.

Architecture
------------
- **Python** (this package): defines default role, goal, backstory,
  capabilities, tools, and group for every available worker.
- **YAML** (``teaming24.yaml``): lists which workers to activate
  (``agents.dev_workers`` / ``agents.prod_workers``), and optionally
  overrides per-worker parameters via ``agents.worker_overrides``.

Usage::

    from teaming24.agent.workers import get_worker, list_workers, resolve_workers

    # Get one definition
    defn = get_worker("ux_designer")

    # Resolve a list of names to full configs (with optional YAML overrides)
    configs = resolve_workers(["ux_designer", "backend_engineer"], overrides={})
"""

from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

DEFAULT_WORKER_MODULES = [
    "node_alpha",
    "node_beta",
    "node_gamma",
    "node_delta",
]

# ---------------------------------------------------------------------------
# Global registry: worker_name -> default config dict
# ---------------------------------------------------------------------------
_WORKER_REGISTRY: dict[str, dict[str, Any]] = {}


def register_worker(name: str, config: dict[str, Any]) -> None:
    """Register a worker blueprint in the global registry.

    Args:
        name:   Unique identifier (snake_case, e.g. ``ux_designer``).
        config: Dict with keys: role, goal, backstory, capabilities,
                tools, allow_delegation, group.
    """
    config.setdefault("name", name)
    _WORKER_REGISTRY[name] = config


def get_worker(name: str) -> dict[str, Any] | None:
    """Return a **copy** of the worker definition, or ``None``."""
    defn = _WORKER_REGISTRY.get(name)
    return dict(defn) if defn else None


def list_workers() -> list[str]:
    """Return all registered worker names."""
    return list(_WORKER_REGISTRY.keys())


def get_all_workers() -> dict[str, dict[str, Any]]:
    """Return a shallow copy of the full registry."""
    return dict(_WORKER_REGISTRY)


def resolve_workers(
    names: list[str],
    overrides: dict[str, dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    """Resolve a list of worker names to full config dicts.

    For each name:
      1. Look up the Python-defined default.
      2. Merge any per-worker overrides from the YAML config.
      3. Skip unknown names (logged as warnings).

    Args:
        names:     Ordered list of worker names to resolve.
        overrides: Optional mapping ``{worker_name: {field: value, …}}``.

    Returns:
        List of fully-resolved worker config dicts, in the same order.
    """
    result: list[dict[str, Any]] = []
    overrides = overrides or {}
    for name in names:
        base = get_worker(name)
        if base is None:
            logger.warning(f"Unknown worker '{name}' — skipping (not in registry)")
            continue
        # Apply YAML overrides on top of Python defaults
        if name in overrides:
            base.update(overrides[name])
        result.append(base)
    return result


# ---------------------------------------------------------------------------
# Dynamic module loader
# ---------------------------------------------------------------------------
_loaded_modules: set = set()


def load_worker_modules(module_names: list[str]) -> None:
    """Dynamically import worker definition modules from this package.

    Each module name corresponds to a file under ``teaming24/agent/workers/``.
    For example, ``"node_alpha"`` imports ``teaming24.agent.workers.node_alpha``.

    Modules are imported at most once (tracked by ``_loaded_modules``).

    Args:
        module_names: List of module names (without the package prefix).
    """
    import importlib

    for name in module_names:
        if name in _loaded_modules:
            continue
        fqn = f"teaming24.agent.workers.{name}"
        try:
            importlib.import_module(fqn)
            _loaded_modules.add(name)
            logger.info(f"Loaded worker module: {name}")
        except ModuleNotFoundError:
            logger.warning(f"Worker module not found: {fqn}")
        except Exception as exc:
            logger.error(f"Failed to load worker module {fqn}: {exc}")


__all__ = [
    "DEFAULT_WORKER_MODULES",
    "register_worker",
    "get_worker",
    "list_workers",
    "get_all_workers",
    "resolve_workers",
    "load_worker_modules",
]
