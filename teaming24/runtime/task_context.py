"""Task execution context for shared workspace across workers.

When a task is running, the sandbox uses the task's output workspace so that
all workers (python_interpreter, file_write, shell_command) share the same
filesystem. This enables Worker A to fetch data and Worker B to use it.
"""

from __future__ import annotations

from contextvars import ContextVar
from typing import Any

_task_id_var: ContextVar[str | None] = ContextVar("teaming24_task_id", default=None)
_task_workspace_var: ContextVar[str | None] = ContextVar("teaming24_task_workspace", default=None)


def set_task_context(task_id: str | None, workspace_dir: str | None) -> None:
    """Set the current task context for sandbox workspace sharing."""
    _task_id_var.set(task_id)
    _task_workspace_var.set(workspace_dir)


def get_task_context() -> tuple[str | None, str | None]:
    """Get (task_id, workspace_dir) for the current task, or (None, None)."""
    return _task_id_var.get(), _task_workspace_var.get()


def clear_task_context() -> None:
    """Clear the task context (call when task ends)."""
    try:
        _task_id_var.set(None)
        _task_workspace_var.set(None)
    except LookupError:
        pass
