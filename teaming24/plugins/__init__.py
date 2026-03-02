"""
Plugin and hook system for Teaming24.

Provides lifecycle hooks that plugins can register callbacks on.

Hook points:
  - before_task_execute(task_id, prompt)
  - after_task_execute(task_id, result)
  - on_routing_decision(task_id, plan)
  - on_agent_step(task_id, step)
  - on_payment(task_id, amount, status)
  - on_session_created(session)
  - on_session_reset(session)

Usage:
    from teaming24.plugins import get_hook_registry

    @get_hook_registry().on("before_task_execute")
    async def my_hook(task_id, prompt):
        print(f"Task starting: {task_id}")
"""

from teaming24.plugins.hooks import HookRegistry, get_hook_registry

__all__ = [
    "HookRegistry",
    "get_hook_registry",
]
