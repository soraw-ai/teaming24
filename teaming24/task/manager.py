"""
Task Manager for Teaming24.

Provides task tracking, unique ID generation, status management, and
real-time phase/progress monitoring.  Thread-safe implementation with
automatic cleanup of old tasks.

Phase lifecycle:
    received → routing → dispatching → executing → aggregating → completed/failed

Each phase transition is logged with a ``[TASK]`` prefix and broadcast
to all SSE subscribers so the dashboard can display accurate real-time
progress.
"""

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from teaming24.config import get_config
from teaming24.utils.ids import normalize_agent_name, task_id as _generate_task_id
from teaming24.utils.logger import LogSource, get_agent_logger, get_logger

logger = get_logger(__name__)
task_logger = get_agent_logger(LogSource.TASK, "manager")

class TaskStatus(Enum):
    """Task execution status."""
    PENDING = "pending"
    RUNNING = "running"
    DELEGATED = "delegated"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPhase(Enum):
    """Task execution phase — finer-grained than TaskStatus.

    Tracks where exactly the task is in the execution pipeline:
      received    → Organizer received the request
      routing     → ANRouter deciding which pool members to use
      dispatching → Organizer dispatching to selected pool members
                    (local team coordinator and/or remote AN Coordinators)
      executing   → Workers actively executing subtasks
      aggregating → Organizer aggregating results from all participants
      completed   → Task finished (success or failure)
    """
    RECEIVED = "received"
    ROUTING = "routing"
    DISPATCHING = "dispatching"
    EXECUTING = "executing"
    AGGREGATING = "aggregating"
    COMPLETED = "completed"


class TaskType(Enum):
    """Type of task execution."""
    LOCAL = "local"           # Executed by local crew
    REMOTE = "remote"         # Delegated to remote node
    HYBRID = "hybrid"         # Mixed local and remote


@dataclass
class PhaseTransition:
    """Records a single phase transition for audit trail."""
    timestamp: float
    from_phase: str
    to_phase: str
    label: str  # Human-readable description of what's happening

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "from_phase": self.from_phase,
            "to_phase": self.to_phase,
            "label": self.label,
        }


@dataclass
class TaskProgress:
    """Fine-grained progress tracking for a task.

    Updated continuously during execution and broadcast via SSE.
    """
    phase: str = "received"
    percentage: int = 0
    # These counters track currently selected execution participants
    # (typically coordinators/pool members) for progress visualization.
    total_workers: int = 0
    completed_workers: int = 0
    active_workers: int = 0
    skipped_workers: int = 0
    current_agent: str = ""         # Which agent is currently active
    current_action: str = ""        # What the agent is doing
    current_step_number: int = 0    # Global step counter
    phase_label: str = "Starting"   # Human-readable description
    worker_statuses: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "phase": self.phase,
            "percentage": self.percentage,
            "total_workers": self.total_workers,
            "completed_workers": self.completed_workers,
            "active_workers": self.active_workers,
            "skipped_workers": self.skipped_workers,
            "current_agent": self.current_agent,
            "current_action": self.current_action,
            "current_step_number": self.current_step_number,
            "phase_label": self.phase_label,
            "worker_statuses": self.worker_statuses,
        }


@dataclass
class TaskStep:
    """A single step in task execution."""
    timestamp: float
    agent: str
    action: str
    content: str
    thought: str | None = None
    observation: str | None = None
    step_number: int = 0
    duration: float | None = None  # Seconds taken for this step

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "agent": self.agent,
            "action": self.action,
            "content": self.content,
            "thought": self.thought,
            "observation": self.observation,
            "step_number": self.step_number,
            "duration": self.duration,
        }


@dataclass
class TaskCost:
    """Cost tracking for task execution."""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    cost_usd: float = 0.0
    x402_payment: float = 0.0     # Payment to remote nodes

    def to_dict(self) -> dict:
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "cost_usd": round(self.cost_usd, 6),
            "x402_payment": round(self.x402_payment, 6),
        }


