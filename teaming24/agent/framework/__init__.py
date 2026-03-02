"""
Framework abstraction layer for Teaming24.

Provides a FrameworkAdapter interface so the execution backend (CrewAI,
native runtime, or future frameworks) is pluggable without touching routing,
pool management, or task lifecycle code.

Usage:
    from teaming24.agent.framework import create_framework_adapter
    adapter = create_framework_adapter("native")   # or "crewai"
    result = await adapter.execute_hierarchical(...)
"""

from teaming24.agent.framework.base import (
    AgentSpec,
    FrameworkAdapter,
    StepOutput,
    ToolSpec,
)

_ADAPTER_REGISTRY: dict[str, type[FrameworkAdapter]] = {}


def register_adapter(name: str, cls: type[FrameworkAdapter]) -> None:
    """Register a framework adapter by name."""
    _ADAPTER_REGISTRY[name] = cls


def create_framework_adapter(name: str, **kwargs) -> FrameworkAdapter:
    """Instantiate a registered framework adapter.

    Args:
        name: Adapter key ("native" or "crewai").
        **kwargs: Forwarded to the adapter constructor.

    Returns:
        A ready-to-use FrameworkAdapter instance.

    Raises:
        ValueError: If *name* is not registered.
    """
    if name not in _ADAPTER_REGISTRY:
        _load_builtin(name)
    cls = _ADAPTER_REGISTRY.get(name)
    if cls is None:
        available = ", ".join(sorted(_ADAPTER_REGISTRY)) or "(none)"
        raise ValueError(
            f"Unknown framework adapter '{name}'. Available: {available}"
        )
    return cls(**kwargs)


def _load_builtin(name: str) -> None:
    """Lazy-import built-in adapters so optional deps stay optional."""
    if name == "native":
        from teaming24.agent.framework.native.adapter import NativeAdapter
        register_adapter("native", NativeAdapter)
    elif name == "crewai":
        from teaming24.agent.framework.crewai_adapter import CrewAIAdapter
        register_adapter("crewai", CrewAIAdapter)


__all__ = [
    "AgentSpec",
    "FrameworkAdapter",
    "StepOutput",
    "ToolSpec",
    "create_framework_adapter",
    "register_adapter",
]
