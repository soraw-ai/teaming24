"""
CrewWrapper — wraps CrewAI Crew execution with Teaming24 integration.

This module provides plug-and-play crew execution: pass pre-built agents,
inject on_step for streaming, swap TaskManager, or use execute/execute_sync
from any caller (core.py, API handlers, tests).

**What it does:**
- Wraps CrewAI Crew.kickoff() with Teaming24 task lifecycle (start/complete/fail).
- Builds crew tasks from the prompt and process type (sequential/hierarchical).
- Integrates StepCallback for step-by-step streaming to TaskManager and on_step.
- Returns structured results (status, result, cost, duration).

**How it builds a crew from agent configs:**
- Agents are passed in at construction (from AgentFactory.create_organizer,
  create_coordinator, create_workers). CrewWrapper does not create agents.
- For hierarchical process: Organizer as manager_agent, Coordinator + Workers
  as local_team; planning and implementation tasks are created from the prompt.
- For sequential: single task assigned to the first agent.

**How it handles execution (sync/async, thread safety):**
- ``execute_sync()`` — runs Crew.kickoff() in the current thread. Use from
  background threads or sync contexts. Preferred when called from worker threads.
- ``execute()`` — runs Crew.kickoff() in a ThreadPoolExecutor via
  run_in_executor to avoid blocking the event loop. Use from async API handlers.
- Both paths use the same _build_crew + StepCallback; no shared mutable state
  between concurrent executions (each call gets its own crew instance).

**How results flow back to the caller:**
- Success: ``{status: "success", task_id, result, cost, duration}``
- Error: ``{status: "error", task_id, error, cost: {}}``
- Already running/completed: ``{status: task.status.value, result, error}``
- TaskManager is updated (start_task, complete_task, fail_task); cost and
  duration come from the task's cost/duration attributes.

**Usage examples:**
    # From core.py / LocalCrew
    wrapper = CrewWrapper(
        agents=[organizer, coordinator, *workers],
        task_manager=get_task_manager(),
        on_step=lambda d: queue.put(d),
        process="hierarchical",
    )
    result = wrapper.execute_sync(prompt, task_id)

    # Async from FastAPI
    result = await wrapper.execute(prompt, task_id)

    # Custom agents + no streaming
    wrapper = CrewWrapper(agents=[my_agent], streaming=False)
    result = wrapper.execute_sync("Analyze this data")
"""

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from teaming24.agent.events import StepCallback
from teaming24.config import get_config
from teaming24.task import TaskManager, get_task_manager
from teaming24.utils.logger import LogSource, get_agent_logger, get_logger

logger = get_logger(__name__)

# Module-level refcount registry for concurrent agent tool management.
# Keys: id(agent) → {'agent': agent, 'original_tools': list, 'refcount': int}
# Prevents a second concurrent CrewWrapper from overwriting the saved tools
# of a first wrapper using the same shared manager agent object.
_agent_tool_registry: dict[int, dict] = {}
_agent_tool_lock = threading.Lock()

# Lazy CrewAI imports
CREWAI_AVAILABLE = False
Crew = None
Process = None
Task = None

try:
    from crewai import Crew, Process, Task
    CREWAI_AVAILABLE = True
except ImportError:
    logger.debug("CrewAI core classes unavailable; CrewWrapper will be disabled")
    pass


