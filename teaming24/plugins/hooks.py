"""
Hook registry for Teaming24's plugin system.

Provides a lightweight pub/sub mechanism for lifecycle events.
Plugins register async callbacks on named hook points; the core
code fires hooks at the appropriate moments.

Supported hooks:
    before_task_execute(task_id: str, prompt: str)
    after_task_execute(task_id: str, result: str)
    on_routing_decision(task_id: str, plan: dict)
    on_agent_step(task_id: str, step: dict)
    on_payment(task_id: str, amount: float, status: str)
    on_session_created(session: dict)
    on_session_reset(session: dict)
"""

from __future__ import annotations

import asyncio
from collections import defaultdict
from collections.abc import Callable
from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

HookCallback = Callable[..., Any]


class HookRegistry:
    """Central registry for lifecycle hook callbacks."""

    def __init__(self):
        self._hooks: dict[str, list[HookCallback]] = defaultdict(list)

    def on(self, hook_name: str):
        """Decorator to register a callback on *hook_name*.

        Usage::

            @hook_registry.on("before_task_execute")
            async def my_hook(task_id, prompt):
                ...
        """
        def decorator(fn: HookCallback) -> HookCallback:
            self.register(hook_name, fn)
            return fn
        return decorator

    def register(self, hook_name: str, callback: HookCallback) -> None:
        """Programmatically register a callback."""
        self._hooks[hook_name].append(callback)
        logger.debug("[Hooks] registered %s on '%s'", callback.__name__, hook_name)

    def unregister(self, hook_name: str, callback: HookCallback) -> None:
        hooks = self._hooks.get(hook_name, [])
        if callback in hooks:
            hooks.remove(callback)

    async def fire(self, hook_name: str, *args, **kwargs) -> None:
        """Invoke all registered callbacks for *hook_name*.

        Exceptions in individual callbacks are logged but do not
        propagate — hooks must never break the core flow.
        """
        callbacks = self._hooks.get(hook_name, [])
        if not callbacks:
            return
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    await cb(*args, **kwargs)
                else:
                    cb(*args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "[Hooks] callback %s on '%s' failed: %s",
                    cb.__name__, hook_name, exc,
                )

    def fire_sync(self, hook_name: str, *args, **kwargs) -> None:
        """Fire a hook synchronously (best-effort, for non-async contexts)."""
        callbacks = self._hooks.get(hook_name, [])
        for cb in callbacks:
            try:
                if asyncio.iscoroutinefunction(cb):
                    try:
                        loop = asyncio.get_running_loop()
                        loop.create_task(cb(*args, **kwargs))
                    except RuntimeError:
                        logger.debug("No running event loop, using asyncio.run for hook callback")
                        asyncio.run(cb(*args, **kwargs))
                else:
                    cb(*args, **kwargs)
            except Exception as exc:
                logger.warning(
                    "[Hooks] sync callback %s on '%s' failed: %s",
                    cb.__name__, hook_name, exc,
                )

    def list_hooks(self) -> dict[str, int]:
        """Return {hook_name: callback_count} for introspection."""
        return {k: len(v) for k, v in self._hooks.items() if v}


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_registry: HookRegistry | None = None


def get_hook_registry() -> HookRegistry:
    """Return the global HookRegistry singleton."""
    global _registry
    if _registry is None:
        _registry = HookRegistry()
    return _registry