@dataclass
class Task:
    """Task entity for tracking execution.

    Includes phase tracking and fine-grained progress monitoring for
    real-time dashboard display.
    """
    id: str
    prompt: str
    status: TaskStatus = TaskStatus.PENDING
    task_type: TaskType = TaskType.LOCAL
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    completed_at: float | None = None
    result: str | None = None
    error: str | None = None
    steps: list[TaskStep] = field(default_factory=list)
    cost: TaskCost = field(default_factory=TaskCost)
    metadata: dict[str, Any] = field(default_factory=dict)
    delegated_to: str | None = None  # Node ID if delegated
    # Agent tracking
    assigned_to: str | None = None  # Coordinator or remote AN ID
    executing_agents: list[str] = field(default_factory=list)  # Agents currently working
    delegated_agents: list[str] = field(default_factory=list)  # Workers that executed
    output_dir: str | None = None  # Output directory for results
    pool_members: list[dict[str, Any]] = field(default_factory=list)  # Agentic Node Workforce Pool snapshot
    selected_members: list[str] = field(default_factory=list)  # Pool member IDs selected by ANRouter
    execution_mode: str = "parallel"  # "parallel" or "sequential"
    # Phase and progress tracking
    current_phase: TaskPhase = TaskPhase.RECEIVED
    progress: TaskProgress = field(default_factory=TaskProgress)
    phase_history: list[PhaseTransition] = field(default_factory=list)
    _step_counter: int = field(default=0, repr=False)
    _phase_percentages: dict[str, int] = field(default_factory=dict, repr=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "prompt": self.prompt,
            "status": self.status.value,
            "task_type": self.task_type.value,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
            "result": self.result,
            "error": self.error,
            "steps": [s.to_dict() for s in self.steps],
            "cost": self.cost.to_dict(),
            "metadata": self.metadata,
            "delegated_to": self.delegated_to,
            "assigned_to": self.assigned_to,
            "executing_agents": self.executing_agents,
            "delegated_agents": self.delegated_agents,
            "output_dir": self.output_dir,
            "pool_members": self.pool_members,
            "selected_members": self.selected_members,
            "execution_mode": self.execution_mode,
            "duration": self.duration,
            "current_phase": self.current_phase.value,
            "progress": self.progress.to_dict(),
            "phase_history": [p.to_dict() for p in self.phase_history],
        }

    @property
    def duration(self) -> float | None:
        """Get task duration in seconds."""
        if self.started_at:
            end = self.completed_at or time.time()
            return round(end - self.started_at, 2)
        return None

    @property
    def step_count(self) -> int:
        """Get the current global step counter."""
        return self._step_counter

    def add_step(self, agent: str, action: str, content: str,
                 thought: str = None, observation: str = None):
        """Add an execution step with auto-incrementing step number."""
        normalized_agent = normalize_agent_name(agent)
        self._step_counter += 1
        self.steps.append(TaskStep(
            timestamp=time.time(),
            agent=normalized_agent,
            action=action,
            content=content,
            thought=thought,
            observation=observation,
            step_number=self._step_counter,
        ))
        # Update progress with current step info
        self.progress.current_agent = normalized_agent
        self.progress.current_action = action
        self.progress.current_step_number = self._step_counter

    def set_phase(self, phase: TaskPhase, label: str = "",
                  percentage: int = None):
        """Transition to a new execution phase.

        Args:
            phase: The new phase.
            label: Human-readable description of the transition.
            percentage: Override progress percentage (auto-calculated if None).
        """
        old_phase = self.current_phase
        if old_phase == phase:
            # Same phase — just update label/percentage
            if label:
                self.progress.phase_label = label
            if percentage is not None:
                self.progress.percentage = percentage
            return

        self.current_phase = phase
        self.phase_history.append(PhaseTransition(
            timestamp=time.time(),
            from_phase=old_phase.value,
            to_phase=phase.value,
            label=label or phase.value,
        ))
        self.progress.phase = phase.value
        if label:
            self.progress.phase_label = label

        # Auto-calculate percentage based on phase if not explicitly given
        if percentage is not None:
            self.progress.percentage = percentage
        else:
            default_phase_pct = {
                "received": 5,
                "routing": 10,
                "dispatching": 20,
                "executing": 30,  # Will be updated more granularly
                "aggregating": 85,
                "completed": 100,
            }
            phase_pct = self._phase_percentages or default_phase_pct
            self.progress.percentage = phase_pct.get(phase.value, 0)

        task_logger.info(
            f"Phase: {old_phase.value} → {phase.value} │ "
            f"task={self.id}, {label or phase.value}"
        )

    def assign_to(self, agent_id: str):
        """Assign task to a coordinator or remote AN."""
        agent_id = normalize_agent_name(agent_id)
        self.assigned_to = agent_id
        if agent_id not in self.executing_agents:
            self.executing_agents.append(agent_id)

    def add_executing_agent(self, agent_id: str):
        """Add an agent to the executing agents list."""
        agent_id = normalize_agent_name(agent_id)
        if agent_id and agent_id not in self.executing_agents:
            self.executing_agents.append(agent_id)

    def add_delegated_agent(self, agent_id: str):
        """Add an agent to the delegated (worker) agents list."""
        agent_id = normalize_agent_name(agent_id)
        if agent_id and agent_id not in self.delegated_agents:
            self.delegated_agents.append(agent_id)
            # Also add to executing agents
            self.add_executing_agent(agent_id)