class CrewWrapper:
    """Wrapper for CrewAI Crew with Teaming24 integration.

    Provides configuration-driven crew creation, task manager integration,
    step-by-step streaming callbacks, and cost tracking. Plug-and-play:
    inject agents, TaskManager, on_step; use execute or execute_sync
    depending on your threading model.
    """

    def __init__(self,
                 agents: list[Any] = None,
                 task_manager: TaskManager = None,
                 on_step: Callable[[dict], None] = None,
                 process: str = "sequential",
                 verbose: bool = True,
                 memory: bool = False,
                 planning: bool = False,
                 planning_llm: str = "",
                 reasoning: bool = False,
                 max_reasoning_attempts: int = 0,
                 streaming: bool = True):
        """Initialize the crew wrapper.

        Args:
            agents: Pre-built CrewAI agents (from AgentFactory).
            task_manager: TaskManager for lifecycle tracking; defaults to global.
            on_step: Callback for each step (e.g., queue.put for SSE).
            process: "sequential" or "hierarchical".
            verbose: CrewAI verbose mode.
            memory: Enable CrewAI memory.
            planning: Enable CrewAI planning mode.
            planning_llm: Model for planning (if planning=True).
            reasoning: Enable reasoning mode.
            max_reasoning_attempts: Max reasoning attempts.
            streaming: Whether step streaming is enabled.
        """
        if not CREWAI_AVAILABLE:
            raise RuntimeError("CrewAI not installed. Run: uv pip install crewai")

        self.agents = agents or []
        _cfg = get_config()
        _defaults = _cfg.agents.defaults

        self.task_manager = task_manager or get_task_manager()
        self.on_step = on_step
        self.process = process
        self.verbose = verbose
        self.memory = memory
        self.planning = planning
        self.planning_llm = planning_llm or _defaults.planning_llm
        self.reasoning = reasoning
        self.max_reasoning_attempts = max_reasoning_attempts or _defaults.max_reasoning_attempts
        self.streaming = streaming
        self._crew = None
        self._saved_manager_tools = None
        self._managed_agent_id: int | None = None

    def add_agent(self, agent: Any):
        """Add an agent to the crew (before execution)."""
        self.agents.append(agent)

    def _create_task(self, description: str, agent: Any = None,
                     expected_output: str = None, context: list[Any] = None,
                     markdown: bool = True) -> Any:
        """Create a CrewAI Task with Teaming24 format hint for final answer."""
        format_hint = (
            "\n\nWhen you are ready to give your final response, you MUST "
            "use this exact format:\n"
            "Thought: I have completed the task.\n"
            "Final Answer: [your complete response here]"
        )
        task_kwargs = {
            "description": description + format_hint,
            "agent": agent or (self.agents[0] if self.agents else None),
            "expected_output": expected_output or "A comprehensive response",
            "markdown": markdown,
        }
        if context:
            task_kwargs["context"] = context
        return Task(**task_kwargs)

    def _create_crew_tasks(self, prompt: str, process_type: Any) -> list[Any]:
        """Create CrewAI tasks based on prompt and process type.

        Sequential: single task. Hierarchical: planning + implementation
        (+ optional review) tasks with context chaining.
        """
        tasks = []

        if len(self.agents) <= 1:
            return [self._create_task(prompt)]

        if process_type == Process.hierarchical:
            local_team = self.agents[1:] if len(self.agents) > 1 else self.agents
            coordinator = local_team[0]
            workers = local_team[1:] if len(local_team) > 1 else []

            if workers:
                planning_task = self._create_task(
                    description=f"""
You are the local team coordinator. The Organizer has routed this task to
your local team. Analyze the request and create a detailed plan for
your Workers to execute.

USER REQUEST:
{prompt}

Your planning should include:
1. **Understanding**: What is being asked
2. **Subtask breakdown**: Split into actionable subtasks for Workers
3. **Worker assignment**: Which Worker handles each subtask
4. **Execution plan**: Step-by-step instructions
5. **Verification**: How to confirm correctness
""",
                    agent=coordinator,
                    expected_output="A detailed execution plan with understanding, subtask breakdown, execution steps, and success criteria",
                )
                tasks.append(planning_task)

                impl_task = self._create_task(
                    description=f"""
Based on the Coordinator's plan, implement the solution for:

{prompt}

IMPORTANT:
1. If code is needed, provide COMPLETE, RUNNABLE code
2. Include exact commands or steps to execute the solution
3. Specify file names and where to save them
4. Include environment setup if needed
5. Provide verification steps
""",
                    agent=workers[0],
                    expected_output="A complete, executable solution with instructions, code, and verification",
                    context=[planning_task],
                )
                tasks.append(impl_task)

                if len(workers) >= 2:
                    review_task = self._create_task(
                        description=f"""
Review and validate the solution for:

Original request: {prompt}

Ensure the solution:
1. Is COMPLETE and RUNNABLE as-is
2. Has clear, executable instructions
3. Includes all necessary files and dependencies
4. Has verification steps that actually work

Provide your final review with any fixes or improvements needed.
""",
                        agent=workers[1],
                        expected_output="Final review with status, summary, and quick start",
                        context=[planning_task, impl_task],
                    )
                    tasks.append(review_task)
            else:
                tasks.append(self._create_task(
                    description=f"Handle this request: {prompt}",
                    agent=coordinator,
                    expected_output="A comprehensive response",
                ))
            return tasks

        # Sequential mode
        return [self._create_task(
            description=prompt,
            agent=self.agents[0],
            expected_output="A comprehensive response to the user's request",
        )]

    def _build_crew(self, prompt: str, task_id: str,
                    step_callback: StepCallback) -> Any:
        """Build the CrewAI Crew object for the given prompt.

        Creates tasks via _create_crew_tasks, wires step_callback,
        and sets manager_agent for hierarchical process.
        """
        process_type = Process.sequential
        if self.process == "hierarchical":
            process_type = Process.hierarchical

        crew_tasks = self._create_crew_tasks(prompt, process_type)
        logger.info(f"[CrewExec] {len(crew_tasks)} task(s), process={process_type.value}")

        if process_type == Process.hierarchical and len(self.agents) > 1:
            manager_agent = self.agents[0]
            local_team = self.agents[1:]
            # Clear manager tools for hierarchical mode. Use a module-level
            # refcount registry so concurrent wrappers sharing the same agent
            # object don't clobber each other's saved-tools snapshot.
            with _agent_tool_lock:
                aid = id(manager_agent)
                if aid not in _agent_tool_registry:
                    _agent_tool_registry[aid] = {
                        'agent': manager_agent,
                        'original_tools': list(manager_agent.tools or []),
                        'refcount': 0,
                    }
                    manager_agent.tools = []
                _agent_tool_registry[aid]['refcount'] += 1
                self._managed_agent_id = aid
                # Read original_tools inside the lock so we can't race with _restore
                self._saved_manager_tools = (manager_agent, _agent_tool_registry[aid]['original_tools'])
            crew_kwargs = {
                "agents": local_team,
                "tasks": crew_tasks,
                "process": process_type,
                "verbose": self.verbose,
                "memory": self.memory,
                "step_callback": step_callback,
                "manager_agent": manager_agent,
            }
        else:
            crew_kwargs = {
                "agents": self.agents,
                "tasks": crew_tasks,
                "process": process_type,
                "verbose": self.verbose,
                "memory": self.memory,
                "step_callback": step_callback,
            }

        if self.planning:
            crew_kwargs["planning"] = True
            crew_kwargs["planning_llm"] = self.planning_llm

        try:
            self._crew = Crew(**crew_kwargs, tracing=False)
        except TypeError:
            logger.debug("Crew constructor rejected advanced kwargs; retrying with compatibility fallback")
            for key in ["planning", "planning_llm", "tracing"]:
                crew_kwargs.pop(key, None)
            self._crew = Crew(**crew_kwargs)
        return self._crew

    def _restore_manager_tools(self):
        """Restore manager agent tools cleared for hierarchical mode.

        Uses the module-level refcount registry so the last concurrent
        wrapper to finish is the one that actually restores the tools.
        """
        aid = self._managed_agent_id
        if aid is None:
            self._saved_manager_tools = None
            return
        with _agent_tool_lock:
            entry = _agent_tool_registry.get(aid)
            if entry is not None:
                entry['refcount'] -= 1
                if entry['refcount'] <= 0:
                    entry['agent'].tools = entry['original_tools']
                    del _agent_tool_registry[aid]
        self._managed_agent_id = None
        self._saved_manager_tools = None

    async def execute(self, prompt: str, task_id: str = None,
                      context: dict[str, Any] = None,
                      _subtask: bool = False) -> dict:
        """Execute a task with the crew (async).

        Runs Crew.kickoff() in a ThreadPoolExecutor to avoid blocking
        the event loop. Use from async API handlers.
        """
        if task_id:
            task = self.task_manager.get_task(task_id)
        else:
            task = self.task_manager.create_task(prompt)
            task_id = task.id

        if not _subtask:
            if task and task.status.value in ("running", "completed", "failed"):
                return {"status": task.status.value, "result": getattr(task, "result", None) or "",
                        "error": f"Task already {task.status.value}"}
            started = self.task_manager.start_task(task_id)
            if started is None:
                return {"status": "error", "result": "", "error": f"Task {task_id} could not be started"}

        step_callback = StepCallback(task_id, self.task_manager, self.on_step)
        logger.info(f"[CrewExec] Starting async: task_id={task_id}, agents={len(self.agents)}")

        try:
            self._build_crew(prompt, task_id, step_callback)

            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as executor:
                result = await asyncio.get_running_loop().run_in_executor(executor, self._crew.kickoff)
        except Exception as e:
            logger.error(f"[CrewExec] Async failed: task_id={task_id}, error={e}", exc_info=True)
            if not _subtask:
                self.task_manager.fail_task(task_id, str(e))
            return {"status": "error", "task_id": task_id, "error": str(e), "cost": {}}
        finally:
            self._restore_manager_tools()

        result_str = str(result)
        if not _subtask:
            self.task_manager.complete_task(task_id, result_str)
        final_task = self.task_manager.get_task(task_id)
        return {
            "status": "success", "task_id": task_id, "result": result_str,
            "cost": final_task.cost.to_dict() if final_task else {},
            "duration": final_task.duration if final_task else 0,
        }

    def execute_sync(self, prompt: str, task_id: str = None,
                     context: dict[str, Any] = None,
                     _subtask: bool = False) -> dict:
        """Synchronous execution.

        Runs Crew.kickoff() in the current thread. Preferred when called
        from a background thread or sync context.
        """
        if task_id:
            task = self.task_manager.get_task(task_id)
        else:
            task = self.task_manager.create_task(prompt)
            task_id = task.id

        if not _subtask:
            if task and task.status.value in ("running", "completed", "failed"):
                return {"status": task.status.value, "result": getattr(task, "result", None) or "",
                        "error": f"Task already {task.status.value}"}
            started = self.task_manager.start_task(task_id)
            if started is None:
                return {"status": "error", "result": "", "error": f"Task {task_id} could not be started"}

        step_callback = StepCallback(task_id, self.task_manager, self.on_step)

        _short = f"task_{task_id.split('_')[1][-6:]}_{task_id.split('_')[-1]}" if "_" in task_id else task_id[:20]
        task_logger = get_agent_logger(LogSource.TASK, _short)
        prompt_preview = prompt[:60].replace("\n", " ").strip()
        task_logger.info(f"TASK EXECUTION: {prompt_preview}... (process={self.process}, agents={len(self.agents)})")

        try:
            self._build_crew(prompt, task_id, step_callback)
            result = self._crew.kickoff()
        except Exception as e:
            task_logger.error(f"TASK FAILED: {str(e)[:60]}")
            logger.error(f"Crew sync execution error: {e}")
            if not _subtask:
                self.task_manager.fail_task(task_id, str(e))
            return {"status": "error", "task_id": task_id, "error": str(e), "cost": {}}
        finally:
            self._restore_manager_tools()

        result_str = str(result)

        token_usage = {}
        if hasattr(result, "token_usage"):
            token_usage = result.token_usage
        elif hasattr(result, "usage_metrics"):
            token_usage = result.usage_metrics

        # Persist cost data to the Task object
        if token_usage:
            if isinstance(token_usage, dict):
                in_tok = token_usage.get("prompt_tokens", 0) or token_usage.get("input_tokens", 0)
                out_tok = token_usage.get("completion_tokens", 0) or token_usage.get("output_tokens", 0)
            else:
                in_tok = getattr(token_usage, "prompt_tokens", 0) or getattr(token_usage, "input_tokens", 0)
                out_tok = getattr(token_usage, "completion_tokens", 0) or getattr(token_usage, "output_tokens", 0)
            self.task_manager.update_cost(task_id, input_tokens=in_tok, output_tokens=out_tok)

        if not _subtask:
            self.task_manager.complete_task(task_id, result_str)

        final_task = self.task_manager.get_task(task_id)
        cost_info = final_task.cost.to_dict() if final_task else {}

        duration = final_task.duration if final_task else 0
        task_logger.info(f"TASK COMPLETED (duration={duration:.1f}s, tokens={cost_info.get('total_tokens', 0)})")

        return {
            "status": "success", "task_id": task_id, "result": result_str,
            "cost": cost_info, "duration": duration,
        }
