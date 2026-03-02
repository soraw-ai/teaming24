"""
CrewAI Event Listener and Step Callback for Teaming24.

This module was extracted from ``core.py`` to provide plug-and-play event
handling and configuration types. It contains:

**What was extracted from core.py:**
- **CrewAI event listening** — Teaming24EventListener subscribes to CrewAI's
  event bus (crew_started, agent_started/completed, task phases, LLM chunks)
  for real-time streaming when CrewAI events are available.
- **Step callbacks** — StepCallback is the synchronous handler passed to
  CrewAI's step_callback; it records steps in TaskManager and forwards to
  external callbacks (e.g., SSE queue.put).
- **Config dataclasses** — AgentConfig and CrewConfig provide typed config
  structures used by the factory and crew wrapper.

**How to extend with new event types:**
1. Import additional event classes from ``crewai.events`` (e.g., custom events).
2. In ``Teaming24EventListener.setup_listeners()``, add
   ``@crewai_event_bus.on(YourNewEvent)`` handlers that call ``self._emit()``.
3. Emit a dict with ``type`` and any payload; the on_event callback receives it.

**How StepCallback integrates with the SSE streaming pipeline:**
- CrewAI invokes the step_callback synchronously from its execution thread.
- StepCallback records each step in TaskManager and calls ``on_step(step_data)``.
- The caller (e.g., core.py) passes ``on_step=queue.put`` so step_data is
  enqueued; the SSE generator reads from the queue and streams to the client.
- Must be synchronous: use ``queue.put()``, not ``await`` or async callbacks.

**How AgentConfig and CrewConfig are used by the factory:**
- AgentConfig: Typed agent definition (role, goal, backstory, tools, model).
  The factory reads from ``config.agents.*`` (OrganizerConfig, etc.) which
  map to these fields; AgentConfig can be used for programmatic agent creation.
- CrewConfig: Crew-level settings (process, verbose, memory, max_rpm).
  Used when building a Crew to control execution mode and limits.

**Usage examples:**
    # Setup event listeners for SSE streaming
    from teaming24.agent.events import setup_crewai_events
    listener = setup_crewai_events(on_event=lambda e: queue.put(e))

    # Create a step callback for a task
    from teaming24.agent.events import StepCallback
    cb = StepCallback(task_id, task_manager, on_step=queue.put)
    agent = factory.create_agent(config, step_callback=cb)

    # Use config dataclasses for custom crew setup
    from teaming24.agent.events import AgentConfig, CrewConfig
    cfg = AgentConfig(name="Custom", role="...", goal="...", ...)
"""

import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from teaming24.config import get_config
from teaming24.task import TaskManager
from teaming24.utils.logger import LogSource, get_agent_logger, get_logger

logger = get_logger(__name__)

# CrewAI event types (lazy imports — populated if available)
CREWAI_EVENTS_AVAILABLE = False
CrewKickoffStartedEvent = None
CrewKickoffCompletedEvent = None
AgentExecutionStartedEvent = None
AgentExecutionCompletedEvent = None
TaskStartedEvent = None
TaskCompletedEvent = None
LLMStreamChunkEvent = None

try:
    from crewai.events import (
        AgentExecutionCompletedEvent,
        AgentExecutionStartedEvent,
        CrewKickoffCompletedEvent,
        CrewKickoffStartedEvent,
        TaskCompletedEvent,
        TaskStartedEvent,
    )
    try:
        from crewai.events import LLMStreamChunkEvent
    except ImportError:
        logger.debug("CrewAI LLMStreamChunkEvent not available")
        pass
    CREWAI_EVENTS_AVAILABLE = True
except ImportError:
    logger.debug("CrewAI events module not available")
    pass


# ---------------------------------------------------------------------------
# Event Listener
# ---------------------------------------------------------------------------

