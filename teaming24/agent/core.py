"""
CrewAI Integration Core for Teaming24.

This module contains LocalCrew — the main orchestration class for local
and remote task execution. Supporting classes have been extracted to:
- events.py: Teaming24EventListener, StepCallback, AgentConfig, CrewConfig
- factory.py: AgentFactory
- crew_wrapper.py: CrewWrapper

TODO(refactor): This module is still too large.
    Event types and workflow-step emission have been extracted, but
    LocalCrew still handles agent creation, routing, local execution,
    remote delegation, aggregation, and the self-improvement loop.
    Next split points:
    - core/execution.py — local and remote execution logic
    - core/aggregation.py — result aggregation
    - core/delegation.py — remote task submission and SSE subscription
"""

import asyncio
import importlib.util
import re
from collections.abc import Callable
from typing import Any

from teaming24.agent.crew_wrapper import CrewWrapper
from teaming24.agent.workflow_steps import emit_workflow_step

# Re-export extracted classes for backward compatibility
from teaming24.agent.factory import AgentFactory
from teaming24.config import get_config
from teaming24.prompting import render_prompt
from teaming24.task import TaskManager, TaskPhase, get_task_manager
from teaming24.utils.logger import LogSource, get_agent_logger, get_logger

logger = get_logger(__name__)


def log_phase(phase: str, message: str, indent: int = 0, agent_name: str = ""):
    prefix = "  " * indent
    log = get_agent_logger(LogSource.AGENT, agent_name) if agent_name else logger
    log.info(f"{prefix}Phase: {phase} -- {message}")


def log_header(title: str, agent_name: str = ""):
    log = get_agent_logger(LogSource.AGENT, agent_name) if agent_name else logger
    log.info(f"{'─'*50} {title} {'─'*50}")


def log_footer(status: str, duration: float = 0, tokens: int = 0, agent_name: str = ""):
    stats = []
    if duration > 0:
        stats.append(f"duration={duration:.1f}s")
    if tokens > 0:
        stats.append(f"tokens={tokens}")
    stats_str = " | ".join(stats) if stats else ""
    log = get_agent_logger(LogSource.AGENT, agent_name) if agent_name else logger
    log.info(f"{status}" + (f" ({stats_str})" if stats_str else ""))


# CrewAI imports (lazy to handle missing dependency)
CREWAI_AVAILABLE = False
CREWAI_EVENTS_AVAILABLE = False
Agent = None
Crew = None
Task = None
Process = None
BaseTool = None
LLM = None

try:
    import crewai as _crewai
    from crewai.tools.base_tool import BaseTool as _CrewAIBaseTool

    LLM = _crewai.LLM
    Agent = _crewai.Agent
    Crew = _crewai.Crew
    Process = _crewai.Process
    Task = _crewai.Task
    BaseTool = _CrewAIBaseTool
    CREWAI_AVAILABLE = True
    CREWAI_EVENTS_AVAILABLE = importlib.util.find_spec("crewai.events") is not None
except ImportError:
    logger.warning("CrewAI not installed. Run: uv pip install crewai")


# ============================================================================
# LocalCrew — Main Orchestration
# ============================================================================

