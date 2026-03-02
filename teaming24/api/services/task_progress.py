"""Task progress helper functions extracted from server.py."""

from __future__ import annotations

import re
import time
from typing import Any


def remote_stage_default_pct(stage: str) -> int:
    """Default progress percentage for remote lifecycle stages."""
    defaults = {
        "submitting": 0,
        "submitted": 8,
        "subscribing": 12,
        "polling": 16,
        "queued": 20,
        "running": 35,
        "finalizing": 75,
        "completed": 100,
        "failed": 100,
    }
    return defaults.get(str(stage or "").strip().lower(), 0)


def normalize_remote_milestone_label(value: str) -> str:
    """Collapse verbose remote labels into stable milestone keys."""
    text = re.sub(r"\s+", " ", str(value or "").strip().lower())
    if not text:
        return ""

    keyword_map = (
        ("submitting to remote node", "submit_start"),
        ("remote node accepted task", "submit_accepted"),
        ("connecting to remote stream", "sse_connecting"),
        ("live remote stream connected", "sse_connected"),
        ("polling remote node", "polling"),
        ("remote task completed", "remote_completed_hint"),
        ("remote task failed", "remote_failed_hint"),
        ("remote task polling timed out", "poll_timeout"),
    )
    for needle, key in keyword_map:
        if needle in text:
            return key

    if "executing with" in text and "workers" in text:
        return "executing_workers"
    if "queued" in text or "pending" in text:
        return "queued"
    if "running" in text and "state=" in text:
        return "running"

    text = re.sub(r"\[(task_[^\]]+)\]", "", text)
    text = re.sub(r"\b\d+%\b", "", text)
    text = re.sub(r"\b\d+\b", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text[:80]


def extract_remote_milestone_signature(step_data: dict[str, Any]) -> tuple[str, tuple[Any, ...]] | None:
    """Return a semantic signature for remote progress deduplication."""
    if str(step_data.get("agent_type", "") or "") != "remote":
        return None

    action = str(step_data.get("action", "") or "").strip().lower()
    remote_progress = step_data.get("remote_progress")
    if not isinstance(remote_progress, dict):
        remote_progress = {}

    node_key = str(
        step_data.get("remote_task_id")
        or step_data.get("remote_node_id")
        or step_data.get("agent_id")
        or step_data.get("agent")
        or "remote"
    )
    stage = str(remote_progress.get("stage", "") or "").strip().lower()
    if not stage:
        if action in ("remote_completed", "remote_done"):
            stage = "completed"
        elif action == "remote_failed":
            stage = "failed"
        else:
            stage = "running"

    raw_pct = remote_progress.get("percentage")
    try:
        pct = int(raw_pct) if raw_pct is not None else remote_stage_default_pct(stage)
    except (TypeError, ValueError):
        pct = remote_stage_default_pct(stage)
    pct = max(0, min(100, pct))
    pct_bucket = pct if pct >= 100 else int(pct / 10) * 10

    phase_label = (
        str(remote_progress.get("phase_label", "") or "").strip()
        or str(step_data.get("content", "") or "").strip()
    )
    label_key = normalize_remote_milestone_label(phase_label)
    transport = str(remote_progress.get("transport", "") or "").strip().lower()
    terminal = action in ("remote_done", "remote_completed", "remote_failed") or stage in ("completed", "failed")
    action_group = action if terminal else "remote_progress"

    return node_key, (action_group, stage, label_key, pct_bucket, transport, terminal)


def should_emit_remote_milestone(
    step_data: dict[str, Any],
    tracker: dict[str, tuple[Any, ...]],
) -> bool:
    """Emit only meaningful remote milestone changes into the timeline."""
    signature = extract_remote_milestone_signature(step_data)
    if signature is None:
        return True

    node_key, milestone = signature
    previous = tracker.get(node_key)
    if previous == milestone:
        return False

    tracker[node_key] = milestone
    return True


def upsert_worker_status(
    worker_states: dict[str, dict[str, Any]],
    worker_name: str,
    *,
    status: str | None = None,
    action: str | None = None,
    detail: str | None = None,
    tool: str | None = None,
    step_count: int | None = None,
    order_hint: int | None = None,
    error: str | None = None,
    updated_at: float | None = None,
    started_at: float | None = None,
    last_heartbeat_at: float | None = None,
    finished_at: float | None = None,
) -> None:
    """Mutate a worker status map in-place with a stable schema."""
    key = str(worker_name or "").strip()
    if not key:
        return

    existing = worker_states.get(key, {})
    payload = dict(existing)
    payload["name"] = key
    if order_hint is not None:
        payload["order"] = int(order_hint)
    elif "order" not in payload:
        payload["order"] = len(worker_states)
    if status:
        payload["status"] = str(status)
    else:
        payload.setdefault("status", "pending")
    if action is not None:
        payload["action"] = str(action)
    if detail is not None:
        payload["detail"] = str(detail)[:180]
    if tool is not None:
        payload["tool"] = str(tool)
    if step_count is not None:
        payload["step_count"] = int(step_count)
    if error:
        payload["error"] = str(error)[:220]
    elif "error" in payload and payload.get("status") not in ("failed", "timeout"):
        payload.pop("error", None)
    now_ts = float(updated_at or time.time())
    payload["updated_at"] = now_ts
    if started_at is not None:
        payload["started_at"] = float(started_at)
    elif payload.get("status") == "running" and "started_at" not in payload:
        payload["started_at"] = now_ts
    if last_heartbeat_at is not None:
        payload["last_heartbeat_at"] = float(last_heartbeat_at)
    elif payload.get("status") == "running" and str(payload.get("action", "")).lower() in ("tool_heartbeat", "worker_heartbeat"):
        payload["last_heartbeat_at"] = now_ts
    if finished_at is not None:
        payload["finished_at"] = float(finished_at)
    elif payload.get("status") in ("completed", "failed", "skipped", "timeout"):
        payload["finished_at"] = now_ts
    worker_states[key] = payload


def serialize_worker_statuses(worker_states: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Return worker status rows sorted by stable order, then name."""
    rows = [dict(value) for value in worker_states.values()]
    rows.sort(key=lambda item: (int(item.get("order", 10**9)), str(item.get("name", "")).lower()))
    return rows