class Teaming24EventListener:
    """Event listener for CrewAI events to enable real-time streaming.

    Captures LLM stream chunks, agent execution events, task events,
    and crew lifecycle events. Uses a singleton pattern so one listener
    can be shared across crew executions. Plug-and-play: swap the
    on_event callback to route events to SSE, logs, or custom handlers.
    """

    _instance = None
    _initialized = False

    def __init__(self, on_event: Callable[[dict], None] = None):
        """Initialize the listener. on_event receives dicts with type, task_id, and payload."""
        self.on_event = on_event
        self._tls = threading.local()

    @classmethod
    def get_instance(cls, on_event: Callable[[dict], None] = None) -> "Teaming24EventListener":
        """Return the singleton instance, optionally updating the callback."""
        if cls._instance is None:
            cls._instance = cls(on_event)
        elif on_event:
            cls._instance.on_event = on_event
        return cls._instance

    def set_callback(self, on_event: Callable[[dict], None]):
        """Replace the event callback (e.g., to point at a new SSE queue)."""
        self.on_event = on_event

    def set_active_task(self, task_id: str):
        """Set the active task ID for event payloads (per-thread)."""
        self._tls.active_task_id = task_id

    @property
    def _active_task_id(self):
        return getattr(self._tls, 'active_task_id', None)

    @property
    def _active_agent(self):
        return getattr(self._tls, 'active_agent', None)

    @_active_agent.setter
    def _active_agent(self, value):
        self._tls.active_agent = value

    def _emit(self, event_type: str, data: dict):
        if self.on_event:
            try:
                self.on_event({"type": event_type, "task_id": self._active_task_id, **data})
            except Exception as e:
                logger.debug(f"Event emission failed: {e}")

    def setup_listeners(self, crewai_event_bus):
        """Register handlers on the CrewAI event bus for crew/agent/task/LLM events."""
        if not CREWAI_EVENTS_AVAILABLE:
            return

        @crewai_event_bus.on(CrewKickoffStartedEvent)
        def on_crew_started(source, event):
            self._emit("crew_started", {
                "crew_name": getattr(event, "crew_name", "Crew"),
                "message": "Crew execution started",
            })

        @crewai_event_bus.on(CrewKickoffCompletedEvent)
        def on_crew_completed(source, event):
            self._emit("crew_completed", {
                "crew_name": getattr(event, "crew_name", "Crew"),
                "message": "Crew execution completed",
            })

        @crewai_event_bus.on(AgentExecutionStartedEvent)
        def on_agent_started(source, event):
            agent_role = getattr(event.agent, "role", "Agent") if hasattr(event, "agent") else "Agent"
            self._active_agent = agent_role
            self._emit("agent_started", {"agent": agent_role, "message": f"{agent_role} started working"})

        @crewai_event_bus.on(AgentExecutionCompletedEvent)
        def on_agent_completed(source, event):
            agent_role = getattr(event.agent, "role", "Agent") if hasattr(event, "agent") else "Agent"
            self._emit("agent_completed", {
                "agent": agent_role,
                "output": str(getattr(event, "output", ""))[:500],
                "message": f"{agent_role} completed",
            })

        @crewai_event_bus.on(TaskStartedEvent)
        def on_task_started(source, event):
            task_desc = getattr(event.task, "description", "")[:100] if hasattr(event, "task") else ""
            self._emit("task_phase_started", {"description": task_desc, "message": f"Task started: {task_desc[:50]}..."})

        @crewai_event_bus.on(TaskCompletedEvent)
        def on_task_completed(source, event):
            self._emit("task_phase_completed", {"message": "Task phase completed"})

        if LLMStreamChunkEvent:
            @crewai_event_bus.on(LLMStreamChunkEvent)
            def on_llm_chunk(source, event):
                chunk = getattr(event, "chunk", "") or getattr(event, "content", "")
                if chunk:
                    self._emit("llm_chunk", {"chunk": chunk, "agent": self._active_agent})


def get_event_listener() -> Teaming24EventListener:
    """Return the global event listener singleton."""
    return Teaming24EventListener.get_instance()


