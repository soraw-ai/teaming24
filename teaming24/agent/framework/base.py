"""
Framework-agnostic base types for multi-agent execution.

These types are the contract between teaming24's routing/orchestration
layer (ANRouter, AgenticNodeWorkforcePool, LocalCrew) and any execution backend
(native runtime, CrewAI, or future frameworks).

Design principles:
  - No imports from crewai, litellm, or any framework-specific package.
  - All fields are plain Python (str, dict, list, Callable).
  - Serialisable to JSON (except ``handler`` on ToolSpec).
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Step output — the universal "callback payload" shape
# ---------------------------------------------------------------------------

@dataclass
class StepOutput:
    """One atomic event produced during agent execution.

    Consumed by the SSE / WebSocket broadcast layer so the dashboard can
    show real-time progress regardless of which backend is running.
    """
    agent: str                          # role name of the acting agent
    action: str = ""                    # "tool_call", "thought", "delegation", "final_answer"
    tool: str = ""                      # tool name (when action == "tool_call")
    tool_input: str = ""                # serialised input to the tool
    content: str = ""                   # observation / thought text
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)


# Type alias for the callback that receives step outputs.
StepCallback = Callable[[StepOutput], None]


# ---------------------------------------------------------------------------
# ToolSpec — framework-agnostic tool definition
# ---------------------------------------------------------------------------

@dataclass
class ToolSpec:
    """A single tool that an agent can invoke.

    ``parameters`` follows the JSON-Schema subset used by the OpenAI
    function-calling spec.  ``handler`` is the Python callable that
    actually performs the work; it can be sync or async.
    """
    name: str
    description: str
    parameters: dict[str, Any] = field(default_factory=lambda: {
        "type": "object",
        "properties": {},
    })
    handler: Callable | None = None

    def to_openai_schema(self) -> dict[str, Any]:
        """Convert to OpenAI function-calling tool format."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ---------------------------------------------------------------------------
# AgentSpec — framework-agnostic agent definition
# ---------------------------------------------------------------------------

@dataclass
class AgentSpec:
    """Everything needed to create an agent in any backend.

    This is the universal agent "blueprint" that LocalCrew stores
    internally.  Adapters translate it into their framework objects
    (e.g. CrewAI ``Agent``, or a native prompt template).
    """
    role: str
    goal: str
    backstory: str = ""
    tools: list[ToolSpec] = field(default_factory=list)
    model: str = "gpt-4"
    capabilities: list[str] = field(default_factory=list)
    allow_delegation: bool = True
    # Optional overrides forwarded to the backend
    max_iter: int | None = None
    max_execution_time: int | None = None
    memory: bool = False
    reasoning: bool = False
    max_reasoning_attempts: int | None = None
    system_prompt: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# FrameworkAdapter — the execution contract
# ---------------------------------------------------------------------------

class FrameworkAdapter(ABC):
    """Abstract interface for multi-agent execution backends.

    teaming24's orchestration layer (LocalCrew) calls these methods
    *after* the ANRouter has decided which pool members handle which
    subtasks and *before* the results are aggregated.

    Lifecycle:
      1.  LocalCrew creates ``AgentSpec`` objects (framework-agnostic).
      2.  LocalCrew calls ``execute_hierarchical`` or ``execute_sequential``.
      3.  The adapter translates specs → framework objects, runs the crew,
          and returns the final result string.

    Adapters must be **stateless** across calls — each ``execute_*``
    invocation is self-contained.
    """

    @abstractmethod
    async def execute_hierarchical(
        self,
        prompt: str,
        manager: AgentSpec,
        workers: list[AgentSpec],
        step_callback: StepCallback | None = None,
        task_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Run a hierarchical crew: *manager* delegates to *workers*.

        Args:
            prompt: The task description to execute.
            manager: The manager/coordinator agent spec.
            workers: Worker agent specs that the manager can delegate to.
            step_callback: Called for every intermediate step.
            task_id: Correlating task identifier for logging.
            context: Additional execution context.

        Returns:
            The final result text produced by the crew.
        """

    @abstractmethod
    async def execute_sequential(
        self,
        prompt: str,
        agents: list[AgentSpec],
        step_callback: StepCallback | None = None,
        task_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        """Run agents sequentially — each agent's output feeds the next.

        Args:
            prompt: The initial task description.
            agents: Agents executed in order.
            step_callback: Called for every intermediate step.
            task_id: Correlating task identifier for logging.
            context: Additional execution context.

        Returns:
            The final result text from the last agent.
        """
