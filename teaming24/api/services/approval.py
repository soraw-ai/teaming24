"""Human-in-the-loop approval service.

This module is the single source of truth for approval flow and task-budget
state. Server routes call into this service; agent/core imports this service
directly so it doesn't depend on ``api.server``.
"""
from __future__ import annotations

import asyncio as _aio
import threading
import time
from typing import Any

import teaming24.api.state as _st
from teaming24.api.deps import config, logger
from teaming24.utils.ids import prefixed_id


def create_approval(
    task_id: str,
    approval_type: str,
    title: str,
    description: str,
    options: list,
    metadata: dict | None = None,
) -> tuple[str, threading.Event]:
    """Create an approval record and schedule SSE broadcast."""
    approval_id = prefixed_id("approval", 8)
    evt = threading.Event()
    with _st.approval_lock:
        _st.approval_requests[approval_id] = {
            "id": approval_id,
            "task_id": task_id,
            "type": approval_type,
            "title": title,
            "description": description,
            "options": options,
            "metadata": metadata or {},
            "event": evt,
            "decision": None,
            "created_at": time.time(),
        }

    payload = {
        "approval": {
            "id": approval_id,
            "task_id": task_id,
            "type": approval_type,
            "title": title,
            "description": description,
            "options": options,
            "metadata": metadata or {},
        }
    }

    async def _bcast() -> None:
        await _st.subscription_manager.broadcast("approval_request", payload)

    try:
        # In async request context, prefer current running loop.
        try:
            running_loop = _aio.get_running_loop()
        except RuntimeError:
            logger.debug("[Approval] No running loop in create_approval context")
            running_loop = None

        if running_loop and running_loop.is_running():
            running_loop.call_soon(_aio.create_task, _bcast())
        else:
            main_loop = getattr(threading, "_teaming24_main_loop", None)
            if main_loop and main_loop.is_running():
                main_loop.call_soon_threadsafe(lambda: _aio.ensure_future(_bcast(), loop=main_loop))
            else:
                logger.warning("[Approval] Main event loop not available; approval_request not broadcast")
    except Exception as e:
        logger.warning(f"[Approval] Failed to broadcast request: {e}")

    return approval_id, evt


def block_until_approval(
    approval_id: str,
    evt: threading.Event,
    timeout: float,
) -> tuple[str, dict[str, Any]]:
    """Block until approval resolved or timeout. Returns (decision, extras)."""
    evt.wait(timeout=timeout)
    with _st.approval_lock:
        record = _st.approval_requests.get(approval_id, {})
        decision = record.get("decision")
        budget = record.get("budget")
        _st.approval_requests.pop(approval_id, None)

    if decision is None:
        logger.warning(f"[Approval] Timed out after {timeout}s: {approval_id}")
        return "timeout", {}

    logger.info(f"[Approval] Resolved: {approval_id} -> {decision}")
    extra: dict[str, Any] = {"budget": budget} if budget is not None else {}
    return str(decision), extra


def wait_for_approval(
    task_id: str,
    approval_type: str,
    title: str,
    description: str,
    options: list,
    metadata: dict | None = None,
    timeout: float | None = None,
) -> str:
    """Compatibility helper that returns only decision (without extras).

    Pauses execution until the user resolves the approval via the
    REST endpoint, or *timeout* seconds elapse.
    """
    if timeout is None:
        timeout = config.api.approval_timeout

    approval_id, evt = create_approval(
        task_id=task_id,
        approval_type=approval_type,
        title=title,
        description=description,
        options=options,
        metadata=metadata,
    )
    decision, _ = block_until_approval(approval_id, evt, timeout)
    return decision


def get_task_budget_info(task_id: str) -> dict[str, Any] | None:
    """Return ``{budget, spent}`` for task, or ``None`` if unset."""
    with _st.approval_lock:
        info = _st.task_budgets.get(task_id)
        return dict(info) if info else None


def set_task_budget(task_id: str, budget: float, spent: float = 0.0) -> None:
    """Set budget for a task."""
    with _st.approval_lock:
        _st.task_budgets[task_id] = {"budget": budget, "spent": spent}


def add_task_spent(task_id: str, amount: float) -> None:
    """Accumulate spent amount for a task budget."""
    with _st.approval_lock:
        if task_id in _st.task_budgets:
            _st.task_budgets[task_id]["spent"] = _st.task_budgets[task_id].get("spent", 0) + amount
