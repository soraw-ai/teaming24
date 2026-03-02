"""
Teaming24 Runtime Manager - Unified Execution Environment for AI Agents.

This module provides a centralized runtime management system that aligns with
the OpenHands SDK design pattern while leveraging Teaming24's native sandbox
infrastructure. It serves as the primary interface for AI agents to execute
code, run tests, and interact with isolated environments.

Design Philosophy:
    The RuntimeManager follows OpenHands SDK principles:
    - Sandbox-first: All agent code execution happens in isolated containers
    - Configuration-driven: Runtime behavior adapts to environment settings
    - Agent-aware: Provides methods for agents to query sandbox capabilities
    - Hot containers: Persistent sandboxes for fast, stateful execution

Architecture:
    ┌─────────────────────────────────────────────────────────────────────┐
    │                      RuntimeManager (Singleton)                      │
    │  ┌─────────────────────────────────────────────────────────────────┐│
    │  │                    Configuration Layer                          ││
    │  │  - Reads from teaming24.yaml and environment variables          ││
    │  │  - Determines default runtime (openhands, sandbox, local)        ││
    │  │  - Manages OpenHands integration settings                       ││
    │  └─────────────────────────────────────────────────────────────────┘│
    │  ┌─────────────────────────────────────────────────────────────────┐│
    │  │                    Runtime Selection                            ││
    │  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐          ││
    │  │  │   Sandbox    │  │  OpenHands   │  │    Local     │          ││
    │  │  │  (Default)   │  │   Runtime    │  │  (Dev Only)  │          ││
    │  │  └──────────────┘  └──────────────┘  └──────────────┘          ││
    │  └─────────────────────────────────────────────────────────────────┘│
    │  ┌─────────────────────────────────────────────────────────────────┐│
    │  │                    Agent Interface                              ││
    │  │  - get_runtime()     : Get configured runtime instance          ││
    │  │  - execute()         : Run shell commands                       ││
    │  │  - run_code()        : Execute code (Python, JS, Bash)          ││
    │  │  - run_tests()       : Execute test scripts                     ││
    │  │  - get_capabilities(): Query available runtime features         ││
    │  └─────────────────────────────────────────────────────────────────┘│
    └─────────────────────────────────────────────────────────────────────┘

OpenHands SDK Alignment:
    This manager aligns with the OpenHands SDK pattern by providing:

    1. Consistent Interface: Same methods work regardless of backend
       - execute(command) -> CommandResult
       - run_code(code, language) -> ExecResult
       - read_file(path) -> str
       - write_file(path, content) -> bool

    2. Event-Driven Design: Runtime events can be captured and streamed
       - Command execution events
       - Test result events
       - Resource usage events

    3. Sandbox-First Execution: All untrusted code runs in containers
       - Docker isolation by default
       - Optional OpenHands runtime for advanced features
       - Local mode only for trusted development

Configuration (teaming24.yaml):
    runtime:
      default: "openhands"        # openhands (Docker isolated), sandbox, local
      sandbox_pool:
        min_size: 0
        max_size: 10
        idle_timeout: 300
      openhands:
        enabled: true
        runtime_type: "docker"
        container_image: "ghcr.io/openhands/agent-server:latest-python"

Usage:
    # Get the global runtime manager
    from teaming24.runtime.manager import get_runtime_manager

    manager = get_runtime_manager()

    # Execute a command in sandbox
    result = await manager.execute("python test_script.py")

    # Run code in specific language
    result = await manager.run_code("print('hello')", language="python")

    # Run tests with isolated environment
    result = await manager.run_tests("pytest tests/", timeout=300)

    # Check capabilities
    caps = manager.get_capabilities()
    if caps.get("browser"):
        await manager.browse("https://example.com")

Agent Integration:
    Agents can query the runtime manager to understand available tools:

    @tool
    def execute_in_sandbox(command: str) -> str:
        manager = get_runtime_manager()
        result = manager.execute_sync(command)
        return result.stdout if result.exit_code == 0 else result.stderr

References:
    - OpenHands SDK: https://docs.openhands.dev/sdk
    - OpenHands Runtime: https://docs.openhands.dev/openhands/usage/run-openhands
    - Teaming24 Sandbox: teaming24.runtime.sandbox

Author: Teaming24 Team
"""

from __future__ import annotations

import asyncio
import threading
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from teaming24.config import get_config
from teaming24.utils.logger import get_logger
from teaming24.utils.shared import SingletonMixin

logger = get_logger(__name__)


class RuntimeBackend(Enum):
    """Available runtime backends."""
    SANDBOX = "sandbox"       # Teaming24 native sandbox (Docker-based)
    OPENHANDS = "openhands"   # OpenHands SDK runtime
    LOCAL = "local"           # Local execution (development only)