class LocalCrew:
    """
    Pre-configured crew for local task execution.

    Task flow:
    1. Agentic Node Workforce Pool contains ALL (Coordinator + ANs). Before execution,
       ANRouter selects a subset from the pool. Organizer splits task and assigns
       subtasks to the selected members only.
    2. Local Agent Workforce Pool contains ALL Workers. Coordinator receives subtask;
       LocalAgentRouter selects subset; assigns sub-subtasks; Workers execute.
    3. Organizer aggregates results from all selected Coordinators/ANs; evaluates quality.

    Agent hierarchy:
    - Organizer: Manages Coordinators and ANs (Agentic Node Workforce Pool level).
    - Coordinator: Manages Workers (selects and assigns sub-subtasks).
    - Workers: Execute assigned sub-subtasks.

    Execution paths:
    - ``execute()`` / ``execute_sync()``: Full Organizer flow (local chat).
    - ``execute_as_coordinator()`` / ``execute_as_coordinator_sync()``:
      Coordinator-only flow (remote tasks — bypasses Organizer/ANRouter).

    Uses the active scenario from configuration to create worker agents.
    Settings can be overridden via runtime_settings parameter (from database).
    """

    def __init__(self,
                 task_manager: TaskManager = None,
                 on_step: Callable[[dict], None] = None,
                 include_organizer: bool = True,
                 include_coordinator: bool = True,
                 runtime_settings: dict[str, Any] = None):
        """
        Initialize local crew from config.

        Args:
            task_manager: Task manager for tracking
            on_step: Callback for step events
            include_organizer: Include the Organizer agent (for network management)
            include_coordinator: Include the Coordinator agent (for local management)
            runtime_settings: Optional settings from database to override config
                - crewaiVerbose: bool (default True for tracking)
                - crewaiProcess: str (sequential/hierarchical)
                - crewaiMemory: bool
                - crewaiMaxRpm: int
                - agentScenario: str
        """
        self.config = get_config()
        self.task_manager = task_manager or get_task_manager()
        self.on_step = on_step
        self.include_organizer = include_organizer
        self.include_coordinator = include_coordinator
        self.runtime_settings = runtime_settings or {}
        self.runtime_default_provider = (
            str(
                self.runtime_settings.get("defaultLLMProvider")
                or self.runtime_settings.get("default_llm_provider")
                or ""
            ).strip()
            or None
        )
        self.runtime_default_model = (
            str(
                self.runtime_settings.get("defaultModel")
                or self.runtime_settings.get("default_model")
                or ""
            ).strip()
            or None
        )
        self.role_model_overrides = {
            "organizer": str(
                self.runtime_settings.get("organizerModel")
                or self.runtime_settings.get("organizer_model")
                or ""
            ).strip(),
            "coordinator": str(
                self.runtime_settings.get("coordinatorModel")
                or self.runtime_settings.get("coordinator_model")
                or ""
            ).strip(),
            "worker_default": str(
                self.runtime_settings.get("workerDefaultModel")
                or self.runtime_settings.get("worker_default_model")
                or ""
            ).strip(),
        }
        self.router_model_overrides = {
            "an_router": str(
                self.runtime_settings.get("anRouterModel")
                or self.runtime_settings.get("an_router_model")
                or ""
            ).strip(),
            "local_agent_router": str(
                self.runtime_settings.get("localAgentRouterModel")
                or self.runtime_settings.get("local_agent_router_model")
                or ""
            ).strip(),
        }
        raw_worker_model_overrides = (
            self.runtime_settings.get("workerModelOverrides")
            or self.runtime_settings.get("worker_model_overrides")
            or {}
        )
        self.worker_model_overrides = (
            dict(raw_worker_model_overrides)
            if isinstance(raw_worker_model_overrides, dict)
            else {}
        )
        self.factory = AgentFactory(
            auto_register_tools=True,
            runtime_default_provider=self.runtime_default_provider,
            runtime_default_model=self.runtime_default_model,
            runtime_settings=self.runtime_settings,
        )

        # Get crew settings - priority: runtime_settings > config > defaults
        crewai_config = getattr(self.config.agents, 'crewai', {}) or {}

        # Process type (sequential or hierarchical)
        self.process = self.runtime_settings.get('crewaiProcess') or \
                       (crewai_config.get("process") if isinstance(crewai_config, dict) else None) or \
                       "hierarchical"

        # Verbose mode - default TRUE for tracking (changed from False)
        self.verbose = self.runtime_settings.get('crewaiVerbose')
        if self.verbose is None:
            self.verbose = crewai_config.get("verbose", True) if isinstance(crewai_config, dict) else True

        # Memory mode
        self.memory = self.runtime_settings.get('crewaiMemory')
        if self.memory is None:
            self.memory = crewai_config.get("memory", False) if isinstance(crewai_config, dict) else False

        # Agent scenario
        self.active_scenario = self.runtime_settings.get('agentScenario') or \
                              (crewai_config.get("active_scenario") if isinstance(crewai_config, dict) else None) or \
                              "product_team"

        # Planning mode (creates step-by-step plan before execution)
        # https://docs.crewai.com/concepts/planning
        self.planning = self.runtime_settings.get('crewaiPlanning') or \
                       self.runtime_settings.get('crewai_planning', False)
        _defaults = self.config.agents.defaults if self.config else None
        self.planning_llm = self.runtime_settings.get('crewaiPlanningLlm') or \
                           self.runtime_settings.get('crewai_planning_llm') or \
                           (_defaults.planning_llm if _defaults else "flock/gpt-5.2")

        # Reasoning mode (agents reflect before executing)
        # https://docs.crewai.com/concepts/agents#reasoning-agent
        self.reasoning = self.runtime_settings.get('crewaiReasoning') or \
                        self.runtime_settings.get('crewai_reasoning', False)
        self.max_reasoning_attempts = self.runtime_settings.get('crewaiMaxReasoningAttempts') or \
                                     self.runtime_settings.get('crewai_max_reasoning_attempts') or \
                                     (_defaults.max_reasoning_attempts if _defaults else 3)

        # Streaming mode
        self.streaming = self.runtime_settings.get('crewaiStreaming') or \
                        self.runtime_settings.get('crewai_streaming', True)

        # Self-improvement rounds: how many times the Organizer may re-dispatch
        # if the result is judged incomplete. Retries (round 2+) do not charge
        # remote ANs again — same main task, no extra payment.
        _cfg_rounds = getattr(self.config.system.api, 'max_execution_rounds', 3) \
                      if self.config and hasattr(self.config, 'system') else 3
        self.max_execution_rounds = int(
            self.runtime_settings.get('maxExecutionRounds') or
            self.runtime_settings.get('max_execution_rounds') or
            _cfg_rounds
        )

        logger.info(f"LocalCrew initialized: verbose={self.verbose}, process={self.process}, memory={self.memory}, scenario={self.active_scenario}, max_rounds={self.max_execution_rounds}")
        if self.role_model_overrides["organizer"] or self.role_model_overrides["coordinator"] or self.role_model_overrides["worker_default"]:
            logger.info(
                "Runtime model overrides active: organizer=%s coordinator=%s worker_default=%s",
                self.role_model_overrides["organizer"] or "<config>",
                self.role_model_overrides["coordinator"] or "<config>",
                self.role_model_overrides["worker_default"] or "<config>",
            )
        if self.router_model_overrides["an_router"] or self.router_model_overrides["local_agent_router"]:
            logger.info(
                "Runtime router model overrides active: an_router=%s local_agent_router=%s",
                self.router_model_overrides["an_router"] or "<config>",
                self.router_model_overrides["local_agent_router"] or "<config>",
            )
        if self.worker_model_overrides:
            logger.info(
                "Runtime per-worker model overrides active for %d worker(s)",
                len(self.worker_model_overrides),
            )
        if self.planning:
            logger.info(f"  Planning enabled with LLM: {self.planning_llm}")
        if self.reasoning:
            logger.info(f"  Reasoning enabled with max attempts: {self.max_reasoning_attempts}")

        # Disable CrewAI interactive prompts for server mode
        import os
        os.environ["CREWAI_TELEMETRY_OPT_OUT"] = "true"
        os.environ["CREWAI_DISABLE_TELEMETRY"] = "true"
        os.environ["AGENTOPS_TELEMETRY_OPT_OUT"] = "true"
        os.environ["OTEL_SDK_DISABLED"] = "true"
        os.environ["CREWAI_EXECUTION_TRACES"] = "false"

        # Create agents (Organizer -> Coordinator -> Workers)
        self.organizer = None
        self.coordinator = None
        self.workers = []
        self._worker_configs: list[dict[str, Any]] = []  # source configs for all created workers
        self._worker_id_map: dict[str, str] = {}  # role/name → stable worker ID
        self._offline_workers: set = set()    # roles currently offline
        self._on_pool_changed: Callable[[], None] | None = None
        self._workforce_pool: Any = None  # AgenticNodeWorkforcePool, bound via bind_workforce_pool()
        self._an_router: Any = None       # Created by bind_workforce_pool()
        self._verifier_llm: Any = None
        self._verifier_model_name: str = ""
        self.agents = self._create_agents()

    def _create_agents(self) -> list[Any]:
        """
        Create agents based on configuration.

        Order: Organizer → Coordinator → Workers.

        Worker source:
          - **Dev mode** (``system.dev_mode.enabled=true``):
            Loads workers listed in ``agents.dev_workers`` from the
            Python registry (``teaming24/agent/workers/``).
          - **Production mode** (``dev_mode.enabled=false``):
            Loads workers listed in ``agents.prod_workers`` from the
            Python registry.
          - **Fallback**: legacy ``agents.scenarios`` or ``agents.workers``.

        The YAML may override per-worker parameters via
        ``agents.worker_overrides``.
        """
        agents = []

        # 1. Create Organizer (entry point for user interaction and network)
        if self.include_organizer:
            self.organizer = self.factory.create_organizer(
                model=self.role_model_overrides.get("organizer") or None,
            )
            if self.organizer:
                agents.append(self.organizer)
                logger.info("Created Organizer agent")

        # 2. Create Coordinator (manages local workers)
        if self.include_coordinator:
            self.coordinator = self.factory.create_coordinator(
                model=self.role_model_overrides.get("coordinator") or None,
            )
            if self.coordinator:
                agents.append(self.coordinator)
                logger.info("Created Coordinator agent")

        # 3. Create Workers from the Python registry
        self._create_registry_workers(agents)

        # 4. Fallback to legacy scenario / default workers
        if not self.workers:
            self._create_legacy_workers(agents)

        if not self.workers:
            logger.warning("No workers loaded from any source")

        logger.info(f"LocalCrew initialized with {len(agents)} agents")
        return agents

    def _create_registry_workers(self, agents: list[Any]) -> None:
        """Resolve and create workers from the Python worker registry.

        1. Loads the full built-in worker definition module set.
        2. Reads ``agents.dev_workers`` or ``agents.prod_workers`` depending
           on ``system.dev_mode.enabled``.
        3. Merges any YAML overrides from ``agents.worker_overrides``.
        4. Builds ``_worker_id_map`` — a stable mapping from role/name
           to ``worker-{registered_name}`` IDs.
        """
        from teaming24.agent.workers import (
            DEFAULT_WORKER_MODULES,
            get_worker,
            load_worker_modules,
            resolve_workers,
        )
        from teaming24.utils.ids import worker_id_from_name

        def _parse_group_ids(raw: Any) -> list[int]:
            """Parse simulation group IDs from list/int/string runtime values."""
            if raw is None:
                return []
            values: list[Any]
            if isinstance(raw, list):
                values = raw
            elif isinstance(raw, int):
                values = [raw]
            elif isinstance(raw, str):
                values = [part.strip() for part in raw.split(",") if part.strip()]
            else:
                logger.warning("[LocalCrew] Unsupported simulation group id type: %s", type(raw).__name__)
                return []
            parsed: list[int] = []
            for item in values:
                try:
                    parsed.append(int(str(item).strip()))
                except Exception as parse_exc:
                    logger.warning(
                        "[LocalCrew] Invalid simulation group id %r ignored: %s",
                        item,
                        parse_exc,
                        exc_info=True,
                    )
            # Keep order, drop duplicates.
            deduped: list[int] = []
            seen: set[int] = set()
            for gid in parsed:
                if gid in seen:
                    continue
                seen.add(gid)
                deduped.append(gid)
            return deduped

        dev_mode = getattr(self.config.system, 'dev_mode', None)
        is_dev = dev_mode and getattr(dev_mode, 'enabled', False)

        worker_modules = getattr(dev_mode, 'worker_modules', []) if dev_mode else []
        requested_modules = [
            str(module_name).strip()
            for module_name in (worker_modules or [])
            if str(module_name).strip()
        ]
        if requested_modules and requested_modules != DEFAULT_WORKER_MODULES:
            logger.info(
                "[LocalCrew] system.dev_mode.worker_modules is deprecated and ignored; using full worker registry: %s",
                DEFAULT_WORKER_MODULES,
            )
        load_worker_modules(DEFAULT_WORKER_MODULES)

        if is_dev:
            names = getattr(self.config.agents, 'dev_workers', []) or []
            mode_label = "dev"

            # Select exactly one predefined demo worker group by numeric ID.
            demo_group_id_raw = getattr(self.config.agents, "demo_active_group_id", None)
            demo_group_id: int | None = None
            try:
                if demo_group_id_raw is not None and str(demo_group_id_raw).strip() != "":
                    demo_group_id = int(str(demo_group_id_raw).strip())
            except Exception as parse_exc:
                logger.warning(
                    "[LocalCrew] Invalid demo_active_group_id %r: %s",
                    demo_group_id_raw,
                    parse_exc,
                    exc_info=True,
                )

            active_group_ids = [demo_group_id] if demo_group_id is not None else []

            group_map_raw = getattr(self.config.agents, "simulation_worker_groups", {}) or {}
            demo_worker_group_map: dict[str, int] = {}

            if isinstance(group_map_raw, dict) and group_map_raw and active_group_ids:
                # Normalize keys so both "0" and 0 work from YAML/runtime.
                group_map: dict[str, list[str]] = {}
                for key, value in group_map_raw.items():
                    workers = [str(v).strip() for v in (value or []) if str(v).strip()]
                    group_map[str(key).strip()] = workers

                selected_names: list[str] = []
                missing_group_ids: list[int] = []
                seen_workers: set[str] = set()
                for gid in active_group_ids:
                    workers = group_map.get(str(gid), [])
                    if not workers:
                        missing_group_ids.append(gid)
                        continue
                    for worker_name in workers:
                        if worker_name in seen_workers:
                            continue
                        seen_workers.add(worker_name)
                        selected_names.append(worker_name)
                        demo_worker_group_map[worker_name] = gid

                if missing_group_ids:
                    logger.warning(
                        "[LocalCrew] simulation worker group IDs not found: %s",
                        missing_group_ids,
                    )
                if selected_names:
                    names = selected_names
                    mode_label = f"dev(demo_group={demo_group_id})"
                    logger.info(
                        "[LocalCrew] Loaded workers by demo group ID %s: %s",
                        active_group_ids,
                        names,
                    )
            elif active_group_ids:
                demo_worker_group_map = {}
                logger.warning(
                    "[LocalCrew] No workers resolved from simulation group IDs %s, falling back to dev_workers",
                    active_group_ids,
                )
        else:
            names = getattr(self.config.agents, 'prod_workers', []) or []
            mode_label = "production"
            demo_worker_group_map = {}

        if not names:
            logger.info(f"No {mode_label} worker names configured, trying legacy")
            return

        missing_names = [name for name in names if not get_worker(name)]
        if missing_names:
            logger.warning(
                "[LocalCrew] Worker blueprints still missing after module load: %s",
                missing_names,
            )

        overrides = getattr(self.config.agents, 'worker_overrides', {}) or {}
        worker_configs = resolve_workers(names, overrides)
        worker_model_override = self.role_model_overrides.get("worker_default") or ""

        for wc in worker_configs:
            worker_cfg = dict(wc) if isinstance(wc, dict) else wc
            worker_name = str(worker_cfg.get("name", "")).strip() if isinstance(worker_cfg, dict) else ""
            role = str(worker_cfg.get("role", "")).strip() if isinstance(worker_cfg, dict) else ""
            wid = worker_id_from_name(worker_name) if worker_name else ""
            worker_specific_model = ""
            for lookup_key in (
                wid,
                worker_name,
                worker_name.lower(),
                role,
                role.lower(),
            ):
                if not lookup_key:
                    continue
                candidate = str(self.worker_model_overrides.get(lookup_key, "") or "").strip()
                if candidate:
                    worker_specific_model = candidate
                    break
            if worker_name and worker_name in demo_worker_group_map and isinstance(worker_cfg, dict):
                worker_cfg["_source"] = "predefined_demo"
                worker_cfg["_demo_group_id"] = demo_worker_group_map[worker_name]
                worker_cfg["_is_predefined_demo_agent"] = True
            if worker_model_override and isinstance(worker_cfg, dict):
                worker_cfg["model"] = worker_model_override
            if worker_specific_model and isinstance(worker_cfg, dict):
                worker_cfg["model"] = worker_specific_model
            agent = self.factory.create_agent(worker_cfg)
            if agent:
                self.workers.append(agent)
                self._worker_configs.append(worker_cfg)
                agents.append(agent)

                # Build stable ID map: role & registry name → worker-{name}
                reg_name = worker_cfg.get("name", "")
                wid = worker_id_from_name(reg_name) if reg_name else wid
                role = worker_cfg.get("role", "") or role
                if wid:
                    if reg_name:
                        self._worker_id_map[reg_name] = wid
                        self._worker_id_map[reg_name.lower()] = wid
                    if role:
                        self._worker_id_map[role] = wid
                        self._worker_id_map[role.lower()] = wid

                group = worker_cfg.get('group', '')
                logger.info(
                    f"Created worker: {role or 'Unknown'}"
                    f" (id={wid})"
                    f"{f' [{group}]' if group else ''}"
                )

        logger.info(f"{mode_label.capitalize()} mode: loaded {len(self.workers)} workers from registry")

    def _create_legacy_workers(self, agents: list[Any]) -> None:
        """Fallback: load workers from legacy scenario or default workers list."""
        active_scenario = getattr(self.config.agents, 'active_scenario', None)
        if active_scenario:
            scenarios = getattr(self.config.agents, 'scenarios', {}) or {}
            scenario = scenarios.get(active_scenario, {}) if isinstance(scenarios, dict) else {}
            logger.info(f"Loading legacy scenario: {active_scenario}")
            scenario_agents = scenario.get("agents", []) if isinstance(scenario, dict) else []
            worker_model_override = self.role_model_overrides.get("worker_default") or ""
            for agent_config in scenario_agents:
                cfg = dict(agent_config) if isinstance(agent_config, dict) else agent_config
                role = str(cfg.get("role", "")).strip() if isinstance(cfg, dict) else ""
                worker_specific_model = ""
                for lookup_key in (role, role.lower()):
                    if not lookup_key:
                        continue
                    candidate = str(self.worker_model_overrides.get(lookup_key, "") or "").strip()
                    if candidate:
                        worker_specific_model = candidate
                        break
                if worker_model_override and isinstance(cfg, dict):
                    cfg["model"] = worker_model_override
                if worker_specific_model and isinstance(cfg, dict):
                    cfg["model"] = worker_specific_model
                agent = self.factory.create_agent(cfg)
                if agent:
                    self.workers.append(agent)
                    self._worker_configs.append(cfg)
                    agents.append(agent)

        # Last resort: default workers list
        if not self.workers:
            worker_model_override = self.role_model_overrides.get("worker_default") or ""
            workers: list[Any] = []
            for worker_config in (self.config.agents.workers or []):
                cfg = (
                    dict(worker_config)
                    if isinstance(worker_config, dict)
                    else self.factory._config_to_dict(worker_config)
                )
                role = str(cfg.get("role", "")).strip() if isinstance(cfg, dict) else ""
                worker_specific_model = ""
                for lookup_key in (role, role.lower()):
                    if not lookup_key:
                        continue
                    candidate = str(self.worker_model_overrides.get(lookup_key, "") or "").strip()
                    if candidate:
                        worker_specific_model = candidate
                        break
                if worker_model_override:
                    cfg["model"] = worker_model_override
                if worker_specific_model:
                    cfg["model"] = worker_specific_model
                if not cfg.get("enabled", True):
                    continue
                agent = self.factory.create_agent(cfg)
                if agent:
                    workers.append(agent)
                    self._worker_configs.append(cfg)
            self.workers = workers
            agents.extend(self.workers)
            if self.workers:
                logger.info(f"Created {len(self.workers)} default workers")

    # -- Worker ID helpers ---------------------------------------------------

    def get_worker_id(self, role_or_name: str) -> str:
        """Resolve a worker role/name to its stable ID.

        Looks up ``_worker_id_map`` first (populated during worker creation).
        Falls back to the positional ``worker-{index}`` scheme for legacy
        workers that have no registry name.

        Args:
            role_or_name: The worker's ``role`` or registry ``name``.

        Returns:
            Stable worker ID string (e.g. ``worker-fullstack_dev``).
        """
        from teaming24.utils.ids import worker_id as _positional_id

        wid = self._worker_id_map.get(role_or_name)
        if wid:
            return wid
        wid = self._worker_id_map.get(role_or_name.lower())
        if wid:
            return wid
        # Fallback: positional lookup
        for i, w in enumerate(self.workers):
            r = getattr(w, "role", "")
            if r == role_or_name or r.lower() == role_or_name.lower():
                return _positional_id(i + 1)
        return _positional_id(1)

    def get_worker_id_for_index(self, index: int) -> str:
        """Return the stable worker ID for the worker at *index* (0-based).

        Uses the registry name from ``_worker_configs`` if available,
        otherwise falls back to ``worker-{index+1}``.
        """
        from teaming24.utils.ids import worker_id as _positional_id
        from teaming24.utils.ids import worker_id_from_name

        if self._worker_configs and index < len(self._worker_configs):
            reg_name = self._worker_configs[index].get("name", "")
            if reg_name:
                return worker_id_from_name(reg_name)
        return _positional_id(index + 1)

    def get_capabilities(self) -> list[str]:
        """Get list of capabilities from **online** workers only.

        Offline workers are excluded so the Agentic Node Workforce Pool and network
        advertisement accurately reflect what this node can currently do.
        Uses ``self._worker_configs`` which is populated at creation time
        from either dev profiles or scenario config.
        """
        capabilities = set()

        for wc in self._worker_configs:
            if not isinstance(wc, dict):
                continue
            role = wc.get("role", "")
            if role in self._offline_workers:
                continue
            for cap in wc.get("capabilities", []):
                capabilities.add(cap)
            # Profile-level capability (dev mode metadata)
            profile_cap = wc.get("_profile_capability")
            if profile_cap:
                capabilities.add(profile_cap)

        return list(capabilities)

    def get_worker_descriptions(self) -> list[dict[str, Any]]:
        """Get role/capability summary for **online** local Workers.

        Returns a list of dicts, each with ``role``, ``capabilities``,
        ``goal`` (first line only), ``status``, and optional ``profile``
        (dev mode origin).  Used by the Agentic Node Workforce Pool to build a rich
        description of the local Coordinator entry so the Organizer can
        compare it against remote ANs.

        Uses ``self._worker_configs`` populated at creation time from
        either dev profiles or scenario config.  Offline workers are
        excluded.
        """
        descriptions: list[dict[str, Any]] = []

        for wc in self._worker_configs:
            if not isinstance(wc, dict):
                continue
            role = wc.get("role", "Worker")
            if role in self._offline_workers:
                continue
            goal_raw = wc.get("goal", "")
            first_line = goal_raw.strip().split("\n")[0] if goal_raw else ""
            desc: dict[str, Any] = {
                "role": role,
                "capabilities": wc.get("capabilities", []),
                "goal": first_line,
                "status": "online",
            }
            # Include dev profile origin when available
            profile_name = wc.get("_profile_name")
            if profile_name:
                desc["profile"] = profile_name
            if wc.get("_is_predefined_demo_agent"):
                desc["source"] = "predefined_demo"
                if wc.get("_demo_group_id") is not None:
                    desc["demo_group_id"] = wc.get("_demo_group_id")
            descriptions.append(desc)

        return descriptions

    def can_handle(self, required_capabilities: list[str]) -> bool:
        """Check if this crew can handle the required capabilities."""
        local_caps = set(self.get_capabilities())
        required = set(required_capabilities)
        return required.issubset(local_caps)

    # -- Worker pool dynamic management ------------------------------------

    def set_on_pool_changed(self, callback: Callable[[], None]):
        """Register a callback invoked whenever the online worker set changes.

        Used by the server layer to refresh the AN's network advertisement
        so that the broadcast reflects current capabilities.
        """
        self._on_pool_changed = callback

    def set_worker_offline(self, role: str) -> bool:
        """Mark a worker as offline by role name.

        The worker stays in the config but is excluded from
        ``get_capabilities()`` and ``get_worker_descriptions()``.
        Returns True if the state actually changed.
        """
        if role in self._offline_workers:
            return False
        self._offline_workers.add(role)
        logger.info(f"Worker offline: {role}")
        if self._on_pool_changed:
            self._on_pool_changed()
        return True

    def set_worker_online(self, role: str) -> bool:
        """Mark a previously-offline worker as online again.

        Returns True if the state actually changed.
        """
        if role not in self._offline_workers:
            return False
        self._offline_workers.discard(role)
        logger.info(f"Worker online: {role}")
        if self._on_pool_changed:
            self._on_pool_changed()
        return True

    def get_online_workers(self) -> list[Any]:
        """Return the subset of ``self.workers`` that are currently online."""
        return [
            w for w in self.workers
            if getattr(w, "role", "") not in self._offline_workers
        ]

    def is_worker_offline(self, role: str) -> bool:
        """Check if a specific worker role is offline."""
        return role in self._offline_workers

    # -- Agentic Node Workforce Pool binding & Task Routing -------------------

    def bind_workforce_pool(self, pool: Any, task_id: str = None) -> None:
        """Bind an AgenticNodeWorkforcePool and create an ANRouter for this execution.

        **Organizer-driven selection process:**

        During task execution the Organizer asks the ANRouter:
        "Given this task and the Agentic Node Workforce Pool (member names,
        capabilities, descriptions), which members should participate
        and what should each do?"

        The ANRouter makes **one standalone LLM call** and returns a
        ``RoutingPlan``.  The Organizer then dispatches:
        - Local subtask → CrewAI (Coordinator → Workers)
        - Remote subtasks → HTTP POST to selected ANs

        The Coordinator still gets ``delegate_to_network`` and
        ``search_network`` tools for when it receives tasks from
        remote ANs and needs to interact with the pool.

        Args:
            pool: An ``AgenticNodeWorkforcePool`` instance.
            task_id: Current task ID (for logging/tracking).
        """
        from teaming24.agent.an_router import create_an_router
        from teaming24.llm.model_resolver import (
            build_runtime_llm_config,
            resolve_model_and_call_params,
        )

        # Model selection priority:
        # 1) runtime override from settings
        # 2) AN router config model
        # 3) Organizer model fallback
        router_model = self.router_model_overrides.get("an_router", "")
        try:
            from teaming24.config import get_config
            ar_cfg = get_config().an_router
            if not router_model and not ar_cfg.model:
                router_model = self.config.agents.organizer.model
        except Exception as e:
            logger.debug(f"Could not resolve ANRouter model fallback: {e}")

        llm_cfg = build_runtime_llm_config(
            self.config.llm,
            runtime_settings=self.runtime_settings,
            runtime_default_provider=self.runtime_default_provider,
        )
        resolved_router_model, router_call_params, _ = resolve_model_and_call_params(
            router_model or getattr(self.config.an_router, "model", ""),
            llm_cfg,
        )

        self._workforce_pool = pool
        self._an_router = create_an_router(
            pool=pool,
            task_id=task_id or "",
            **({"model": resolved_router_model} if resolved_router_model else {}),
            llm_call_params=router_call_params,
        )

        # Snapshot and log the pool state immediately
        self._an_router.log_pool_snapshot()

        # Patch network tools on the Coordinator only.
        # The Organizer has tools=[] (CrewAI hierarchical requirement).
        # The Coordinator keeps network tools for receiving remote tasks.
        if not self.coordinator:
            return

        coordinator_tools = getattr(self.coordinator, "tools", []) or []
        patched = 0
        for tool in coordinator_tools:
            tool_name = getattr(tool, "name", "")
            if tool_name in ("delegate_to_network", "search_network"):
                tool._workforce_pool = pool
                if hasattr(tool, "_current_task_id"):
                    tool._current_task_id = task_id
                patched += 1

        # Also update the factory registry
        for name in ("delegate_to_network", "search_network"):
            if name in self.factory.tools_registry:
                self.factory.tools_registry[name]._workforce_pool = pool
                if hasattr(self.factory.tools_registry[name], "_current_task_id"):
                    self.factory.tools_registry[name]._current_task_id = task_id

        if patched:
            logger.info(
                f"Bound AgenticNodeWorkforcePool to {patched} Coordinator tool(s), "
                f"pool size={len(pool.get_pool())}"
            )
        else:
            # Inject tools if missing
            try:
                from teaming24.agent.tools.network_tools import (
                    DelegateToNetworkTool,
                    SearchNetworkTool,
                )
                delegate_tool = DelegateToNetworkTool(
                    workforce_pool=pool, task_id=task_id,
                )
                search_tool = SearchNetworkTool(workforce_pool=pool)

                real_tools = getattr(self.coordinator, "tools", None)
                if real_tools is not None and isinstance(real_tools, list):
                    real_tools.append(delegate_tool)
                    real_tools.append(search_tool)
                else:
                    self.coordinator.tools = [delegate_tool, search_tool]

                logger.info(
                    "Injected network tools into Coordinator "
                    f"(pool size={len(pool.get_pool())})"
                )
            except Exception as e:
                logger.error(
                    f"Failed to inject network tools into Coordinator: {e}",
                    exc_info=True,
                )

    def _parse_cost_to_float(self, cost_str: str) -> float:
        """Parse cost string (e.g. '0.001', '0.001 ETH') to float."""
        if not cost_str:
            return 0.0
        import re
        m = re.search(r"[\d.]+", str(cost_str))
        return float(m.group()) if m else 0.0

    def _request_routing_approval(self, task_id: str, plan: Any) -> Any:
        """If remote ANs are selected, pause and ask the user for approval.

        Budget-based auto-approve: if user set a budget and remaining >= estimate,
        auto-approve without popup. When exceeded, show approval again.
        Returns the (possibly modified) plan. If user denies, remote subtasks
        are removed and only local execution proceeds.
        """
        remote_count = len(plan.remote_subtasks) if plan else 0
        if remote_count == 0:
            return plan

        # Build cost lookup from the bound AgenticNodeWorkforcePool (entry.id == an_id)
        cost_map: dict = {}
        if self._workforce_pool:
            try:
                for entry in self._workforce_pool.get_pool():
                    if entry.entry_type == "remote" and entry.cost:
                        cost_map[entry.id] = str(entry.cost)
            except Exception as _e:
                logger.debug(f"[Approval] Could not read pool costs: {_e}")

        # Compute total estimated cost (ETH)
        total_estimate = 0.0
        for st in plan.remote_subtasks:
            c = cost_map.get(st.target_node_id, "")
            if c:
                total_estimate += self._parse_cost_to_float(c)
        if total_estimate <= 0:
            try:
                from teaming24.config import get_config
                task_price = float(get_config().payment.task_price)
                total_estimate = remote_count * task_price
            except Exception as exc:
                logger.warning(
                    "[Approval] Failed to read payment.task_price; using fallback estimate: %s",
                    exc,
                    exc_info=True,
                )
                total_estimate = remote_count * 0.001

        # Budget-based auto-approve: if budget remaining >= estimate, skip popup
        try:
            from teaming24.api.services.approval import (
                add_task_spent,
                get_task_budget_info,
            )
            info = get_task_budget_info(task_id)
            if info:
                budget = info.get("budget", 0)
                spent = info.get("spent", 0)
                remaining = budget - spent
                if remaining >= total_estimate and budget > 0:
                    add_task_spent(task_id, total_estimate)
                    self._wf_step(
                        task_id, "Organizer", "approval_resolved",
                        f"✅ Auto-approved (within budget ${remaining:.4f}) — dispatching to {remote_count} remote AN(s)",
                        agent_type="organizer",
                    )
                    return plan
        except ImportError:
            logger.debug("[Approval] approval service module unavailable; skipping budget auto-approval")
            pass

        # Build a human-readable description of the routing plan with costs
        mode = getattr(plan, "execution_mode", "parallel")
        lines = [f"Execution mode: {mode}", f"{remote_count} remote AN(s) will be used:"]
        total_known_cost: list = []
        for st in plan.remote_subtasks:
            cost_str = cost_map.get(st.target_node_id, "")
            cost_label = f" [cost: {cost_str}]" if cost_str else " [cost: unknown]"
            if cost_str:
                total_known_cost.append(cost_str)
            lines.append(f"  • {st.assigned_to}{cost_label}")
            lines.append(f"    Task: {st.description[:80]}")
        if plan.has_local:
            lines.append("  • local team coordinator [free]")
        if total_known_cost:
            lines.append(f"\nEstimated payment: {', '.join(total_known_cost)}")
        lines.append("\nSet budget (ETH) to auto-approve future dispatches within limit:")
        description = "\n".join(lines)

        try:
            from teaming24.api.services.approval import block_until_approval, create_approval
            _timeout = getattr(get_config().api, "approval_timeout", 300.0)
            approval_id, evt = create_approval(
                task_id=task_id,
                approval_type="routing",
                title=f"Approve routing to {remote_count} remote AN(s)?",
                description=description,
                options=[
                    {"id": "approve", "label": "Approve", "style": "primary"},
                    {"id": "local_only", "label": "Local only", "style": "secondary"},
                    {"id": "deny", "label": "Cancel task", "style": "danger"},
                ],
                metadata={
                    "remote_count": remote_count,
                    "mode": mode,
                    "total_estimate": total_estimate,
                    "allow_budget": True,
                },
            )
            # Emit step AFTER approval exists so chat stream yields approval_request
            self._wf_step(
                task_id, "Organizer", "approval_request",
                f"⏸ Waiting for your approval to dispatch to {remote_count} remote AN(s)...",
                agent_type="organizer",
            )
            decision, extra = block_until_approval(approval_id, evt, _timeout)
        except ImportError:
            logger.debug("[Approval] server module not available, auto-approving")
            decision, extra = "approve", {}

        if decision == "approve" or decision == "timeout":
            budget_val = extra.get("budget") if isinstance(extra, dict) else None
            if budget_val is not None and budget_val > 0:
                try:
                    from teaming24.api.services.approval import set_task_budget
                    set_task_budget(task_id, float(budget_val), spent=total_estimate)
                except Exception as exc:
                    logger.warning(
                        "[Approval] Failed to persist task budget for %s: %s",
                        task_id,
                        exc,
                        exc_info=True,
                    )
            self._wf_step(
                task_id, "Organizer", "approval_resolved",
                f"✅ Routing approved — dispatching to {remote_count} remote AN(s)",
                agent_type="organizer",
            )
            return plan
        elif decision == "local_only":
            self._wf_step(
                task_id, "Organizer", "approval_resolved",
                "⚠️ Routing modified — running local only (remote ANs removed)",
                agent_type="organizer",
            )
            plan.subtasks = [s for s in plan.subtasks if not s.is_remote]
            return plan
        else:
            self._wf_step(
                task_id, "Organizer", "approval_resolved",
                "❌ Task cancelled by user",
                agent_type="organizer",
            )
            return None

    def _log_routing_decision(self, router: Any, task_id: str, result: dict) -> None:
        """Log post-execution routing decision: which agents/ANs were used.

        Collects info from multiple sources:
        1. ``task.delegated_agents`` — local worker names from StepCallback
        2. ``task.executing_agents`` — all agents that executed work
        3. ``task.steps`` — look for remote delegation actions
        4. ``task.delegated_to`` — remote node ID if task was delegated
        """
        try:
            task = self.task_manager.get_task(task_id) if task_id else None
            selected: list[str] = []

            logger.debug(
                f"[ANRouter] _log_routing_decision called: task_id={task_id}, "
                f"task_exists={task is not None}, result_status={result.get('status', '?')}"
            )

            if task:
                logger.info(
                    f"[ANRouter] Post-execution agent tracking for task={task_id}: "
                    f"assigned_to={getattr(task, 'assigned_to', None)}, "
                    f"delegated_agents={getattr(task, 'delegated_agents', [])}, "
                    f"executing_agents={getattr(task, 'executing_agents', [])}, "
                    f"steps_count={len(getattr(task, 'steps', []))}"
                )
                # Coordinator (assigned_to)
                assigned = getattr(task, "assigned_to", None)
                if assigned and assigned not in selected:
                    selected.append(f"{assigned} (Coordinator)")
                # Local workers that participated (from StepCallback tracking)
                for name in getattr(task, "delegated_agents", []):
                    if name and name not in selected:
                        selected.append(name)
                # Also check executing_agents
                for name in getattr(task, "executing_agents", []):
                    if name and name not in selected:
                        selected.append(name)
                # Remote AN delegation — from steps
                for step in getattr(task, "steps", []):
                    action = getattr(step, "action", "") or ""
                    content = getattr(step, "content", "") or ""
                    if "delegate" in action.lower() and ("Delegating to" in content or "remote" in content.lower()):
                        selected.append(f"[REMOTE] {content}")
                # Remote AN delegation — from task-level tracking
                if getattr(task, "delegated_to", None):
                    selected.append(f"[REMOTE] delegated_to={task.delegated_to}")

            if not selected:
                selected = ["local team coordinator (default — no worker steps captured)"]

            router.log_decision(
                selected_names=selected,
                reasoning="Organizer LLM runtime decision",
            )
            router.log_execution_result(
                delegated_agents=selected,
                result_status=result.get("status", "unknown"),
                duration=result.get("duration", 0.0),
            )
        except Exception as e:
            logger.error(f"[ANRouter] Failed to log routing decision: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # Workflow-log helper (shared by execute / execute_sync)
    # ------------------------------------------------------------------
    # Output sandbox binding
    # ------------------------------------------------------------------

    def _bind_task_output_dir(self, task_id: str) -> str:
        """Bind file tools to a task-specific output workspace directory.

        Must be called BEFORE CrewAI kickoff so that any ``file_write``
        by an agent lands in ``{output.base_dir}/{task_id}/workspace/``
        instead of modifying the project source tree.

        Returns:
            The absolute path to the workspace directory.
        """
        import os
        try:
            from teaming24.config import get_config
            base = os.path.expanduser(get_config().output.base_dir)
        except Exception as e:
            logger.debug(f"Could not read output.base_dir from config, using default: {e}")
            base = os.path.expanduser("~/.teaming24/outputs")

        workspace_dir = os.path.join(base, task_id, "workspace")
        os.makedirs(workspace_dir, exist_ok=True)

        try:
            from teaming24.agent.tools.openhands_tools import (
                bind_task_output_dir,
            )
            bind_task_output_dir(self.factory.tools_registry, workspace_dir)
        except ImportError:
            logger.debug("openhands_tools not available — skipping output sandbox binding")

        try:
            from teaming24.runtime.task_context import set_task_context
            set_task_context(task_id, workspace_dir)
        except ImportError:
            pass

        logger.info(
            f"[OutputSandbox] task={task_id} → {workspace_dir}"
        )
        return workspace_dir

    # ------------------------------------------------------------------

    def _wf_step(self, task_id: str, agent: str, action: str, content: str,
                 agent_type: str = None, phase: TaskPhase = None,
                 phase_label: str = "", percentage: int = None):
        """Record a workflow step and stream it to the frontend.

        Args:
            task_id: Task identifier.
            agent: Agent display name (e.g. "Organizer", "ANRouter", "local team coordinator").
            action: Action label (e.g. "decision", "dispatch", "local_start").
            content: Human-readable description.
            agent_type: One of organizer / router / coordinator / remote / worker.
                        Auto-detected from *agent* name if omitted.
            phase: Optional phase transition to apply before sending.
            phase_label: Human-readable label for the phase.
            percentage: Optional explicit progress percentage.
        """
        emit_workflow_step(
            task_manager=self.task_manager,
            on_step=self.on_step,
            task_id=task_id,
            agent=agent,
            action=action,
            content=content,
            agent_type=agent_type,
            phase=phase,
            phase_label=phase_label,
            percentage=percentage,
        )

    def _log_routing_plan(self, task_id: str, plan: Any, local_prompt: str):
        """Log the ANRouter's routing decision as workflow steps.

        Emits a ``routing_decision`` step that carries ``selected_members``
        — the definitive list of pool member IDs chosen by the ANRouter.
        The frontend topology uses this to show only selected members.
        """
        from teaming24.utils.ids import COORDINATOR_ID as _COORD_ID

        mode = getattr(plan, "execution_mode", "parallel")

        # Build the definitive selected-member list for the frontend
        selected_member_ids: list[str] = []
        selected_names: list[str] = []
        if plan.has_local:
            selected_member_ids.append(_COORD_ID)
            selected_names.append("local team coordinator")
        for st in getattr(plan, "remote_subtasks", []):
            if st.target_node_id and st.target_node_id not in selected_member_ids:
                selected_member_ids.append(st.target_node_id)
            selected_names.append(f"{st.assigned_to}")

        mode_label = "parallel (concurrent)" if mode == "parallel" else "sequential (chained)"

        # Emit the routing decision step with selected_members metadata
        if self.on_step:
            try:
                step_data = {
                    "task_id": task_id,
                    "agent": "ANRouter",
                    "agent_type": "router",
                    "action": "routing_decision",
                    "content": (
                        f"Execution mode: {mode_label} | "
                        f"Selected {len(selected_names)} participant(s): "
                        f"{', '.join(selected_names)}"
                    ),
                    "type": "workflow",
                    "selected_members": selected_member_ids,
                    "execution_mode": mode,
                }
                if task_id and self.task_manager:
                    task = self.task_manager.get_task(task_id)
                    if task:
                        step_data["progress"] = task.progress.to_dict()
                        step_data["step_number"] = task.step_count
                self.on_step(step_data)
            except Exception as e:
                logger.debug(f"Failed to emit routing_decision step: {e}")

        # Also log via standard _wf_step for step history
        self._wf_step(
            task_id, "ANRouter", "decision",
            f"Execution mode: {mode_label} | Selected {len(selected_names)} participant(s): {', '.join(selected_names)}",
            agent_type="router",
        )

        display = getattr(plan, "ordered_subtasks", plan.subtasks) if mode == "sequential" else plan.subtasks
        for _i, st in enumerate(display, 1):
            order_str = f" [step {st.order}]" if mode == "sequential" else ""
            if not st.is_remote:
                self._wf_step(
                    task_id, "ANRouter", "assign",
                    f"local team coordinator{order_str} → {st.description[:80]}...",
                    agent_type="router",
                )
            else:
                self._wf_step(
                    task_id, "ANRouter", "assign",
                    f"{st.assigned_to}{order_str} → {st.description[:80]}...",
                    agent_type="router",
                )

    # ------------------------------------------------------------------
    # Subtask executors (used by both parallel & sequential paths)
    # ------------------------------------------------------------------

    def _build_local_pool(self) -> list:
        """Build the local agent pool: Coordinator + ALL online workers.

        The local team coordinator's pool is always the complete set of registered
        workers (minus any explicitly marked offline).  CrewAI's hierarchical
        delegation lets the Coordinator decide which workers handle each
        subtask based on their roles, goals, and capabilities.
        """
        online_workers = self.get_online_workers()
        pool = [self.coordinator] + online_workers
        pool = self._strip_network_tools(pool)

        worker_roles = [getattr(w, "role", "?") for w in online_workers]
        logger.info(
            f"[LocalPool] Coordinator + {len(online_workers)} online workers: "
            f"{worker_roles}"
        )
        if self._offline_workers:
            logger.info(
                f"[LocalPool] {len(self._offline_workers)} offline (excluded): "
                f"{sorted(self._offline_workers)}"
            )
        return pool

    # ------------------------------------------------------------------
    # Framework adapter support
    # ------------------------------------------------------------------

    @property
    def _use_native(self) -> bool:
        """True when the configured backend is 'native' (not CrewAI)."""
        return self.config.framework.backend == "native"

    def _get_framework_adapter(self):
        """Lazily create or return the FrameworkAdapter if native backend is selected."""
        if not hasattr(self, '_framework_adapter'):
            self._framework_adapter = None
        if self._framework_adapter is not None:
            return self._framework_adapter
        try:
            fw_cfg = self.config.framework
            if fw_cfg.backend == "native":
                from teaming24.agent.framework import create_framework_adapter
                from teaming24.llm.model_resolver import (
                    build_runtime_llm_config,
                    resolve_model_and_call_params,
                )

                planning_model_input = str(
                    self.runtime_settings.get("crewaiPlanningLlm")
                    or self.runtime_settings.get("crewai_planning_llm")
                    or fw_cfg.native.planning_model
                    or self.runtime_default_model
                    or "flock/gpt-5.2"
                ).strip()
                llm_cfg = build_runtime_llm_config(
                    self.config.llm,
                    runtime_settings=self.runtime_settings,
                    runtime_default_provider=self.runtime_default_provider,
                )
                resolved_planning_model, planning_call_params, _ = resolve_model_and_call_params(
                    planning_model_input,
                    llm_cfg,
                )
                self._framework_adapter = create_framework_adapter(
                    "native",
                    max_iterations=fw_cfg.native.max_iterations,
                    planning_model=resolved_planning_model,
                    planning_llm_call_params=planning_call_params,
                )
                logger.info("[LocalCrew] Using native framework adapter")
            elif fw_cfg.backend == "crewai":
                # Keep using existing CrewWrapper path
                self._framework_adapter = None
        except Exception as e:
            logger.error("[LocalCrew] Framework adapter not available: %s", e)
            self._framework_adapter = None
        return self._framework_adapter

    def _agents_to_specs(self, crewai_agents):
        """Convert a list of CrewAI agents to AgentSpec objects for the adapter."""
        from teaming24.agent.framework.base import AgentSpec
        from teaming24.agent.tools.base import crewai_tool_to_spec
        from teaming24.llm.model_resolver import (
            build_runtime_llm_config,
            resolve_model_and_call_params,
        )
        specs = []
        llm_cfg_for_resolution = build_runtime_llm_config(
            self.config.llm,
            runtime_settings=self.runtime_settings,
            runtime_default_provider=self.runtime_default_provider,
        )
        for agent in crewai_agents:
            tools = []
            for t in (getattr(agent, 'tools', None) or []):
                try:
                    tools.append(crewai_tool_to_spec(t))
                except Exception as _tool_exc:
                    logger.debug(
                        f"[LocalCrew] Skipped tool {getattr(t, 'name', t)!r} "
                        f"for agent {getattr(agent, 'role', '?')!r}: {_tool_exc}"
                    )
            model = "gpt-4"
            llm = getattr(agent, 'llm', None)
            if llm is not None:
                model = getattr(llm, 'model', None) or getattr(llm, 'model_name', "gpt-4") or "gpt-4"
            resolved_model = str(model)
            llm_params: dict[str, Any] = {}
            try:
                resolved_model, llm_params, _provider = resolve_model_and_call_params(
                    str(model),
                    llm_cfg_for_resolution,
                )
            except Exception as exc:
                logger.warning(
                    "[LocalCrew] Failed to resolve model/provider for %s: %s",
                    getattr(agent, "role", "Agent"),
                    exc,
                    exc_info=True,
                )
            specs.append(AgentSpec(
                role=getattr(agent, 'role', 'Agent'),
                goal=getattr(agent, 'goal', ''),
                backstory=getattr(agent, 'backstory', ''),
                tools=tools,
                model=resolved_model,
                allow_delegation=getattr(agent, 'allow_delegation', True),
                metadata={"llm_call_params": llm_params},
            ))
        return specs

    def _step_callback_bridge(self):
        """Create a StepCallback that feeds into the existing on_step handler."""
        if not self.on_step:
            return None
        from teaming24.agent.framework.base import StepOutput
        def _bridge(step: StepOutput):
            self.on_step({
                "agent": step.agent,
                "action": step.action,
                "tool": step.tool,
                "tool_input": step.tool_input,
                "content": step.content,
                "timestamp": step.timestamp,
                "metadata": dict(step.metadata or {}),
            })
        return _bridge

    def _is_local_result_acceptable(self, result: str) -> bool:
        """Define when local Coordinator has obtained an acceptable result.

        Returns False if result is empty, placeholder-like, or clearly incomplete —
        in which case local refinement may be attempted.
        """
        if not result or not isinstance(result, str):
            return False
        text = result.strip()
        if not text:
            return False

        # Accept explicit give-up: agent states task is impossible — stop refinement
        if self._is_explicit_give_up(text):
            return True

        # Hard reject obvious placeholders.
        placeholder_patterns = (
            r"\btodo\b",
            r"\btbd\b",
            r"\bfixme\b",
            r"\[to be filled\]",
            r"\[placeholder\]",
            r"\[\.\.\.\]",
            r"\(details omitted\)",
        )
        if any(re.search(pat, text, re.IGNORECASE) for pat in placeholder_patterns):
            return False

        try:
            cfg = get_config()
            api_cfg = getattr(cfg, "api", cfg) if cfg else None
            thresh = getattr(api_cfg, "result_empty_threshold", 50)
            if len(text) >= thresh:
                return True
            # Accept concise but concrete local outputs to avoid unnecessary local refine loops.
            if len(text) >= 24 and self._result_has_concrete_evidence(text):
                return True
            return False
        except Exception as exc:
            logger.warning(
                "Failed to read result_empty_threshold; using default 50: %s",
                exc,
                exc_info=True,
            )
            if len(text) >= 50:
                return True
            if len(text) >= 24 and self._result_has_concrete_evidence(text):
                return True
            return False

    def _build_local_refinement_prompt(self, prompt: str, result: str, round_num: int) -> str:
        """Build refinement prompt for local Coordinator retry."""
        return render_prompt(
            "core.local_refinement",
            round_num=round_num,
            original_task=prompt,
            previous_output=(result[:800] + ("..." if len(result) > 800 else "")),
        )

    async def _run_via_adapter(self, prompt: str, task_id: str,
                               crewai_agents: list) -> dict:
        """Execute via the FrameworkAdapter (native or crewai adapter).

        local team coordinator controls success: if result is not acceptable,
        performs refinement rounds before returning to Organizer.
        """
        try:
            cfg = get_config()
            api_cfg = getattr(cfg, "api", cfg) if cfg else None
            max_local_rounds = getattr(api_cfg, "local_coordinator_max_refinement_rounds", 2) or 1
        except Exception as exc:
            logger.warning(
                "Failed to read local_coordinator_max_refinement_rounds; using default 1: %s",
                exc,
                exc_info=True,
            )
            max_local_rounds = 1
        adapter = self._get_framework_adapter()
        specs = self._agents_to_specs(crewai_agents)
        context = self._build_adapter_context()
        current_prompt = prompt
        last_result = ""
        last_status = "success"
        previous_signature = ""
        stagnated_rounds = 0

        for round_num in range(1, max_local_rounds + 1):
            if round_num > 1 and task_id and self.on_step:
                self._wf_step(
                    task_id, "local team coordinator", "local_refine",
                    f"Refinement round {round_num}/{max_local_rounds} — retrying with feedback...",
                    agent_type="coordinator",
                )
            try:
                if len(specs) < 2:
                    base_spec = (specs or [self._agents_to_specs([self.coordinator or self.organizer])[0]])[0]
                    result_text = await adapter.execute_hierarchical(
                        prompt=current_prompt,
                        manager=base_spec,
                        workers=[],
                        step_callback=self._step_callback_bridge(),
                        task_id=task_id,
                        context=context,
                    )
                else:
                    manager_spec = specs[0]
                    worker_specs = specs[1:]
                    result_text = await adapter.execute_hierarchical(
                        prompt=current_prompt,
                        manager=manager_spec,
                        workers=worker_specs,
                        step_callback=self._step_callback_bridge(),
                        task_id=task_id,
                        context=context,
                    )
                last_result = result_text if isinstance(result_text, str) else str(result_text)
                last_status = "success"
                signature = self._normalize_refinement_signature(last_result)
                if round_num > 1 and signature == previous_signature:
                    stagnated_rounds += 1
                else:
                    stagnated_rounds = 0
                previous_signature = signature
                if self._is_local_result_acceptable(last_result):
                    return {"status": "success", "result": last_result, "duration": 0}
                if stagnated_rounds >= 1 and round_num < max_local_rounds:
                    logger.info(
                        "[LocalCrew] Local refinement converged without improvement at round %s/%s; stopping early",
                        round_num,
                        max_local_rounds,
                    )
                    return {
                        "status": "success",
                        "result": last_result,
                        "duration": 0,
                        "refinement_converged": True,
                    }
                if round_num < max_local_rounds:
                    current_prompt = self._build_local_refinement_prompt(prompt, last_result, round_num)
            except Exception as e:
                logger.error(f"[LocalCrew] Adapter execution failed: {e}")
                return {"status": "error", "error": str(e), "result": last_result, "duration": 0}

        return {"status": last_status, "result": last_result or "No satisfactory result after refinement.", "duration": 0}

    def _build_adapter_context(self) -> dict:
        """Build context for FrameworkAdapter (LocalAgentPool + LocalAgentRouter)."""
        context = {}
        if not self._use_native:
            return context
        try:
            from teaming24.agent.local_agent_pool import LocalAgentWorkforcePool
            from teaming24.agent.local_agent_router import create_local_agent_router
            from teaming24.llm.model_resolver import (
                build_runtime_llm_config,
                resolve_model_and_call_params,
            )

            pool = LocalAgentWorkforcePool(self)
            lar_cfg = getattr(self.config, "local_agent_router", None)
            model = (
                self.router_model_overrides.get("local_agent_router")
                or self.router_model_overrides.get("an_router")
                or getattr(lar_cfg, "model", "")
                or getattr(self.config.an_router, "model", "")
                or "flock/gpt-5.2"
            )
            llm_cfg = build_runtime_llm_config(
                self.config.llm,
                runtime_settings=self.runtime_settings,
                runtime_default_provider=self.runtime_default_provider,
            )
            resolved_model, router_call_params, _ = resolve_model_and_call_params(model, llm_cfg)
            router = create_local_agent_router(
                model=resolved_model,
                temperature=getattr(lar_cfg, "routing_temperature", 0.2),
                max_tokens=getattr(lar_cfg, "routing_max_tokens", 1000),
                llm_call_params=router_call_params,
            )
            context["local_agent_pool"] = pool
            context["local_agent_router"] = router
        except Exception as e:
            logger.error("[LocalCrew] LocalAgentPool/Router not available: %s", e)
        return context

    # ------------------------------------------------------------------
    # Local subtask dispatch
    # ------------------------------------------------------------------

    def _run_local_subtask_sync(self, local_prompt: str, task_id: str,
                                router_active: bool) -> dict:
        """Execute a single local subtask synchronously.

        Hierarchy: Organizer manages Coordinators/ANs; Coordinator manages Workers.
        Local execution always uses ``[Coordinator, Workers…]`` — Coordinator is
        the manager, Workers execute. Organizer operates at routing/aggregation
        level only.

        **Important**: passes ``_subtask=True`` to the inner execute_sync so
        that the task lifecycle guards (status check, start_task,
        complete_task, fail_task) are skipped — the parent Organizer already
        owns the task lifecycle.
        """
        adapter = self._get_framework_adapter()

        if adapter is not None:
            import asyncio
            # Coordinator manages Workers; Organizer manages Coordinators/ANs (routing level).
            # Local execution is always Coordinator + Workers.
            agents = self._build_local_pool()
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError as e:
                    logger.debug("No running event loop: %s", e)
                    loop = None

                if loop is not None and loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            asyncio.run,
                            self._run_via_adapter(local_prompt, task_id, agents),
                        ).result()
                    return result
                else:
                    return asyncio.run(
                        self._run_via_adapter(local_prompt, task_id, agents)
                    )
            except Exception as e:
                if self._use_native:
                    logger.error(f"[LocalCrew] Native adapter failed: {e}")
                    return {"status": "error", "error": str(e), "duration": 0}
                logger.error(f"[LocalCrew] Native adapter failed: {e}, falling back to CrewAI")

        if self._use_native and adapter is None:
            logger.error("[LocalCrew] Native backend configured but adapter unavailable")
            return {"status": "error", "error": "Native adapter unavailable", "duration": 0}

        # Coordinator manages Workers; Organizer manages Coordinators/ANs.
        # Local execution (CrewWrapper fallback) is always Coordinator + Workers.
        coord_agents = self._build_local_pool()
        crew = CrewWrapper(
            agents=coord_agents,
            task_manager=self.task_manager,
            on_step=self.on_step,
            process=self.process,
            verbose=self.verbose,
            memory=self.memory,
            planning=self.planning,
            planning_llm=self.planning_llm,
            reasoning=self.reasoning,
            max_reasoning_attempts=self.max_reasoning_attempts,
            streaming=self.streaming,
        )
        try:
            return crew.execute_sync(local_prompt, task_id, _subtask=True)
        except Exception as e:
            logger.error(f"CrewWrapper.execute_sync raised: {e}")
            return {"status": "error", "error": str(e), "duration": 0}

    async def _run_local_subtask_async(self, local_prompt: str, task_id: str,
                                       router_active: bool) -> dict:
        """Execute a single local subtask asynchronously.

        Hierarchy: Coordinator manages Workers. Local execution uses
        [Coordinator, Workers]. Passes ``_subtask=True`` so the inner execute()
        skips lifecycle guards.
        """
        adapter = self._get_framework_adapter()

        if adapter is not None:
            # Coordinator manages Workers; Organizer manages Coordinators/ANs (routing level).
            # Local execution is always Coordinator + Workers.
            agents = self._build_local_pool()
            try:
                return await self._run_via_adapter(local_prompt, task_id, agents)
            except Exception as e:
                if self._use_native:
                    logger.error(f"[LocalCrew] Native adapter failed: {e}")
                    return {"status": "error", "error": str(e), "duration": 0}
                logger.error(f"[LocalCrew] Native adapter failed: {e}, falling back to CrewAI")

        if self._use_native and adapter is None:
            logger.error("[LocalCrew] Native backend configured but adapter unavailable")
            return {"status": "error", "error": "Native adapter unavailable", "duration": 0}

        # Coordinator manages Workers; Organizer manages Coordinators/ANs.
        # Local execution (adapter fallback to CrewWrapper) is always Coordinator + Workers.
        coord_agents = self._build_local_pool()
        crew = CrewWrapper(
            agents=coord_agents,
            task_manager=self.task_manager,
            on_step=self.on_step,
            process=self.process,
            verbose=self.verbose,
            memory=self.memory,
            planning=self.planning,
            planning_llm=self.planning_llm,
            reasoning=self.reasoning,
            max_reasoning_attempts=self.max_reasoning_attempts,
            streaming=self.streaming,
        )
        return await crew.execute(local_prompt, task_id, _subtask=True)

    def _run_single_remote_subtask(
        self, subtask: Any, task_id: str, *, round_num: int = 1
    ) -> dict:
        """Execute a single remote subtask synchronously (submit + wait)."""
        from teaming24.agent.an_router import RoutingPlan
        # Wrap the single subtask into a mini-plan for _execute_remote_subtasks
        mini_plan = RoutingPlan(
            subtasks=[subtask],
            execution_mode="parallel",  # irrelevant for single subtask
        )
        results = self._execute_remote_subtasks(
            mini_plan, task_id, is_retry=(round_num > 1)
        )
        return results[0] if results else {
            "assigned_to": subtask.assigned_to,
            "status": "error", "error": "No result returned",
        }

    @staticmethod
    def _infer_remote_stage(
        status_state: str = "",
        status_event: str = "",
        *,
        is_final: bool = False,
        fallback: str = "running",
        percentage: int | None = None,
        phase_label: str = "",
    ) -> str:
        """Map remote runtime state into a stable stage label for UI + tracking."""
        state = str(status_state or "").strip().lower()
        event = str(status_event or "").strip().lower()
        label = str(phase_label or "").strip().lower()

        if is_final:
            if state in ("completed", "success"):
                return "completed"
            return "failed"

        try:
            pct_num = int(percentage) if percentage is not None else None
        except (TypeError, ValueError):
            pct_num = None
        if pct_num is not None and pct_num >= 100:
            return "finalizing"
        if any(token in label for token in ("remote task completed", "finalizing", "wrapping up", "completed")):
            return "finalizing"

        if state in ("pending", "queued", "received"):
            return "queued"
        if state in ("routing", "dispatching", "submitted"):
            return "submitted"
        if state in ("running", "executing", "in_progress"):
            return "running"
        if state in ("polling",):
            return "polling"
        if event in ("task_snapshot",) and state in ("", "pending"):
            return "queued"
        if event in ("phase_change", "step"):
            return "running"
        return fallback

    def _emit_remote_status_event(
        self,
        subtask: Any,
        *,
        action: str = "remote_progress",
        stage: str,
        transport: str,
        content: str,
        remote_task_id: str = "",
        main_task_id: str | None = None,
        remote_status: str = "",
        status_event: str = "",
        percentage: int | None = None,
        phase: str | None = None,
        phase_label: str = "",
        progress: dict | None = None,
        error_text: str = "",
        is_final: bool = False,
    ) -> None:
        """Emit a structured remote timeline event with normalized progress metadata."""
        if not self.on_step:
            return

        import time as _time

        progress_payload = dict(progress or {})
        now_ts = _time.time()

        raw_pct = progress_payload.get("percentage", percentage)
        pct_num = None
        if raw_pct is not None:
            try:
                pct_num = max(0, min(100, int(raw_pct)))
            except (TypeError, ValueError):
                pct_num = None
        if pct_num is not None:
            progress_payload["percentage"] = pct_num

        resolved_phase = str(progress_payload.get("phase", "") or phase or "").strip()
        if not resolved_phase:
            resolved_phase = "completed" if stage == "completed" else "executing"
        progress_payload["phase"] = resolved_phase

        resolved_label = str(progress_payload.get("phase_label", "") or phase_label or "").strip()
        if not resolved_label:
            resolved_label = content[:120]
        progress_payload["phase_label"] = resolved_label

        if "current_step_number" in progress_payload and progress_payload.get("current_step_number") is None:
            progress_payload.pop("current_step_number", None)
        if "total_steps" in progress_payload and progress_payload.get("total_steps") is None:
            progress_payload.pop("total_steps", None)

        progress_payload["stage"] = str(stage or "running")
        progress_payload["transport"] = str(transport or "unknown")
        progress_payload["remote_status"] = str(progress_payload.get("remote_status", "") or remote_status or "")
        progress_payload["remote_status_event"] = str(progress_payload.get("remote_status_event", "") or status_event or "")
        progress_payload["heartbeat_at"] = now_ts
        if progress_payload.get("stall_seconds") is None:
            progress_payload.pop("stall_seconds", None)
        if error_text:
            progress_payload["last_error"] = str(error_text)[:180]

        try:
            self.on_step({
                "agent": subtask.assigned_to,
                "agent_type": "remote",
                "action": action,
                "content": content,
                "is_delegation": True,
                "remote_task_id": remote_task_id,
                "remote_node_id": subtask.target_node_id,
                "remote_stage": progress_payload["stage"],
                "remote_transport": progress_payload["transport"],
                "remote_status": progress_payload["remote_status"],
                "remote_status_event": progress_payload["remote_status_event"],
                "progress": progress_payload,
                "remote_progress": progress_payload,
                "main_task_id": main_task_id,
            })
        except Exception as emit_exc:
            logger.warning(
                "[ANRouter] Failed to emit remote status for %s (%s): %s",
                subtask.assigned_to,
                remote_task_id or "pending",
                emit_exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # Parallel execution
    # ------------------------------------------------------------------

    def _dispatch_parallel(self, plan: Any, local_prompt: str,
                           task_id: str, is_async: bool = False,
                           round_num: int = 1):
        """Dispatch all subtasks concurrently. Returns (local_result, remote_results).

        For the *async* path the caller awaits the returned coroutine-objects
        where needed; for the *sync* path everything runs in a ThreadPoolExecutor.
        """
        import concurrent.futures

        has_remote = plan.has_remote
        run_local = plan.has_local
        remote_count = len(plan.remote_subtasks)
        local_count = len(plan.local_subtasks)
        logger.info(
            f"[Parallel] Dispatch: remote={remote_count}, local={local_count}, "
            f"total_subtasks={len(plan.subtasks)}"
        )

        import threading as _threading
        _completed = 0
        _completed_lock = _threading.Lock()

        def _track_completion(task_id_: str, count: int = 1):
            nonlocal _completed
            with _completed_lock:
                _completed += count
                current = _completed
            if task_id_ and self.task_manager:
                self.task_manager.update_progress(
                    task_id_, completed_workers=current,
                )

        if has_remote and run_local:
            self._wf_step(
                task_id, "Organizer", "parallel_start",
                f"Dispatching {remote_count} remote + {local_count} local subtask(s) in parallel...",
                agent_type="organizer",
            )

            for st in plan.remote_subtasks:
                logger.info(
                    f"[Parallel] Remote target: {st.assigned_to} "
                    f"(node_id={st.target_node_id})"
                )
                self._wf_step(
                    task_id, st.assigned_to, "dispatching",
                    f"Receiving task via HTTP: {st.description[:60]}...",
                    agent_type="remote",
                )

            def _run_remote():
                return self._execute_remote_subtasks(
                    plan, task_id, is_retry=(round_num > 1)
                )

            def _run_local():
                return self._run_local_subtask_sync(local_prompt, task_id, True)

            with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
                remote_fut = pool.submit(_run_remote)
                local_fut = pool.submit(_run_local)
                self._wf_step(
                    task_id, "local team coordinator", "local_start",
                    "Executing local subtask (parallel)...",
                    agent_type="coordinator",
                )

                # Collect results as they complete (whichever finishes first)
                local_result = None
                remote_results = []
                fut_map = {local_fut: "local", remote_fut: "remote"}

                for fut in concurrent.futures.as_completed(fut_map):
                    kind = fut_map[fut]
                    if kind == "local":
                        try:
                            local_result = fut.result()
                        except Exception as e:
                            logger.error(f"Local crew execution raised: {e}")
                            local_result = {"status": "error", "error": str(e), "duration": 0}
                        _track_completion(task_id)
                        self._wf_step(
                            task_id, "local team coordinator", "local_done",
                            f"Local execution completed: status={local_result.get('status', '?')}",
                            agent_type="coordinator",
                        )
                        if not remote_fut.done():
                            self._wf_step(
                                task_id,
                                "Organizer",
                                "waiting_remote",
                                (
                                    f"Local execution finished. Waiting for {remote_count} "
                                    f"remote AN(s) to complete."
                                ),
                                agent_type="organizer",
                            )
                    else:
                        try:
                            remote_results = fut.result()
                        except Exception as e:
                            logger.error(f"Remote dispatch raised: {e}")
                            remote_results = []
                        for _ in remote_results:
                            _track_completion(task_id)

                if local_result is None:
                    local_result = {"status": "error", "error": "Local execution did not produce a result", "duration": 0}

            return local_result, remote_results

        elif has_remote:
            self._wf_step(
                task_id, "Organizer", "dispatch",
                "Dispatching remote subtasks (no local execution)...",
                agent_type="organizer",
            )
            for st in plan.remote_subtasks:
                self._wf_step(
                    task_id, st.assigned_to, "dispatching",
                    f"Receiving task via HTTP: {st.description[:60]}...",
                    agent_type="remote",
                )
            remote_results = self._execute_remote_subtasks(
                plan, task_id, is_retry=(round_num > 1)
            )
            for _ in remote_results:
                _track_completion(task_id)
            self._wf_step(
                task_id, "Organizer", "skip_local",
                "local team coordinator not selected — skipping.",
                agent_type="organizer",
            )
            return {"status": "skipped", "task_id": task_id, "result": "", "cost": {}, "duration": 0}, remote_results

        elif run_local:
            self._wf_step(
                task_id, "local team coordinator", "local_start",
                "Starting local Coordinator execution...",
                agent_type="coordinator",
            )
            local_result = self._run_local_subtask_sync(local_prompt, task_id, True)
            _track_completion(task_id)
            self._wf_step(
                task_id, "local team coordinator", "local_done",
                f"Local execution completed: status={local_result.get('status', '?')}",
                agent_type="coordinator",
            )
            return local_result, []

        else:
            logger.warning("[ANRouter] Neither local nor remote selected")
            return {"status": "skipped", "task_id": task_id, "result": "", "cost": {}, "duration": 0}, []

    # ------------------------------------------------------------------
    # Sequential execution
    # ------------------------------------------------------------------

    def _dispatch_sequential(self, plan: Any, prompt: str,
                             task_id: str,
                             round_context: str = "",
                             round_num: int = 1) -> (dict, list[dict]):
        """Execute subtasks one by one in ``order``.

        Each step receives the previous step's output as additional context,
        so that later steps can build upon earlier results.

        Args:
            round_context: Non-empty on refinement rounds (round 2+). Prepended
                to each *local* subtask description so workers know what to fix.
            round_num: Current refinement round (1 = first attempt). Used for
                payment skip on retry (round_num>1 → no charge to remote ANs).
        Returns (last_result_as_local, remote_results_list).
        """
        ordered = getattr(plan, "ordered_subtasks", None) or getattr(plan, "subtasks", [])
        ordered = sorted(ordered, key=lambda s: getattr(s, "order", 0)) if ordered else []
        previous_outputs: list[dict] = []   # accumulate all previous results
        remote_results: list[dict] = []
        local_result: dict = {
            "status": "skipped", "task_id": task_id,
            "result": "", "cost": {}, "duration": 0,
        }
        total = len(ordered)
        if total == 0:
            logger.warning("[Sequential] No subtasks to execute")
            return local_result, remote_results
        self._wf_step(
            task_id, "Organizer", "sequential_start",
            f"Executing {total} subtask(s) sequentially — each step's output feeds into the next",
            agent_type="organizer",
        )
        logger.info(
            f"[Sequential] Starting {total} step(s) in order: "
            + ", ".join(f"[{st.order}] {st.assigned_to}" for st in ordered)
        )

        for i, st in enumerate(ordered, 1):
            # For refinement rounds, prepend feedback context to local subtasks
            base_desc = (
                f"{round_context}\n\n{st.description}"
                if round_context and not st.is_remote
                else st.description
            )

            # Build context from ALL previous steps (not just the last one)
            if previous_outputs:
                context_parts = []
                for j, prev in enumerate(previous_outputs, 1):
                    prev_name = prev.get("assigned_to", f"Step {j}")
                    prev_result = prev.get("result", "")
                    if prev_result:
                        context_parts.append(
                            f"[Step {j} — {prev_name}]\n{prev_result}"
                        )
                context_block = "\n\n".join(context_parts)
                enriched = (
                    f"{base_desc}\n\n"
                    f"--- Results from previous step(s) (use as context) ---\n"
                    f"{context_block}"
                )
            else:
                enriched = base_desc

            has_ctx = bool(previous_outputs)
            self._wf_step(
                task_id, "Organizer", "seq_step",
                f"Sequential step {i}/{total}: {st.assigned_to} — {st.description[:60]}..."
                + (f" (with context from {len(previous_outputs)} prior step(s))" if has_ctx else ""),
                agent_type="organizer",
            )
            logger.info(
                f"[Sequential] Step {i}/{total}: assigned_to={st.assigned_to}, "
                f"is_remote={st.is_remote}, context_steps={len(previous_outputs)}"
            )

            if st.is_remote:
                self._wf_step(
                    task_id, st.assigned_to, "dispatching",
                    f"Receiving sequential step {i}/{total} via HTTP...",
                    agent_type="remote",
                )
                from teaming24.agent.an_router import RoutingSubtask
                enriched_st = RoutingSubtask(
                    description=enriched,
                    assigned_to=st.assigned_to,
                    is_remote=True,
                    target_node_id=st.target_node_id,
                    reason=st.reason,
                    order=st.order,
                )
                result = self._run_single_remote_subtask(
                    enriched_st, task_id, round_num=round_num
                )
                result["assigned_to"] = st.assigned_to
                remote_results.append(result)
                # Emit key result as separate step for human-readable markdown display
                raw_result = result.get("result", "")
                full_result = str(raw_result) if raw_result is not None else ""
                if full_result.strip():
                    self._wf_step(
                        task_id, st.assigned_to, "output",
                        full_result,
                        agent_type="remote",
                    )
            else:
                self._wf_step(
                    task_id, "local team coordinator", "local_start",
                    f"Sequential step {i}/{total}: executing locally...",
                    agent_type="coordinator",
                )
                result = self._run_local_subtask_sync(enriched, task_id, True)
                result["assigned_to"] = st.assigned_to
                local_result = result
                status = result.get("status", "?")
                result_preview = str(result.get("result") or "")[:100]
                self._wf_step(
                    task_id, "local team coordinator", "local_done",
                    f"Step {i}/{total} done (status={status}): {result_preview}...",
                    agent_type="coordinator",
                )
                # Emit key result as separate step for human-readable markdown display
                raw_result = result.get("result", "")
                full_result = str(raw_result) if raw_result is not None else ""
                if full_result.strip():
                    self._wf_step(
                        task_id, "local team coordinator", "output",
                        full_result,
                        agent_type="coordinator",
                    )

            if result.get("status") != "error":
                result_text = str(result.get("result", "") or "").strip()
                if not result_text:
                    last_non_empty = next(
                        (
                            prev for prev in reversed(previous_outputs)
                            if str(prev.get("result", "") or "").strip()
                        ),
                        None,
                    )
                    if last_non_empty is not None:
                        logger.warning(
                            "[Sequential] Step %s/%s returned empty result; preserving previous non-empty output for context continuity",
                            i,
                            total,
                        )
                        result = {
                            **result,
                            "result": last_non_empty.get("result", ""),
                            "inherited_context": True,
                        }

            previous_outputs.append(result)
            # Track worker completion for progress display
            if task_id and self.task_manager:
                completed = len(previous_outputs)
                pct = 25 + int(55 * completed / total)
                self.task_manager.update_progress(
                    task_id, completed_workers=completed,
                    percentage=min(pct, 80),
                )
            logger.info(
                f"[Sequential] Step {i}/{total} completed: "
                f"status={result.get('status', '?')}, "
                f"result_len={len(result.get('result', ''))}"
            )

            if result.get("status") == "error":
                err = result.get("error", "unknown error")
                logger.warning(
                    f"[Sequential] Step {i}/{total} failed ({err}) — aborting remaining steps"
                )
                self._wf_step(
                    task_id, "Organizer", "seq_abort",
                    f"Step {i} failed — stopping sequential chain: {err[:80]}",
                    agent_type="organizer",
                )
                break

            # Handoff notification: only emit if step succeeded and there's a next step
            if i < total:
                next_st = ordered[i]  # 0-indexed: ordered[i] is step i+1
                handoff_from = st.assigned_to
                handoff_to = next_st.assigned_to
                self._wf_step(
                    task_id, "Organizer", "handoff",
                    f"📋 Handoff: passing output from {handoff_from} → {handoff_to} (step {i} → {i+1})",
                    agent_type="organizer",
                )

        logger.info(
            f"[Sequential] Finished {len(previous_outputs)}/{total} steps, "
            f"remote_results={len(remote_results)}"
        )
        return local_result, remote_results

    # ------------------------------------------------------------------
    # Main execute (async)
    # ------------------------------------------------------------------

    async def execute(self, prompt: str, task_id: str = None) -> dict:
        """Execute a task with the local crew (async).

        Organizer-driven flow:
        1. Organizer → ANRouter: routing decision (includes execution_mode).
        2a. **parallel**  — all subtasks run concurrently.
        2b. **sequential** — subtasks run in order; each step's output
            feeds into the next step as context.
        3. Organizer aggregates all results and returns to the user.
        """
        import time as _wf_time
        _wf_start = _wf_time.time()
        router = getattr(self, "_an_router", None)

        # Guard: prevent stacking if task was already completed/failed
        if task_id and self.task_manager:
            _existing = self.task_manager.get_task(task_id)
            if _existing and _existing.status.value in ("completed", "failed"):
                logger.warning(
                    f"[LocalCrew] Task {task_id} already "
                    f"'{_existing.status.value}' — aborting"
                )
                return {
                    "status": _existing.status.value,
                    "result": getattr(_existing, "result", None) or "",
                    "error": f"Task already {_existing.status.value}",
                }

        # Sandbox file tools to the task output workspace
        if task_id:
            self._bind_task_output_dir(task_id)

        try:
            return await self._execute_body(prompt, task_id, router, _wf_start)
        finally:
            if task_id:
                try:
                    from teaming24.runtime.task_context import clear_task_context
                    clear_task_context()
                except ImportError:
                    pass
                try:
                    from teaming24.runtime.manager import get_runtime_manager
                    await get_runtime_manager().release_sandbox(task_id)
                except Exception:
                    pass

    async def _execute_body(self, prompt: str, task_id: str | None, router, _wf_start: float) -> dict:
        """Execute body (task context is set; cleared by caller's finally)."""
        import time as _wf_time
        # Fire plugin hook: before_task_execute
        try:
            from teaming24.plugins.hooks import get_hook_registry
            await get_hook_registry().fire("before_task_execute", task_id=task_id, prompt=prompt)
        except Exception as _hook_exc:
            logger.debug(f"[Hook] before_task_execute failed (ignored): {_hook_exc}")

        # Phase: RECEIVED
        self._wf_step(task_id, "Organizer", "receive",
                      f"Task received: {prompt[:100]}...",
                      agent_type="organizer",
                      phase=TaskPhase.RECEIVED,
                      phase_label="Task received by Organizer",
                      percentage=5)

        # --- Step 1: Organizer asks ANRouter for routing decision ---
        remote_results: list[dict] = []
        local_result: dict = {
            "status": "skipped", "task_id": task_id,
            "result": "", "cost": {}, "duration": 0,
        }
        plan = None
        local_prompt = prompt

        if router:
            # Phase: ROUTING
            self._wf_step(task_id, "Organizer", "route",
                          "Consulting ANRouter for workforce allocation...",
                          agent_type="organizer",
                          phase=TaskPhase.ROUTING,
                          phase_label="ANRouter routing (LLM may take 1–2 min)",
                          percentage=10)
            plan = router.route(prompt)
            local_prompt = plan.local_prompt or prompt
            self._log_routing_plan(task_id, plan, local_prompt)

            # --- Human approval for routing (if remote ANs selected) ---
            plan = self._request_routing_approval(task_id, plan)
            if plan is None:
                # User cancelled
                result = {"status": "cancelled", "result": "Task cancelled by user."}
                if task_id and self.task_manager:
                    self.task_manager.fail_task(task_id, "Cancelled by user")
                return result

        # --- Step 2+3: Dispatch → Synthesize (with self-improvement rounds) ---
        mode = getattr(plan, "execution_mode", "parallel") if plan else None

        # Initial dispatch setup (once, before rounds)
        if plan:
            self._wf_step(task_id, "Organizer", "dispatch",
                          f"Dispatching to selected coordinators ({mode} mode)...",
                          agent_type="organizer",
                          phase=TaskPhase.DISPATCHING,
                          phase_label=f"Dispatching to coordinators ({mode})",
                          percentage=20)
            if task_id and self.task_manager:
                local_count = 1 if plan.has_local else 0
                remote_count = len(plan.remote_subtasks)
                self.task_manager.update_progress(
                    task_id, total_workers=local_count + remote_count,
                )
        else:
            self._wf_step(task_id, "local team coordinator", "local_start",
                          "Starting local Coordinator execution...",
                          agent_type="coordinator",
                          phase=TaskPhase.EXECUTING,
                          phase_label="Coordinator assigning to workers",
                          percentage=25)
            if task_id and self.task_manager:
                self.task_manager.update_progress(task_id, total_workers=1)

        # current_local_prompt evolves each round (original → refined with feedback)
        current_local_prompt = local_prompt
        max_rounds = self._resolve_max_rounds_for_prompt(prompt)
        quality_gate_passed = False
        quality_gate_feedback = ""
        policy_for_rounds = self._get_quality_policy(prompt)
        best_round_result: dict[str, Any] | None = None
        best_round_score = -1.0
        prev_round_signature = ""
        stagnation_count = 0

        for round_num in range(1, max_rounds + 1):
            if round_num > 1:
                self._wf_step(
                    task_id, "Organizer", "round_start",
                    f"Refinement round {round_num}/{max_rounds} — applying feedback...",
                    agent_type="organizer",
                    phase=TaskPhase.DISPATCHING,
                    phase_label=f"Refinement round {round_num}/{max_rounds}",
                    percentage=20,
                )
                logger.info(f"[Organizer] Starting refinement round {round_num}/{max_rounds}")

            # Dispatch
            if plan:
                if mode == "sequential":
                    round_ctx = current_local_prompt if round_num > 1 else ""
                    local_result, remote_results = self._dispatch_sequential(
                        plan, current_local_prompt, task_id,
                        round_context=round_ctx, round_num=round_num,
                    )
                else:
                    local_result, remote_results = self._dispatch_parallel(
                        plan, current_local_prompt, task_id,
                        round_num=round_num,
                    )
            else:
                local_result = await self._run_local_subtask_async(
                    current_local_prompt, task_id, False
                )
                if task_id and self.task_manager:
                    self.task_manager.update_progress(task_id, completed_workers=1)
                self._wf_step(
                    task_id, "local team coordinator", "local_done",
                    f"Local execution completed: status={local_result.get('status', '?')}",
                    agent_type="coordinator",
                )
                remote_results = []

            # Synthesize
            self._wf_step(
                task_id, "Organizer", "aggregate",
                f"Synthesizing answer (round {round_num}/{max_rounds})...",
                agent_type="organizer",
                phase=TaskPhase.AGGREGATING,
                phase_label=f"Synthesizing answer (round {round_num})",
                percentage=85,
            )
            result = self._aggregate_results(
                local_result, remote_results, prompt=prompt, task_id=task_id
            )
            result_text_for_round = (
                result.get("result", "") if isinstance(result, dict) else str(result)
            )
            round_signature = self._normalize_refinement_signature(result_text_for_round)
            prev_best_score = best_round_score
            round_score = self._compute_round_quality_score(
                result_text=result_text_for_round,
                prompt=prompt,
                task_id=task_id,
                policy=policy_for_rounds,
            )
            if round_score > best_round_score + 0.01:
                best_round_score = round_score
                if isinstance(result, dict):
                    best_round_result = dict(result)
                else:
                    best_round_result = {"status": "success", "result": str(result)}
            improved = round_score > prev_best_score + 0.01
            if round_num > 1 and (round_signature == prev_round_signature or not improved):
                stagnation_count += 1
            else:
                stagnation_count = 0
            prev_round_signature = round_signature

            # Evaluate on every round (including the final round)
            self._wf_step(
                task_id, "Organizer", "eval_round",
                f"Evaluating result quality (round {round_num}/{max_rounds})...",
                agent_type="organizer",
                percentage=90,
            )
            satisfied, feedback = self._evaluate_result(
                prompt, result, round_num, task_id=task_id
            )
            quality_gate_passed = satisfied
            quality_gate_feedback = feedback or ""
            if satisfied:
                logger.info(
                    f"[Organizer] Round {round_num}/{max_rounds}: result accepted"
                )
                break
            if round_num < max_rounds:
                if (
                    stagnation_count >= 1
                    and best_round_result is not None
                    and best_round_score >= 0.45
                ):
                    quality_gate_passed = True
                    result = best_round_result
                    msg = (
                        f"Refinement converged after round {round_num}/{max_rounds}; "
                        f"using best available result (score={best_round_score:.2f})"
                    )
                    self._wf_step(
                        task_id,
                        "Organizer",
                        "refine_converged",
                        msg,
                        agent_type="organizer",
                    )
                    logger.info("[Organizer] %s", msg)
                    break
                logger.info(
                    f"[Organizer] Round {round_num}/{max_rounds}: not satisfied "
                    f"— starting round {round_num + 1}"
                )
                current_local_prompt = self._build_round_prompt(
                    prompt, result, feedback, round_num
                )
            else:
                logger.warning(
                    "[Organizer] Final round %s/%s did not satisfy quality gate: %s",
                    round_num,
                    max_rounds,
                    (feedback or "unspecified feedback")[:300],
                )

        if not quality_gate_passed:
            quality_msg = (
                f"Quality gate failed after {max_rounds} round(s): "
                f"{quality_gate_feedback or 'result did not meet completion criteria'}"
            )
            self._wf_step(
                task_id, "Organizer", "quality_gate_failed",
                quality_msg,
                agent_type="organizer",
            )
            logger.error("[Organizer] %s", quality_msg)
            if isinstance(result, dict):
                result["status"] = "error"
                result["error"] = quality_msg
                result["quality_gate"] = {
                    "passed": False,
                    "feedback": quality_gate_feedback,
                    "rounds": max_rounds,
                }

        # --- Step 4: Save all outputs ---
        self._save_all_outputs(task_id, prompt, result, remote_results)

        # --- Emit AN status summary for user tracking in thinking/steps ---
        self._emit_an_status_summary(task_id, local_result, remote_results)

        elapsed = _wf_time.time() - _wf_start
        self._wf_step(
            task_id, "Organizer", "complete",
            f"Task finished: status={result.get('status', '?')}, duration={elapsed:.1f}s",
            agent_type="organizer",
            phase=TaskPhase.COMPLETED,
            phase_label="Completed",
            percentage=100,
        )

        # Fallback: when result is empty but we have worker steps with content,
        # use the last meaningful step (fallback to tool/worker output)
        cfg = get_config()
        result_str = result.get("result", "") if isinstance(result, dict) else str(result)
        empty_thresh = cfg.api.result_empty_threshold
        fallback_min = cfg.api.result_fallback_min_chars
        if (not result_str or len(result_str.strip()) < empty_thresh) and task_id and self.task_manager:
            task = self.task_manager.get_task(task_id)
            if task and task.steps:
                for step in reversed(task.steps):
                    content = (step.content or "").strip()
                    if content and len(content) > fallback_min:
                        result_str = content
                        result = {**result, "result": result_str} if isinstance(result, dict) else result
                        logger.info(
                            f"[Organizer] Fallback: using step content from {step.agent} "
                            f"({len(result_str)} chars)"
                        )
                        break

        # Mark task as completed in TaskManager so SSE subscribers receive
        # the final event (critical for remote AN result collection).
        if task_id and self.task_manager:
            result_status = (
                str(result.get("status", "")).lower()
                if isinstance(result, dict) else ""
            )
            if result_status == "error":
                self.task_manager.fail_task(task_id, result.get("error", result_str))
            else:
                self.task_manager.complete_task(task_id, result_str)

        # Fire plugin hook: after_task_execute
        try:
            from teaming24.plugins.hooks import get_hook_registry
            result_text = result.get("result", "") if isinstance(result, dict) else str(result)
            await get_hook_registry().fire("after_task_execute", task_id=task_id, result=result_text)
        except Exception as _hook_exc:
            logger.debug(f"[Hook] after_task_execute failed (ignored): {_hook_exc}")

        return result

    # ------------------------------------------------------------------
    # Main execute (sync)
    # ------------------------------------------------------------------

    def execute_sync(self, prompt: str, task_id: str = None) -> dict:
        """Execute a task with the local crew (sync).

        Same flow as async execute() — supports both parallel and
        sequential execution modes decided by the ANRouter.
        """
        import time as _wf_time
        _wf_start = _wf_time.time()
        router = getattr(self, "_an_router", None)

        # Sandbox file tools to the task output workspace
        if task_id:
            self._bind_task_output_dir(task_id)

        try:
            return self._execute_sync_with_context(prompt, task_id, _wf_start, router)
        finally:
            if task_id:
                try:
                    from teaming24.runtime.task_context import clear_task_context
                    clear_task_context()
                except ImportError:
                    pass
                try:
                    import asyncio
                    from teaming24.runtime.manager import get_runtime_manager
                    asyncio.run(get_runtime_manager().release_sandbox(task_id))
                except Exception:
                    pass

    def _execute_sync_with_context(self, prompt: str, task_id: str, _wf_start: float, router) -> dict:
        """Execute sync body (task context is set; cleared by caller's finally)."""
        # Fire plugin hook: before_task_execute (sync)
        try:
            from teaming24.plugins.hooks import get_hook_registry
            get_hook_registry().fire_sync("before_task_execute", task_id=task_id, prompt=prompt)
        except Exception as _hook_exc:
            logger.debug(f"[Hook] before_task_execute failed (ignored): {_hook_exc}")

        # Phase: RECEIVED
        self._wf_step(task_id, "Organizer", "receive",
                      f"Task received: {prompt[:100]}...",
                      agent_type="organizer",
                      phase=TaskPhase.RECEIVED,
                      phase_label="Task received by Organizer",
                      percentage=5)

        try:
            return self._execute_sync_body(prompt, task_id, _wf_start, router)
        except Exception as _fatal:
            logger.error("[LocalCrew] execute_sync unhandled: task=%s err=%s", task_id, _fatal, exc_info=True)
            if task_id and self.task_manager:
                self.task_manager.fail_task(task_id, str(_fatal))
            return {"status": "error", "task_id": task_id, "result": "", "error": str(_fatal)}

    def _execute_sync_body(self, prompt: str, task_id: str, _wf_start: float, router) -> dict:
        """Inner body of execute_sync; extracted for top-level exception guard."""
        import time as _wf_time

        # --- Step 1: Organizer asks ANRouter for routing decision ---
        remote_results: list[dict] = []
        local_result: dict = {
            "status": "skipped", "task_id": task_id,
            "result": "", "cost": {}, "duration": 0,
        }
        plan = None
        local_prompt = prompt

        if router:
            # Phase: ROUTING
            self._wf_step(task_id, "Organizer", "route",
                          "Consulting ANRouter for workforce allocation...",
                          agent_type="organizer",
                          phase=TaskPhase.ROUTING,
                          phase_label="ANRouter routing (LLM may take 1–2 min)",
                          percentage=10)
            plan = router.route(prompt)
            local_prompt = plan.local_prompt or prompt
            self._log_routing_plan(task_id, plan, local_prompt)

            # --- Human approval for routing (if remote ANs selected) ---
            plan = self._request_routing_approval(task_id, plan)
            if plan is None:
                result = {"status": "cancelled", "result": "Task cancelled by user."}
                if task_id and self.task_manager:
                    self.task_manager.fail_task(task_id, "Cancelled by user")
                return result

        # --- Step 2+3: Dispatch → Synthesize (with self-improvement rounds) ---
        mode = getattr(plan, "execution_mode", "parallel") if plan else None

        # Initial dispatch setup (once, before rounds)
        if plan:
            self._wf_step(task_id, "Organizer", "dispatch",
                          f"Dispatching to selected coordinators ({mode} mode)...",
                          agent_type="organizer",
                          phase=TaskPhase.DISPATCHING,
                          phase_label=f"Dispatching to coordinators ({mode})",
                          percentage=20)
            if task_id and self.task_manager:
                local_count = 1 if plan.has_local else 0
                remote_count = len(plan.remote_subtasks)
                self.task_manager.update_progress(
                    task_id, total_workers=local_count + remote_count,
                )
        else:
            self._wf_step(task_id, "local team coordinator", "local_start",
                          "Starting local Coordinator execution...",
                          agent_type="coordinator",
                          phase=TaskPhase.EXECUTING,
                          phase_label="Coordinator assigning to workers",
                          percentage=25)
            if task_id and self.task_manager:
                self.task_manager.update_progress(task_id, total_workers=1)

        # current_local_prompt evolves each round (original → refined with feedback)
        current_local_prompt = local_prompt
        max_rounds = self._resolve_max_rounds_for_prompt(prompt)
        quality_gate_passed = False
        quality_gate_feedback = ""
        policy_for_rounds = self._get_quality_policy(prompt)
        best_round_result: dict[str, Any] | None = None
        best_round_score = -1.0
        prev_round_signature = ""
        stagnation_count = 0

        for round_num in range(1, max_rounds + 1):
            if round_num > 1:
                self._wf_step(
                    task_id, "Organizer", "round_start",
                    f"Refinement round {round_num}/{max_rounds} — applying feedback...",
                    agent_type="organizer",
                    phase=TaskPhase.DISPATCHING,
                    phase_label=f"Refinement round {round_num}/{max_rounds}",
                    percentage=20,
                )
                logger.info(f"[Organizer] Starting refinement round {round_num}/{max_rounds}")

            # Dispatch
            if plan:
                if mode == "sequential":
                    round_ctx = current_local_prompt if round_num > 1 else ""
                    local_result, remote_results = self._dispatch_sequential(
                        plan, current_local_prompt, task_id,
                        round_context=round_ctx, round_num=round_num,
                    )
                else:
                    local_result, remote_results = self._dispatch_parallel(
                        plan, current_local_prompt, task_id,
                        round_num=round_num,
                    )
            else:
                local_result = self._run_local_subtask_sync(
                    current_local_prompt, task_id, False
                )
                if task_id and self.task_manager:
                    self.task_manager.update_progress(task_id, completed_workers=1)
                self._wf_step(
                    task_id, "local team coordinator", "local_done",
                    f"Local execution completed: status={local_result.get('status', '?')}",
                    agent_type="coordinator",
                )
                remote_results = []

            # Synthesize
            self._wf_step(
                task_id, "Organizer", "aggregate",
                f"Synthesizing answer (round {round_num}/{max_rounds})...",
                agent_type="organizer",
                phase=TaskPhase.AGGREGATING,
                phase_label=f"Synthesizing answer (round {round_num})",
                percentage=85,
            )
            result = self._aggregate_results(
                local_result, remote_results, prompt=prompt, task_id=task_id
            )
            result_text_for_round = (
                result.get("result", "") if isinstance(result, dict) else str(result)
            )
            round_signature = self._normalize_refinement_signature(result_text_for_round)
            prev_best_score = best_round_score
            round_score = self._compute_round_quality_score(
                result_text=result_text_for_round,
                prompt=prompt,
                task_id=task_id,
                policy=policy_for_rounds,
            )
            if round_score > best_round_score + 0.01:
                best_round_score = round_score
                if isinstance(result, dict):
                    best_round_result = dict(result)
                else:
                    best_round_result = {"status": "success", "result": str(result)}
            improved = round_score > prev_best_score + 0.01
            if round_num > 1 and (round_signature == prev_round_signature or not improved):
                stagnation_count += 1
            else:
                stagnation_count = 0
            prev_round_signature = round_signature

            # Evaluate on every round (including the final round)
            self._wf_step(
                task_id, "Organizer", "eval_round",
                f"Evaluating result quality (round {round_num}/{max_rounds})...",
                agent_type="organizer",
                percentage=90,
            )
            satisfied, feedback = self._evaluate_result(
                prompt, result, round_num, task_id=task_id
            )
            quality_gate_passed = satisfied
            quality_gate_feedback = feedback or ""
            if satisfied:
                logger.info(
                    f"[Organizer] Round {round_num}/{max_rounds}: result accepted"
                )
                break
            if round_num < max_rounds:
                if (
                    stagnation_count >= 1
                    and best_round_result is not None
                    and best_round_score >= 0.45
                ):
                    quality_gate_passed = True
                    result = best_round_result
                    msg = (
                        f"Refinement converged after round {round_num}/{max_rounds}; "
                        f"using best available result (score={best_round_score:.2f})"
                    )
                    self._wf_step(
                        task_id,
                        "Organizer",
                        "refine_converged",
                        msg,
                        agent_type="organizer",
                    )
                    logger.info("[Organizer] %s", msg)
                    break
                logger.info(
                    f"[Organizer] Round {round_num}/{max_rounds}: not satisfied "
                    f"— starting round {round_num + 1}"
                )
                current_local_prompt = self._build_round_prompt(
                    prompt, result, feedback, round_num
                )
            else:
                logger.warning(
                    "[Organizer] Final round %s/%s did not satisfy quality gate: %s",
                    round_num,
                    max_rounds,
                    (feedback or "unspecified feedback")[:300],
                )

        if not quality_gate_passed:
            quality_msg = (
                f"Quality gate failed after {max_rounds} round(s): "
                f"{quality_gate_feedback or 'result did not meet completion criteria'}"
            )
            self._wf_step(
                task_id, "Organizer", "quality_gate_failed",
                quality_msg,
                agent_type="organizer",
            )
            logger.error("[Organizer] %s", quality_msg)
            if isinstance(result, dict):
                result["status"] = "error"
                result["error"] = quality_msg
                result["quality_gate"] = {
                    "passed": False,
                    "feedback": quality_gate_feedback,
                    "rounds": max_rounds,
                }

        # --- Step 4: Save all outputs ---
        self._save_all_outputs(task_id, prompt, result, remote_results)

        # --- Emit AN status summary for user tracking in thinking/steps ---
        self._emit_an_status_summary(task_id, local_result, remote_results)

        elapsed = _wf_time.time() - _wf_start
        self._wf_step(
            task_id, "Organizer", "complete",
            f"Task finished: status={result.get('status', '?')}, duration={elapsed:.1f}s",
            agent_type="organizer",
            phase=TaskPhase.COMPLETED,
            phase_label="Completed",
            percentage=100,
        )

        # Mark task as completed/failed in TaskManager
        if task_id and self.task_manager:
            result_str = result.get("result", "") if isinstance(result, dict) else str(result)
            if result.get("status") == "error":
                self.task_manager.fail_task(task_id, result.get("error", result_str))
            else:
                self.task_manager.complete_task(task_id, result_str)

        # Fire plugin hook: after_task_execute (sync)
        try:
            from teaming24.plugins.hooks import get_hook_registry
            result_text = result.get("result", "") if isinstance(result, dict) else str(result)
            get_hook_registry().fire_sync("after_task_execute", task_id=task_id, result=result_text)
        except Exception as _hook_exc:
            logger.debug(f"[Hook] after_task_execute failed (ignored): {_hook_exc}")

        return result

    # ------------------------------------------------------------------
    # Coordinator-only execution (for remote tasks received from other ANs)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_ml_prompt(prompt: str) -> bool:
        raw_prompt = str(prompt or "")
        lower = raw_prompt.lower()
        ml_keywords = (
            "machine learning",
            "ml model",
            "train model",
            "training",
            "fine-tune",
            "finetune",
            "inference",
            "classification",
            "regression",
            "deep learning",
            "pytorch",
            "tensorflow",
            "xgboost",
            "lightgbm",
            "random forest",
        )
        return any(k in lower for k in ml_keywords)

    @classmethod
    def _enrich_ml_prompt_for_local_execution(cls, prompt: str) -> str:
        """For ML tasks, enforce real local model execution and reproducible outputs."""
        raw_prompt = str(prompt or "")
        if not cls._is_ml_prompt(raw_prompt):
            return raw_prompt

        directive = (
            "\n\n[ML_EXECUTION_POLICY]\n"
            "- Use LOCAL compute resources on this AN and run a REAL model.\n"
            "- Choose approach yourself: pretrained model OR train/fine-tune from scratch based on task/data.\n"
            "- Execute real training/inference code; do not return only conceptual guidance.\n"
            "- Return concrete artifacts: model/checkpoint path, metrics, data split details, and runnable commands.\n"
            "- If full-scale training is constrained, run a smaller but real experiment and report constraints clearly.\n"
        )
        return raw_prompt + directive

    def _run_local_ml_experiment(self, prompt: str, task_id: str | None) -> str:
        """Run a real local ML experiment and return a short execution summary."""
        import json as _json
        import os
        import pickle
        import random
        from pathlib import Path

        if not self._is_ml_prompt(prompt):
            return ""

        try:
            base_dir = os.path.expanduser(get_config().output.base_dir)
        except Exception as cfg_exc:
            logger.warning(
                "Failed to read output base dir for ML experiment, using default: %s",
                cfg_exc,
                exc_info=True,
            )
            base_dir = os.path.expanduser("~/.teaming24/outputs")

        run_task_id = task_id or "adhoc_ml_task"
        out_dir = Path(base_dir) / run_task_id / "ml_artifacts"
        out_dir.mkdir(parents=True, exist_ok=True)

        csv_path = None
        try:
            candidates = re.findall(r'([~./\w-]+\.csv)', str(prompt or ""))
            for cand in candidates:
                p = Path(os.path.expanduser(cand))
                if not p.is_absolute():
                    p = Path(os.getcwd()) / p
                if p.exists() and p.is_file():
                    csv_path = p
                    break
        except Exception as path_exc:
            logger.warning("Failed parsing CSV path from ML prompt: %s", path_exc, exc_info=True)

        lower = str(prompt or "").lower()
        is_classification = any(k in lower for k in ("classif", "label", "accuracy", "roc", "f1"))

        try:
            from sklearn.datasets import load_breast_cancer, load_diabetes  # type: ignore
            from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor  # type: ignore
            from sklearn.linear_model import LinearRegression, LogisticRegression  # type: ignore
            from sklearn.metrics import accuracy_score, f1_score, mean_absolute_error, r2_score  # type: ignore
            from sklearn.model_selection import train_test_split  # type: ignore

            X = None
            y = None
            dataset_name = ""
            target_name = ""

            if csv_path is not None:
                try:
                    import pandas as pd  # type: ignore

                    df = pd.read_csv(csv_path)
                    if df.shape[1] < 2:
                        raise ValueError(f"CSV needs >=2 columns, got {df.shape[1]}")
                    target_name = "target" if "target" in df.columns else str(df.columns[-1])
                    y = df[target_name]
                    X = df.drop(columns=[target_name])
                    X = pd.get_dummies(X, drop_first=False)
                    dataset_name = f"csv:{csv_path}"
                except Exception as csv_exc:
                    logger.warning(
                        "CSV ML load failed for %s, fallback to builtin dataset: %s",
                        csv_path,
                        csv_exc,
                        exc_info=True,
                    )

            if X is None or y is None:
                if is_classification:
                    ds = load_breast_cancer()
                    X, y = ds.data, ds.target
                    dataset_name = "sklearn:breast_cancer"
                    target_name = "target"
                else:
                    ds = load_diabetes()
                    X, y = ds.data, ds.target
                    dataset_name = "sklearn:diabetes"
                    target_name = "target"

            X_train, X_test, y_train, y_test = train_test_split(
                X, y, test_size=0.2, random_state=42
            )

            if is_classification:
                try:
                    model = LogisticRegression(max_iter=1200)
                    model.fit(X_train, y_train)
                except Exception as lr_exc:
                    logger.warning(
                        "LogisticRegression failed, fallback RandomForestClassifier: %s",
                        lr_exc,
                        exc_info=True,
                    )
                    model = RandomForestClassifier(
                        n_estimators=200,
                        max_depth=12,
                        random_state=42,
                    )
                    model.fit(X_train, y_train)
                preds = model.predict(X_test)
                metric_primary = float(accuracy_score(y_test, preds))
                metric_secondary = float(f1_score(y_test, preds, average="weighted"))
                metrics = {
                    "accuracy": round(metric_primary, 6),
                    "f1_weighted": round(metric_secondary, 6),
                }
            else:
                try:
                    model = LinearRegression()
                    model.fit(X_train, y_train)
                except Exception as lin_exc:
                    logger.warning(
                        "LinearRegression failed, fallback RandomForestRegressor: %s",
                        lin_exc,
                        exc_info=True,
                    )
                    model = RandomForestRegressor(
                        n_estimators=220,
                        max_depth=16,
                        random_state=42,
                    )
                    model.fit(X_train, y_train)
                preds = model.predict(X_test)
                metrics = {
                    "r2": round(float(r2_score(y_test, preds)), 6),
                    "mae": round(float(mean_absolute_error(y_test, preds)), 6),
                }

            model_path = out_dir / ("model_classification.pkl" if is_classification else "model_regression.pkl")
            metrics_path = out_dir / "metrics.json"
            with open(model_path, "wb") as f:
                pickle.dump(model, f)
            with open(metrics_path, "w", encoding="utf-8") as f:
                _json.dump({
                    "dataset": dataset_name,
                    "target": target_name,
                    "is_classification": is_classification,
                    "train_size": int(len(X_train)),
                    "test_size": int(len(X_test)),
                    "metrics": metrics,
                }, f, ensure_ascii=True, indent=2)

            return (
                f"[ML Local Execution] model={model.__class__.__name__}, "
                f"dataset={dataset_name}, metrics={metrics}, "
                f"artifact={model_path}"
            )
        except Exception as sk_exc:
            logger.warning(
                "Sklearn ML experiment failed, fallback to pure-python regression: %s",
                sk_exc,
                exc_info=True,
            )

        # Pure-python fallback: linear regression on synthetic data
        try:
            xs = [float(i) for i in range(1, 81)]
            ys = [3.2 * x + 5.0 + random.uniform(-1.0, 1.0) for x in xs]
            n = len(xs)
            mean_x = sum(xs) / n
            mean_y = sum(ys) / n
            den = sum((x - mean_x) ** 2 for x in xs) or 1e-8
            num = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
            slope = num / den
            intercept = mean_y - slope * mean_x
            preds = [slope * x + intercept for x in xs]
            mse = sum((y - p) ** 2 for y, p in zip(ys, preds)) / n

            coeff_path = out_dir / "linear_coefficients.json"
            with open(coeff_path, "w", encoding="utf-8") as f:
                _json.dump({
                    "model": "pure_python_linear_regression",
                    "slope": slope,
                    "intercept": intercept,
                    "mse": mse,
                    "n_samples": n,
                }, f, ensure_ascii=True, indent=2)

            return (
                "[ML Local Execution] model=pure_python_linear_regression, "
                f"mse={round(mse, 6)}, artifact={coeff_path}"
            )
        except Exception as py_exc:
            logger.error("Pure-python ML fallback failed: %s", py_exc, exc_info=True)
            return "[ML Local Execution] failed: local ML runtime error (see backend logs)"

    async def execute_as_coordinator(self, prompt: str, task_id: str = None) -> dict:
        """Execute a task using only the Coordinator + Workers.

        This is the entry point for **remote tasks** received from another
        Agentic Node.  The Organizer and ANRouter are bypassed entirely —
        the Coordinator acts as manager and delegates to local Workers.

        Flow:
          Remote AN ─HTTP→ /api/agent/execute ─→ Coordinator → Workers
        """
        import time as _time
        _start = _time.time()

        # Sandbox file tools to the task output workspace
        if task_id:
            self._bind_task_output_dir(task_id)

        def _fail_and_return(err_msg: str) -> dict:
            """Mark the task as failed in TaskManager and return an error dict."""
            if task_id and self.task_manager:
                self.task_manager.fail_task(task_id, err_msg)
            return {"status": "error", "error": err_msg, "duration": 0}

        coordinator = self.coordinator
        if not coordinator:
            logger.error("[REMOTE-EXEC] No coordinator agent — cannot execute remote task")
            return _fail_and_return("No coordinator agent available")

        online_workers = self.get_online_workers()
        coord_agents = [coordinator] + online_workers
        worker_roles = [getattr(a, 'role', '?') for a in online_workers]
        effective_prompt = self._enrich_ml_prompt_for_local_execution(prompt)
        prompt_preview = effective_prompt[:120].replace('\n', ' ').strip()

        logger.info(
            "[REMOTE-EXEC] ═══════════════════════════════════════════════════"
        )
        logger.info(
            "[REMOTE-EXEC] Received remote task → Coordinator-only path"
        )
        logger.info(f"[REMOTE-EXEC]   task_id   : {task_id}")
        logger.info(f"[REMOTE-EXEC]   prompt    : {prompt_preview}...")
        logger.info(f"[REMOTE-EXEC]   coordinator: {getattr(coordinator, 'role', '?')}")
        logger.info(f"[REMOTE-EXEC]   workers ({len(online_workers)}): {worker_roles}")
        logger.info(f"[REMOTE-EXEC]   process   : {self.process}")
        logger.info(
            "[REMOTE-EXEC] ═══════════════════════════════════════════════════"
        )

        self._wf_step(
            task_id, "local team coordinator", "remote_receive",
            f"Received remote task → assigning to {len(online_workers)} workers: {worker_roles}",
            agent_type="coordinator",
            phase=TaskPhase.RECEIVED,
            phase_label="Remote task received by Coordinator",
            percentage=5,
        )

        if task_id and self.task_manager:
            self.task_manager.update_progress(
                task_id, total_workers=len(online_workers),
            )
        self._wf_step(
            task_id, "local team coordinator", "workers_selected",
            (
                f"Execution mode={self.process}. Selected workers: "
                f"{', '.join(worker_roles)}"
            ),
            agent_type="coordinator",
            phase=TaskPhase.EXECUTING,
            phase_label=f"Workers selected ({len(online_workers)})",
            percentage=25,
        )

        adapter = self._get_framework_adapter()
        result = None

        if adapter is not None:
            try:
                result = await self._run_via_adapter(effective_prompt, task_id, coord_agents)
            except Exception as e:
                if self._use_native:
                    logger.error(f"[REMOTE-EXEC] Native adapter failed: {e}")
                    return _fail_and_return(str(e))
                logger.error(f"[REMOTE-EXEC] Native adapter failed: {e}, falling back to CrewAI")
                result = None

        if result is None and self._use_native:
            logger.error("[REMOTE-EXEC] Native backend configured but adapter unavailable")
            return _fail_and_return("Native adapter unavailable")

        if result is None:
            logger.info(f"[REMOTE-EXEC] Using CrewWrapper with {len(coord_agents)} agents...")
            crew = CrewWrapper(
                agents=coord_agents,
                task_manager=self.task_manager,
                on_step=self.on_step,
                process=self.process,
                verbose=self.verbose,
                memory=self.memory,
                planning=self.planning,
                planning_llm=self.planning_llm,
                reasoning=self.reasoning,
                max_reasoning_attempts=self.max_reasoning_attempts,
                streaming=self.streaming,
            )
            result = await crew.execute(effective_prompt, task_id, _subtask=True)

        elapsed = _time.time() - _start
        status = result.get("status", "unknown")
        result_preview = str(result.get("result", ""))[:150].replace('\n', ' ')
        logger.info(
            "[REMOTE-EXEC] ───────────────────────────────────────────────────"
        )
        logger.info(
            f"[REMOTE-EXEC] Task completed: task_id={task_id}, "
            f"status={status}, duration={elapsed:.1f}s"
        )
        logger.info(f"[REMOTE-EXEC]   result preview: {result_preview}...")
        logger.info(
            "[REMOTE-EXEC] ───────────────────────────────────────────────────"
        )

        if self._is_ml_prompt(prompt):
            ml_summary = self._run_local_ml_experiment(prompt, task_id)
            if ml_summary:
                self._wf_step(
                    task_id, "local team coordinator", "ml_local_execution",
                    ml_summary,
                    agent_type="coordinator",
                )
                if isinstance(result, dict):
                    existing_result = str(result.get("result", "") or "").strip()
                    if ml_summary not in existing_result:
                        result["result"] = (
                            f"{existing_result}\n\n{ml_summary}".strip()
                            if existing_result else ml_summary
                        )

        self._wf_step(
            task_id, "local team coordinator", "remote_complete",
            f"Remote task done: status={status}, duration={elapsed:.1f}s, "
            f"workers={len(online_workers)}",
            agent_type="coordinator",
            phase=TaskPhase.COMPLETED,
            phase_label="Remote task completed",
            percentage=100,
        )

        self._save_all_outputs(task_id, effective_prompt, result)

        # Mark task as completed/failed in TaskManager so SSE subscribers
        # receive the final event (critical for remote AN result collection).
        if task_id and self.task_manager:
            result_str = result.get("result", "") if isinstance(result, dict) else str(result)
            if result.get("status") == "error":
                self.task_manager.fail_task(task_id, result.get("error", result_str))
            else:
                self.task_manager.complete_task(task_id, result_str)

        return result

    def execute_as_coordinator_sync(self, prompt: str, task_id: str = None) -> dict:
        """Synchronous version of :meth:`execute_as_coordinator`.

        Same Coordinator-only flow — see docstring above.
        """
        import time as _time
        _start = _time.time()

        def _fail_and_return(err_msg: str) -> dict:
            """Mark the task as failed in TaskManager and return an error dict."""
            if task_id and self.task_manager:
                self.task_manager.fail_task(task_id, err_msg)
            return {"status": "error", "error": err_msg, "duration": 0}

        if task_id:
            self._bind_task_output_dir(task_id)

        coordinator = self.coordinator
        if not coordinator:
            logger.error("[REMOTE-EXEC] No coordinator agent — cannot execute remote task (sync)")
            return _fail_and_return("No coordinator agent available")

        online_workers = self.get_online_workers()
        coord_agents = [coordinator] + online_workers
        worker_roles = [getattr(a, 'role', '?') for a in online_workers]
        effective_prompt = self._enrich_ml_prompt_for_local_execution(prompt)
        prompt_preview = effective_prompt[:120].replace('\n', ' ').strip()

        logger.info(
            "[REMOTE-EXEC] ═══════════════════════════════════════════════════"
        )
        logger.info("[REMOTE-EXEC] Received remote task → Coordinator-only path (sync)")
        logger.info(f"[REMOTE-EXEC]   task_id   : {task_id}")
        logger.info(f"[REMOTE-EXEC]   prompt    : {prompt_preview}...")
        logger.info(f"[REMOTE-EXEC]   coordinator: {getattr(coordinator, 'role', '?')}")
        logger.info(f"[REMOTE-EXEC]   workers ({len(online_workers)}): {worker_roles}")
        logger.info(
            "[REMOTE-EXEC] ═══════════════════════════════════════════════════"
        )

        self._wf_step(
            task_id, "local team coordinator", "remote_receive",
            f"Received remote task → assigning to {len(online_workers)} workers: {worker_roles}",
            agent_type="coordinator",
            phase=TaskPhase.RECEIVED,
            phase_label="Remote task received by Coordinator",
            percentage=5,
        )

        if task_id and self.task_manager:
            self.task_manager.update_progress(
                task_id, total_workers=len(online_workers),
            )
        self._wf_step(
            task_id, "local team coordinator", "workers_selected",
            (
                f"Execution mode={self.process}. Selected workers: "
                f"{', '.join(worker_roles)}"
            ),
            agent_type="coordinator",
            phase=TaskPhase.EXECUTING,
            phase_label=f"Workers selected ({len(online_workers)})",
            percentage=25,
        )

        adapter = self._get_framework_adapter()
        result = None

        if adapter is not None:
            try:
                try:
                    loop = asyncio.get_running_loop()
                except RuntimeError as e:
                    logger.debug("No running event loop (remote-exec): %s", e)
                    loop = None

                if loop is not None and loop.is_running():
                    import concurrent.futures
                    with concurrent.futures.ThreadPoolExecutor() as pool:
                        result = pool.submit(
                            asyncio.run,
                            self._run_via_adapter(effective_prompt, task_id, coord_agents),
                        ).result()
                else:
                    result = asyncio.run(
                        self._run_via_adapter(effective_prompt, task_id, coord_agents)
                    )
            except Exception as e:
                if self._use_native:
                    logger.error(f"[REMOTE-EXEC] Native adapter failed (sync): {e}")
                    return _fail_and_return(str(e))
                logger.error(f"[REMOTE-EXEC] Native adapter failed: {e}, falling back to CrewAI")
                result = None

        if result is None and self._use_native:
            logger.error("[REMOTE-EXEC] Native backend configured but adapter unavailable (sync)")
            return _fail_and_return("Native adapter unavailable")

        if result is None:
            crew = CrewWrapper(
                agents=coord_agents,
                task_manager=self.task_manager,
                on_step=self.on_step,
                process=self.process,
                verbose=self.verbose,
                memory=self.memory,
                planning=self.planning,
                planning_llm=self.planning_llm,
                reasoning=self.reasoning,
                max_reasoning_attempts=self.max_reasoning_attempts,
                streaming=self.streaming,
            )
            try:
                result = crew.execute_sync(effective_prompt, task_id, _subtask=True)
            except Exception as e:
                elapsed = _time.time() - _start
                logger.error(
                    f"[REMOTE-EXEC] Task failed (sync): task_id={task_id}, "
                    f"error={e}, duration={elapsed:.1f}s"
                )
                self._wf_step(
                    task_id, "local team coordinator", "remote_failed",
                    f"Remote task failed: {e}",
                    agent_type="coordinator",
                )
                return _fail_and_return(str(e))

        elapsed = _time.time() - _start
        logger.info(
            f"[REMOTE-EXEC] Task completed (sync): task_id={task_id}, "
            f"status={result.get('status', '?')}, duration={elapsed:.1f}s"
        )

        if self._is_ml_prompt(prompt):
            ml_summary = self._run_local_ml_experiment(prompt, task_id)
            if ml_summary:
                self._wf_step(
                    task_id, "local team coordinator", "ml_local_execution",
                    ml_summary,
                    agent_type="coordinator",
                )
                if isinstance(result, dict):
                    existing_result = str(result.get("result", "") or "").strip()
                    if ml_summary not in existing_result:
                        result["result"] = (
                            f"{existing_result}\n\n{ml_summary}".strip()
                            if existing_result else ml_summary
                        )

        self._wf_step(
            task_id, "local team coordinator", "remote_complete",
            f"Remote task done: status={result.get('status', '?')}, "
            f"duration={elapsed:.1f}s, workers={len(self.workers)}",
            agent_type="coordinator",
            phase=TaskPhase.COMPLETED,
            phase_label="Remote task completed",
            percentage=100,
        )

        self._save_all_outputs(task_id, effective_prompt, result)

        # Mark task as completed/failed in TaskManager so SSE subscribers
        # receive the final event (critical for remote AN result collection).
        if task_id and self.task_manager:
            result_str = result.get("result", "") if isinstance(result, dict) else str(result)
            if result.get("status") == "error":
                self.task_manager.fail_task(task_id, result.get("error", result_str))
            else:
                self.task_manager.complete_task(task_id, result_str)

        return result

    # ------------------------------------------------------------------
    # Remote subtask dispatch (Organizer → remote ANs via HTTP)
    # ------------------------------------------------------------------

    def _execute_remote_subtasks(
        self, plan: Any, task_id: str = None, *, is_retry: bool = False
    ) -> list[dict]:
        """Organizer dispatches remote subtasks from the confirmed
        routing plan to selected Remote ANs via HTTP.

        When is_retry=True (refinement round 2+), payment amount is 0 —
        retries belong to the same main task and do not incur additional charge.

        Called after the Organizer receives the RoutingPlan from the
        ANRouter.  This does NOT go through CrewAI.

        Uses the **async submit + SSE subscribe** pattern:

        1. POST ``/api/agent/execute`` with ``async_mode=true``
           → remote AN returns immediately with ``task_id``
        2. Subscribe to ``GET /api/agent/tasks/{task_id}/subscribe``
           → real-time SSE stream of status updates
        3. When ``final: true`` arrives, task is complete → collect result
        4. Fallback: if SSE fails, falls back to polling
        """


        # ---- Configuration (from YAML: an_router.*) ----
        _rc = self.config.an_router if self.config else None
        SUBMIT_TIMEOUT = _rc.remote_submit_timeout if _rc else 30.0
        SSE_TIMEOUT = _rc.remote_sse_timeout if _rc else 600.0
        POLL_INTERVAL = _rc.remote_poll_interval if _rc else 5.0
        POLL_TIMEOUT = _rc.remote_poll_timeout if _rc else 600.0

        results = []
        pool = getattr(self, "_workforce_pool", None)
        if not pool:
            logger.warning("[ANRouter] No pool bound — skipping remote subtasks")
            return results

        # Build id → entry lookup from current pool snapshot
        entries = pool.get_pool()
        entry_by_id = {e.id: e for e in entries}

        # Resolve unique node UID for delegation chain (hostname+MAC+port hash).
        # This is globally unique even when config.local_node.name or host:port
        # are identical across machines.
        from teaming24.utils.ids import get_node_uid
        local_node_uid = get_node_uid()

        # Build delegation chain: append this node's UID to prevent loops.
        existing_chain: list[str] = getattr(self, "_delegation_chain", [])
        chain = existing_chain + [local_node_uid]

        # ================================================================
        # Phase 1: Submit ALL remote tasks concurrently
        # ================================================================
        import concurrent.futures as _cf

        remote_subtasks = plan.remote_subtasks
        logger.info(
            f"[ANRouter] Phase 1: submitting {len(remote_subtasks)} remote subtask(s) concurrently"
        )
        for i, st in enumerate(remote_subtasks, 1):
            logger.info(
                f"[ANRouter]   [{i}] assigned_to={st.assigned_to}, "
                f"node_id={st.target_node_id}, desc={st.description[:60]}..."
            )

        # Prepare submission info — track already-submitted an_ids
        submit_items: list[dict] = []
        _submitted_an_ids: set = set()
        for subtask in remote_subtasks:
            if subtask.target_node_id in _submitted_an_ids:
                logger.warning(
                    f"[ANRouter] Skipping duplicate submission for "
                    f"an_id={subtask.target_node_id} ({subtask.assigned_to})"
                )
                continue
            _submitted_an_ids.add(subtask.target_node_id)

            entry = entry_by_id.get(subtask.target_node_id)
            if not entry or not getattr(entry, "node_info", None):
                logger.error(
                    f"[ANRouter] Cannot dispatch to {subtask.assigned_to}: "
                    f"no pool entry or node_info for id={subtask.target_node_id}. "
                    f"Available entry IDs: {list(entry_by_id.keys())}"
                )
                self._emit_remote_status_event(
                    subtask,
                    action="remote_failed",
                    stage="failed",
                    transport="router",
                    content=(
                        f"❌ Cannot dispatch to {subtask.assigned_to}: "
                        f"missing node routing info"
                    ),
                    main_task_id=task_id,
                    remote_status="unresolved",
                    status_event="routing_missing_node",
                    percentage=100,
                    phase="completed",
                    phase_label="Remote routing failed",
                    progress={},
                    error_text=f"No node_info for pool entry {subtask.target_node_id}",
                    is_final=True,
                )
                results.append({
                    "assigned_to": subtask.assigned_to,
                    "node_id": subtask.target_node_id,
                    "status": "error",
                    "error": f"No node_info for pool entry {subtask.target_node_id}",
                })
                continue

            node_info = entry.node_info
            ip = getattr(node_info, "ip", None) or "127.0.0.1"
            port = getattr(node_info, "port", None) or 8000
            base_url = f"http://{ip}:{port}"

            if task_id and self.task_manager:
                try:
                    self.task_manager.delegate_task(task_id, subtask.target_node_id)
                    self.task_manager.add_step(
                        task_id, agent="Organizer", action="delegate",
                        content=f"Delegating to {subtask.assigned_to} ({ip}:{port})",
                    )
                except Exception as e:
                    logger.warning(
                        f"[ANRouter] Failed to track delegation for "
                        f"{subtask.assigned_to} ({ip}:{port}): {e}"
                    )

            submit_items.append({
                "base_url": base_url,
                "subtask": subtask,
                "ip": ip,
                "port": port,
            })

        if not submit_items:
            logger.warning("[ANRouter] No valid submit items after resolution — returning")
            return results

        # Read payment config once for all per-AN payment steps
        # Retries (refinement round 2+) are free — same main task, no extra charge.
        try:
            from teaming24.config import get_config as _get_cfg
            _pay_cfg = _get_cfg().payment
            _pay_amt = "0" if is_retry else str(_pay_cfg.task_price)
            _pay_mode = _pay_cfg.mode
            _pay_net = "base-sepolia" if _pay_mode != "mock" else "mock"
        except Exception as _pay_exc:
            logger.debug(f"[Payment] Could not read payment config, using mock defaults: {_pay_exc}")
            _pay_amt = "0" if is_retry else "0.001"
            _pay_mode = "mock"
            _pay_net = "mock"

        try:
            _pay_num = float(_pay_amt) if _pay_amt else 0.0
        except (TypeError, ValueError):
            _pay_num = 0.0

        # Emit "payment_processing" for EVERY selected AN before submission
        if self.on_step:
            for item in submit_items:
                st = item["subtask"]
                content = (
                    f"🔄 Retry (no charge) — re-dispatching to {st.assigned_to} "
                    f"({item['ip']}:{item['port']}) — same main task"
                    if is_retry else
                    f"💳 x402 Payment — sending {_pay_amt} ETH to "
                    f"{st.assigned_to} ({item['ip']}:{item['port']}) (mode: {_pay_mode})"
                )
                try:
                    self.on_step({
                        "agent": "Organizer",
                        "agent_type": "organizer",
                        "action": "payment_processing",
                        "content": content,
                        "is_delegation": True,
                        "payment": {
                            "mode": _pay_mode, "amount": _pay_amt,
                            "network": _pay_net,
                            "is_retry": is_retry,
                        },
                    })
                except Exception as pe:
                    logger.debug(f"Payment processing step callback failed: {pe}")

                self._emit_remote_status_event(
                    st,
                    stage="submitting",
                    transport="http_submit",
                    content=(
                        f"📡 Preparing remote dispatch to {st.assigned_to} "
                        f"({item['ip']}:{item['port']})"
                    ),
                    main_task_id=task_id,
                    remote_status="submitting",
                    status_event="submit_started",
                    percentage=0,
                    phase_label="Submitting to remote node",
                    phase="dispatching",
                )

        # Submit ALL remote tasks concurrently using a thread pool
        pending: list[dict] = []

        def _do_submit(item: dict) -> dict:
            """Submit a single remote task (runs in thread)."""
            st = item["subtask"]
            tid = self._submit_remote_task(
                item["base_url"], st, local_node_uid, SUBMIT_TIMEOUT,
                item["ip"], item["port"], delegation_chain=chain,
                is_retry=is_retry, parent_task_id=task_id,
            )
            return {**item, "remote_task_id": tid}

        with _cf.ThreadPoolExecutor(max_workers=min(len(submit_items), 10)) as executor:
            futures = {executor.submit(_do_submit, it): it for it in submit_items}
            for future in _cf.as_completed(futures):
                item = futures[future]
                st = item["subtask"]
                try:
                    result_item = future.result()
                    tid = result_item["remote_task_id"]
                except Exception as e:
                    logger.error(
                        f"[ANRouter] Submit thread failed for "
                        f"{st.assigned_to}: {e}", exc_info=True,
                    )
                    self._emit_remote_status_event(
                        st,
                        action="remote_failed",
                        stage="failed",
                        transport="http_submit",
                        content=f"❌ Remote submit failed for {st.assigned_to}: {str(e)[:180]}",
                        main_task_id=task_id,
                        remote_status="submit_failed",
                        status_event="submit_exception",
                        percentage=100,
                        phase="completed",
                        phase_label="Remote submit failed",
                        progress={},
                        error_text=str(e),
                        is_final=True,
                    )
                    results.append({
                        "assigned_to": st.assigned_to,
                        "node_id": st.target_node_id,
                        "status": "error",
                        "error": f"Submit thread exception: {e}",
                    })
                    continue

                if tid is None:
                    logger.warning(
                        f"[ANRouter] Submit returned None for {st.assigned_to} "
                        f"({item['ip']}:{item['port']}) — task not sent"
                    )
                    self._emit_remote_status_event(
                        st,
                        action="remote_failed",
                        stage="failed",
                        transport="http_submit",
                        content=f"❌ {st.assigned_to} rejected or failed remote submit",
                        main_task_id=task_id,
                        remote_status="submit_failed",
                        status_event="submit_rejected",
                        percentage=100,
                        phase="completed",
                        phase_label="Remote submit rejected",
                        progress={},
                        error_text="Submit failed (see logs)",
                        is_final=True,
                    )
                    results.append({
                        "assigned_to": st.assigned_to,
                        "node_id": st.target_node_id,
                        "status": "error",
                        "error": "Submit failed (see logs)",
                    })
                    continue

                if tid == "":
                    logger.warning(
                        f"[ANRouter] Submit returned empty task_id for {st.assigned_to} "
                        f"({item['ip']}:{item['port']}) — remote AN may have executed synchronously"
                    )
                    msg = "Remote node returned no task_id for async execution"
                    self._emit_remote_status_event(
                        st,
                        action="remote_failed",
                        stage="failed",
                        transport="http_submit",
                        content=f"❌ {st.assigned_to} did not return a remote task ID",
                        main_task_id=task_id,
                        remote_status="submit_invalid",
                        status_event="submit_missing_task_id",
                        percentage=100,
                        phase="completed",
                        phase_label="Remote submit missing task ID",
                        progress={},
                        error_text=msg,
                        is_final=True,
                    )
                    results.append({
                        "assigned_to": st.assigned_to,
                        "node_id": st.target_node_id,
                        "status": "error",
                        "error": msg,
                    })
                    continue

                logger.info(
                    f"[ANRouter] Submitted OK: {st.assigned_to} "
                    f"({item['ip']}:{item['port']}) → remote_task_id={tid}"
                )
                pending.append({
                    "base_url": result_item["base_url"],
                    "remote_task_id": tid,
                    "subtask": st,
                    "ip": result_item["ip"],
                    "port": result_item["port"],
                })

                self._emit_remote_status_event(
                    st,
                    stage="submitted",
                    transport="http_submit",
                    content=(
                        f"📡 Remote task accepted by {st.assigned_to} "
                        f"[{tid}]"
                    ),
                    remote_task_id=tid,
                    main_task_id=task_id,
                    remote_status="submitted",
                    status_event="submit_accepted",
                    percentage=8,
                    phase_label="Remote node accepted task",
                    phase="dispatching",
                )

                # Payment approved + sent for each successfully submitted AN
                if self.on_step:
                    try:
                        approved_content = (
                            f"✅ Retry dispatch to {st.assigned_to} — no charge (same main task)"
                            if is_retry else
                            f"✅ Payment approved to {st.assigned_to} — {_pay_amt} ETH"
                        )
                        sent_content = (
                            f"🔄 Dispatched (retry) to {st.assigned_to} ({item['ip']}:{item['port']})"
                            if is_retry else
                            f"💰 Payment sent to {st.assigned_to} ({item['ip']}:{item['port']})"
                        )
                        self.on_step({
                            "agent": "Organizer",
                            "agent_type": "organizer",
                            "action": "payment_approved",
                            "content": approved_content,
                            "is_delegation": True,
                            "payment": {
                                "mode": _pay_mode, "amount": _pay_amt,
                                "status": "approved",
                                "is_retry": is_retry,
                            },
                        })
                        self.on_step({
                            "agent": "Organizer",
                            "agent_type": "organizer",
                            "action": "payment_sent",
                            "content": sent_content,
                            "is_delegation": True,
                            "payment_info": {
                                "target_an": st.assigned_to,
                                "target_node_id": st.target_node_id,
                                "ip": item["ip"],
                                "port": item["port"],
                                "remote_task_id": tid,
                                "is_retry": is_retry,
                                "amount": _pay_amt,
                                "amount_num": _pay_num,
                            },
                        })
                    except Exception as pe:
                        logger.debug(f"Payment step callback failed: {pe}")

        logger.info(
            f"[ANRouter] Phase 1 complete: {len(pending)} submitted OK, "
            f"{len(results)} failed/skipped, out of {len(remote_subtasks)} total"
        )
        if not pending:
            return results

        # ================================================================
        # Phase 2: Wait for ALL submitted tasks concurrently
        # ================================================================
        logger.info(
            f"[ANRouter] All {len(pending)} remote task(s) submitted — "
            f"waiting for results concurrently"
        )

        def _wait_for_remote(info: dict) -> dict:
            """Subscribe/poll a single remote task until completion."""
            self._emit_remote_status_event(
                info["subtask"],
                stage="subscribing",
                transport="sse",
                content=(
                    f"📡 Opening live stream to {info['subtask'].assigned_to} "
                    f"[{info['remote_task_id']}]"
                ),
                remote_task_id=info["remote_task_id"],
                main_task_id=task_id,
                remote_status="subscribing",
                status_event="sse_connecting",
                percentage=12,
                phase_label="Connecting to remote stream",
                phase="executing",
            )
            result = self._subscribe_remote_task(
                info["base_url"], info["remote_task_id"],
                info["subtask"], info["ip"], info["port"], SSE_TIMEOUT,
                main_task_id=task_id,
            )
            if result is None:
                logger.info(
                    f"[ANRouter] SSE unavailable, falling back to polling "
                    f"for {info['remote_task_id']}"
                )
                self._emit_remote_status_event(
                    info["subtask"],
                    stage="polling",
                    transport="poll",
                    content=(
                        f"📡 SSE unavailable for {info['subtask'].assigned_to} "
                        f"[{info['remote_task_id']}] — switching to polling"
                    ),
                    remote_task_id=info["remote_task_id"],
                    main_task_id=task_id,
                    remote_status="polling",
                    status_event="poll_fallback",
                    percentage=15,
                    phase_label="Polling remote node",
                    phase="executing",
                )
                result = self._poll_remote_task(
                    info["base_url"], info["remote_task_id"],
                    info["subtask"], info["ip"], info["port"],
                    POLL_INTERVAL, POLL_TIMEOUT,
                    main_task_id=task_id,
                )
            return result

        with _cf.ThreadPoolExecutor(max_workers=len(pending)) as executor:
            futures = {
                executor.submit(_wait_for_remote, info): info
                for info in pending
            }
            for future in _cf.as_completed(futures):
                info = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                except Exception as e:
                    logger.error(
                        f"[ANRouter] Remote wait failed for "
                        f"{info['subtask'].assigned_to}: {e}",
                        exc_info=True,
                    )
                    results.append({
                        "assigned_to": info["subtask"].assigned_to,
                        "node_id": info["subtask"].target_node_id,
                        "status": "error",
                        "error": str(e),
                    })

        return results

    def _submit_remote_task(
        self, base_url: str, subtask: Any, requester_uid: str,
        timeout: float, ip: str, port: int,
        delegation_chain: list[str] = None,
        *,
        is_retry: bool = False,
        parent_task_id: str = None,
    ) -> str | None:
        """Submit a task to a remote AN with async_mode=true.

        When is_retry=True (refinement round), payment amount is 0 —
        retries belong to the same main task and do not incur extra charge.

        Returns:
            remote_task_id (str) on success, "" if sync result, None on failure.
        """
        import json as _json

        import httpx

        # Build x402 payment data — use 0 for retries (same main task, no charge)
        try:
            from teaming24.config import get_config as _get_cfg
            _pay_cfg = _get_cfg().payment
            _pay_amount = 0.0 if is_retry else float(_pay_cfg.task_price)
            _pay_mode = _pay_cfg.mode
            _pay_currency = _pay_cfg.token_symbol
        except Exception as e:
            logger.debug(f"Could not read payment config, using defaults: {e}")
            _pay_amount = 0.0 if is_retry else 0.001
            _pay_mode = "mock"
            _pay_currency = "ETH"

        remote_task_text = str(subtask.description or "").strip()
        if "[Relevant long-term memory]\n" in remote_task_text:
            stripped_remote_task_text = re.sub(
                r"\[Relevant long-term memory\]\n.*?(?=\n\nUse the conversation context below to preserve continuity\.|\n\n\[Conversation context\]\n|\n\n\[Current user request\]\n|$)",
                "",
                remote_task_text,
                count=1,
                flags=re.S,
            ).strip()
            if stripped_remote_task_text:
                remote_task_text = stripped_remote_task_text

        payload = {
            "task": remote_task_text,
            "requester_id": requester_uid,
            "async_mode": True,
            "payment": {
                "amount": _pay_amount,
                "currency": _pay_currency,
                "protocol": "x402",
                "mode": _pay_mode,
            },
            "delegation_chain": delegation_chain or [],
        }
        if parent_task_id:
            payload["parent_task_id"] = parent_task_id
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(
                    f"{base_url}/api/agent/execute",
                    json=payload,
                    headers={
                        "X-402-Payment": _json.dumps(payload["payment"]),
                        "Content-Type": "application/json",
                    },
                )
            if resp.status_code == 409:
                detail = resp.json().get("detail", "Loop detected")
                logger.warning(
                    f"[ANRouter] Remote AN rejected task (loop/depth): "
                    f"{subtask.assigned_to} → {detail}"
                )
                return None
            if resp.status_code != 200:
                logger.warning(
                    f"[ANRouter] Remote submit failed: "
                    f"{subtask.assigned_to} → HTTP {resp.status_code}"
                )
                return None

            data = resp.json()
            remote_task_id = data.get("task_id", "")
            logger.info(
                f"[ANRouter] Remote task submitted: "
                f"{subtask.assigned_to} ({ip}:{port}) → "
                f"remote_task_id={remote_task_id}"
            )
            return remote_task_id

        except Exception as e:
            logger.error(
                f"[ANRouter] Remote submit failed: "
                f"{subtask.assigned_to} ({ip}:{port}) → {e}",
                exc_info=True,
            )
            return None

    def _subscribe_remote_task(
        self, base_url: str, remote_task_id: str, subtask: Any,
        ip: str, port: int, timeout: float,
        *,
        main_task_id: str | None = None,
    ) -> dict | None:
        """Subscribe to a remote task via SSE.

        Opens an SSE stream to ``GET /api/agent/tasks/{id}/subscribe``
        and reads events until ``final: true`` is received.

        Returns a result dict, or None if SSE is not available.
        """
        import json as _json
        import time as _time

        import httpx

        _rc = self.config.an_router if self.config else None

        subscribe_url = f"{base_url}/api/agent/tasks/{remote_task_id}/subscribe"
        start = _time.time()
        last_progress_sig: tuple | None = None
        terminal_emitted = False

        logger.info(
            f"[ANRouter] Subscribing to remote task via SSE: "
            f"{subtask.assigned_to} ({ip}:{port}) → {remote_task_id}"
        )

        def _emit_remote_progress_step(
            status_state: str,
            status_event: str,
            progress: dict | None = None,
            *,
            is_final: bool = False,
            error_text: str = "",
        ) -> None:
            """Forward remote AN progress updates into local task timeline."""
            nonlocal last_progress_sig, terminal_emitted
            if not self.on_step:
                return

            progress = progress or {}
            phase = str(progress.get("phase", "") or "").strip()
            pct = progress.get("percentage", None)
            try:
                pct_num = int(pct) if pct is not None else None
            except (TypeError, ValueError):
                pct_num = None
            step_no = progress.get("current_step_number", None)
            sig = (status_state, phase, pct_num, step_no, bool(is_final), str(error_text or ""))
            if sig == last_progress_sig:
                return
            last_progress_sig = sig

            phase_label = str(progress.get("phase_label", "") or "").strip()
            base = (
                f"📡 {subtask.assigned_to} [{remote_task_id}] "
                f"state={status_state or 'running'}"
            )
            if status_event:
                base += f" ({status_event})"
            if phase_label:
                base += f" · {phase_label}"
            if pct_num is not None:
                base += f" · {pct_num}%"
            if is_final and error_text:
                base += f" · error={error_text[:180]}"

            action = "remote_progress"
            if is_final:
                action = "remote_failed" if error_text else "remote_completed"
                terminal_emitted = True
            stage = self._infer_remote_stage(
                status_state,
                status_event,
                is_final=bool(is_final),
                fallback="running",
                percentage=pct_num,
                phase_label=phase_label,
            )
            self._emit_remote_status_event(
                subtask,
                action=action,
                stage=stage,
                transport="sse",
                content=base,
                remote_task_id=remote_task_id,
                main_task_id=main_task_id,
                remote_status=status_state,
                status_event=status_event,
                percentage=pct_num,
                phase=phase or ("completed" if stage == "completed" else "executing"),
                phase_label=phase_label,
                progress=progress,
                error_text=error_text,
                is_final=is_final,
            )

        try:
            with httpx.Client(timeout=None) as client:
                with client.stream(
                    "GET", subscribe_url,
                    timeout=httpx.Timeout(
                        connect=_rc.remote_http_connect_timeout if _rc else 10.0,
                        read=timeout,
                        write=_rc.remote_http_write_timeout if _rc else 10.0,
                        pool=_rc.remote_http_pool_timeout if _rc else 10.0,
                    ),
                ) as response:
                    if response.status_code != 200:
                        logger.warning(
                            f"[ANRouter] SSE subscribe HTTP {response.status_code} "
                            f"for {remote_task_id} — will fall back to polling"
                        )
                        return None

                    final_result = ""
                    final_error = ""
                    final_status = "pending"

                    self._emit_remote_status_event(
                        subtask,
                        stage="subscribing",
                        transport="sse",
                        content=(
                            f"📡 Live stream connected to {subtask.assigned_to} "
                            f"[{remote_task_id}]"
                        ),
                        remote_task_id=remote_task_id,
                        main_task_id=main_task_id,
                        remote_status="subscribing",
                        status_event="sse_connected",
                        percentage=15,
                        phase_label="Live remote stream connected",
                        phase="executing",
                    )

                    for line in response.iter_lines():
                        # SSE format: "data: {...}"
                        if not line.startswith("data: "):
                            continue

                        try:
                            event = _json.loads(line[6:])
                        except _json.JSONDecodeError as e:
                            logger.debug("SSE line JSON decode failed: %s", e)
                            continue

                        event_type = event.get("type", "")
                        data = event.get("data", {})

                        if event_type == "task":
                            # Initial task state
                            status = data.get("status", "pending")
                            progress = data.get("progress") if isinstance(data, dict) else None
                            is_terminal = status in ("completed", "success", "failed", "error")
                            error_text = str(data.get("error", "") or "") if isinstance(data, dict) else ""
                            _emit_remote_progress_step(
                                status_state=status,
                                status_event="task_snapshot",
                                progress=progress if isinstance(progress, dict) else None,
                                is_final=is_terminal,
                                error_text=error_text,
                            )
                            if status in ("completed", "success"):
                                final_status = "success"
                                final_result = data.get("result", str(data))
                                break
                            elif status in ("failed", "error"):
                                final_status = "error"
                                final_error = data.get("error", "")
                                break

                        elif event_type == "status_update":
                            state_info = data.get("status", {}) if isinstance(data, dict) else {}
                            state = state_info.get("state", "")
                            state_event = state_info.get("event", "")
                            is_final = data.get("final", False)
                            progress = data.get("progress") if isinstance(data, dict) else None
                            _emit_remote_progress_step(
                                status_state=state,
                                status_event=state_event,
                                progress=progress if isinstance(progress, dict) else None,
                                is_final=bool(is_final),
                                error_text=str(data.get("error", "") or ""),
                            )

                            if is_final:
                                if state in ("completed", "success"):
                                    final_status = "success"
                                    final_result = data.get("result", "")
                                else:
                                    final_status = "error"
                                    final_error = data.get("error", state)
                                break
                            else:
                                # Progress update — log periodically
                                elapsed = _time.time() - start
                                if int(elapsed) % 30 < 5:
                                    logger.info(
                                        f"[ANRouter] Remote task update: "
                                        f"{subtask.assigned_to} ({remote_task_id}) "
                                        f"state={state}, elapsed={elapsed:.0f}s"
                                    )

                        elif event_type == "ping":
                            continue  # keep-alive, ignore

                        # Check timeout
                        if (_time.time() - start) > timeout:
                            final_status = "error"
                            final_error = (
                                f"SSE stream timed out after {timeout:.0f}s"
                            )
                            logger.warning(
                                f"[ANRouter] {final_error} for {remote_task_id}"
                            )
                            break

                    elapsed = _time.time() - start
                    if final_status == "error" and final_error and not terminal_emitted:
                        self._emit_remote_status_event(
                            subtask,
                            action="remote_failed",
                            stage="failed",
                            transport="sse",
                            content=(
                                f"❌ {subtask.assigned_to} [{remote_task_id}] failed: "
                                f"{str(final_error)[:180]}"
                            ),
                            remote_task_id=remote_task_id,
                            main_task_id=main_task_id,
                            remote_status="error",
                            status_event="sse_terminal",
                            percentage=100,
                            phase="completed",
                            phase_label="Remote task failed",
                            progress={},
                            error_text=final_error,
                            is_final=True,
                        )
                    if final_status == "success":
                        logger.info(
                            f"[ANRouter] Remote task completed (SSE): "
                            f"{subtask.assigned_to} ({ip}:{port}) → "
                            f"task={remote_task_id}, elapsed={elapsed:.1f}s"
                        )

                    return {
                        "assigned_to": subtask.assigned_to,
                        "node_id": subtask.target_node_id,
                        "ip": ip,
                        "port": port,
                        "status": final_status,
                        "result": final_result,
                        "error": final_error,
                        "remote_task_id": remote_task_id,
                    }

        except (
            httpx.ConnectError, httpx.ReadTimeout,
            httpx.RemoteProtocolError, OSError,
        ) as e:
            logger.info(
                f"[ANRouter] SSE connection failed for {remote_task_id}: "
                f"{type(e).__name__}: {e} — will fall back to polling"
            )
            return None
        except Exception as e:
            logger.warning(
                f"[ANRouter] SSE subscribe error for {remote_task_id}: {e}",
                exc_info=True,
            )
            return None

    def _poll_remote_task(
        self, base_url: str, remote_task_id: str, subtask: Any,
        ip: str, port: int, interval: float, timeout: float,
        *,
        main_task_id: str | None = None,
    ) -> dict:
        """Fallback: poll GET /api/agent/tasks/{id} until terminal state."""
        import time as _time

        import httpx

        _rc = self.config.an_router if self.config else None

        poll_url = f"{base_url}/api/agent/tasks/{remote_task_id}"
        start = _time.time()
        final_status = "pending"
        final_result = ""
        final_error = ""
        last_poll_progress_sig: tuple | None = None

        logger.info(
            f"[ANRouter] Polling remote task {remote_task_id} at "
            f"{subtask.assigned_to} ({ip}:{port})..."
        )

        self._emit_remote_status_event(
            subtask,
            stage="polling",
            transport="poll",
            content=(
                f"📡 Polling {subtask.assigned_to} [{remote_task_id}] "
                f"for status updates"
            ),
            remote_task_id=remote_task_id,
            main_task_id=main_task_id,
            remote_status="polling",
            status_event="poll_started",
            percentage=16,
            phase_label="Polling remote node",
            phase="executing",
        )

        while (_time.time() - start) < timeout:
            _time.sleep(interval)
            try:
                _poll_http_timeout = _rc.remote_poll_http_timeout if _rc else 15.0
                with httpx.Client(timeout=_poll_http_timeout) as client:
                    resp = client.get(poll_url)

                if resp.status_code != 200:
                    continue

                data = resp.json()
                status = data.get("status", "pending")
                if self.on_step and status not in ("completed", "success", "failed", "error"):
                    try:
                        progress = data.get("progress") if isinstance(data, dict) else None
                        phase_label = str((progress or {}).get("phase_label", "") or "").strip()
                        pct = (progress or {}).get("percentage", None)
                        step_no = (progress or {}).get("current_step_number", None)
                        sig = (status, phase_label, pct, step_no)
                        if sig == last_poll_progress_sig:
                            continue
                        last_poll_progress_sig = sig
                        pct_part = ""
                        if isinstance(pct, int):
                            pct_part = f" · {pct}%"
                        content = (
                            f"📡 {subtask.assigned_to} [{remote_task_id}] "
                            f"state={status}{f' · {phase_label}' if phase_label else ''}{pct_part}"
                        )
                        self._emit_remote_status_event(
                            subtask,
                            action="remote_progress",
                            stage=self._infer_remote_stage(
                                status,
                                "poll_status",
                                is_final=False,
                                fallback="polling",
                                percentage=pct if isinstance(pct, int) else None,
                                phase_label=phase_label,
                            ),
                            transport="poll",
                            content=content,
                            remote_task_id=remote_task_id,
                            main_task_id=main_task_id,
                            remote_status=status,
                            status_event="poll_status",
                            percentage=pct if isinstance(pct, int) else None,
                            phase=str((progress or {}).get("phase", "") or "executing"),
                            phase_label=phase_label,
                            progress=progress if isinstance(progress, dict) else {},
                        )
                    except Exception as emit_exc:
                        logger.warning(
                            "[ANRouter] Poll progress emit failed for %s (%s): %s",
                            subtask.assigned_to,
                            remote_task_id,
                            emit_exc,
                            exc_info=True,
                        )

                if status in ("completed", "success"):
                    final_status = "success"
                    final_result = data.get("result", str(data))
                    if self.on_step:
                        try:
                            self._emit_remote_status_event(
                                subtask,
                                action="remote_completed",
                                stage="completed",
                                transport="poll",
                                content=f"✅ {subtask.assigned_to} [{remote_task_id}] completed",
                                remote_task_id=remote_task_id,
                                main_task_id=main_task_id,
                                remote_status=status,
                                status_event="poll_completed",
                                percentage=100,
                                phase="completed",
                                phase_label="Remote task completed",
                                progress=data.get("progress", {}) if isinstance(data, dict) else {},
                                is_final=True,
                            )
                        except Exception as emit_exc:
                            logger.warning(
                                "[ANRouter] Completion emit failed for %s (%s): %s",
                                subtask.assigned_to,
                                remote_task_id,
                                emit_exc,
                                exc_info=True,
                            )
                    elapsed = _time.time() - start
                    logger.info(
                        f"[ANRouter] Remote task completed (poll): "
                        f"{subtask.assigned_to} ({ip}:{port}) → "
                        f"task={remote_task_id}, elapsed={elapsed:.1f}s"
                    )
                    break
                elif status in ("failed", "error"):
                    final_status = "error"
                    final_error = data.get("error", "Remote task failed")
                    if self.on_step:
                        try:
                            self._emit_remote_status_event(
                                subtask,
                                action="remote_failed",
                                stage="failed",
                                transport="poll",
                                content=f"❌ {subtask.assigned_to} [{remote_task_id}] failed: {str(final_error)[:180]}",
                                remote_task_id=remote_task_id,
                                main_task_id=main_task_id,
                                remote_status=status,
                                status_event="poll_failed",
                                percentage=100,
                                phase="completed",
                                phase_label="Remote task failed",
                                progress=data.get("progress", {}) if isinstance(data, dict) else {},
                                error_text=str(final_error),
                                is_final=True,
                            )
                        except Exception as emit_exc:
                            logger.warning(
                                "[ANRouter] Failure emit failed for %s (%s): %s",
                                subtask.assigned_to,
                                remote_task_id,
                                emit_exc,
                                exc_info=True,
                            )
                    break
                else:
                    elapsed = _time.time() - start
                    if int(elapsed) % 30 < interval:
                        logger.info(
                            f"[ANRouter] Remote task still running: "
                            f"{subtask.assigned_to} ({remote_task_id}), "
                            f"elapsed={elapsed:.0f}s"
                        )
            except Exception as e:
                logger.debug(
                    f"[ANRouter] Poll request failed for {remote_task_id} "
                    f"at {subtask.assigned_to}: {e}"
                )
                continue
        else:
            elapsed = _time.time() - start
            final_status = "error"
            final_error = f"Poll timed out after {elapsed:.0f}s"
            logger.warning(
                f"[ANRouter] {final_error} for {remote_task_id}"
            )
            if self.on_step:
                try:
                    self._emit_remote_status_event(
                        subtask,
                        action="remote_failed",
                        stage="failed",
                        transport="poll",
                        content=f"❌ {subtask.assigned_to} [{remote_task_id}] failed: {final_error}",
                        remote_task_id=remote_task_id,
                        main_task_id=main_task_id,
                        remote_status="timeout",
                        status_event="poll_timeout",
                        percentage=100,
                        phase="completed",
                        phase_label="Remote task polling timed out",
                        progress={},
                        error_text=final_error,
                        is_final=True,
                    )
                except Exception as emit_exc:
                    logger.warning(
                        "[ANRouter] Timeout failure emit failed for %s (%s): %s",
                        subtask.assigned_to,
                        remote_task_id,
                        emit_exc,
                        exc_info=True,
                    )

        return {
            "assigned_to": subtask.assigned_to,
            "node_id": subtask.target_node_id,
            "ip": ip,
            "port": port,
            "status": final_status,
            "result": final_result,
            "error": final_error,
            "remote_task_id": remote_task_id,
        }

    @staticmethod
    def _strip_network_tools(agents: list[Any]) -> list[Any]:
        """Return shallow copies of agents with network delegation tools removed.

        When the ANRouter has already handled task routing (deciding which
        pool members participate), the local CrewAI crew must NOT also try
        HTTP delegation via ``DelegateToNetworkTool``.  Removing these tools
        prevents the CrewAI manager from calling remote nodes again, which
        would cause timeouts or duplicate work.
        """
        from teaming24.agent.tools.network_tools import DelegateToNetworkTool

        cleaned = []
        for agent in agents:
            tools = getattr(agent, "tools", None)
            if tools and isinstance(tools, list):
                filtered = [
                    t for t in tools
                    if not isinstance(t, DelegateToNetworkTool)
                ]
                if len(filtered) != len(tools):
                    # Create a shallow copy so we don't mutate the original agent
                    import copy
                    agent_copy = copy.copy(agent)
                    agent_copy.tools = filtered
                    logger.debug(
                        f"Stripped DelegateToNetworkTool from agent "
                        f"'{getattr(agent, 'role', '?')}' for local execution"
                    )
                    cleaned.append(agent_copy)
                    continue
            cleaned.append(agent)
        return cleaned

    def _save_all_outputs(
        self,
        task_id: str,
        prompt: str,
        result: dict,
        remote_results: list[dict] = None,
    ):
        """Persist aggregated task output (local + remote) to disk.

        Writes everything to ``{output.base_dir}/{task_id}/``:
          - result.txt   — aggregated text
          - local/       — extracted code files from local execution
          - remote/{an}/ — each remote AN's result + extracted files
          - manifest.json
        """
        if not task_id:
            return
        try:
            from teaming24.task.output import save_aggregated_output
            aggregated_text = result.get("result", "")
            duration = result.get("duration", 0)
            tokens = result.get("cost", {}).get("total_tokens", 0)
            task_output = save_aggregated_output(
                task_id=task_id,
                task_name=prompt[:100],
                aggregated_text=aggregated_text,
                remote_results=remote_results or [],
                duration=duration,
                tokens=tokens,
            )
            # Attach output info to result dict for the API layer
            result["output"] = {
                "output_dir": task_output.output_dir,
                "files": [
                    {
                        "filename": f.filename,
                        "filepath": f.filepath,
                        "language": f.language,
                        "run_command": f.run_command,
                    }
                    for f in task_output.files
                ],
            }
            logger.info(
                f"[OUTPUT] Saved aggregated output: {task_output.output_dir} "
                f"({len(task_output.files)} local files, "
                f"{len(remote_results or [])} remote results)"
            )
        except Exception as e:
            logger.warning(f"Failed to save aggregated task output: {e}")

    # ------------------------------------------------------------------
    # Self-improvement helpers
    # ------------------------------------------------------------------

    def _resolve_max_rounds_for_prompt(self, prompt: str) -> int:
        """Resolve max rounds from benchmark profile + task-class policy."""
        policy = self._get_quality_policy(prompt)
        configured = policy.get("max_rounds", getattr(self, "max_execution_rounds", 3))
        try:
            rounds = int(configured)
        except (TypeError, ValueError):
            rounds = int(getattr(self, "max_execution_rounds", 3))
        # Keep bounded to prevent runaway retries from bad config.
        return max(1, min(rounds, 8))

    @staticmethod
    def _classify_task_class(prompt: str) -> str:
        """Classify request into empirical/coding/analysis/general."""
        text = str(prompt or "").lower()
        empirical_markers = (
            "predict", "forecast", "price", "tomorrow", "stock", "market",
            "machine learning", "ml", "train", "fine-tune", "inference",
            "download data", "fetch data", "real-time", "live data",
            "backtest", "benchmark", "simulate",
        )
        coding_markers = (
            "code", "implement", "bug", "fix", "refactor", "function",
            "class", "api", "endpoint", "test", "unit test", "integration test",
        )
        analysis_markers = (
            "analyze", "analysis", "compare", "evaluate", "report",
            "summary", "investigate", "root cause", "tradeoff",
        )
        if any(k in text for k in empirical_markers):
            return "empirical"
        if any(k in text for k in coding_markers):
            return "coding"
        if any(k in text for k in analysis_markers):
            return "analysis"
        return "general"

    @staticmethod
    def _default_quality_policy(profile: str) -> dict[str, dict[str, Any]]:
        """Benchmark-tuned baseline policy map by task class."""
        profile_norm = str(profile or "balanced").lower()
        baseline = {
            "empirical": {
                "max_rounds": 4,
                "min_result_chars": 160,
                "require_evidence": True,
                "min_evidence_score": 2,
                "confidence_threshold": 0.68,
                "allow_plan_output": False,
            },
            "coding": {
                "max_rounds": 3,
                "min_result_chars": 100,
                "require_evidence": True,
                "min_evidence_score": 2,
                "confidence_threshold": 0.62,
                "allow_plan_output": False,
            },
            "analysis": {
                "max_rounds": 3,
                "min_result_chars": 90,
                "require_evidence": False,
                "min_evidence_score": 1,
                "confidence_threshold": 0.58,
                "allow_plan_output": False,
            },
            "general": {
                "max_rounds": 2,
                "min_result_chars": 70,
                "require_evidence": False,
                "min_evidence_score": 1,
                "confidence_threshold": 0.55,
                "allow_plan_output": False,
            },
        }
        if profile_norm == "fast":
            for cfg in baseline.values():
                cfg["max_rounds"] = max(1, int(cfg["max_rounds"]) - 1)
                cfg["confidence_threshold"] = max(0.45, float(cfg["confidence_threshold"]) - 0.08)
            return baseline
        if profile_norm == "strict":
            for cfg in baseline.values():
                cfg["max_rounds"] = min(8, int(cfg["max_rounds"]) + 1)
                cfg["min_result_chars"] = int(cfg["min_result_chars"]) + 40
                cfg["confidence_threshold"] = min(0.95, float(cfg["confidence_threshold"]) + 0.08)
                cfg["min_evidence_score"] = int(cfg["min_evidence_score"]) + 1
            return baseline
        return baseline

    def _get_quality_policy(self, prompt: str) -> dict[str, Any]:
        """Get merged quality policy for the prompt's task class."""
        cfg = get_config().api
        task_class = self._classify_task_class(prompt)
        defaults = self._default_quality_policy(getattr(cfg, "quality_benchmark_profile", "balanced"))
        class_policy = dict(defaults.get(task_class, defaults["general"]))
        overrides = getattr(cfg, "quality_task_class_policies", {}) or {}
        if isinstance(overrides, dict):
            merged_override = overrides.get(task_class, {})
            if isinstance(merged_override, dict):
                class_policy.update(merged_override)
        class_policy["task_class"] = task_class
        return class_policy

    @staticmethod
    def _normalize_refinement_signature(text: str) -> str:
        """Canonical signature used to detect repeated refinement outputs."""
        return re.sub(r"\s+", " ", str(text or "").strip().lower())[:2500]

    def _compute_round_quality_score(
        self,
        result_text: str,
        prompt: str,
        task_id: str | None,
        policy: dict[str, Any],
    ) -> float:
        """Lightweight quality score used for refinement convergence checks."""
        text = str(result_text or "").strip()
        if not text:
            return 0.0
        try:
            min_chars = max(40, int(policy.get("min_result_chars", 80)))
        except (TypeError, ValueError):
            min_chars = 80
        schema = self._build_evidence_schema(text)
        has_trace = self._task_has_execution_trace(task_id)

        score = 0.1
        if not self._is_plan_like_result(text):
            score += 0.25
        score += min(0.25, (len(text) / max(min_chars, 1)) * 0.25)
        score += min(0.30, (float(schema.get("evidence_score", 0)) / 6.0) * 0.30)
        if has_trace:
            score += 0.10
        if self._is_empirical_request(prompt) and not schema.get("sources"):
            score -= 0.15
        return max(0.0, min(1.0, score))

    def _evaluate_result(
        self, prompt: str, result: dict, round_num: int, task_id: str | None = None
    ) -> tuple[bool, str]:
        """Evaluate result quality with policy, evidence schema, and verifier model."""
        try:
            cfg = get_config().api
            if not getattr(cfg, "quality_gate_enabled", True):
                return True, ""

            result_text = result.get("result", "") if isinstance(result, dict) else str(result)
            if not result_text or not result_text.strip():
                return False, "The result was empty. Please provide a complete response."

            # Accept explicit give-up: agent states task is impossible — stop refinement
            if self._is_explicit_give_up(result_text):
                logger.info("[Organizer] Round %s: agent gave up (explicit); accepting as final", round_num)
                return True, ""

            policy = self._get_quality_policy(prompt)
            task_class = str(policy.get("task_class", "general"))

            # Heuristic checks (cheap fail-fast)
            reject, feedback = self._evaluate_result_heuristics(prompt, result_text, policy)
            if reject:
                logger.info("[Organizer] Round %s heuristic reject: %s", round_num, feedback[:120])
                return False, feedback

            # Plan/evidence gate (policy-aware)
            evidence_ok, evidence_feedback = self._check_execution_evidence(
                prompt, result_text, task_id, task_class, policy
            )
            if not evidence_ok:
                logger.info("[Organizer] Round %s evidence-gate reject: %s", round_num, evidence_feedback[:120])
                return False, evidence_feedback

            evidence_schema = self._build_evidence_schema(result_text)
            has_trace = self._task_has_execution_trace(task_id)
            schema_ok, schema_feedback = self._validate_evidence_schema(
                evidence_schema, task_class, policy, has_trace=has_trace
            )
            if not schema_ok:
                logger.info("[Organizer] Round %s schema reject: %s", round_num, schema_feedback[:120])
                return False, schema_feedback

            if round_num >= 2:
                if self._should_short_circuit_quality_accept(
                    result_text=result_text,
                    task_class=task_class,
                    policy=policy,
                    has_trace=has_trace,
                    evidence_schema=evidence_schema,
                ):
                    logger.info(
                        "[Organizer] Round %s accepted via fast-path quality gate (class=%s)",
                        round_num,
                        task_class,
                    )
                    return True, ""

            min_chars = 80
            try:
                min_chars = max(40, int(policy.get("min_result_chars", 80)))
            except (TypeError, ValueError):
                min_chars = 80
            if (
                round_num >= 2
                and not self._is_plan_like_result(result_text)
                and len(str(result_text or "").strip()) >= min_chars
            ):
                logger.info(
                    "[Organizer] Round %s accepted after schema pass to avoid excessive refinement",
                    round_num,
                )
                return True, ""

            local_confidence = self._compute_local_confidence(
                result_text, evidence_schema, task_class, has_trace, policy
            )

            organizer_eval = self._run_organizer_quality_eval(
                prompt, result_text, round_num, task_class
            )
            if not organizer_eval["satisfied"]:
                return False, organizer_eval["feedback"]

            verifier_eval = self._run_independent_verifier_eval(
                prompt, result_text, round_num, task_class, evidence_schema
            )
            if verifier_eval and not verifier_eval.get("satisfied", True):
                return False, str(verifier_eval.get("feedback", "Independent verifier rejected the result"))

            organizer_conf = float(organizer_eval.get("confidence", 0.7))
            verifier_conf = (
                float(verifier_eval.get("confidence", organizer_conf))
                if verifier_eval else organizer_conf
            )
            combined_conf = max(
                0.0,
                min(1.0, (local_confidence * 0.35) + (organizer_conf * 0.35) + (verifier_conf * 0.30)),
            )
            confidence_threshold = float(
                policy.get(
                    "confidence_threshold",
                    getattr(cfg, "quality_confidence_threshold", 0.65),
                )
            )
            if (
                getattr(cfg, "quality_auto_fallback_low_confidence", True)
                and combined_conf < confidence_threshold
            ):
                if round_num >= 2:
                    logger.info(
                        "[Organizer] Round %s accepted despite low confidence %.2f < %.2f (class=%s)",
                        round_num,
                        combined_conf,
                        confidence_threshold,
                        task_class,
                    )
                    return True, ""
                return (
                    False,
                    f"Confidence {combined_conf:.2f} below threshold {confidence_threshold:.2f}; "
                    "produce stronger evidence and a clearer direct answer.",
                )

            logger.info(
                "[Organizer] Round %s quality accepted (class=%s, conf=%.2f)",
                round_num,
                task_class,
                combined_conf,
            )
            return True, ""
        except Exception as eval_exc:
            logger.warning(
                "[Organizer] Result evaluation failed (assuming satisfied): %s",
                eval_exc,
                exc_info=True,
            )
            return True, ""

    def _evaluate_result_heuristics(
        self, prompt: str, result_text: str, policy: dict[str, Any] | None = None
    ) -> tuple[bool, str]:
        """Fast heuristic checks before LLM evaluation. Returns (reject, feedback)."""
        text_lower = result_text.lower().strip()
        text_len = len(result_text)

        # Placeholder / incomplete patterns
        placeholder_patterns = [
            (r"\btodo\b", "Result contains TODO — complete the task."),
            (r"\btbd\b", "Result contains TBD — provide concrete values."),
            (r"\bfixme\b", "Result contains FIXME — complete the implementation."),
            (r"\[to be filled\]", "Result contains placeholders — fill in all details."),
            (r"\[placeholder\]", "Result contains placeholders — replace with real content."),
            (r"\[\.\.\.\]", "Result appears incomplete — provide full content."),
            (r"\(details omitted\)", "Result omits details — include requested information."),
            (r"\.\.\.\s*$", "Result may be truncated — ensure complete output."),
            (r"can'?t\s+access\s+(live\s+)?(market\s+)?data", "Agents have tools to fetch data — use python_interpreter/shell to get live data (e.g. yfinance), never claim cannot access."),
            (r"cannot\s+access\s+(live\s+)?(market\s+)?data", "Agents have tools to fetch data — use python_interpreter/shell to get live data (e.g. yfinance), never claim cannot access."),
            (r"unable\s+to\s+access\s+(live\s+)?(market\s+)?data", "Agents have tools to fetch data — use python_interpreter/shell to get live data (e.g. yfinance), never claim unable to access."),
            (r"modulenotfounderror", "Execution failed due to missing dependency — install required package(s) and rerun to produce final result."),
            (r"no module named ['\"][^'\"]+['\"]", "Execution failed due to missing dependency — install required package(s) and rerun to produce final result."),
            (r"command not found", "Execution environment command missing — install/fix environment and rerun to produce final result."),
        ]
        for pat, msg in placeholder_patterns:
            if re.search(pat, text_lower, re.IGNORECASE):
                return True, msg

        min_chars = 50
        if isinstance(policy, dict):
            try:
                min_chars = max(20, int(policy.get("min_result_chars", min_chars)))
            except (TypeError, ValueError):
                min_chars = 50

        # Very short result for a non-trivial request
        if text_len < min_chars and len(prompt.strip()) > 100:
            return True, "Result is too short for the request — provide a complete answer."

        return False, ""

    @staticmethod
    def _is_explicit_give_up(result_text: str) -> bool:
        """Detect when agent explicitly states the task cannot be completed."""
        text = str(result_text or "").strip().lower()
        if len(text) < 60:
            return False
        give_up_phrases = (
            "cannot complete", "unable to complete", "impossible to",
            "cannot obtain", "unable to obtain", "cannot access the",
            "unable to access", "cannot retrieve", "unable to retrieve",
            "cannot get", "unable to get", "cannot fetch", "unable to fetch",
            "cannot proceed", "unable to proceed", "cannot provide a prediction",
            "unable to provide", "cannot produce", "unable to produce",
            "not possible to", "it is not possible", "it's not possible",
            "i cannot", "i am unable", "we cannot", "we are unable",
        )
        return any(p in text for p in give_up_phrases)

    @staticmethod
    def _is_empirical_request(prompt: str) -> bool:
        """Whether the request likely requires execution/data-backed output."""
        text = str(prompt or "").lower()
        empirical_markers = (
            "predict", "forecast", "tomorrow", "price", "stock",
            "machine learning", "ml", "train", "fine-tune", "inference",
            "backtest", "benchmark", "evaluate model", "accuracy", "rmse",
            "download data", "fetch data", "latest", "real-time", "live data",
            "from internet", "from web", "api data", "scrape", "crawl",
            "calculate", "compute", "simulation", "simulate",
        )
        return any(marker in text for marker in empirical_markers)

    def _should_short_circuit_quality_accept(
        self,
        result_text: str,
        task_class: str,
        policy: dict[str, Any],
        has_trace: bool,
        evidence_schema: dict[str, Any],
    ) -> bool:
        """Fast accept path to reduce unnecessary refinement rounds."""
        text = str(result_text or "").strip()
        if not text:
            return False
        if self._is_plan_like_result(text):
            return False

        try:
            min_chars = max(40, int(policy.get("min_result_chars", 80)))
        except (TypeError, ValueError):
            min_chars = 80

        if task_class in ("general", "analysis"):
            return len(text) >= min_chars

        if task_class == "coding":
            has_code_block = "```" in text
            has_artifact = bool(evidence_schema.get("artifacts"))
            has_command = bool(evidence_schema.get("commands"))
            return len(text) >= min_chars and (
                has_code_block or has_artifact or has_command or has_trace
            )

        if task_class == "empirical":
            has_text_evidence = self._result_has_concrete_evidence(text)
            return has_text_evidence and (has_trace or len(text) >= max(min_chars, 140))

        return len(text) >= min_chars

    @staticmethod
    def _prompt_requests_plan(prompt: str) -> bool:
        """Whether the user explicitly asks for plan/strategy instead of direct output."""
        text = str(prompt or "").lower()
        plan_request_markers = (
            "plan", "strategy", "roadmap", "architecture proposal",
            "how should", "step by step", "implementation steps",
        )
        direct_result_markers = (
            "direct answer", "final answer only", "just result", "no plan",
            "execute it", "run it", "give me the output",
        )
        if any(marker in text for marker in direct_result_markers):
            return False
        return any(marker in text for marker in plan_request_markers)

    @staticmethod
    def _is_plan_like_result(result_text: str) -> bool:
        """Detect plan-only responses that do not provide deliverables."""
        text = str(result_text or "")
        lower = text.lower()
        plan_markers = (
            "here is how", "you can", "you should", "recommended approach",
            "proposed solution", "step 1", "next steps", "implementation plan",
            "i suggest", "would do", "strategy",
        )
        concrete_markers = (
            "final answer", "prediction", "predicted", "result:", "metrics",
            "accuracy", "rmse", "mae", "r2", "artifact", "saved to",
            "data source", "source:", "command", "```", "|",
        )
        has_plan = any(m in lower for m in plan_markers)
        has_concrete = any(m in lower for m in concrete_markers)
        return has_plan and not has_concrete

    @staticmethod
    def _result_has_concrete_evidence(result_text: str) -> bool:
        """Check for concrete execution evidence in the response text."""
        text = str(result_text or "")
        lower = text.lower()

        has_number = bool(
            re.search(r"\b-?\d+(?:\.\d+)?(?:%|usd|usdc|ms|s|m|h)?\b", text)
        )
        has_table = text.count("|") >= 6 and "\n" in text
        has_code_block = "```" in text
        has_metric = any(
            m in lower for m in (
                "accuracy", "precision", "recall", "f1", "auc",
                "rmse", "mae", "mape", "r2", "loss", "metric",
            )
        )
        has_artifact = bool(
            re.search(
                r"(?:^|[\s(])(?:[~./\w-]+)\.(?:csv|json|parquet|pkl|pt|pth|onnx|txt|png|jpg|jpeg)\b",
                text,
                re.IGNORECASE,
            )
        )
        has_source = any(
            s in lower for s in (
                "data source", "source:", "fetched from", "downloaded from",
                "api endpoint", "yfinance", "huggingface", "kaggle",
            )
        )

        evidence_score = sum(
            [
                has_number,
                has_table,
                has_code_block,
                has_metric,
                has_artifact,
                has_source,
            ]
        )
        return evidence_score >= 2

    def _task_has_execution_trace(self, task_id: str | None) -> bool:
        """Detect if workers/tools actually executed for this task."""
        if not task_id or not self.task_manager:
            return False
        try:
            task = self.task_manager.get_task(task_id)
            if not task or not task.steps:
                return False

            non_exec_actions = {
                "receive", "route", "decision", "assign", "dispatch",
                "dispatching", "aggregate", "eval_round", "complete",
                "round_start", "skip_local", "remote_progress",
                "remote_done", "remote_completed", "remote_failed",
                "payment_processing", "payment_approved", "payment_sent",
            }
            exec_markers = (
                "python", "shell", "tool", "execute", "run", "query",
                "download", "fetch", "http", "api", "dataset", "train",
                "predict", "inference", "model", "artifact", "saved",
                "pandas", "numpy", "sklearn", "huggingface", "yfinance",
            )

            for step in task.steps[-120:]:
                action = str(getattr(step, "action", "") or "").lower()
                content = " ".join(
                    [
                        str(getattr(step, "content", "") or ""),
                        str(getattr(step, "thought", "") or ""),
                        str(getattr(step, "observation", "") or ""),
                    ]
                ).lower()

                if action and action not in non_exec_actions:
                    if any(m in action for m in ("python", "shell", "tool", "execute", "run", "search")):
                        return True
                if any(marker in content for marker in exec_markers):
                    return True
            return False
        except Exception as trace_exc:
            logger.warning(
                "Failed to inspect execution trace task=%s: %s",
                task_id,
                trace_exc,
                exc_info=True,
            )
            return False

    def _check_execution_evidence(
        self,
        prompt: str,
        result_text: str,
        task_id: str | None,
        task_class: str | None = None,
        policy: dict[str, Any] | None = None,
    ) -> tuple[bool, str]:
        """Require concrete evidence for empirical tasks."""
        plan_requested = self._prompt_requests_plan(prompt)
        empirical_requested = bool(task_class == "empirical" or self._is_empirical_request(prompt))
        allow_plan_output = bool((policy or {}).get("allow_plan_output", False))

        if not allow_plan_output and not plan_requested and self._is_plan_like_result(result_text):
            return (
                False,
                "Result is still plan-oriented. Provide the direct deliverable first, not an execution plan.",
            )

        if not empirical_requested:
            return True, ""

        has_text_evidence = self._result_has_concrete_evidence(result_text)
        has_trace_evidence = self._task_has_execution_trace(task_id)

        # Accept if result text is concrete; execution trace is a strong extra signal.
        if has_text_evidence:
            return True, ""
        min_chars = 120
        if isinstance(policy, dict):
            try:
                min_chars = max(80, int(policy.get("min_result_chars", min_chars)))
            except (TypeError, ValueError):
                min_chars = 120
        if has_trace_evidence and len(str(result_text or "").strip()) >= min_chars:
            return True, ""

        return (
            False,
            "Empirical request requires executable evidence: include actual values/metrics, "
            "data source, and artifact or runnable command (not only methodology).",
        )

    def _build_evidence_schema(self, result_text: str) -> dict[str, Any]:
        """Build a structured evidence schema from free-form text."""
        text = str(result_text or "")
        lower = text.lower()
        urls = re.findall(r"https?://[^\s)]+", text, re.IGNORECASE)

        source_lines = re.findall(
            r"(?im)^\s*(?:data\s*source|source|dataset|api\s*endpoint)\s*[:=-]\s*(.+?)\s*$",
            text,
        )
        # Also detect common data sources mentioned inline
        inline_sources = [n for n in ("yfinance", "yahoo finance", "pandas", "api", "kaggle", "huggingface") if n in lower]
        sources = []
        for item in [*source_lines, *urls, *inline_sources]:
            value = str(item).strip()
            if value and value not in sources:
                sources.append(value)

        metric_pairs = re.findall(
            r"(?i)\b([a-z][a-z0-9_ \-]{1,30})\s*[:=]\s*(-?\d+(?:\.\d+)?)\b",
            text,
        )
        metrics: list[dict[str, Any]] = []
        seen_metric_names: set[str] = set()
        for name, value in metric_pairs:
            key = re.sub(r"\s+", "_", name.strip().lower())
            if key in seen_metric_names:
                continue
            seen_metric_names.add(key)
            try:
                num = float(value)
            except ValueError:
                continue
            metrics.append({"name": key, "value": num})

        artifacts = re.findall(
            r"(?:^|[\s(])([~./\w-]+\.(?:csv|json|parquet|pkl|pt|pth|onnx|txt|png|jpg|jpeg))\b",
            text,
            re.IGNORECASE,
        )
        artifact_paths = []
        for path in artifacts:
            p = str(path).strip()
            if p and p not in artifact_paths:
                artifact_paths.append(p)

        commands: list[str] = []
        for block in re.findall(r"```(?:bash|sh|shell|zsh|python)?\n(.*?)```", text, re.DOTALL | re.IGNORECASE):
            block_text = str(block).strip()
            if block_text:
                commands.append(block_text)
        for line in text.splitlines():
            stripped = line.strip()
            if stripped.startswith("$ "):
                commands.append(stripped[2:].strip())

        number_count = len(re.findall(r"\b-?\d+(?:\.\d+)?\b", text))
        direct_answer = (
            not self._is_plan_like_result(text)
            and len(text.strip()) >= 40
            and ("final answer" in lower or number_count >= 2 or "result:" in lower)
        )
        evidence_score = 0
        evidence_score += 1 if direct_answer else 0
        evidence_score += 1 if sources else 0
        evidence_score += 1 if metrics else 0
        evidence_score += 1 if artifact_paths else 0
        evidence_score += 1 if commands else 0
        evidence_score += 1 if number_count >= 3 else 0

        return {
            "direct_answer": bool(direct_answer),
            "sources": sources[:8],
            "metrics": metrics[:15],
            "artifacts": artifact_paths[:15],
            "commands": commands[:8],
            "number_count": number_count,
            "evidence_score": evidence_score,
        }

    def _validate_evidence_schema(
        self, schema: dict[str, Any], task_class: str, policy: dict[str, Any],
        has_trace: bool = False,
    ) -> tuple[bool, str]:
        """Strict validation for structured evidence schema."""
        required_keys = (
            "direct_answer",
            "sources",
            "metrics",
            "artifacts",
            "commands",
            "number_count",
            "evidence_score",
        )
        for key in required_keys:
            if key not in schema:
                return False, f"Evidence schema missing key '{key}'."
        if not isinstance(schema["direct_answer"], bool):
            return False, "Evidence schema 'direct_answer' must be boolean."
        for list_key in ("sources", "metrics", "artifacts", "commands"):
            if not isinstance(schema[list_key], list):
                return False, f"Evidence schema '{list_key}' must be a list."

        min_evidence = int(policy.get("min_evidence_score", 1))
        if int(schema.get("evidence_score", 0)) < min_evidence:
            return False, (
                f"Evidence quality is too low: score {schema.get('evidence_score', 0)} "
                f"< required {min_evidence}."
            )
        if task_class == "empirical":
            ev_score = int(schema.get("evidence_score", 0))
            num_count = int(schema.get("number_count", 0))
            # Relax when has execution trace or sufficient evidence
            relaxed = has_trace or ev_score >= 2 or num_count >= 2
            if not schema["direct_answer"] and not relaxed:
                return False, "Empirical tasks require a direct answer before methodology."
            if not schema["sources"] and not relaxed:
                return False, "Empirical tasks require at least one explicit data source."
            if not schema["metrics"] and num_count < (2 if relaxed else 3):
                return False, "Empirical tasks require explicit metrics or concrete numeric values."
        if task_class == "coding":
            if not schema["artifacts"] and not schema["commands"]:
                return False, "Coding tasks require an artifact path or runnable command."
        return True, ""

    def _compute_local_confidence(
        self,
        result_text: str,
        schema: dict[str, Any],
        task_class: str,
        has_trace: bool,
        policy: dict[str, Any],
    ) -> float:
        """Compute local confidence from schema + execution trace."""
        score = 0.15
        score += 0.2 if schema.get("direct_answer") else 0.0
        score += 0.15 if schema.get("sources") else 0.0
        score += 0.15 if schema.get("metrics") else 0.0
        score += 0.1 if schema.get("artifacts") else 0.0
        score += 0.1 if schema.get("commands") else 0.0
        score += 0.1 if has_trace else 0.0
        if len(str(result_text or "").strip()) >= int(policy.get("min_result_chars", 80)):
            score += 0.1
        if self._is_plan_like_result(result_text):
            score -= 0.25
        if task_class == "empirical" and not has_trace:
            score -= 0.1
        return max(0.0, min(1.0, score))

    def _run_organizer_quality_eval(
        self, prompt: str, result_text: str, round_num: int, task_class: str
    ) -> dict[str, Any]:
        """Run organizer-side quality evaluation and return structured verdict."""
        import json as _json

        organizer = self.organizer or (self.agents[0] if self.agents else None)
        llm = getattr(organizer, "llm", None) if organizer else None
        if not llm or not hasattr(llm, "call"):
            return {"satisfied": True, "feedback": "", "confidence": 0.65}

        ctx_max = get_config().api.execution_round_eval_ctx_chars
        eval_prompt = render_prompt(
            "core.organizer_quality_eval",
            task_class=task_class,
            original_request=prompt,
            round_num=round_num,
            result_snippet=result_text[:ctx_max],
        )
        try:
            resp = llm.call([{"role": "user", "content": eval_prompt}])
            if not resp or not isinstance(resp, str):
                return {"satisfied": True, "feedback": "", "confidence": 0.65}
            json_str = self._extract_eval_json(resp)
            if not json_str:
                return {"satisfied": True, "feedback": "", "confidence": 0.6}
            data = _json.loads(json_str)
            satisfied = bool(data.get("satisfied", True))
            feedback = str(data.get("feedback", "")).strip()
            try:
                confidence = float(data.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))
            return {
                "satisfied": satisfied,
                "feedback": feedback,
                "confidence": confidence,
            }
        except Exception as org_eval_exc:
            logger.warning(
                "Organizer quality eval failed: %s",
                org_eval_exc,
                exc_info=True,
            )
            return {"satisfied": True, "feedback": "", "confidence": 0.6}

    def _get_independent_verifier_llm(self) -> Any | None:
        """Get independent verifier LLM (separate model from organizer by default)."""
        api_cfg = get_config().api
        if not getattr(api_cfg, "quality_verifier_enabled", True):
            return None
        model = str(getattr(api_cfg, "quality_verifier_model", "") or "").strip()
        if not model:
            return None
        if self._verifier_llm is not None and self._verifier_model_name == model:
            return self._verifier_llm
        try:
            verifier = None
            try:
                from crewai import LLM as _CrewLLM  # type: ignore
                verifier = _CrewLLM(
                    model=model,
                    temperature=float(getattr(api_cfg, "quality_verifier_temperature", 0.0)),
                )
            except Exception as llm_ctor_exc:
                logger.debug(
                    "Direct verifier LLM construction failed for model=%s: %s",
                    model,
                    llm_ctor_exc,
                    exc_info=True,
                )
            if verifier is None:
                verifier = self.factory._create_llm(model)
            if verifier and hasattr(verifier, "call"):
                self._verifier_llm = verifier
                self._verifier_model_name = model
                organizer_model = getattr(getattr(self.organizer, "llm", None), "model", "")
                if organizer_model and str(organizer_model) == model:
                    logger.warning(
                        "Verifier model matches organizer model (%s); configure a different verifier model for stronger independence.",
                        model,
                    )
                return verifier
        except Exception as verifier_exc:
            logger.warning(
                "Failed to create independent verifier LLM model=%s: %s",
                model,
                verifier_exc,
                exc_info=True,
            )
        return None

    def _run_independent_verifier_eval(
        self,
        prompt: str,
        result_text: str,
        round_num: int,
        task_class: str,
        evidence_schema: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Run independent verifier model and return structured verdict."""
        import json as _json

        verifier = self._get_independent_verifier_llm()
        if verifier is None:
            return None
        ctx_max = get_config().api.execution_round_eval_ctx_chars
        verify_prompt = render_prompt(
            "core.independent_verifier_eval",
            task_class=task_class,
            original_request=prompt,
            round_num=round_num,
            result_snippet=result_text[:ctx_max],
            evidence_schema_json=_json.dumps(evidence_schema, ensure_ascii=True),
        )
        try:
            resp = verifier.call([{"role": "user", "content": verify_prompt}])
            if not resp or not isinstance(resp, str):
                return None
            json_str = self._extract_eval_json(resp)
            if not json_str:
                return None
            data = _json.loads(json_str)
            satisfied = bool(data.get("satisfied", True))
            feedback = str(data.get("feedback", "")).strip()
            try:
                confidence = float(data.get("confidence", 0.7))
            except (TypeError, ValueError):
                confidence = 0.7
            confidence = max(0.0, min(1.0, confidence))
            checks = data.get("checks", {})
            if not isinstance(checks, dict):
                checks = {}
            return {
                "satisfied": satisfied,
                "feedback": feedback,
                "confidence": confidence,
                "checks": checks,
            }
        except Exception as verify_exc:
            logger.warning(
                "Independent verifier evaluation failed: %s",
                verify_exc,
                exc_info=True,
            )
            return None

    def _extract_eval_json(self, resp: str) -> str | None:
        """Extract JSON object from LLM response, handling nested braces in feedback."""
        import json as _json

        start = resp.find("{")
        if start == -1:
            return None

        depth = 0
        for i, c in enumerate(resp[start:], start):
            if c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    try:
                        _json.loads(resp[start : i + 1])
                        return resp[start : i + 1]
                    except Exception as e:
                        logger.debug("JSON parse attempt failed: %s", e)
                    return None
        return None

    def _build_round_prompt(
        self, original_prompt: str, prev_result: dict, feedback: str, round_num: int
    ) -> str:
        """Build an enriched prompt for round N+1 that includes feedback."""
        prev_text = (
            prev_result.get("result", "") if isinstance(prev_result, dict)
            else str(prev_result)
        )
        prev_preview = prev_text[:1500].strip() if prev_text else "(empty)"

        return (
            f"{original_prompt}\n\n"
            f"{'━' * 60}\n"
            f"IMPROVEMENT ROUND {round_num + 1} — address the feedback below\n"
            f"{'━' * 60}\n"
            f"Your previous attempt (round {round_num}) was evaluated as incomplete "
            f"or incorrect.\n\n"
            f"FEEDBACK:\n{feedback}\n\n"
            f"PREVIOUS ATTEMPT (for reference):\n{prev_preview}\n\n"
            "EXECUTION CONTRACT (mandatory):\n"
            "1. Start with the direct final result, not methodology.\n"
            "2. If task is empirical, actually execute tools/code and use fetched/computed data.\n"
            "3. Include concise evidence: data source/time range, concrete metrics/values, "
            "and artifact path or runnable command.\n"
            "4. Do not return only a plan, recommendation list, or generic template.\n\n"
            "5. Include an `Evidence` section with explicit fields: sources, metrics, artifacts, commands.\n\n"
            "6. If execution fails due to missing dependencies/tools, install/fix environment and retry before responding.\n\n"
            f"Produce a COMPLETE, CORRECT solution that fully addresses the feedback.\n"
            f"{'━' * 60}"
        )

    def _aggregate_results(
        self,
        local_result: dict,
        remote_results: list[dict],
        prompt: str = "",
        task_id: str = "",
    ) -> dict:
        """Organizer aggregates results from all selected members
        (local CrewAI + remote ANs) and returns a unified result to the user.

        - Single source: uses the Organizer's LLM to synthesize a clear
          answer/conclusion from the workers' output.
        - Multiple sources: uses the Organizer's LLM to merge into ONE fluid,
          concrete response. The merge prioritizes actual deliverables (code,
          data, predictions) over generic templates (Summary/Key Findings).
          Lead with the answer, not the plan.
        """
        cfg = get_config()
        # Build a combined result string
        parts = []

        # Local result (only include if Coordinator actually executed)
        local_status = local_result.get("status", "")
        local_text = local_result.get("result", "")
        if local_text and local_status != "skipped":
            parts.append(f"## Local Team Result\n\n{local_text}")

        # Remote results
        for r in remote_results:
            an_name = r.get("assigned_to", "Remote AN")
            if r.get("status") == "success":
                parts.append(f"## {an_name} Result\n\n{r.get('result', '')}")
            else:
                parts.append(
                    f"## {an_name} (FAILED)\n\nError: {r.get('error', 'unknown')}"
                )

        raw_combined = "\n\n---\n\n".join(parts) if parts else local_text

        # --- Single source: pass through directly, strip redundant headers ---
        if len(parts) == 1:
            unified = raw_combined
            # Remove "## Local Team Result" or "## {AN name} Result" header for cleaner output
            for prefix in ("## Local Team Result\n\n", "## Local Team Result\n"):
                if unified.startswith(prefix):
                    unified = unified[len(prefix):].strip()
                    break
            else:
                # Match "## {anything} Result\n\n" (e.g. "## 127.0.0.1:8000 Result")
                m = re.match(r'^## .+ Result\s*\n+', unified)
                if m:
                    unified = unified[m.end():].strip()
        else:
            unified = raw_combined

        # --- LLM synthesis: always run for any result ---
        ctx_max = cfg.api.aggregate_context_max_chars
        out_max = cfg.api.aggregate_output_max_chars
        llm_min = cfg.api.aggregate_llm_min_response
        if parts:
            try:
                organizer = self.organizer or (self.agents[0] if self.agents else None)
                llm = getattr(organizer, "llm", None) if organizer else None
                if llm and hasattr(llm, "call"):
                    question_section = (
                        f"USER QUESTION: {prompt}\n\n" if prompt else ""
                    )
                    if len(parts) > 1:
                        task_desc = (
                            f"Merge results from {len(parts)} agents into ONE fluid, "
                            "concrete response."
                        )
                    else:
                        task_desc = (
                            "Based on what the agent team completed, provide a clear, "
                            "direct answer to the user's question."
                        )
                    output_files_str = ""
                    if task_id:
                        try:
                            from teaming24.task.output import get_output_manager
                            filenames = get_output_manager().list_workspace_filenames(task_id)
                            output_files_str = ", ".join(filenames) if filenames else "(none yet)"
                        except Exception as _e:
                            logger.debug("Could not list workspace files for synthesis: %s", _e)
                    summary_prompt = render_prompt(
                        "core.organizer_summary_synthesis",
                        task_desc=task_desc,
                        question_section=question_section,
                        out_max=out_max,
                        agent_output=raw_combined[:ctx_max],
                        task_id=task_id or "",
                        output_files=output_files_str,
                    )
                    resp = llm.call([{"role": "user", "content": summary_prompt}])
                    if resp and isinstance(resp, str) and len(resp) > llm_min:
                        unified = resp
                        logger.info(
                            f"[Organizer] Synthesized response: "
                            f"{len(unified)} chars from {len(parts)} source(s)"
                        )
                    else:
                        logger.warning("[Organizer] LLM response too short, using raw output")
            except Exception as e:
                logger.warning(f"[Organizer] Failed to synthesize response: {e}")

        # Fallback: when unified is empty or too short but we have raw content, use it
        # (never return empty when workers produced output)
        fallback_min = cfg.api.result_fallback_min_chars
        empty_thresh = cfg.api.result_empty_threshold
        if (not unified or len(unified.strip()) < fallback_min) and raw_combined and len(raw_combined.strip()) > empty_thresh:
            unified = raw_combined
            # Strip headers for cleaner output when using raw fallback
            for prefix in ("## Local Team Result\n\n", "## Local Team Result\n"):
                if unified.startswith(prefix):
                    unified = unified[len(prefix):].strip()
                    break
            else:
                m = re.match(r'^## .+ Result\s*\n+', unified)
                if m:
                    unified = unified[m.end():].strip()
            logger.info("[Organizer] Using raw output fallback (unified was empty/short)")

        # Use the best available status for the overall result
        overall_status = local_status if local_status not in ("skipped", "") else "success"
        if any(r.get("status") == "error" for r in remote_results):
            if local_status in ("skipped", ""):
                overall_status = "partial"

        return {
            **local_result,
            "status": overall_status,
            "result": unified,
            "remote_results": remote_results,
        }

    def _emit_an_status_summary(
        self,
        task_id: str,
        local_result: dict,
        remote_results: list[dict],
    ) -> None:
        """Emit a step summarizing each AN's execution result for user tracking in thinking.

        Shows success/failure per AN so users can see which nodes succeeded,
        which failed, and why (e.g. no network, timeout).
        """
        lines: list[str] = []
        local_status = (local_result or {}).get("status", "")
        if local_status == "error":
            err = (local_result or {}).get("error", "unknown")
            lines.append(f"• Local Team: failed — {str(err)[:200]}")
        elif local_status not in ("", "skipped"):
            lines.append("• Local Team: success")
        for r in remote_results or []:
            an_name = r.get("assigned_to", "Remote AN")
            r_status = r.get("status", "unknown")
            if r_status == "success":
                lines.append(f"• {an_name}: success")
            else:
                err = r.get("error", "unknown")
                lines.append(f"• {an_name}: failed — {str(err)[:200]}")
        if not lines:
            return
        content = "AN execution summary:\n" + "\n".join(lines)
        self._wf_step(
            task_id,
            "Organizer",
            "an_status_summary",
            content,
            agent_type="organizer",
        )


def create_local_crew(task_manager: TaskManager = None,
                      on_step: Callable[[dict], None] = None,
                      runtime_settings: dict[str, Any] = None) -> LocalCrew:
    """
    Factory function for creating a local crew.

    Args:
        task_manager: Task manager for tracking
        on_step: Callback for step events
        runtime_settings: Optional settings from database/frontend to override defaults
            - crewaiVerbose: bool - Enable verbose mode for tracking
            - crewaiProcess: str - 'sequential' or 'hierarchical'
            - crewaiMemory: bool - Enable memory across executions
            - crewaiMaxRpm: int - Max requests per minute
            - agentScenario: str - Worker agent scenario
    """
    return LocalCrew(task_manager, on_step, runtime_settings=runtime_settings)


def check_crewai_available() -> bool:
    """Check if CrewAI is installed and available."""
    return CREWAI_AVAILABLE


def check_agent_framework_available() -> bool:
    """Check if any agent framework (native or CrewAI) is available.

    The native adapter only needs ``litellm``, which is a core dependency.
    CrewAI is optional — the native adapter works without it.
    """
    if CREWAI_AVAILABLE:
        return True
    try:
        from teaming24.config import get_config
        if get_config().framework.backend == "native":
            return True
    except Exception as exc:
        logger.warning(
            "Failed to inspect framework backend for availability check: %s",
            exc,
            exc_info=True,
        )
    return False
