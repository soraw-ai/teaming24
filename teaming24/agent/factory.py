"""
AgentFactory — creates CrewAI Agent instances from configuration.

This module provides plug-and-play agent creation: swap the tools registry,
override create_agent logic, or extend with custom agent types without
touching core orchestration.

**What it does:**
- Creates CrewAI Agent instances from config dicts or dataclasses.
- Resolves tools by name from a registry (OpenHands, Network, custom).
- Caches LLM instances per model to avoid repeated construction.
- Injects skill prompts into agent backstories when skill_ids are set.

**How it reads from teaming24.yaml agent definitions:**
- Agent configs live under ``agents.organizer``, ``agents.coordinator``,
  ``agents.workers``, and ``agents.scenarios.<name>.agents``.
- Each config is converted via ``config_to_dict()`` and passed to
  ``create_agent()``. Supported fields: role, goal, backstory, tools,
  model, allow_delegation, allow_code_execution, reasoning, memory, etc.
- Defaults come from ``agents.defaults`` (model, allow_delegation, etc.).

**How tool registration works:**
- ``tools_registry`` maps tool names (e.g., "shell_command", "file_read")
  to CrewAI tool instances.
- ``_register_all_tools()`` auto-registers OpenHands and Network tools
  when ``auto_register_tools=True``.
- Extend: call ``register_tool(name, tool)`` or pass a pre-populated
  ``tools_registry`` to the constructor. Agents reference tools by name
  in their config; missing tools are logged but do not block creation.

**How to extend with custom agent creation logic:**
1. Subclass AgentFactory and override ``create_agent()`` for custom behavior.
2. Pass a custom ``tools_registry`` to add/override tools.
3. Use ``create_scenario_agents(scenario_name)`` for scenario-specific agents.
4. Override ``_inject_skills()`` to change skill prompt injection.

**Usage examples:**
    # Create agents from config (used by core.py / LocalCrew)
    factory = AgentFactory()
    organizer = factory.create_organizer(step_callback=cb)
    workers = factory.create_workers(step_callback=cb)

    # Custom tools + agent
    factory = AgentFactory(tools_registry={"my_tool": MyTool()})
    agent = factory.create_agent({"role": "Analyst", "goal": "...", "tools": ["my_tool"]})

    # Scenario-based agents
    agents = factory.create_scenario_agents("research", step_callback=cb)
"""

from collections.abc import Callable
from typing import Any

from teaming24.config import get_config
from teaming24.llm.model_resolver import (
    build_runtime_llm_config,
    resolve_model_and_call_params,
)
from teaming24.utils.logger import get_logger
from teaming24.utils.shared import config_to_dict

logger = get_logger(__name__)

# Lazy CrewAI imports
CREWAI_AVAILABLE = False
Agent = None
LLM = None

try:
    from crewai import LLM, Agent
    CREWAI_AVAILABLE = True
except ImportError:
    logger.debug("CrewAI not available in AgentFactory")
    pass


