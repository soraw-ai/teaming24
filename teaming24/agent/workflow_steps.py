from __future__ import annotations

from typing import Any

from teaming24.utils.ids import normalize_agent_name
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


def emit_workflow_step(
    *,
    task_manager: Any,
    on_step: Any,
    task_id: str,
    agent: str,
    action: str,
    content: str,
    agent_type: str | None = None,
    phase: Any = None,
    phase_label: str = "",
    percentage: int | None = None,
) -> None:
    """Record a workflow step and optionally stream it to the frontend."""
    agent = normalize_agent_name(agent)

    if agent_type is None:
        name_lower = agent.lower()
        if "organizer" in name_lower:
            agent_type = "organizer"
        elif "router" in name_lower or "anrouter" in name_lower:
            agent_type = "router"
        elif "coordinator" in name_lower:
            agent_type = "coordinator"
        elif "remote" in name_lower or "agentic node" in name_lower:
            agent_type = "remote"
        else:
            agent_type = "worker"

    if phase and task_id and task_manager:
        task_manager.update_phase(
            task_id,
            phase,
            label=phase_label or content[:80],
            percentage=percentage,
        )

    if task_id and task_manager:
        task_manager.add_step(task_id, agent=agent, action=action, content=content)
        try:
            if agent_type in ("coordinator", "remote", "worker"):
                task_manager.add_executing_agent(task_id, agent)
            if agent_type == "remote":
                task_manager.add_delegated_agent(task_id, agent)
        except Exception as track_exc:
            logger.warning(
                "Failed to update execution tracking for task=%s agent=%s type=%s: %s",
                task_id,
                agent,
                agent_type,
                track_exc,
                exc_info=True,
            )

    if on_step:
        try:
            step_data = {
                "task_id": task_id,
                "agent": agent,
                "agent_type": agent_type,
                "action": action,
                "content": content,
                "type": "workflow",
            }
            if task_id and task_manager:
                task = task_manager.get_task(task_id)
                if task:
                    step_data["progress"] = task.progress.to_dict()
                    step_data["step_number"] = task.step_count
            on_step(step_data)
        except Exception as exc:
            logger.debug("on_step callback failed in emit_workflow_step: %s", exc)