@dataclass
class RuntimeCapabilities:
    """Runtime capability descriptor.

    Describes what features are available in the current runtime.
    Agents can query this to determine available tools.
    """
    shell: bool = True                # Shell command execution
    file_read: bool = True            # Read files from workspace
    file_write: bool = True           # Write files to workspace
    python: bool = True               # Python code execution
    javascript: bool = False          # JavaScript/Node.js execution
    browser: bool = False             # Browser automation (Playwright)
    code_interpreter: bool = True     # IPython/Jupyter support
    vnc: bool = False                 # VNC screen sharing
    cdp: bool = False                 # Chrome DevTools Protocol
    metrics: bool = True              # System metrics collection
    isolation: bool = True            # Container isolation

    def to_dict(self) -> dict[str, bool]:
        """Convert to dictionary."""
        return {
            "shell": self.shell,
            "file_read": self.file_read,
            "file_write": self.file_write,
            "python": self.python,
            "javascript": self.javascript,
            "browser": self.browser,
            "code_interpreter": self.code_interpreter,
            "vnc": self.vnc,
            "cdp": self.cdp,
            "metrics": self.metrics,
            "isolation": self.isolation,
        }

    def available_tools(self) -> list[str]:
        """Get list of available tool names."""
        tools = []
        if self.shell:
            tools.extend(["shell_command", "shell"])
        if self.file_read:
            tools.extend(["file_read", "read_file"])
        if self.file_write:
            tools.extend(["file_write", "write_file"])
        if self.python:
            tools.extend(["python_interpreter", "python", "code"])
        if self.browser:
            tools.extend(["browser", "browse"])
        return tools


@dataclass
class RuntimeConfig:
    """Configuration for RuntimeManager.

    Loaded from teaming24.yaml and environment variables.
    """
    default_backend: RuntimeBackend = RuntimeBackend.SANDBOX
    workspace_path: str = "/workspace"
    timeout: int = 300

    # Sandbox pool settings
    pool_min_size: int = 0
    pool_max_size: int = 10
    pool_idle_timeout: int = 300

    # OpenHands settings
    openhands_enabled: bool = True
    openhands_runtime_type: str = "docker"
    openhands_image: str = "ghcr.io/openhands/agent-server:latest-python"

    # Sandbox settings
    sandbox_image: str = "ghcr.io/agent-infra/sandbox:latest"
    sandbox_enable_browser: bool = True
    sandbox_enable_vnc: bool = False


