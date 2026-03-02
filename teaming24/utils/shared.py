"""Teaming24 Shared Utilities.

Provides reusable patterns used across the runtime, agent, and API layers:
- SingletonMixin: Thread-safe singleton pattern for pool/manager classes
- sync_async_cleanup: Safely run async cleanup from synchronous contexts (atexit, signals)
- create_http_client: Factory for httpx.AsyncClient with consistent timeout handling
- config_to_dict: Unified dataclass-to-dict conversion
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# =============================================================================
# Singleton Mixin
# =============================================================================


class SingletonMixin:
    """Thread-safe singleton mixin using double-checked locking.

    Subclasses get ``get_instance()`` and ``reset_instance()`` class methods.
    Override ``_on_first_init`` for one-time setup after the singleton is created.

    Usage::

        class MyPool(SingletonMixin):
            def __init__(self):
                ...

            def _on_first_init(self):
                # Called once after the singleton is created
                ...
    """

    _instance: Any | None = None
    _singleton_lock: threading.Lock = threading.Lock()

    @classmethod
    def get_instance(cls) -> Any:
        """Get or create the singleton instance (thread-safe)."""
        if cls._instance is None:
            with cls._singleton_lock:
                if cls._instance is None:
                    cls._instance = cls()
                    if hasattr(cls._instance, "_on_first_init"):
                        cls._instance._on_first_init()
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """Reset the singleton instance (primarily for testing)."""
        with cls._singleton_lock:
            cls._instance = None


# =============================================================================
# Async Cleanup from Synchronous Context
# =============================================================================


def sync_async_cleanup(coro_fn: Callable[[], Awaitable[None]], label: str = "cleanup") -> None:
    """Run an async cleanup coroutine from a synchronous context.

    Safe to call from ``atexit`` handlers and signal handlers where no event
    loop may be running, or a loop may already be active.

    Args:
        coro_fn: Zero-argument callable that returns an awaitable (e.g. ``self.shutdown``).
        label: Human-readable label for log messages.
    """
    try:
        # If a loop is already running, schedule on it
        try:
            loop = asyncio.get_running_loop()
            asyncio.ensure_future(coro_fn(), loop=loop)
            return
        except RuntimeError as e:
            logger.debug(f"No running event loop, will create new one: {e}")

        # No running loop -- create one
        try:
            asyncio.run(coro_fn())
        except RuntimeError as exc:
            if "cannot be called from a running event loop" in str(exc):
                asyncio.ensure_future(coro_fn())
            else:
                raise
    except Exception as exc:
        if "no current event loop" not in str(exc).lower():
            logger.warning(f"Error during {label}: {exc}")


# =============================================================================
# HTTP Client Factory
# =============================================================================


def create_http_client(
    timeout: float = 10.0,
    base_url: str = "",
    follow_redirects: bool = True,
) -> httpx.AsyncClient:
    """Create an ``httpx.AsyncClient`` with consistent defaults.

    All HTTP clients across the codebase should be created through this factory
    so timeout policies and transport settings stay uniform.

    Args:
        timeout: Request timeout in seconds.
        base_url: Optional base URL for all requests.
        follow_redirects: Whether to follow HTTP redirects.

    Returns:
        Configured ``httpx.AsyncClient`` instance.
    """
    return httpx.AsyncClient(
        timeout=timeout,
        base_url=base_url,
        follow_redirects=follow_redirects,
    )


# =============================================================================
# Config / Dataclass Helpers
# =============================================================================


def config_to_dict(config_obj: Any) -> dict[str, Any]:
    """Convert a dataclass config object to a plain dict.

    Handles dataclass instances, plain dicts, and unknown types gracefully.

    Args:
        config_obj: A dataclass instance, dict, or any object.

    Returns:
        Dictionary representation of the config.
    """
    if config_obj is None:
        return {}
    if hasattr(config_obj, "__dataclass_fields__"):
        from dataclasses import asdict
        return asdict(config_obj)
    if isinstance(config_obj, dict):
        return config_obj
    return {}


__all__ = [
    "SingletonMixin",
    "sync_async_cleanup",
    "create_http_client",
    "config_to_dict",
]
