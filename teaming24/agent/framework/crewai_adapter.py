"""
CrewAI adapter for the FrameworkAdapter interface.

Translates framework-agnostic AgentSpec / ToolSpec into CrewAI objects
(Agent, Task, Crew, Process) and delegates execution to Crew.kickoff().

This adapter wraps the existing CrewAI integration so that LocalCrew
can switch between CrewAI and the native runtime without changing any
orchestration code.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from teaming24.agent.framework.base import (
    AgentSpec,
    FrameworkAdapter,
    StepCallback,
    StepOutput,
    ToolSpec,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="crewai")


# ---------------------------------------------------------------------------
# Lazy CrewAI imports (optional dependency)
# ---------------------------------------------------------------------------

def _import_crewai():
    """Import CrewAI components; raises RuntimeError if unavailable."""
    try:
        from crewai import LLM, Agent, Crew, Process, Task
        from crewai.tools.base_tool import BaseTool
        return Agent, Crew, Process, Task, LLM, BaseTool
    except ImportError as e:
        raise RuntimeError(
            "CrewAI is required for the crewai adapter. "
            "Install it with: uv pip install crewai"
        ) from e


# ---------------------------------------------------------------------------
# ToolSpec → CrewAI BaseTool bridge
# ---------------------------------------------------------------------------

def _toolspec_to_crewai(spec: ToolSpec) -> Any:
    """Wrap a ToolSpec in a dynamic CrewAI BaseTool subclass."""
    _, _, _, _, _, BaseTool = _import_crewai()

    props = spec.parameters.get("properties", {})
    required = spec.parameters.get("required", [])

    # Build Pydantic field annotations for the dynamic model
    field_defs: dict[str, Any] = {}
    for pname, pschema in props.items():
        json_type = pschema.get("type", "string")
        py_type = {"string": str, "integer": int, "number": float, "boolean": bool,
                    "array": list, "object": dict}.get(json_type, str)
        if pname not in required:
            py_type = py_type | None
        field_defs[pname] = (py_type, pschema.get("default", ...))

    # The handler reference is captured in the closure.
    handler = spec.handler

    class _DynTool(BaseTool):
        name: str = spec.name
        description: str = spec.description

        def _run(self, **kwargs) -> str:
            if handler is None:
                return f"[error] tool '{spec.name}' has no handler"
            try:
                import asyncio as _aio
                if _aio.iscoroutinefunction(handler):
                    loop = _aio.new_event_loop()
                    try:
                        result = loop.run_until_complete(handler(**kwargs))
                    finally:
                        loop.close()
                else:
                    result = handler(**kwargs)
                return str(result) if result is not None else ""
            except Exception as exc:
                logger.warning(
                    "[CrewAIAdapter] Tool execution failed tool=%s kwargs=%s err=%s",
                    spec.name,
                    kwargs,
                    exc,
                    exc_info=True,
                )
                return f"[error] {spec.name}: {exc}"

    _DynTool.__name__ = f"DynTool_{spec.name}"
    return _DynTool()


# ---------------------------------------------------------------------------
# CrewAIAdapter
# ---------------------------------------------------------------------------

class CrewAIAdapter(FrameworkAdapter):
    """FrameworkAdapter backed by CrewAI's Crew.kickoff()."""

    def __init__(
        self,
        verbose: bool = False,
        memory: bool = False,
        planning: bool = False,
        planning_llm: str = "flock/gpt-5.2",
        **_extra,
    ):
        self.verbose = verbose
        self.memory = memory
        self.planning = planning
        self.planning_llm = planning_llm

    # ----- public API (FrameworkAdapter) ------------------------------------

    async def execute_hierarchical(
        self,
        prompt: str,
        manager: AgentSpec,
        workers: list[AgentSpec],
        step_callback: StepCallback | None = None,
        task_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        Agent, Crew, Process, Task, LLM, _ = _import_crewai()

        manager_agent = self._to_crewai_agent(manager, step_callback)
        worker_agents = [self._to_crewai_agent(w, step_callback) for w in workers]

        tasks = self._build_hierarchical_tasks(
            prompt, manager_agent, worker_agents, Task,
        )

        crew_kwargs: dict[str, Any] = {
            "agents": worker_agents,
            "tasks": tasks,
            "process": Process.hierarchical,
            "manager_agent": manager_agent,
            "verbose": self.verbose,
            "memory": self.memory,
        }
        if self.planning:
            crew_kwargs["planning"] = True
            crew_kwargs["planning_llm"] = self.planning_llm

        crew = Crew(**crew_kwargs)
        logger.info("[CrewAIAdapter] kickoff hierarchical  task_id=%s  workers=%d",
                     task_id, len(worker_agents))

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, crew.kickoff)
        return str(result)

    async def execute_sequential(
        self,
        prompt: str,
        agents: list[AgentSpec],
        step_callback: StepCallback | None = None,
        task_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        Agent, Crew, Process, Task, LLM, _ = _import_crewai()

        crewai_agents = [self._to_crewai_agent(a, step_callback) for a in agents]
        tasks = self._build_sequential_tasks(prompt, crewai_agents, Task)

        crew = Crew(
            agents=crewai_agents,
            tasks=tasks,
            process=Process.sequential,
            verbose=self.verbose,
            memory=self.memory,
        )
        logger.info("[CrewAIAdapter] kickoff sequential  task_id=%s  agents=%d",
                     task_id, len(crewai_agents))

        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(_executor, crew.kickoff)
        return str(result)

    # ----- internal helpers ------------------------------------------------

    def _to_crewai_agent(self, spec: AgentSpec, step_callback: StepCallback | None = None) -> Any:
        """Convert AgentSpec → CrewAI Agent."""
        Agent, _, _, _, LLM, _ = _import_crewai()

        tools = [_toolspec_to_crewai(t) for t in spec.tools]

        kwargs: dict[str, Any] = {
            "role": spec.role,
            "goal": spec.goal,
            "backstory": spec.backstory or "A capable assistant.",
            "tools": tools,
            "llm": self._get_llm(spec.model, LLM),
            "allow_delegation": spec.allow_delegation,
            "verbose": False,
        }
        if spec.max_iter:
            kwargs["max_iter"] = spec.max_iter
        if spec.max_execution_time:
            kwargs["max_execution_time"] = spec.max_execution_time
        if spec.reasoning:
            kwargs["reasoning"] = True
            if spec.max_reasoning_attempts:
                kwargs["max_reasoning_attempts"] = spec.max_reasoning_attempts
        if spec.memory:
            kwargs["memory"] = True

        if step_callback:
            def _bridge(output):
                step_callback(StepOutput(
                    agent=spec.role,
                    action="step",
                    content=str(output) if output else "",
                ))
            kwargs["step_callback"] = _bridge

        return Agent(**kwargs)

    def _get_llm(self, model: str, LLM_cls) -> Any:
        """Create a CrewAI LLM instance (or fall back to model string)."""
        try:
            return LLM_cls(model=model)
        except Exception as exc:
            logger.warning(
                "[CrewAIAdapter] Failed to instantiate CrewAI LLM for model=%s, using model string: %s",
                model,
                exc,
                exc_info=True,
            )
            return model

    # ----- task builders ---------------------------------------------------

    @staticmethod
    def _build_hierarchical_tasks(prompt, manager_agent, worker_agents, Task) -> list:
        """Create CrewAI Task objects for hierarchical delegation."""
        fmt_hint = (
            "\n\nWhen you are ready to give your final response, you MUST "
            "use this exact format:\n"
            "Thought: I have completed the task.\n"
            "Final Answer: [your complete response here]"
        )
        if not worker_agents:
            return [Task(
                description=prompt + fmt_hint,
                expected_output="A comprehensive response",
                markdown=True,
            )]

        tasks = []
        planning_task = Task(
            description=(
                f"You are the coordinator. Analyze the request and create a "
                f"detailed execution plan for your workers.\n\nREQUEST:\n{prompt}"
                + fmt_hint
            ),
            agent=worker_agents[0] if worker_agents else None,
            expected_output="A detailed execution plan with subtask breakdown.",
            markdown=True,
        )
        tasks.append(planning_task)

        if len(worker_agents) > 0:
            impl_task = Task(
                description=(
                    f"Based on the coordinator's plan, implement the solution "
                    f"for:\n\n{prompt}" + fmt_hint
                ),
                agent=worker_agents[0],
                expected_output="A complete, executable solution.",
                markdown=True,
                context=[planning_task],
            )
            tasks.append(impl_task)

        if len(worker_agents) > 1:
            review_task = Task(
                description=(
                    f"Review the implementation for quality, correctness, and "
                    f"completeness. Provide the final polished output for:\n\n{prompt}"
                    + fmt_hint
                ),
                agent=worker_agents[-1],
                expected_output="Final polished result after review.",
                markdown=True,
                context=[tasks[-1]],
            )
            tasks.append(review_task)

        return tasks

    @staticmethod
    def _build_sequential_tasks(prompt, agents, Task) -> list:
        """Create CrewAI Task objects for sequential execution."""
        fmt_hint = (
            "\n\nWhen you are ready to give your final response, you MUST "
            "use this exact format:\n"
            "Thought: I have completed the task.\n"
            "Final Answer: [your complete response here]"
        )
        tasks = []
        prev = None
        for i, agent in enumerate(agents):
            ctx = [prev] if prev else None
            task = Task(
                description=(
                    f"{'Continue from the previous result. ' if i > 0 else ''}"
                    f"Complete this task:\n\n{prompt}" + fmt_hint
                ),
                agent=agent,
                expected_output="A comprehensive response.",
                markdown=True,
                context=ctx,
            )
            tasks.append(task)
            prev = task
        return tasks