class RuntimeManager(SingletonMixin):
    """Centralized runtime management for AI agents.

    Provides a unified interface for code execution across different
    runtime backends (Sandbox, OpenHands, Local). Follows OpenHands SDK
    patterns while leveraging Teaming24's native infrastructure.

    Features:
        - Sandbox-first execution (all code runs in isolated containers)
        - Configuration-driven backend selection
        - Hot sandbox pool for persistent containers
        - Agent-friendly capability queries
        - OpenHands SDK compatibility layer

    Example:
        manager = get_runtime_manager()

        # Execute shell command
        result = await manager.execute("ls -la")

        # Run Python code
        result = await manager.run_code("print(1+1)", "python")

        # Check if browser is available
        if manager.capabilities.browser:
            await manager.browse("https://example.com")
    """

    def __init__(self, config: RuntimeConfig = None):
        """Initialize runtime manager.

        Args:
            config: Runtime configuration. If None, loads from teaming24.yaml.
        """
        self._config = config or self._load_config()
        self._sandbox = None
        # OpenHands runtimes are managed by OpenHandsPool (agent-level allocation)
        self._capabilities = None
        self._initialized = False
        self._init_lock = threading.Lock()
        self._event_handlers: list[Callable] = []

    def _load_config(self) -> RuntimeConfig:
        """Load configuration from teaming24.yaml."""
        try:
            config = get_config()
            runtime_cfg = getattr(config, 'runtime', None)

            if runtime_cfg is None:
                logger.debug("No runtime config found, using defaults")
                return RuntimeConfig()

            # Handle both dict and dataclass config
            if hasattr(runtime_cfg, '__dict__'):
                cfg_dict = vars(runtime_cfg)
            elif isinstance(runtime_cfg, dict):
                cfg_dict = runtime_cfg
            else:
                cfg_dict = {}

            # Parse default backend
            default_str = cfg_dict.get('default', 'openhands')
            try:
                default_backend = RuntimeBackend(default_str)
            except ValueError:
                logger.warning(f"Unknown runtime backend: {default_str}, using openhands")
                default_backend = RuntimeBackend.OPENHANDS

            # Parse pool settings
            pool_cfg = cfg_dict.get('sandbox_pool', {})
            if hasattr(pool_cfg, '__dict__'):
                pool_cfg = vars(pool_cfg)

            # Parse OpenHands settings
            oh_cfg = cfg_dict.get('openhands', {})
            if hasattr(oh_cfg, '__dict__'):
                oh_cfg = vars(oh_cfg)

            # Parse sandbox settings
            sb_cfg = cfg_dict.get('sandbox', {})
            if hasattr(sb_cfg, '__dict__'):
                sb_cfg = vars(sb_cfg)

            return RuntimeConfig(
                default_backend=default_backend,
                workspace_path=oh_cfg.get('workspace_path', '/workspace'),
                timeout=oh_cfg.get('timeout', 300),
                pool_min_size=pool_cfg.get('min_size', 0),
                pool_max_size=pool_cfg.get('max_size', 10),
                pool_idle_timeout=pool_cfg.get('idle_timeout', 300),
                openhands_enabled=oh_cfg.get('enabled', True),
                openhands_runtime_type=oh_cfg.get('runtime_type', 'docker'),
                openhands_image=oh_cfg.get('container_image', 'ghcr.io/openhands/agent-server:latest-python'),
                sandbox_image=sb_cfg.get('docker_image', 'ghcr.io/agent-infra/sandbox:latest'),
                sandbox_enable_browser=sb_cfg.get('enable_browser', True),
                sandbox_enable_vnc=sb_cfg.get('enable_vnc', False),
            )

        except Exception as e:
            logger.warning(f"Failed to load runtime config: {e}, using defaults")
            return RuntimeConfig()

    async def initialize(self) -> RuntimeManager:
        """Initialize the runtime manager and default backend.

        This method is idempotent and uses an async lock to prevent
        concurrent initialization from multiple coroutines.

        Returns:
            Self for chaining.
        """
        if self._initialized:
            return self

        with self._init_lock:
            # Double-check after acquiring lock
            if self._initialized:
                return self

            logger.info(f"Initializing RuntimeManager with backend: {self._config.default_backend.value}")

            # Check if configured backend is actually available
            if self._config.default_backend == RuntimeBackend.OPENHANDS:
                try:
                    from teaming24.runtime.openhands import OPENHANDS_AVAILABLE, get_openhands_mode
                    if not OPENHANDS_AVAILABLE:
                        logger.warning(
                            "⚠ Runtime backend configured as 'openhands' but OpenHands is NOT installed.\n"
                            "  Install with: uv pip install openhands-sdk openhands-tools\n"
                            "  Or: pip install openhands-sdk openhands-tools\n"
                            "  Falling back to sandbox (Docker) → local execution."
                        )
                    else:
                        mode = get_openhands_mode()
                        logger.info(f"OpenHands available in mode: {mode}")
                except ImportError:
                    logger.warning(
                        "⚠ Runtime backend configured as 'openhands' but import failed.\n"
                        "  Will fall back to sandbox → local."
                    )

            # Initialize capabilities based on backend
            self._capabilities = self._detect_capabilities()

            # Pre-initialize sandbox pool if using sandbox backend
            if self._config.default_backend == RuntimeBackend.SANDBOX:
                try:
                    from teaming24.runtime.sandbox import get_pool
                    get_pool()
                    logger.info(f"Sandbox pool ready (max_size={self._config.pool_max_size})")
                except Exception as e:
                    logger.warning(f"Failed to initialize sandbox pool: {e}")

            # Crash-recovery: clean up orphaned containers from previous runs
            try:
                from teaming24.runtime.sandbox.docker import cleanup_teaming24_containers
                removed = await cleanup_teaming24_containers(force=True)
                if removed:
                    logger.info(f"Crash recovery: removed {removed} orphaned container(s)")
            except Exception as e:
                logger.debug(f"Crash recovery cleanup skipped: {e}")

            self._initialized = True
            self._emit_event("runtime_initialized", {"backend": self._config.default_backend.value})
            return self

    async def shutdown(self):
        """Shutdown runtime manager and cleanup resources."""
        if not self._initialized:
            return

        logger.info("Shutting down RuntimeManager")

        # Cleanup sandbox
        if self._sandbox:
            try:
                await self._sandbox.stop()
            except Exception as e:
                logger.debug(f"Error stopping sandbox: {e}")
            self._sandbox = None

        # Cleanup all OpenHands runtimes via pool
        try:
            from teaming24.runtime.openhands import OPENHANDS_AVAILABLE, cleanup_all_openhands
            if OPENHANDS_AVAILABLE:
                await cleanup_all_openhands()
        except ImportError as e:
            logger.debug("OpenHands cleanup import failed: %s", e)
        except Exception as e:
            logger.debug(f"Error cleaning up OpenHands pool: {e}")

        self._initialized = False
        self._emit_event("runtime_shutdown", {})

    def _detect_capabilities(self) -> RuntimeCapabilities:
        """Detect available capabilities based on configuration."""
        caps = RuntimeCapabilities()

        if self._config.default_backend == RuntimeBackend.SANDBOX:
            caps.browser = self._config.sandbox_enable_browser
            caps.vnc = self._config.sandbox_enable_vnc
            caps.cdp = self._config.sandbox_enable_browser
            caps.javascript = True
        elif self._config.default_backend == RuntimeBackend.OPENHANDS:
            caps.browser = True
            caps.code_interpreter = True
        elif self._config.default_backend == RuntimeBackend.LOCAL:
            caps.isolation = False
            caps.vnc = False
            caps.cdp = False

        return caps

    @property
    def capabilities(self) -> RuntimeCapabilities:
        """Get current runtime capabilities."""
        if self._capabilities is None:
            self._capabilities = self._detect_capabilities()
        return self._capabilities

    @property
    def config(self) -> RuntimeConfig:
        """Get runtime configuration."""
        return self._config

    @property
    def backend(self) -> RuntimeBackend:
        """Get current backend type."""
        return self._config.default_backend

    @property
    def is_sandbox(self) -> bool:
        """Check if using sandbox backend."""
        return self._config.default_backend == RuntimeBackend.SANDBOX

    @property
    def is_initialized(self) -> bool:
        """Check if manager is initialized."""
        return self._initialized

    # =========================================================================
    # Runtime Acquisition
    # =========================================================================

    async def get_sandbox(self, agent_id: str = "default") -> Any:
        """Get or create a sandbox instance for an agent.

        Uses hot sandbox pool for efficient container reuse.
        When task context is set, uses task workspace so all workers share files.

        Args:
            agent_id: Unique identifier for the agent

        Returns:
            Sandbox instance
        """
        from teaming24.runtime.sandbox import Sandbox, get_pool
        from teaming24.runtime.sandbox.docker import get_docker_availability
        from teaming24.runtime.types import RuntimeMode

        # Use task workspace when in task context (shared files across workers)
        task_id, task_workspace = (None, None)
        try:
            from teaming24.runtime.task_context import get_task_context
            task_id, task_workspace = get_task_context()
        except ImportError:
            pass

        effective_agent_id = agent_id
        workspace_override = None
        if task_id and task_workspace:
            effective_agent_id = task_id
            workspace_override = task_workspace

        try:
            pool = get_pool()
            sandbox = await pool.acquire(effective_agent_id, workspace=workspace_override)
            self._emit_event("sandbox_acquired", {"agent_id": agent_id})
            return sandbox
        except Exception as e:
            docker_ok, docker_reason = get_docker_availability()
            if not docker_ok:
                logger.info(
                    "Docker sandbox unavailable for %s; using local sandbox fallback: %s",
                    agent_id,
                    docker_reason,
                )
                sandbox = Sandbox(runtime=RuntimeMode.LOCAL)
                await sandbox.start()
                self._emit_event("sandbox_acquired", {"agent_id": agent_id, "fallback": "local"})
                return sandbox

            logger.warning(f"Failed to acquire sandbox from pool: {e}, creating new")
            sandbox = Sandbox()
            await sandbox.start()
            return sandbox

    async def release_sandbox(self, agent_id: str = "default"):
        """Release a sandbox back to the pool.

        Args:
            agent_id: Agent identifier
        """
        from teaming24.runtime.sandbox import get_pool

        try:
            pool = get_pool()
            await pool.release(agent_id)
            self._emit_event("sandbox_released", {"agent_id": agent_id})
        except Exception as e:
            logger.debug(f"Error releasing sandbox: {e}")

    async def get_openhands(self, agent_id: str = "default") -> Any | None:
        """Get OpenHands runtime adapter for an agent.

        Uses pooled allocation for agent-level runtime persistence.
        Each agent gets a dedicated runtime that persists until released
        or program exit.

        Supports both new OpenHands SDK (openhands-sdk) and legacy API (openhands-ai).
        See: https://docs.openhands.dev/sdk/getting-started

        Args:
            agent_id: Unique agent identifier for runtime allocation

        Returns:
            OpenHandsAdapter instance or None if not available
        """
        if not self._config.openhands_enabled:
            return None

        try:
            from teaming24.runtime.openhands import (
                OPENHANDS_AVAILABLE,
                OpenHandsConfig,
                allocate_openhands,
                get_openhands_mode,
            )

            if not OPENHANDS_AVAILABLE:
                logger.debug(
                    "OpenHands not installed. Install with:\n"
                    "  uv pip install openhands-sdk openhands-tools\n"
                    "  Or: pip install openhands-sdk openhands-tools"
                )
                return None

            # Create config with both SDK and legacy compatible settings
            config = OpenHandsConfig(
                workspace_path=self._config.workspace_path,
                timeout=self._config.timeout,
                # Legacy settings (used if SDK not available)
                runtime_type=self._config.openhands_runtime_type,
                container_image=self._config.openhands_image,
            )

            mode = get_openhands_mode()
            logger.debug(f"OpenHands mode: {mode}")

            # Use pool for agent-level allocation
            runtime = await allocate_openhands(agent_id, config)
            if runtime:
                self._emit_event("openhands_connected", {"mode": mode, "agent_id": agent_id})
            return runtime

        except ImportError:
            logger.debug("OpenHands not installed")
            return None
        except Exception as e:
            logger.warning(f"Failed to connect to OpenHands: {e}")
            return None

    async def release_openhands(self, agent_id: str = "default") -> bool:
        """Release OpenHands runtime for an agent.

        Call this when an agent is done using the runtime to free resources.

        Args:
            agent_id: Agent identifier

        Returns:
            True if released successfully
        """
        try:
            from teaming24.runtime.openhands import release_openhands
            result = await release_openhands(agent_id)
            if result:
                self._emit_event("openhands_released", {"agent_id": agent_id})
            return result
        except ImportError as e:
            logger.debug("OpenHands release import failed: %s", e)
            return False
        except Exception as e:
            logger.warning(f"Failed to release OpenHands: {e}")
            return False

    # =========================================================================
    # Execution Methods (OpenHands SDK Compatible)
    # =========================================================================

    async def execute(
        self,
        command: str,
        timeout: int | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        agent_id: str = "default",
    ) -> dict[str, Any]:
        """Execute a shell command in the runtime.

        This is the primary method for agents to execute commands.
        Commands run in sandbox by default for safety.

        Args:
            command: Shell command to execute
            timeout: Command timeout in seconds
            cwd: Working directory
            env: Environment variables
            agent_id: Agent identifier for sandbox allocation

        Returns:
            Dict with exit_code, stdout, stderr
        """
        timeout = timeout or self._config.timeout

        self._emit_event("command_start", {"command": command, "agent_id": agent_id})

        backend = self._config.default_backend
        result = await self._execute_with_fallback(command, timeout, cwd, env, agent_id, backend)
        return result

    async def _execute_with_fallback(
        self,
        command: str,
        timeout: int,
        cwd: str | None,
        env: dict[str, str] | None,
        agent_id: str,
        backend: RuntimeBackend,
    ) -> dict[str, Any]:
        """Execute with automatic fallback if primary backend is unavailable."""
        if backend == RuntimeBackend.SANDBOX:
            return await self._execute_sandbox(command, timeout, cwd, env, agent_id)
        elif backend == RuntimeBackend.OPENHANDS:
            result = await self._execute_openhands(command, timeout, cwd, agent_id)
            # Fallback: if OpenHands is not available, try sandbox then local
            if result.get("exit_code") == -1 and "not available" in result.get("stderr", "").lower():
                logger.info(
                    "OpenHands runtime not available — falling back to sandbox. "
                    "Install: uv pip install openhands-sdk openhands-tools"
                )
                try:
                    return await self._execute_sandbox(command, timeout, cwd, env, agent_id)
                except Exception as e:
                    logger.warning(f"Sandbox fallback also failed: {e} — using local execution")
                    return await self._execute_local(command, timeout, cwd, env)
            return result
        else:
            return await self._execute_local(command, timeout, cwd, env)

    async def _execute_sandbox(
        self,
        command: str,
        timeout: int,
        cwd: str | None,
        env: dict[str, str] | None,
        agent_id: str,
    ) -> dict[str, Any]:
        """Execute command in Teaming24 sandbox."""
        try:
            sandbox = await self.get_sandbox(agent_id)
            result = await sandbox.execute(command, cwd=cwd, timeout=timeout, env=env)

            output = {
                "exit_code": result.exit_code,
                "stdout": result.stdout,
                "stderr": result.stderr,
                "duration": result.duration_ms,
            }
            self._emit_event("command_complete", {"result": output})
            return output

        except Exception as e:
            logger.warning("Sandbox execution failed (%s), falling back to OpenHands", e)
            try:
                return await self._execute_openhands(command, timeout, cwd, agent_id)
            except Exception as oh_err:
                logger.debug("OpenHands fallback failed: %s", oh_err)
            logger.error(f"Sandbox execution error: {e}")
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def _execute_openhands(
        self,
        command: str,
        timeout: int,
        cwd: str | None,
        agent_id: str = "default",
    ) -> dict[str, Any]:
        """Execute command in OpenHands runtime."""
        try:
            runtime = await self.get_openhands(agent_id)
            if runtime is None:
                return {"exit_code": -1, "stdout": "", "stderr": "OpenHands not available"}

            result = await runtime.run_command(command, timeout=timeout, cwd=cwd)

            output = {
                "exit_code": result.get("exit_code", -1),
                "stdout": result.get("output", ""),
                "stderr": result.get("error", ""),
            }
            self._emit_event("command_complete", {"result": output})
            return output

        except Exception as e:
            logger.error(f"OpenHands execution error: {e}")
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    async def _execute_local(
        self,
        command: str,
        timeout: int,
        cwd: str | None,
        env: dict[str, str] | None,
    ) -> dict[str, Any]:
        """Execute command locally (development only)."""
        import subprocess

        try:
            result = subprocess.run(
                command,
                shell=True,
                cwd=cwd,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )

            output = {
                "exit_code": result.returncode,
                "stdout": result.stdout,
                "stderr": result.stderr,
            }
            self._emit_event("command_complete", {"result": output})
            return output

        except subprocess.TimeoutExpired as e:
            logger.debug("Local execution timeout: %s", e)
            return {"exit_code": -1, "stdout": "", "stderr": "Command timed out"}
        except Exception as e:
            logger.exception("Local execution error: %s", e)
            return {"exit_code": -1, "stdout": "", "stderr": str(e)}

    def execute_sync(
        self,
        command: str,
        timeout: int | None = None,
        agent_id: str = "default",
    ) -> dict[str, Any]:
        """Synchronous execution wrapper for agents.

        Args:
            command: Shell command to execute
            timeout: Command timeout in seconds
            agent_id: Agent identifier

        Returns:
            Dict with exit_code, stdout, stderr
        """
        try:
            asyncio.get_running_loop()
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self.execute(command, timeout, agent_id=agent_id)
                )
                return future.result(timeout=(timeout or self._config.timeout) + 10)
        except RuntimeError as e:
            logger.debug("No event loop, using asyncio.run: %s", e)
            return asyncio.run(self.execute(command, timeout, agent_id=agent_id))

    async def run_code(
        self,
        code: str,
        language: str = "python",
        timeout: int | None = None,
        agent_id: str = "default",
    ) -> dict[str, Any]:
        """Execute code in the specified language.

        Args:
            code: Code to execute
            language: Programming language (python, javascript, bash)
            timeout: Execution timeout
            agent_id: Agent identifier

        Returns:
            Dict with output and error
        """
        timeout = timeout or self._config.timeout

        if self._config.default_backend == RuntimeBackend.SANDBOX:
            from teaming24.runtime.sandbox import Language

            try:
                sandbox = await self.get_sandbox(agent_id)

                # Map language string to enum
                lang_map = {
                    "python": Language.PYTHON,
                    "py": Language.PYTHON,
                    "javascript": Language.JAVASCRIPT,
                    "js": Language.JAVASCRIPT,
                    "bash": Language.BASH,
                    "sh": Language.BASH,
                }
                lang = lang_map.get(language.lower(), Language.PYTHON)

                result = await sandbox.run_code(code, language=lang, timeout=timeout)
                return {
                    "output": result.stdout,
                    "error": result.stderr if result.exit_code != 0 else "",
                    "exit_code": result.exit_code,
                }

            except Exception as e:
                logger.warning("Sandbox run_code failed (%s), falling back to OpenHands", e)
                # Fallback: try OpenHands (handsoff sandbox)
                try:
                    runtime = await self.get_openhands()
                    if runtime and language.lower() in ("python", "py"):
                        result = await runtime.run_python(code)
                        return {
                            "output": result.get("output", ""),
                            "error": result.get("error", ""),
                            "exit_code": 0 if not result.get("error") else -1,
                        }
                except Exception as oh_err:
                    logger.debug("OpenHands fallback failed: %s", oh_err)
                logger.exception("Sandbox run_code error: %s", e)
                return {"output": "", "error": str(e), "exit_code": -1}

        elif self._config.default_backend == RuntimeBackend.OPENHANDS:
            runtime = await self.get_openhands()
            if runtime is None:
                logger.info(
                    "OpenHands unavailable for run_code — falling back to sandbox/local."
                )
                # Fallback: try sandbox first, then local
                try:
                    from teaming24.runtime.sandbox import Language
                    sandbox = await self.get_sandbox(agent_id)
                    lang_map = {
                        "python": Language.PYTHON, "py": Language.PYTHON,
                        "javascript": Language.JAVASCRIPT, "js": Language.JAVASCRIPT,
                        "bash": Language.BASH, "sh": Language.BASH,
                    }
                    lang = lang_map.get(language.lower(), Language.PYTHON)
                    result = await sandbox.run_code(code, language=lang, timeout=timeout)
                    return {
                        "output": result.stdout,
                        "error": result.stderr if result.exit_code != 0 else "",
                        "exit_code": result.exit_code,
                    }
                except Exception as fallback_err:
                    logger.warning(f"Sandbox fallback for run_code failed: {fallback_err}")
                    # Fall through to local execution below
            else:
                if language.lower() in ("python", "py"):
                    result = await runtime.run_python(code)
                    return {
                        "output": result.get("output", ""),
                        "error": result.get("error", ""),
                        "exit_code": 0 if not result.get("error") else -1,
                    }
                else:
                    # Fall back to shell for other languages
                    return await self.execute(f"echo '{code}' | {language}", timeout)

        # Local execution (fallback or explicit)
        if language.lower() in ("python", "py"):
            return await self.execute(f"python3 -c '{code}'", timeout)
        elif language.lower() in ("javascript", "js"):
            return await self.execute(f"node -e '{code}'", timeout)
        else:
            return await self.execute(code, timeout)

    async def run_tests(
        self,
        test_command: str,
        timeout: int = 300,
        agent_id: str = "default",
    ) -> dict[str, Any]:
        """Execute test scripts in sandbox.

        Specialized method for running test suites with extended timeout.

        Args:
            test_command: Test command (e.g., "pytest tests/", "npm test")
            timeout: Test timeout in seconds
            agent_id: Agent identifier

        Returns:
            Dict with test results
        """
        self._emit_event("tests_start", {"command": test_command})

        result = await self.execute(test_command, timeout=timeout, agent_id=agent_id)

        # Parse test results if possible
        test_result = {
            **result,
            "passed": result["exit_code"] == 0,
        }

        self._emit_event("tests_complete", {"result": test_result})
        return test_result

    # =========================================================================
    # File Operations
    # =========================================================================

    async def read_file(self, path: str, agent_id: str = "default") -> str:
        """Read file from workspace.

        Args:
            path: File path
            agent_id: Agent identifier

        Returns:
            File contents
        """
        if self._config.default_backend == RuntimeBackend.SANDBOX:
            sandbox = await self.get_sandbox(agent_id)
            return await sandbox.read_file(path)
        elif self._config.default_backend == RuntimeBackend.OPENHANDS:
            runtime = await self.get_openhands()
            if runtime:
                result = await runtime.read_file(path)
                return result.get("content", "")

        # Local fallback
        return Path(path).read_text()

    async def write_file(
        self,
        path: str,
        content: str,
        agent_id: str = "default",
    ) -> bool:
        """Write file to workspace.

        Args:
            path: File path
            content: Content to write
            agent_id: Agent identifier

        Returns:
            True if successful
        """
        if self._config.default_backend == RuntimeBackend.SANDBOX:
            sandbox = await self.get_sandbox(agent_id)
            await sandbox.write_file(path, content)
            return True
        elif self._config.default_backend == RuntimeBackend.OPENHANDS:
            runtime = await self.get_openhands()
            if runtime:
                result = await runtime.write_file(path, content)
                return result.get("success", False)

        # Local fallback — write to the output sandbox, NEVER to project root.
        # If the path is already absolute and inside the output base_dir, use
        # it as-is.  Otherwise resolve it under the output base_dir.
        import os
        try:
            from teaming24.config import get_config
            output_base = os.path.expanduser(get_config().output.base_dir)
        except Exception as e:
            logger.debug(f"Failed to load output config, using default: {e}")
            output_base = os.path.expanduser("~/.teaming24/outputs")

        abs_path = os.path.abspath(path)
        norm_base = os.path.normpath(output_base)
        if not abs_path.startswith(norm_base):
            # Path is outside the output sandbox — redirect it
            safe_name = os.path.basename(path) or "output.txt"
            abs_path = os.path.join(norm_base, "_local_fallback", safe_name)
            logger.warning(
                f"[RuntimeManager] write_file local fallback: "
                f"redirected '{path}' → '{abs_path}' (sandbox enforcement)"
            )

        p = Path(abs_path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content)
        return True

    # =========================================================================
    # Browser Operations
    # =========================================================================

    async def browse(
        self,
        url: str,
        agent_id: str = "default",
    ) -> dict[str, Any]:
        """Browse a URL and get page content.

        Args:
            url: URL to browse
            agent_id: Agent identifier

        Returns:
            Dict with page content
        """
        if not self.capabilities.browser:
            return {"content": "", "error": "Browser not available"}

        if self._config.default_backend == RuntimeBackend.SANDBOX:
            sandbox = await self.get_sandbox(agent_id)
            page_info = await sandbox.goto(url)
            content = await sandbox.get_page_content()
            return {
                "content": content,
                "title": page_info.title,
                "url": page_info.url,
            }
        elif self._config.default_backend == RuntimeBackend.OPENHANDS:
            runtime = await self.get_openhands()
            if runtime:
                result = await runtime.browse_url(url)
                return {
                    "content": result.get("content", ""),
                    "error": result.get("error", ""),
                }

        return {"content": "", "error": "Browser not supported in local mode"}

    # =========================================================================
    # Event System
    # =========================================================================

    def on_event(self, handler: Callable[[str, dict], None]):
        """Register event handler.

        Args:
            handler: Callback function(event_type, data)
        """
        self._event_handlers.append(handler)

    def _emit_event(self, event_type: str, data: dict):
        """Emit runtime event to handlers."""
        for handler in self._event_handlers:
            try:
                handler(event_type, data)
            except Exception as e:
                logger.debug(f"Event handler error: {e}")

    # =========================================================================
    # Agent Information Methods
    # =========================================================================

    def get_capabilities(self) -> dict[str, bool]:
        """Get runtime capabilities as dictionary.

        Use this method in agents to check available features.

        Returns:
            Dict of capability name -> enabled
        """
        return self.capabilities.to_dict()

    def get_available_tools(self) -> list[str]:
        """Get list of available tool names.

        Use this method in agents to determine which tools to register.

        Returns:
            List of tool names
        """
        return self.capabilities.available_tools()

    def get_runtime_info(self) -> dict[str, Any]:
        """Get runtime information for agents.

        Returns:
            Dict with runtime details
        """
        return {
            "backend": self._config.default_backend.value,
            "is_sandbox": self.is_sandbox,
            "initialized": self._initialized,
            "workspace": self._config.workspace_path,
            "timeout": self._config.timeout,
            "capabilities": self.get_capabilities(),
            "openhands_enabled": self._config.openhands_enabled,
        }