class AgentFactory:
    """Factory for creating CrewAI agents from configuration.

    Based on CrewAI v1.9.0+ Agent class which uses:
    - role, goal, backstory for agent definition
    - tools list for capabilities (resolved from tools_registry)
    - llm for language model (can be string or LLM instance)
    - allow_delegation, allow_code_execution for permissions
    - step_callback for streaming updates

    Plug-and-play: inject a custom tools_registry, subclass to override
    create_agent, or use create_organizer/coordinator/workers for standard
    hierarchy from teaming24.yaml.
    """

    def __init__(
        self,
        tools_registry: dict[str, Any] = None,
        auto_register_tools: bool = True,
        runtime_default_provider: str | None = None,
        runtime_default_model: str | None = None,
        runtime_settings: dict[str, Any] | None = None,
    ):
        """Initialize the factory.

        Args:
            tools_registry: Optional dict of name -> tool; extended by _register_all_tools.
            auto_register_tools: If True, register OpenHands and Network tools.
        """
        self.tools_registry = tools_registry or {}
        self.config = get_config()
        self._llm_cache: dict[str, Any] = {}
        self._runtime_default_provider = (runtime_default_provider or "").strip() or None
        self._runtime_default_model = (runtime_default_model or "").strip() or None
        self._runtime_settings = runtime_settings or {}
        if auto_register_tools:
            self._register_all_tools()

    def register_tool(self, name: str, tool: Any):
        """Register a tool by name for use in agent configs."""
        self.tools_registry[name] = tool

    def _register_all_tools(self):
        try:
            from teaming24.agent.tools.openhands_tools import (
                BrowserTool,
                FileReadTool,
                FileWriteTool,
                PythonInterpreterTool,
                ShellCommandTool,
                check_openhands_tools_available,
            )
            if check_openhands_tools_available():
                logger.info("Registering OpenHands tools")
                self.tools_registry["shell_command"] = ShellCommandTool()
                self.tools_registry["shell"] = self.tools_registry["shell_command"]
                self.tools_registry["file_read"] = FileReadTool()
                self.tools_registry["file_write"] = FileWriteTool()
                self.tools_registry["python_interpreter"] = PythonInterpreterTool()
                self.tools_registry["python"] = self.tools_registry["python_interpreter"]
                self.tools_registry["browser"] = BrowserTool()
            else:
                logger.debug("OpenHands tools not available")
        except ImportError as e:
            logger.debug(f"Could not import OpenHands tools: {e}")

        try:
            from teaming24.agent.tools.network_tools import (
                DelegateToNetworkTool,
                SearchNetworkTool,
            )
            logger.info("Registering Network tools")
            self.tools_registry["delegate_to_network"] = DelegateToNetworkTool()
            self.tools_registry["search_network"] = SearchNetworkTool()
        except Exception as e:
            logger.warning("Could not register Network tools: %s", e, exc_info=True)

        try:
            from teaming24.agent.tools.memory_tools import (
                CREWAI_AVAILABLE as MEMORY_TOOL_CREWAI_AVAILABLE,
                MemorySaveTool,
                MemorySearchTool,
            )
            if MEMORY_TOOL_CREWAI_AVAILABLE:
                self.tools_registry["memory_search"] = MemorySearchTool()
                self.tools_registry["memory_save"] = MemorySaveTool()
                logger.info("Registering Memory tools")
            else:
                logger.debug("CrewAI memory tools not available")
        except Exception as e:
            logger.warning("Could not register Memory tools: %s", e, exc_info=True)

        # OpenClaw tools — only when extensions.openclaw.enabled is True
        try:
            oc_cfg = (
                self.config.extensions.get("openclaw", {})
                if isinstance(getattr(self.config, "extensions", None), dict)
                else {}
            )
            if oc_cfg.get("enabled", False):
                from teaming24.agent.tools.openclaw_tools import (
                    create_openclaw_tools,
                )
                for tool in create_openclaw_tools():
                    self.tools_registry[tool.name] = tool
                logger.info("OpenClaw tools registered")
            else:
                logger.debug("OpenClaw tools skipped (extensions.openclaw.enabled=false or not set)")
        except Exception as e:
            logger.warning("[AgentFactory] Could not register OpenClaw tools: %s", e, exc_info=True)

        logger.info(f"Registered {len(self.tools_registry)} tools: {list(self.tools_registry.keys())}")

    def _create_llm(self, model: str) -> Any:
        """Create or return cached LLM instance for the given model string."""
        llm_config = self.config.llm
        model_input = str(model or "").strip()

        if not model_input and self._runtime_default_model:
            model_input = self._runtime_default_model

        llm_config_for_resolution = build_runtime_llm_config(
            llm_config,
            runtime_settings=self._runtime_settings,
            runtime_default_provider=self._runtime_default_provider,
        )

        resolved_model, llm_call_params, provider = resolve_model_and_call_params(
            model_input,
            llm_config_for_resolution,
        )
        if resolved_model in self._llm_cache:
            return self._llm_cache[resolved_model]
        if not CREWAI_AVAILABLE or LLM is None:
            return resolved_model

        providers = getattr(llm_config_for_resolution, "providers", {}) if llm_config_for_resolution else {}
        provider_config = providers.get(provider, {}) if isinstance(providers, dict) else {}
        provider_config = provider_config if isinstance(provider_config, dict) else {}

        try:
            llm_kwargs: dict[str, Any] = {
                "model": resolved_model,
                "temperature": provider_config.get("temperature", 0.7),
                "max_tokens": provider_config.get("max_tokens"),
            }
            if llm_call_params.get("api_base"):
                # CrewAI's LLM wrapper accepts base_url for OpenAI-compatible endpoints.
                llm_kwargs["base_url"] = llm_call_params["api_base"]
            if llm_call_params.get("api_key"):
                llm_kwargs["api_key"] = llm_call_params["api_key"]
            llm_instance = LLM(**llm_kwargs)
            self._llm_cache[resolved_model] = llm_instance
            return llm_instance
        except Exception as e:
            logger.warning(
                "[AgentFactory] Could not create LLM instance for %s: %s; using model string",
                resolved_model,
                e,
                exc_info=True,
            )
            return resolved_model

    def create_agent(self, agent_config: dict, model: str = None,
                     step_callback: Callable = None) -> Any | None:
        """Create a CrewAI Agent from config dict.

        Resolves tools from tools_registry, applies defaults from
        config.agents.defaults, injects skills if skill_ids present.
        """
        if not CREWAI_AVAILABLE:
            logger.error("CrewAI not available")
            return None

        defaults_config = self.config.agents.defaults
        defaults = self._config_to_dict(defaults_config) if defaults_config else {}

        tool_names = agent_config.get("tools", [])
        tools = [self.tools_registry[t] for t in tool_names if t in self.tools_registry]
        if tool_names:
            resolved = [t for t in tool_names if t in self.tools_registry]
            missing = [t for t in tool_names if t not in self.tools_registry]
            logger.info(f"Agent '{agent_config.get('role', '?')}' tools: resolved={resolved}, missing={missing}")

        llm_model = model or agent_config.get("model", defaults.get("model", "gpt-4"))
        llm = self._create_llm(llm_model)

        backstory = agent_config.get("backstory", "A helpful assistant")
        backstory = self._inject_skills(agent_config, backstory)

        agent_kwargs = {
            "role": agent_config.get("role", "Assistant"),
            "goal": agent_config.get("goal", "Help complete tasks"),
            "backstory": backstory,
            "tools": tools,
            "llm": llm,
            "allow_delegation": agent_config.get(
                "allow_delegation", defaults.get("allow_delegation", True)
            ),
            "verbose": False,
        }

        if step_callback:
            agent_kwargs["step_callback"] = step_callback
        if agent_config.get("allow_code_execution"):
            agent_kwargs["allow_code_execution"] = True
            agent_kwargs["code_execution_mode"] = agent_config.get("code_execution_mode", "safe")
        if agent_config.get("max_iter"):
            agent_kwargs["max_iter"] = agent_config["max_iter"]
        if agent_config.get("max_execution_time"):
            agent_kwargs["max_execution_time"] = agent_config["max_execution_time"]
        if agent_config.get("reasoning", False):
            agent_kwargs["reasoning"] = True
            if agent_config.get("max_reasoning_attempts"):
                agent_kwargs["max_reasoning_attempts"] = agent_config["max_reasoning_attempts"]
        if agent_config.get("memory", False):
            agent_kwargs["memory"] = True
        rcw = agent_config.get("respect_context_window")
        if rcw is not None:
            agent_kwargs["respect_context_window"] = bool(rcw)

        return Agent(**agent_kwargs)

    def _config_to_dict(self, config_obj) -> dict:
        """Convert config dataclass/object to dict for create_agent."""
        return config_to_dict(config_obj)

    def _inject_skills(self, agent_config: dict, backstory: str) -> str:
        """Append skill prompt section to backstory if agent has skills."""
        agent_id = agent_config.get("id", "")
        skill_ids = agent_config.get("skill_ids", [])
        if not agent_id and not skill_ids:
            return backstory
        try:
            from teaming24.agent.skills import get_skill_registry
            from teaming24.agent.skills.prompt import build_skill_system_prompt_section
            from teaming24.data.database import get_database

            registry = get_skill_registry()
            if not registry.list_all():
                registry.load()
            if not skill_ids and agent_id:
                db = get_database()
                skill_ids = db.get_agent_skill_ids(agent_id)
            if not skill_ids:
                return backstory
            skills = registry.get_for_agent(skill_ids)
            if not skills:
                return backstory
            section = build_skill_system_prompt_section(skills)
            if section:
                return f"{backstory}\n\n{section}"
        except Exception as exc:
            logger.debug("[AgentFactory] skill injection skipped: %s", exc)
        return backstory

    def create_organizer(self, step_callback: Callable = None, model: str | None = None) -> Any | None:
        """Create the Organizer agent from config.agents.organizer."""
        organizer_config = self.config.agents.organizer
        config = self._config_to_dict(organizer_config)
        if not config.get("enabled", True):
            return None
        return self.create_agent(config, model=model, step_callback=step_callback)

    def create_coordinator(self, step_callback: Callable = None, model: str | None = None) -> Any | None:
        """Create the Coordinator agent from config.agents.coordinator."""
        coordinator_config = self.config.agents.coordinator
        config = self._config_to_dict(coordinator_config)
        if not config.get("enabled", True):
            return None
        return self.create_agent(config, model=model, step_callback=step_callback)

    def create_workers(self, step_callback: Callable = None) -> list[Any]:
        """Create Worker agents from config.agents.workers (or dev/prod_workers)."""
        workers = []
        worker_configs = self.config.agents.workers or []
        for worker_config in worker_configs:
            config = self._config_to_dict(worker_config) if not isinstance(worker_config, dict) else worker_config
            if config.get("enabled", True):
                agent = self.create_agent(config, step_callback=step_callback)
                if agent:
                    workers.append(agent)
        return workers

    def create_scenario_agents(self, scenario_name: str,
                               step_callback: Callable = None) -> list[Any]:
        """Create agents from config.agents.scenarios.<scenario_name>.agents."""
        scenarios = self.config.agents.scenarios or {}
        scenario = scenarios.get(scenario_name, {})
        agents = []
        for agent_config in scenario.get("agents", []):
            config = self._config_to_dict(agent_config) if not isinstance(agent_config, dict) else agent_config
            agent = self.create_agent(config, step_callback=step_callback)
            if agent:
                agents.append(agent)
        return agents