def setup_crewai_events(on_event: Callable[[dict], None] = None):
    """Setup CrewAI event listeners for real-time streaming.

    Registers Teaming24EventListener on crewai_event_bus. Returns the
    listener if CrewAI events are available, else None. Plug-and-play:
    pass a callback (e.g., queue.put) to route events to your pipeline.
    """
    if not CREWAI_EVENTS_AVAILABLE:
        logger.debug("CrewAI events not available, using step callbacks only")
        return None
    try:
        from crewai.events import crewai_event_bus
        listener = get_event_listener()
        listener.set_callback(on_event)
        listener.setup_listeners(crewai_event_bus)
        logger.info("CrewAI event listeners configured for streaming")
        return listener
    except Exception as e:
        logger.debug(f"Failed to setup CrewAI events: {e}")
        return None


# ---------------------------------------------------------------------------
# Configuration Dataclasses
# ---------------------------------------------------------------------------

@dataclass
class AgentConfig:
    """Configuration for a CrewAI agent.

    Typed dataclass for agent definition. Used by AgentFactory and
    programmatic agent creation. Fields map to CrewAI Agent kwargs.
    """
    name: str
    role: str
    goal: str
    backstory: str
    capabilities: list[str]
    tools: list[str]
    model: str = "gpt-4"
    allow_delegation: bool = True
    verbose: bool = True


@dataclass
class CrewConfig:
    """Configuration for a CrewAI crew.

    Typed dataclass for crew-level settings (process, verbose, memory).
    Used when building a Crew to control execution mode and limits.
    """
    name: str
    process: str = "sequential"
    verbose: bool = True
    memory: bool = False
    max_rpm: int = 10


# ---------------------------------------------------------------------------
# Step Callback
# ---------------------------------------------------------------------------