# =============================================================================
# Global Instance and Factory Functions
# =============================================================================

_runtime_manager: RuntimeManager | None = None


def get_runtime_manager() -> RuntimeManager:
    """Get the global RuntimeManager instance.

    Returns:
        Singleton RuntimeManager instance
    """
    global _runtime_manager
    if _runtime_manager is None:
        _runtime_manager = RuntimeManager.get_instance()
    return _runtime_manager


def set_runtime_manager(manager: RuntimeManager):
    """Set the global RuntimeManager instance.

    Args:
        manager: RuntimeManager instance to use globally
    """
    global _runtime_manager
    _runtime_manager = manager
    RuntimeManager._instance = manager


async def initialize_runtime(config: RuntimeConfig = None) -> RuntimeManager:
    """Initialize the global runtime manager.

    Args:
        config: Optional runtime configuration

    Returns:
        Initialized RuntimeManager
    """
    manager = RuntimeManager(config) if config else get_runtime_manager()
    await manager.initialize()
    return manager


async def shutdown_runtime():
    """Shutdown the global runtime manager."""
    global _runtime_manager
    if _runtime_manager:
        await _runtime_manager.shutdown()
        _runtime_manager = None
        RuntimeManager.reset_instance()


def check_sandbox_available() -> bool:
    """Check if sandbox runtime is available.

    Returns:
        True if sandbox can be used
    """
    try:
        import teaming24.runtime.sandbox as _sandbox
        return hasattr(_sandbox, "Sandbox")
    except ImportError as e:
        logger.debug("Sandbox import failed: %s", e)
        return False


def check_openhands_available() -> bool:
    """Check if OpenHands runtime is available.

    Returns:
        True if OpenHands SDK or legacy is installed
    """
    try:
        from teaming24.runtime.openhands import (
            OPENHANDS_AVAILABLE,
        )
        return OPENHANDS_AVAILABLE
    except ImportError as e:
        logger.debug("OpenHands check import failed: %s", e)
        return False


__all__ = [
    # Main class
    "RuntimeManager",
    # Data classes
    "RuntimeCapabilities",
    "RuntimeConfig",
    # Enums
    "RuntimeBackend",
    # Factory functions
    "get_runtime_manager",
    "set_runtime_manager",
    "initialize_runtime",
    "shutdown_runtime",
    # Availability checks
    "check_sandbox_available",
    "check_openhands_available",
]
