"""
Task management module for Teaming24.
"""

from teaming24.task.manager import (
    PhaseTransition,
    Task,
    TaskCost,
    TaskManager,
    TaskPhase,
    TaskProgress,
    TaskStatus,
    TaskStep,
    TaskType,
    get_task_manager,
)
from teaming24.task.output import (
    OutputFile,
    TaskOutput,
    TaskOutputManager,
    get_output_manager,
    save_aggregated_output,
    save_task_output,
)

__all__ = [
    # Manager
    "Task",
    "TaskCost",
    "TaskManager",
    "TaskPhase",
    "TaskProgress",
    "TaskStatus",
    "TaskStep",
    "TaskType",
    "PhaseTransition",
    "get_task_manager",
    # Output
    "OutputFile",
    "TaskOutput",
    "TaskOutputManager",
    "get_output_manager",
    "save_task_output",
    "save_aggregated_output",
]