class StepCallback:
    """Callback handler for CrewAI step events.

    Streams agent thoughts, actions, and observations to task manager
    and optional external callbacks (e.g., SSE via thread-safe queue).

    Tracks per-step timing and includes the task's current progress data
    so that every ``task_step`` SSE event carries up-to-date progress.

    Note: CrewAI calls this synchronously from its execution thread,
    so the on_step callback must be synchronous (e.g., queue.put).
    """

    def __init__(self, task_id: str, task_manager: TaskManager,
                 on_step: Callable[[dict], None] = None):
        """Initialize the callback. on_step must be synchronous (e.g., queue.put)."""
        self.task_id = task_id
        self.task_manager = task_manager
        self.on_step = on_step
        self._step_count = 0
        self._last_step_time: float = 0.0
        _api_cfg = get_config().system.api
        self._step_content_max_chars = _api_cfg.step_content_max_chars
        self._step_thought_max_chars = _api_cfg.step_thought_max_chars
        self._step_observation_max_chars = _api_cfg.step_observation_max_chars

    def _log_parse_failure(self, agent: str, action: str, content: str,
                           thought: Any, observation: Any, raw_output: Any) -> None:
        """Log full LLM parse failure details to a dedicated file."""
        import os
        from pathlib import Path
        log_dir = Path(os.getcwd()) / "logs"
        log_dir.mkdir(exist_ok=True)
        log_file = log_dir / "llm_parse_failures.log"

        raw_attrs = {}
        for attr in dir(raw_output):
            if attr.startswith("_"):
                continue
            try:
                val = getattr(raw_output, attr)
                if callable(val):
                    continue
                raw_attrs[attr] = repr(val)[:2000]
            except Exception as exc:
                logger.debug(
                    "Failed to serialize raw_output attr %s for parse-failure log: %s",
                    attr,
                    exc,
                    exc_info=True,
                )

        import time as _time
        ts = _time.strftime("%Y-%m-%d %H:%M:%S")
        separator = "=" * 80
        entry = (
            f"\n{separator}\n"
            f"[{ts}] LLM PARSE FAILURE\n"
            f"Task: {self.task_id}\n"
            f"Agent: {agent}\n"
            f"Action: {action}\n"
            f"Step #: {self._step_count}\n"
            f"{separator}\n"
            f"CONTENT (full):\n{content}\n"
            f"{separator}\n"
            f"THOUGHT (full):\n{thought}\n"
            f"{separator}\n"
            f"OBSERVATION (full):\n{observation}\n"
            f"{separator}\n"
            f"RAW OUTPUT TYPE: {type(raw_output).__name__}\n"
            f"RAW OUTPUT ATTRIBUTES:\n"
        )
        for k, v in raw_attrs.items():
            entry += f"  {k}: {v}\n"
        entry += f"{separator}\n"

        try:
            with open(log_file, "a", encoding="utf-8") as f:
                f.write(entry)
            logger.warning(
                f"LLM parse failure logged to {log_file} "
                f"(task={self.task_id}, agent={agent}, step={self._step_count})"
            )
        except Exception:
            logger.warning(f"LLM PARSE FAILURE (task={self.task_id}):\n{content[:1000]}")

    def __call__(self, output: Any) -> None:
        """Handle a step output from CrewAI (invoked by CrewAI as step_callback)."""
        self._step_count += 1

        logger.debug(f"StepCallback step {self._step_count}, type: {type(output).__name__}")

        # --- Extract agent name ---
        agent_name = None
        if hasattr(output, "agent"):
            agent_obj = output.agent
            if hasattr(agent_obj, "role"):
                agent_name = agent_obj.role
            elif hasattr(agent_obj, "name"):
                agent_name = agent_obj.name
            elif isinstance(agent_obj, str):
                agent_name = agent_obj
        if not agent_name and hasattr(output, "agent_name"):
            agent_name = output.agent_name
        if not agent_name and hasattr(output, "task"):
            task_obj = output.task
            if hasattr(task_obj, "agent"):
                task_agent = task_obj.agent
                agent_name = getattr(task_agent, "role", None) or getattr(task_agent, "name", None)
        if not agent_name:
            agent_name = "Organizer"

        thought = getattr(output, "thought", None)
        action = getattr(output, "action", "thinking")
        action_input = getattr(output, "action_input", "")
        observation = getattr(output, "observation", None)

        # Extract reasoning
        reasoning = None
        for attr in ["reasoning", "thoughts", "thinking", "plan", "explanation"]:
            if hasattr(output, attr):
                val = getattr(output, attr)
                if val:
                    reasoning = str(val)
                    break
        if not thought and reasoning:
            thought = reasoning

        # Extract token usage
        token_usage = None
        for attr in ["token_usage", "usage", "tokens", "usage_metrics"]:
            if hasattr(output, attr):
                val = getattr(output, attr)
                if val:
                    token_usage = val if isinstance(val, dict) else str(val)
                    break

        # Extract content
        if hasattr(output, "raw"):
            content = str(output.raw) if output.raw else str(output)
        elif hasattr(output, "text"):
            content = str(output.text)
        elif hasattr(output, "result"):
            content = str(output.result) if output.result else str(output)
        elif action_input:
            content = str(action_input)
        else:
            content = str(output)

        # Detect tool-input format errors (transient, auto-retry)
        _tool_input_err_markers = (
            "action input is not a valid key",
            "could not parse tool input",
            "not a valid key, value dictionary",
        )
        raw_thought_str = (str(thought) if thought else "").lower()
        raw_content_str = content.lower() if content else ""
        is_tool_input_error = any(
            m in raw_content_str or m in raw_thought_str
            for m in _tool_input_err_markers
        )
        if is_tool_input_error:
            logger.debug(
                f"[StepCallback] Tool input format error (will auto-retry): "
                f"agent={agent_name}, content={content[:200]}"
            )
            action = "tool_input_retry"
            thought = None
            content = "Retrying tool call with corrected input format..."

        # Detect LLM parse failures
        has_parse_warning = (
            not is_tool_input_error
            and ("failed to parse" in raw_content_str or "failed to parse" in raw_thought_str)
        )
        is_parse_failure = False
        if has_parse_warning:
            output_type_name = type(output).__name__
            agent_finish_output = getattr(output, "output", None) or getattr(output, "text", None)
            if output_type_name == "AgentFinish" and agent_finish_output and len(str(agent_finish_output)) > 50:
                logger.debug(
                    f"[StepCallback] CrewAI parse warning (AgentFinish with "
                    f"{len(str(agent_finish_output))} chars) — treating as successful"
                )
                thought = None
                content = str(agent_finish_output)
            else:
                is_parse_failure = True
                self._log_parse_failure(agent_name, action, content, thought, observation, output)

        # Record step in task manager
        self.task_manager.add_step(
            self.task_id,
            agent=str(agent_name),
            action=str(action),
            content=content[:self._step_content_max_chars],
            thought=thought,
            observation=observation,
        )

        # Smooth executing progress so long-running tasks do not appear as
        # a flat 25% until completion.
        try:
            task_obj = self.task_manager.get_task(self.task_id)
            if task_obj:
                current_pct = int(getattr(task_obj.progress, "percentage", 0) or 0)
                phase = str(getattr(task_obj.progress, "phase", "") or "")
                if phase in ("received", "routing", "dispatching", "executing") and current_pct < 85:
                    smooth_pct = min(80, 25 + self._step_count * 6)
                    if smooth_pct > current_pct:
                        self.task_manager.update_progress(
                            self.task_id,
                            phase="executing",
                            percentage=smooth_pct,
                            phase_label=f"Executing · step {self._step_count}",
                        )
        except Exception as progress_exc:
            logger.warning(
                "Failed to smooth step progress task=%s step=%s: %s",
                self.task_id,
                self._step_count,
                progress_exc,
                exc_info=True,
            )

        # Track executing agent
        agent_lower = str(agent_name).lower()
        if "organizer" not in agent_lower:
            task = self.task_manager.get_task(self.task_id)
            if task:
                if "coordinator" in agent_lower:
                    task.assign_to(str(agent_name))
                else:
                    task.add_delegated_agent(str(agent_name))

        # Log agent activity
        content_preview = content[:120].replace("\n", " ").strip() if content else ""
        thought_preview = str(thought)[:100].replace("\n", " ").strip() if thought else ""
        agent_short = agent_name[:20] if len(agent_name) > 20 else agent_name
        action_short = action[:15] if len(action) > 15 else action
        token_str = ""
        if token_usage and isinstance(token_usage, dict):
            total = token_usage.get("total_tokens", 0)
            if total:
                token_str = f" [tokens: {total}]"

        step_logger = get_agent_logger(LogSource.AGENT, agent_short)
        if is_parse_failure:
            step_logger.warning(f"{action_short:<15} | LLM PARSE FAILURE — see logs/llm_parse_failures.log")
        else:
            step_logger.info(f"{action_short:<15} | {thought_preview or content_preview[:80]}{token_str}")

        # Step duration
        import time as _step_time
        now = _step_time.time()
        step_duration = round(now - self._last_step_time, 2) if self._last_step_time else None
        self._last_step_time = now

        # Notify external callback
        if self.on_step:
            try:
                step_data = {
                    "task_id": self.task_id,
                    "agent": str(agent_name),
                    "action": str(action),
                    "content": content[:self._step_content_max_chars],
                    "thought": thought[:self._step_thought_max_chars] if thought else None,
                    "observation": observation[:self._step_observation_max_chars] if observation else None,
                    "step_number": self._step_count,
                    "step_duration": step_duration,
                }
                if token_usage:
                    step_data["token_usage"] = token_usage
                task = self.task_manager.get_task(self.task_id)
                if task:
                    step_data["progress"] = task.progress.to_dict()
                self.on_step(step_data)
            except Exception as e:
                logger.warning(f"Step callback failed: {e}")