class TaskManager:
    """
    Manages task lifecycle and tracking.

    Features:
    - Unique task ID generation based on timestamp + prompt hash
    - Task status tracking (PENDING -> RUNNING -> COMPLETED/FAILED)
    - Step-by-step execution logging
    - Cost tracking (tokens + x402 payments)
    - Thread-safe operations
    - Automatic cleanup of old tasks
    """

    def __init__(self, node_id: str = "local"):
        """
        Initialize task manager.

        Args:
            node_id: Identifier for this node (used in task IDs)
        """
        self.node_id = node_id
        self._tasks: dict[str, Task] = {}
        self._listeners: list[Callable[[Task, str], None]] = []
        self._lock = threading.RLock()  # Reentrant lock for thread safety
        self._last_cleanup = time.time()
        cfg = get_config().system.task_manager
        self._max_tasks_in_memory = cfg.max_tasks_in_memory
        self._task_expiry_seconds = cfg.task_expiry_seconds
        self._cleanup_interval_seconds = cfg.cleanup_interval_seconds
        self._list_tasks_default_limit = cfg.list_tasks_default_limit
        self._phase_percentages = dict(cfg.phase_percentages or {})

    def generate_task_id(self, prompt: str, user_id: str = "default") -> str:
        """Generate a globally unique task ID.

        Format: ``task_{YYYYMMDDHHmmss}_{8hex}``

        The datetime segment makes IDs human-readable and sortable by
        creation time, while the random hex suffix prevents collisions.

        Args:
            prompt: Task prompt (unused, kept for API compatibility).
            user_id: User identifier (unused, kept for API compatibility).

        Returns:
            A unique task ID string, e.g. ``task_20260209143025_a3f8b1c2``.
        """
        return _generate_task_id()

    def create_task(self, prompt: str, user_id: str = "default",
                    task_type: TaskType = TaskType.LOCAL,
                    metadata: dict[str, Any] = None,
                    task_id: str | None = None,
                    reuse_if_exists: bool = False,
                    preserve_history: bool = False) -> Task:
        """
        Create a new task.

        Args:
            prompt: The task prompt/instruction
            user_id: User who initiated the task
            task_type: Type of execution (local/remote/hybrid)
            metadata: Additional task metadata
            task_id: Optional explicit task ID (for master task continuity)
            reuse_if_exists: Reuse/reset task when explicit ID already exists
            preserve_history: Keep existing steps/workers/cost when reusing

        Returns:
            Created Task instance
        """
        # Cleanup old tasks periodically
        self._maybe_cleanup()

        resolved_task_id = str(task_id or "").strip() or self.generate_task_id(prompt, user_id)
        base_metadata = dict(metadata or {})

        with self._lock:
            existing = self._tasks.get(resolved_task_id)
            if reuse_if_exists and existing is not None:
                self._prepare_task_for_reuse(
                    task=existing,
                    prompt=prompt,
                    user_id=user_id,
                    task_type=task_type,
                    metadata=base_metadata,
                    preserve_history=preserve_history,
                )
                task = existing
                event = "reused"
            else:
                task = Task(
                    id=resolved_task_id,
                    prompt=prompt,
                    task_type=task_type,
                    metadata=base_metadata,
                    _phase_percentages=self._phase_percentages,
                )
                task.metadata["user_id"] = user_id
                task.metadata["node_id"] = self.node_id
                self._tasks[resolved_task_id] = task
                event = "created"

        self._notify(task, event)
        if event == "reused":
            task_logger.info(
                f"Reused │ task={resolved_task_id}, type={task_type.value}, "
                f"prompt={prompt[:80].replace(chr(10), ' ')}..."
            )
        else:
            task_logger.info(
                f"Created │ task={resolved_task_id}, type={task_type.value}, "
                f"prompt={prompt[:80].replace(chr(10), ' ')}..."
            )
        return task

    def _prepare_task_for_reuse(
        self,
        task: Task,
        prompt: str,
        user_id: str,
        task_type: TaskType,
        metadata: dict[str, Any],
        preserve_history: bool,
    ) -> None:
        """Reset mutable runtime fields so an existing task ID can run again."""
        task.prompt = prompt
        task.task_type = task_type
        task.status = TaskStatus.PENDING
        task.started_at = None
        task.completed_at = None
        task.result = None
        task.error = None
        task.delegated_to = None
        task.assigned_to = None
        task.current_phase = TaskPhase.RECEIVED
        task.progress = TaskProgress()

        task.metadata = dict(task.metadata or {})
        task.metadata.update(metadata or {})
        task.metadata["user_id"] = user_id
        task.metadata["node_id"] = self.node_id

        if not preserve_history:
            task.steps = []
            task.cost = TaskCost()
            task.executing_agents = []
            task.delegated_agents = []
            task.pool_members = []
            task.selected_members = []
            task.phase_history = []
            task.execution_mode = "parallel"
            task._step_counter = 0

    def _maybe_cleanup(self):
        """Cleanup old tasks if needed."""
        now = time.time()
        # Cleanup at configured cadence.
        if now - self._last_cleanup < self._cleanup_interval_seconds:
            return

        with self._lock:
            self._last_cleanup = now

            # Remove expired tasks
            expired = []
            for task_id, task in self._tasks.items():
                age = now - task.created_at
                if age > self._task_expiry_seconds and task.status in (
                    TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED
                ):
                    expired.append(task_id)

            for task_id in expired:
                del self._tasks[task_id]

            if expired:
                logger.debug(f"Cleaned up {len(expired)} expired tasks")

            # If still over limit, remove oldest terminal tasks
            if len(self._tasks) > self._max_tasks_in_memory:
                completed = [
                    (tid, t) for tid, t in self._tasks.items()
                    if t.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED)
                ]
                completed.sort(key=lambda x: x[1].created_at)

                to_remove = len(self._tasks) - self._max_tasks_in_memory
                for task_id, _ in completed[:to_remove]:
                    del self._tasks[task_id]

                logger.debug(f"Removed {to_remove} oldest tasks to stay under limit")

    def get_task(self, task_id: str) -> Task | None:
        """Get a task by ID."""
        with self._lock:
            return self._tasks.get(task_id)

    def start_task(self, task_id: str) -> Task | None:
        """Mark a task as started.

        Transitions to RUNNING status and sets the phase to RECEIVED.
        Returns None (with a warning) if the task is already RUNNING,
        COMPLETED, or FAILED — prevents double-execution / stacking.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in (TaskStatus.RUNNING, TaskStatus.COMPLETED, TaskStatus.FAILED):
                task_logger.warning(
                    f"Blocked │ task={task_id} already in state "
                    f"'{task.status.value}' — refusing to start again"
                )
                return None
            task.status = TaskStatus.RUNNING
            task.started_at = time.time()
            task.set_phase(
                TaskPhase.RECEIVED,
                label="Task received — preparing execution",
                percentage=5,
            )
        self._notify(task, "started")
        task_logger.info(
            f"Started │ task={task_id}, type={task.task_type.value}"
        )
        return task

    def complete_task(self, task_id: str, result: str) -> Task | None:
        """Mark a task as completed.

        No-op if already COMPLETED or FAILED (prevents double-completion).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                task_logger.warning(
                    f"Blocked │ complete_task({task_id}) already "
                    f"'{task.status.value}' — ignoring"
                )
                return task
            task.status = TaskStatus.COMPLETED
            task.completed_at = time.time()
            task.result = result
            task.set_phase(
                TaskPhase.COMPLETED,
                label="Completed",
                percentage=100,
            )
            task.progress.active_workers = 0
        self._notify(task, "completed")
        task_logger.info(
            f"Completed │ task={task_id}, duration={task.duration}s, "
            f"steps={task.step_count}, workers={len(task.delegated_agents)}"
        )
        return task

    def fail_task(self, task_id: str, error: str) -> Task | None:
        """Mark a task as failed.

        No-op if already COMPLETED or FAILED (prevents double-failure).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED):
                task_logger.warning(
                    f"Blocked │ fail_task({task_id}) already "
                    f"'{task.status.value}' — ignoring"
                )
                return task
            task.status = TaskStatus.FAILED
            task.completed_at = time.time()
            task.error = error
            task.set_phase(
                TaskPhase.COMPLETED,
                label="Failed",
                percentage=100,
            )
            task.progress.active_workers = 0
        self._notify(task, "failed")
        task_logger.error(
            f"Failed │ task={task_id}, error={error[:200]}"
        )
        return task

    def cancel_task(self, task_id: str) -> Task | None:
        """Mark a task as cancelled.

        No-op if already in a terminal state (COMPLETED, FAILED, CANCELLED).
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                task_logger.warning(
                    f"Blocked │ cancel_task({task_id}) already "
                    f"'{task.status.value}' — ignoring"
                )
                return task
            task.status = TaskStatus.CANCELLED
            task.completed_at = time.time()
            task.set_phase(
                TaskPhase.COMPLETED,
                label="Cancelled",
                percentage=100,
            )
            task.progress.active_workers = 0
        self._notify(task, "cancelled")
        task_logger.info(f"Cancelled │ task={task_id}")
        return task

    def delegate_task(self, task_id: str, node_id: str) -> Task | None:
        """Mark a task as delegated to a remote node."""
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return None
            if task.status in (TaskStatus.COMPLETED, TaskStatus.FAILED, TaskStatus.CANCELLED):
                task_logger.warning(
                    f"Blocked │ delegate_task({task_id}) already '{task.status.value}'"
                )
                return task
            task.status = TaskStatus.DELEGATED
            task.task_type = TaskType.REMOTE
            task.delegated_to = node_id
        if task:
            self._notify(task, "delegated")
            task_logger.info(
                f"Delegated │ task={task_id} → node={node_id}"
            )
        return task

    def set_pool_members(self, task_id: str, members: list[dict[str, Any]]) -> Optional['Task']:
        """Persist the Agentic Node Workforce Pool snapshot on a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.pool_members = members
        return task

    def set_selected_members(self, task_id: str, member_ids: list[str]) -> Optional['Task']:
        """Persist the ANRouter's selected pool member IDs on a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.selected_members = member_ids
        return task

    def set_execution_mode(self, task_id: str, mode: str) -> Optional['Task']:
        """Persist the execution mode (parallel/sequential) on a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task and mode in ("parallel", "sequential"):
                task.execution_mode = mode
        return task

    def add_executing_agent(self, task_id: str, agent_id: str) -> Optional['Task']:
        """Track one executing agent on a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.add_executing_agent(agent_id)
        return task

    def add_delegated_agent(self, task_id: str, agent_id: str) -> Optional['Task']:
        """Track one delegated/participating agent on a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.add_delegated_agent(agent_id)
        return task

    def update_phase(self, task_id: str, phase: TaskPhase,
                     label: str = "", percentage: int = None) -> Task | None:
        """Update the execution phase of a task.

        This is the primary method for tracking fine-grained progress.
        Each phase transition is logged and broadcast via SSE.

        Args:
            task_id: Task identifier.
            phase: New execution phase.
            label: Human-readable description.
            percentage: Optional explicit progress percentage.

        Returns:
            Updated Task or None if not found.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.set_phase(phase, label, percentage)
        if task:
            self._notify(task, "phase_change")
        return task

    def update_progress(self, task_id: str, **kwargs) -> Task | None:
        """Update fine-grained progress fields on a task.

        Accepts any fields from TaskProgress as keyword arguments:
        phase, percentage, total_workers, completed_workers, active_workers,
        skipped_workers, current_agent, current_action, current_step_number,
        phase_label, worker_statuses.

        Args:
            task_id: Task identifier.
            **kwargs: Progress fields to update.

        Returns:
            Updated Task or None if not found.
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                for key, val in kwargs.items():
                    if hasattr(task.progress, key):
                        setattr(task.progress, key, val)
                # Sync phase if provided
                if "phase" in kwargs:
                    try:
                        task.current_phase = TaskPhase(kwargs["phase"])
                    except ValueError:
                        logger.warning(
                            f"Ignored invalid phase '{kwargs['phase']}' for task={task_id}"
                        )
        if task:
            self._notify(task, "progress")
        return task

    def add_step(self, task_id: str, agent: str, action: str, content: str,
                 thought: str = None, observation: str = None) -> Task | None:
        """Add an execution step to a task."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.add_step(agent, action, content, thought, observation)
        if task:
            self._notify(task, "step")
        return task

    def update_cost(self, task_id: str, input_tokens: int = 0,
                    output_tokens: int = 0, cost_usd: float = 0.0,
                    x402_payment: float = 0.0) -> Task | None:
        """Update task cost tracking."""
        with self._lock:
            task = self._tasks.get(task_id)
            if task:
                task.cost.input_tokens += input_tokens
                task.cost.output_tokens += output_tokens
                task.cost.total_tokens = task.cost.input_tokens + task.cost.output_tokens
                task.cost.cost_usd += cost_usd
                task.cost.x402_payment += x402_payment
        if task:
            self._notify(task, "cost_update")
        return task

    def list_tasks(self, status: TaskStatus = None,
                   limit: int | None = None) -> list[Task]:
        """List tasks, optionally filtered by status."""
        with self._lock:
            tasks = list(self._tasks.values())
        if status:
            tasks = [t for t in tasks if t.status == status]
        # Sort by creation time, newest first
        tasks.sort(key=lambda t: t.created_at, reverse=True)
        effective_limit = (
            self._list_tasks_default_limit
            if limit is None
            else limit
        )
        return tasks[:effective_limit]

    def clear_tasks(self) -> int:
        """Drop all in-memory tasks while keeping listeners attached."""
        with self._lock:
            count = len(self._tasks)
            self._tasks.clear()
        if count:
            logger.info("Cleared %d in-memory tasks from TaskManager", count)
        return count

    def add_listener(self, callback: Callable[[Task, str], None]):
        """Add a task event listener."""
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[Task, str], None]):
        """Remove a task event listener."""
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _notify(self, task: Task, event: str):
        """Notify listeners of task events."""
        for listener in self._listeners:
            try:
                listener(task, event)
            except Exception as e:
                logger.error(f"Task listener error: {e}")


# Global task manager instance
_task_manager: TaskManager | None = None


def get_task_manager(node_id: str = None) -> TaskManager:
    """Get or create the global task manager."""
    global _task_manager
    if _task_manager is None:
        if node_id is None:
            try:
                from teaming24.utils.ids import get_node_uid
                node_id = get_node_uid()
            except Exception as exc:
                logger.warning("Failed to derive node uid, using 'local': %s", exc, exc_info=True)
                node_id = "local"
        _task_manager = TaskManager(node_id)
    return _task_manager
