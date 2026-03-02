"""
OpenHands SDK Adapter for Teaming24.

This module provides integration with the OpenHands SDK for AI agent execution.
It supports multiple modes:

    1. SDK Mode (openhands-sdk): Full AI agent with conversation-based execution
    2. Workspace Mode (openhands-workspace): Direct sandbox execution via Docker
    3. Local Mode: Direct local execution (development only)
    4. Legacy Mode (openhands-ai): Older event-based API for compatibility

OpenHands SDK Installation (uv recommended):
    uv pip install openhands-sdk openhands-tools openhands-workspace
    # Or with pip:
    pip install openhands-sdk openhands-tools openhands-workspace

Reference: https://docs.openhands.dev/sdk/getting-started

Key Concepts:
    - Agent: AI-powered entity using tools to complete tasks
    - Conversation: Manages interaction lifecycle between user and agent
    - Workspace: Execution environment (local, Docker, or remote)
    - Tools: Capabilities like bash execution, file editing, web browsing

Usage Patterns:

    1. Direct Command Execution (recommended for simple commands):
        ```python
        from teaming24.runtime.openhands import OpenHandsAdapter, OpenHandsConfig

        config = OpenHandsConfig(workspace_path="/workspace")
        adapter = OpenHandsAdapter(config)

        async with adapter:
            result = await adapter.run_command("ls -la")
            print(result["output"])
        ```

    2. AI-Assisted Execution (for complex tasks):
        ```python
        from teaming24.runtime.openhands import OpenHandsAdapter, OpenHandsConfig

        config = OpenHandsConfig(
            workspace_path="/workspace",
            model="anthropic/claude-sonnet-4-5-20250929",
        )
        adapter = OpenHandsAdapter(config)

        async with adapter:
            result = await adapter.execute_task(
                "Write a Python script that analyzes CSV files"
            )
        ```

Environment Variables:
    LLM_API_KEY    - API key for LLM provider (required for SDK mode)
    LLM_MODEL      - Model to use (default: anthropic/claude-sonnet-4-5-20250929)
    LLM_BASE_URL   - Custom base URL for LLM provider

Author: Teaming24 Team
"""

import asyncio
import os
import time
from dataclasses import dataclass, field
from typing import Any

from teaming24.utils.ids import prefixed_id, sandbox_id_for_openhands
from teaming24.utils.logger import get_logger
from teaming24.utils.shared import SingletonMixin, sync_async_cleanup

logger = get_logger(__name__)

# Check available OpenHands packages
OPENHANDS_SDK_AVAILABLE = False
OPENHANDS_TOOLS_AVAILABLE = False
OPENHANDS_WORKSPACE_AVAILABLE = False
OPENHANDS_LEGACY_AVAILABLE = False

# Try new OpenHands SDK (openhands-sdk package)
try:
    from openhands.sdk import LLM, Agent, Conversation, Tool
    OPENHANDS_SDK_AVAILABLE = True
    logger.debug("OpenHands SDK available")
except ImportError:
    logger.debug("OpenHands SDK not installed. Install with: uv pip install openhands-sdk")

# Try OpenHands Tools (openhands-tools package)
try:
    from openhands.tools.file_editor import FileEditorTool
    from openhands.tools.terminal import TerminalTool
    OPENHANDS_TOOLS_AVAILABLE = True
    logger.debug("OpenHands Tools available")
except ImportError:
    logger.debug("OpenHands Tools not installed. Install with: uv pip install openhands-tools")

# Try OpenHands Workspace (openhands-workspace package) for Docker sandbox
try:
    import openhands.workspace as _openhands_workspace

    OPENHANDS_WORKSPACE_AVAILABLE = hasattr(_openhands_workspace, "DockerWorkspace")
    if OPENHANDS_WORKSPACE_AVAILABLE:
        logger.debug("OpenHands Workspace available")
except ImportError:
    logger.debug("OpenHands Workspace not installed. Install with: uv pip install openhands-workspace")

# Try legacy OpenHands runtime (openhands-ai package)
try:
    from openhands.events.action import (
        BrowseURLAction,
        CmdRunAction,
        FileReadAction,
        FileWriteAction,
        IPythonRunCellAction,
    )
    from openhands.events.observation import (
        CmdOutputObservation,
        ErrorObservation,
        FileReadObservation,
    )
    from openhands.runtime import get_runtime_cls
    OPENHANDS_LEGACY_AVAILABLE = True
    logger.debug("OpenHands Legacy runtime available")
except ImportError:
    logger.debug("OpenHands Legacy not installed. Install with: uv pip install openhands-ai")

# Overall availability
OPENHANDS_AVAILABLE = (
    OPENHANDS_SDK_AVAILABLE or
    OPENHANDS_WORKSPACE_AVAILABLE or
    OPENHANDS_LEGACY_AVAILABLE
)


@dataclass
class OpenHandsConfig:
    """Configuration for OpenHands runtime.

    Attributes:
        workspace_path: Path to workspace directory
        model: LLM model to use (e.g., anthropic/claude-sonnet-4-5-20250929)
        api_key: LLM API key (defaults to LLM_API_KEY env var)
        base_url: Custom LLM base URL (optional)
        runtime_type: Runtime type for legacy mode (docker, local)
        container_image: Docker image for legacy mode
        timeout: Command timeout in seconds
        headless_mode: Run without UI
    """
    workspace_path: str = "/workspace"
    model: str = "anthropic/claude-sonnet-4-5-20250929"
    api_key: str = None
    base_url: str = None
    runtime_type: str = "docker"
    container_image: str = "ghcr.io/openhands/agent-server:latest-python"
    timeout: int = 120
    headless_mode: bool = True
    enable_auto_lint: bool = True
    enable_jupyter: bool = True
    env_vars: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        # Load API key from environment if not provided
        if self.api_key is None:
            self.api_key = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")

        # Load model from environment if different from default
        env_model = os.getenv("LLM_MODEL")
        if env_model:
            self.model = env_model

        # Load base URL from environment
        if self.base_url is None:
            self.base_url = os.getenv("LLM_BASE_URL")


class OpenHandsAdapter:
    """
    Adapter for OpenHands SDK in Teaming24.

    Provides a unified interface for AI agent execution:
    - Shell command execution
    - File operations (read, write, edit)
    - Code execution (Python, Bash)
    - Browser automation (if available)

    Supports both new SDK and legacy API for compatibility.

    Example:
        config = OpenHandsConfig(workspace_path="./workspace")
        adapter = OpenHandsAdapter(config)

        async with adapter:
            result = await adapter.run_command("python --version")
            print(result["output"])
    """

    def __init__(self, config: OpenHandsConfig = None, sid: str = "teaming24"):
        """Initialize OpenHands adapter.

        Args:
            config: OpenHands configuration
            sid: Session ID for runtime isolation
        """
        self.config = config or OpenHandsConfig()
        self.sid = sid
        self._mode = self._determine_mode()
        self._connected = False

        # SDK mode objects (for AI-assisted execution)
        self._llm = None
        self._agent = None
        self._conversation = None

        # Workspace mode objects (for direct execution)
        self._workspace = None

        # Legacy mode objects
        self._runtime = None
        self._last_connect_error = ""
        self._last_connect_kind = ""
        # Command logs are consumed by the sandbox monitor bridge.
        self._command_logs: list[dict[str, Any]] = []
        self._command_log_seq = 0

    @property
    def is_connected(self) -> bool:
        """Check if runtime is connected."""
        return self._connected

    @property
    def mode(self) -> str:
        """Get the current OpenHands mode."""
        return self._mode

    @property
    def last_connect_error(self) -> str:
        """Last connection failure message, if any."""
        return self._last_connect_error

    @property
    def last_connect_kind(self) -> str:
        """Last connection failure category."""
        return self._last_connect_kind

    def _record_connect_failure(self, message: str, kind: str = "runtime") -> None:
        self._last_connect_error = str(message or "").strip()
        self._last_connect_kind = str(kind or "runtime")
        self._connected = False

    def _clear_connect_failure(self) -> None:
        self._last_connect_error = ""
        self._last_connect_kind = ""

    def _ensure_docker_ready(self, label: str) -> bool:
        """Short-circuit Docker-dependent modes when daemon is unavailable."""
        from teaming24.runtime.sandbox.docker import get_docker_availability

        docker_ok, docker_reason = get_docker_availability()
        if docker_ok:
            return True

        self._record_connect_failure(docker_reason, kind="environment")
        logger.info("%s unavailable: %s", label, docker_reason)
        return False

    def _determine_mode(self) -> str:
        """Determine which OpenHands mode to use.

        Priority:
            1. SDK + Workspace (full featured)
            2. Workspace only (direct execution)
            3. SDK only (AI-assisted, local workspace)
            4. Legacy (older openhands-ai)
            5. Local (fallback, no isolation)
        """
        if OPENHANDS_SDK_AVAILABLE and OPENHANDS_TOOLS_AVAILABLE and OPENHANDS_WORKSPACE_AVAILABLE:
            return "sdk_workspace"
        elif OPENHANDS_WORKSPACE_AVAILABLE:
            return "workspace"
        elif OPENHANDS_SDK_AVAILABLE and OPENHANDS_TOOLS_AVAILABLE:
            return "sdk"
        elif OPENHANDS_LEGACY_AVAILABLE:
            return "legacy"
        else:
            return "none"

    async def connect(self) -> bool:
        """Connect to OpenHands runtime.

        Returns:
            True if connection successful
        """
        if self._connected:
            return True

        if self._mode == "sdk_workspace":
            return await self._connect_sdk_workspace()
        elif self._mode == "workspace":
            return await self._connect_workspace()
        elif self._mode == "sdk":
            return await self._connect_sdk()
        elif self._mode == "legacy":
            return await self._connect_legacy()
        elif self._mode == "local":
            return await self._connect_local()
        else:
            logger.error(
                "OpenHands not installed. Install with:\n"
                "  uv pip install openhands-sdk openhands-tools openhands-workspace"
            )
            return False

    async def _connect_sdk_workspace(self) -> bool:
        """Connect using SDK with Docker workspace for sandboxed execution.

        Note: For basic command execution, we primarily use the workspace.
        The full SDK Agent/Conversation is only created when API key is available
        and AI-assisted execution is needed.
        """
        if not self._ensure_docker_ready("OpenHands SDK + Workspace"):
            return False

        try:
            from openhands.workspace import DockerWorkspace

            # Create Docker workspace for isolated execution
            # DockerWorkspace is a pydantic model that manages containers automatically
            self._workspace = DockerWorkspace(
                working_dir=self.config.workspace_path,
                mount_dir=self.config.workspace_path,
                server_image=self.config.container_image,
                read_timeout=float(self.config.timeout),
            )

            # Only create SDK agent if API key is available
            if self.config.api_key:
                try:
                    # Create LLM instance
                    self._llm = LLM(
                        model=self.config.model,
                        api_key=self.config.api_key,
                        base_url=self.config.base_url,
                    )

                    # Create agent with tools
                    tools = [
                        Tool(name=TerminalTool.name),
                        Tool(name=FileEditorTool.name),
                    ]

                    self._agent = Agent(
                        llm=self._llm,
                        tools=tools,
                    )

                    # Create conversation with Docker workspace
                    self._conversation = Conversation(
                        agent=self._agent,
                        workspace=self._workspace,
                    )
                    logger.info(f"OpenHands SDK + Workspace connected: model={self.config.model}")
                except Exception as e:
                    logger.warning(f"SDK agent creation failed, using workspace only: {e}")
            else:
                logger.info("OpenHands Workspace connected (no API key, workspace-only mode)")

            self._connected = True
            self._clear_connect_failure()
            return True

        except Exception as e:
            self._record_connect_failure(str(e))
            logger.warning(f"Failed to connect to OpenHands SDK + Workspace: {e}")
            return False

    async def _connect_workspace(self) -> bool:
        """Connect using only workspace for direct command execution."""
        if not self._ensure_docker_ready("OpenHands Workspace"):
            return False

        try:
            from openhands.workspace import DockerWorkspace

            # Use Docker workspace (auto-manages containers)
            self._workspace = DockerWorkspace(
                working_dir=self.config.workspace_path,
                mount_dir=self.config.workspace_path,
                server_image=self.config.container_image,
                read_timeout=float(self.config.timeout),
            )

            self._connected = True
            self._clear_connect_failure()
            logger.info("OpenHands Docker Workspace connected")
            return True

        except Exception as e:
            self._record_connect_failure(str(e))
            logger.warning(f"Failed to connect to OpenHands Workspace: {e}")
            return False

    async def _connect_sdk(self) -> bool:
        """Connect using new OpenHands SDK with local workspace."""
        try:
            if not self.config.api_key:
                logger.error("LLM API key not configured. Set LLM_API_KEY environment variable.")
                return False

            # Create LLM instance
            self._llm = LLM(
                model=self.config.model,
                api_key=self.config.api_key,
                base_url=self.config.base_url,
            )

            # Create agent with tools
            tools = [
                Tool(name=TerminalTool.name),
                Tool(name=FileEditorTool.name),
            ]

            self._agent = Agent(
                llm=self._llm,
                tools=tools,
            )

            # Create conversation with local workspace
            workspace = self.config.workspace_path
            if not os.path.exists(workspace):
                os.makedirs(workspace, exist_ok=True)

            self._conversation = Conversation(
                agent=self._agent,
                workspace=workspace,
            )

            self._connected = True
            self._clear_connect_failure()
            logger.info(f"OpenHands SDK connected: model={self.config.model}")
            return True

        except Exception as e:
            self._record_connect_failure(str(e))
            logger.error(f"Failed to connect to OpenHands SDK: {e}")
            return False

    async def _connect_local(self) -> bool:
        """Connect using local execution (no isolation)."""
        workspace = self.config.workspace_path
        if not os.path.exists(workspace):
            os.makedirs(workspace, exist_ok=True)

        self._connected = True
        self._clear_connect_failure()
        logger.info(f"OpenHands Local mode connected: workspace={workspace}")
        return True

    async def _connect_legacy(self) -> bool:
        """Connect using legacy OpenHands runtime."""
        if str(self.config.runtime_type).strip().lower() == "docker":
            if not self._ensure_docker_ready("OpenHands Legacy runtime"):
                return False

        try:
            from openhands.core.config import OpenHandsConfig as OHConfig
            from openhands.core.config import SandboxConfig
            from openhands.events import EventStream
            from openhands.storage.memory import InMemoryFileStore

            # Get runtime class
            runtime_cls = get_runtime_cls(self.config.runtime_type)

            # Create config
            oh_config = OHConfig(
                sandbox=SandboxConfig(
                    base_container_image=self.config.container_image,
                    timeout=self.config.timeout,
                    enable_auto_lint=self.config.enable_auto_lint,
                ),
                workspace_mount_path_in_sandbox=self.config.workspace_path,
            )

            # Create event stream
            file_store = InMemoryFileStore()
            event_stream = EventStream(sid=self.sid, file_store=file_store)

            # Create LLM registry if needed
            try:
                from openhands.llm.llm_registry import LLMRegistry
                llm_registry = LLMRegistry(oh_config)
            except ImportError:
                logger.debug("OpenHands LLMRegistry not available; initializing runtime without registry")
                llm_registry = None

            # Initialize runtime
            if llm_registry:
                self._runtime = runtime_cls(
                    config=oh_config,
                    event_stream=event_stream,
                    llm_registry=llm_registry,
                    sid=self.sid,
                    headless_mode=self.config.headless_mode,
                )
            else:
                self._runtime = runtime_cls(
                    config=oh_config,
                    event_stream=event_stream,
                    sid=self.sid,
                    headless_mode=self.config.headless_mode,
                )

            # Connect
            await self._runtime.connect()
            self._connected = True
            self._clear_connect_failure()

            logger.info(f"OpenHands Legacy runtime connected: {self.config.runtime_type}")
            return True

        except Exception as e:
            self._record_connect_failure(str(e))
            logger.warning(f"Failed to connect to OpenHands Legacy runtime: {e}")
            return False

    async def disconnect(self):
        """Disconnect from OpenHands runtime."""
        if self._mode in ("sdk", "sdk_workspace"):
            # SDK mode cleanup
            self._conversation = None
            self._agent = None
            self._llm = None

        if self._mode in ("workspace", "sdk_workspace"):
            # Workspace cleanup
            if self._workspace:
                try:
                    self._workspace.cleanup()
                except Exception as e:
                    logger.debug(f"Error cleaning up workspace: {e}")
                self._workspace = None

        if self._mode == "legacy":
            # Legacy mode cleanup
            if self._runtime:
                try:
                    self._runtime.close()
                except Exception as e:
                    logger.debug(f"Error closing runtime: {e}")
                self._runtime = None

        self._connected = False

    async def run_command(
        self,
        command: str,
        timeout: int = None,
        cwd: str = None,
    ) -> dict[str, Any]:
        """Run a shell command in the OpenHands runtime.

        For direct command execution (without AI reasoning), use workspace mode.
        SDK mode will use the AI agent to interpret and execute commands.

        Args:
            command: Shell command to execute
            timeout: Optional timeout in seconds
            cwd: Working directory (optional)

        Returns:
            Dict with exit_code, output, and error fields
        """
        if not self._connected:
            success = await self.connect()
            if not success:
                return {"exit_code": -1, "output": "", "error": "Failed to connect to OpenHands"}

        timeout = timeout or self.config.timeout

        if self._mode in ("workspace", "sdk_workspace"):
            result = await self._run_command_workspace(command, timeout, cwd)
        elif self._mode == "sdk":
            result = await self._run_command_sdk(command, timeout, cwd)
        elif self._mode == "legacy":
            result = await self._run_command_legacy(command, timeout, cwd)
        elif self._mode == "local":
            result = await self._run_command_local(command, timeout, cwd)
        else:
            result = {"exit_code": -1, "output": "", "error": "OpenHands not available"}

        self._record_command_log(command=command, cwd=cwd, timeout=timeout, result=result)
        return result

    def _record_command_log(
        self,
        *,
        command: str,
        cwd: str | None,
        timeout: int | None,
        result: dict[str, Any],
    ) -> None:
        """Persist a compact command log entry for UI consumption."""
        self._command_log_seq += 1
        output = str(result.get("output", "") or "")
        error = str(result.get("error", "") or "")
        entry = {
            "seq": self._command_log_seq,
            "timestamp": time.time(),
            "command": str(command or ""),
            "cwd": str(cwd or ""),
            "timeout": int(timeout) if isinstance(timeout, int) else None,
            "exit_code": int(result.get("exit_code", -1) or -1),
            "output": output[:4000],
            "error": error[:4000],
            "mode": self._mode,
            "sid": self.sid,
        }
        self._command_logs.append(entry)
        if len(self._command_logs) > 500:
            self._command_logs = self._command_logs[-500:]

    def get_command_logs(self, after_seq: int = 0) -> list[dict[str, Any]]:
        """Return command logs newer than ``after_seq`` (inclusive-exclusive)."""
        try:
            threshold = int(after_seq or 0)
        except Exception:
            threshold = 0
        return [entry for entry in self._command_logs if int(entry.get("seq", 0)) > threshold]

    async def _run_command_workspace(
        self,
        command: str,
        timeout: int = None,
        cwd: str = None,
    ) -> dict[str, Any]:
        """Run command using workspace (Docker)."""
        try:
            # Execute command in workspace using execute_command
            result = self._workspace.execute_command(
                command=command,
                cwd=cwd,
                timeout=float(timeout) if timeout else 30.0,
            )

            # CommandResult has: exit_code, stdout, stderr
            return {
                "exit_code": result.exit_code if hasattr(result, 'exit_code') else 0,
                "output": result.stdout if hasattr(result, 'stdout') else str(result),
                "error": result.stderr if hasattr(result, 'stderr') else "",
            }

        except Exception as e:
            logger.error(f"Workspace command execution error: {e}")
            return {"exit_code": -1, "output": "", "error": str(e)}

    async def _run_command_sdk(
        self,
        command: str,
        timeout: int = None,
        cwd: str = None,
    ) -> dict[str, Any]:
        """Run command using SDK mode (AI-assisted execution)."""
        try:
            # Use conversation to send command
            prompt = f"Execute this command and return only the output: {command}"
            if cwd:
                prompt = f"cd {cwd} && {command}"

            self._conversation.send_message(prompt)
            self._conversation.run()

            # Get last response
            history = self._conversation.get_history()
            if history:
                last_response = history[-1]
                return {
                    "exit_code": 0,
                    "output": str(last_response),
                    "error": "",
                }

            return {"exit_code": 0, "output": "", "error": ""}

        except Exception as e:
            logger.error(f"SDK command execution error: {e}")
            return {"exit_code": -1, "output": "", "error": str(e)}

    async def _run_command_local(
        self,
        command: str,
        timeout: int = None,
        cwd: str = None,
    ) -> dict[str, Any]:
        """Run command locally (no isolation - development only)."""
        import subprocess

        try:
            cwd = cwd or self.config.workspace_path

            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout,
            )

            return {
                "exit_code": proc.returncode,
                "output": stdout.decode() if stdout else "",
                "error": stderr.decode() if stderr else "",
            }

        except TimeoutError:
            logger.warning("Local command execution timed out command=%r timeout=%s", command, timeout)
            return {"exit_code": -1, "output": "", "error": "Command timed out"}
        except Exception as e:
            logger.error(f"Local command execution error: {e}")
            return {"exit_code": -1, "output": "", "error": str(e)}

    async def _run_command_legacy(
        self,
        command: str,
        timeout: int = None,
        cwd: str = None,
    ) -> dict[str, Any]:
        """Run command using legacy runtime."""
        try:
            action = CmdRunAction(command=command, blocking=True)
            if timeout:
                action.set_hard_timeout(timeout)

            obs = self._runtime.run(action)

            if isinstance(obs, CmdOutputObservation):
                return {
                    "exit_code": obs.exit_code,
                    "output": obs.content,
                    "error": "" if obs.exit_code == 0 else obs.content,
                }
            elif isinstance(obs, ErrorObservation):
                return {
                    "exit_code": -1,
                    "output": "",
                    "error": obs.content,
                }
            else:
                return {
                    "exit_code": -1,
                    "output": str(obs),
                    "error": "",
                }

        except Exception as e:
            logger.error(f"Legacy command execution error: {e}")
            return {"exit_code": -1, "output": "", "error": str(e)}

    async def read_file(self, path: str) -> dict[str, Any]:
        """Read a file from the workspace.

        Args:
            path: File path to read

        Returns:
            Dict with content or error
        """
        if not self._connected:
            success = await self.connect()
            if not success:
                return {"content": "", "error": "Failed to connect to OpenHands"}

        try:
            if self._mode in ("workspace", "sdk_workspace"):
                # Use cat command to read file in workspace
                result = await self.run_command(f"cat {path}")
                if result.get("exit_code", -1) == 0:
                    return {"content": result.get("output", ""), "error": ""}
                else:
                    return {"content": "", "error": result.get("error", "File read failed")}

            elif self._mode in ("sdk", "local"):
                # Use file system directly
                full_path = os.path.join(self.config.workspace_path, path)
                if os.path.exists(full_path):
                    with open(full_path) as f:
                        return {"content": f.read(), "error": ""}
                return {"content": "", "error": f"File not found: {path}"}

            elif self._mode == "legacy":
                action = FileReadAction(path=path)
                obs = self._runtime.read(action)

                if isinstance(obs, FileReadObservation):
                    return {"content": obs.content, "error": ""}
                elif isinstance(obs, ErrorObservation):
                    return {"content": "", "error": obs.content}
                else:
                    return {"content": "", "error": f"Unexpected response: {obs}"}

            return {"content": "", "error": "OpenHands not available"}

        except Exception as e:
            logger.exception("OpenHands read_file failed for %s: %s", path, e)
            return {"content": "", "error": str(e)}

    async def write_file(self, path: str, content: str) -> dict[str, Any]:
        """Write content to a file in the workspace.

        Args:
            path: File path to write
            content: Content to write

        Returns:
            Dict with success status or error
        """
        if not self._connected:
            success = await self.connect()
            if not success:
                return {"success": False, "error": "Failed to connect to OpenHands"}

        try:
            if self._mode in ("workspace", "sdk_workspace"):
                # Write file using heredoc for multi-line content
                # This is more reliable than echo for complex content
                import base64
                encoded = base64.b64encode(content.encode()).decode()
                ws = (self.config.workspace_path or "/workspace").rstrip("/") or "/workspace"
                result = await self.run_command(
                    f"echo '{encoded}' | base64 -d > {path}",
                    cwd=ws,
                )
                return {
                    "success": result.get("exit_code", -1) == 0,
                    "error": result.get("error", ""),
                }

            elif self._mode in ("sdk", "local"):
                # Use file system directly
                full_path = os.path.join(self.config.workspace_path, path)
                dir_path = os.path.dirname(full_path)
                if dir_path:
                    os.makedirs(dir_path, exist_ok=True)
                with open(full_path, 'w') as f:
                    f.write(content)
                return {"success": True, "error": ""}

            elif self._mode == "legacy":
                action = FileWriteAction(path=path, content=content)
                obs = self._runtime.write(action)

                if isinstance(obs, ErrorObservation):
                    return {"success": False, "error": obs.content}
                else:
                    return {"success": True, "error": ""}

            return {"success": False, "error": "OpenHands not available"}

        except Exception as e:
            logger.exception("OpenHands write_file failed for %s: %s", path, e)
            return {"success": False, "error": str(e)}

    async def run_python(self, code: str) -> dict[str, Any]:
        """Run Python code in the interpreter.

        Args:
            code: Python code to execute

        Returns:
            Dict with output or error
        """
        if not self._connected:
            success = await self.connect()
            if not success:
                return {"output": "", "error": "Failed to connect to OpenHands"}

        try:
            if self._mode in ("workspace", "sdk_workspace", "sdk", "local"):
                # TODO(tech-debt): Replace temp file execution with in-memory or safer temp dir strategy
                # Write code to temp file and execute
                script_name = prefixed_id("_temp_", 8, separator="") + ".py"
                ws = (self.config.workspace_path or "/workspace").rstrip("/")
                if not ws or ws == "/":
                    ws = "/workspace"
                script_path = f"{ws}/{script_name}"

                # Write script (relative path for write_file)
                await self.write_file(script_name, code)

                # Execute with explicit cwd and absolute path to avoid "//_temp_*.py" errors
                result = await self.run_command(
                    f"python3 {script_path}",
                    cwd=ws,
                )

                # Cleanup
                await self.run_command(f"rm -f {script_path}", cwd=ws)

                return {
                    "output": result.get("output", ""),
                    "error": result.get("error", ""),
                }

            elif self._mode == "legacy":
                action = IPythonRunCellAction(code=code)
                obs = self._runtime.run_ipython(action)

                if isinstance(obs, ErrorObservation):
                    return {"output": "", "error": obs.content}
                else:
                    return {"output": getattr(obs, 'content', str(obs)), "error": ""}

            return {"output": "", "error": "OpenHands not available"}

        except Exception as e:
            logger.exception("OpenHands run_python failed: %s", e)
            return {"output": "", "error": str(e)}

    async def browse_url(self, url: str) -> dict[str, Any]:
        """Browse a URL in the runtime's browser.

        Args:
            url: URL to browse

        Returns:
            Dict with page content or error
        """
        if not self._connected:
            success = await self.connect()
            if not success:
                return {"content": "", "error": "Failed to connect to OpenHands"}

        try:
            if self._mode in ("workspace", "sdk_workspace", "sdk", "local"):
                # Use curl as fallback for all non-browser modes
                result = await self.run_command(f'curl -s "{url}"')
                return {
                    "content": result.get("output", ""),
                    "error": result.get("error", ""),
                }

            elif self._mode == "legacy":
                action = BrowseURLAction(url=url)
                obs = self._runtime.browse(action)

                if isinstance(obs, ErrorObservation):
                    return {"content": "", "error": obs.content}
                else:
                    return {"content": getattr(obs, 'content', str(obs)), "error": ""}

            return {"content": "", "error": "OpenHands not available"}

        except Exception as e:
            logger.exception("OpenHands browse_url failed for %s: %s", url, e)
            return {"content": "", "error": str(e)}

    def get_status(self) -> dict[str, Any]:
        """Get current status of the OpenHands runtime.

        Returns status information compatible with sandbox tracking system.

        Returns:
            Dict with status information
        """
        import time

        state = "running" if self._connected else "stopped"

        return {
            "id": sandbox_id_for_openhands(self.sid),
            "name": f"OpenHands Runtime ({self._mode})",
            "state": state,
            "runtime": "openhands",
            "mode": self._mode,
            "workspace": self.config.workspace_path,
            "model": self.config.model if hasattr(self.config, 'model') else None,
            "connected": self._connected,
            "timestamp": time.time(),
            # SDK-specific info
            "sdk_available": OPENHANDS_SDK_AVAILABLE,
            "tools_available": OPENHANDS_TOOLS_AVAILABLE,
            "workspace_available": OPENHANDS_WORKSPACE_AVAILABLE,
            "legacy_available": OPENHANDS_LEGACY_AVAILABLE,
        }

    async def get_metrics(self) -> dict[str, Any]:
        """Get runtime metrics (CPU, memory usage if available).

        For Docker-based OpenHands runtimes, uses `docker stats` to get
        real container metrics.

        Returns:
            Dict with metrics information
        """
        metrics = {
            "cpu_pct": 0.0,
            "mem_pct": 0.0,
            "mem_used_mb": 0,
            "disk_pct": 0.0,
        }

        if not self._connected:
            return metrics

        try:
            # Try to get Docker container metrics
            # OpenHands workspace runs in a Docker container
            if self._mode in ("workspace", "sdk_workspace") and self._workspace:
                # Try to get container ID from workspace
                container_id = None
                if hasattr(self._workspace, 'container_id'):
                    container_id = self._workspace.container_id
                elif hasattr(self._workspace, '_container_id'):
                    container_id = self._workspace._container_id

                if container_id:
                    # Use teaming24's Docker metrics utility
                    try:
                        from teaming24.runtime.sandbox.docker import get_container_metrics
                        docker_metrics = await get_container_metrics(container_id)
                        metrics.update(docker_metrics)
                        return metrics
                    except ImportError:
                        logger.debug("Docker metrics helper unavailable for OpenHands container metrics")
                        pass

                # Fallback: try workspace's get_metrics if available
                if hasattr(self._workspace, 'get_metrics'):
                    ws_metrics = await self._workspace.get_metrics()
                    metrics.update(ws_metrics)
        except Exception as e:
            logger.debug(f"Error getting OpenHands metrics: {e}")

        return metrics

    async def __aenter__(self) -> "OpenHandsAdapter":
        """Async context manager entry."""
        await self.connect()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit."""
        await self.disconnect()


def create_openhands_runtime(
    workspace_path: str = "/workspace",
    model: str = None,
    **kwargs,
) -> OpenHandsAdapter | None:
    """Factory function to create an OpenHands adapter.

    Args:
        workspace_path: Workspace path for file operations
        model: LLM model to use (optional)
        **kwargs: Additional configuration options

    Returns:
        Configured OpenHandsAdapter instance or None if unavailable
    """
    if not OPENHANDS_AVAILABLE:
        logger.warning(
            "OpenHands not available. Install with:\n"
            "  uv pip install openhands-sdk openhands-tools\n"
            "  Or: pip install openhands-sdk openhands-tools"
        )
        return None

    try:
        config_kwargs = {
            "workspace_path": workspace_path,
            **{k: v for k, v in kwargs.items() if hasattr(OpenHandsConfig, k)}
        }
        if model:
            config_kwargs["model"] = model

        config = OpenHandsConfig(**config_kwargs)
        return OpenHandsAdapter(config)
    except Exception as e:
        logger.error(f"Failed to create OpenHands adapter: {e}")
        return None


def check_openhands_available() -> bool:
    """Check if OpenHands is installed and available."""
    return OPENHANDS_AVAILABLE


def get_openhands_mode() -> str:
    """Get the available OpenHands mode.

    Returns:
        'sdk_workspace' - Full SDK with Docker workspace (best)
        'workspace' - Docker workspace only (direct execution)
        'sdk' - SDK with local workspace
        'legacy' - Legacy openhands-ai API
        'local' - Local fallback (no isolation)
        'none' - OpenHands not installed
    """
    if OPENHANDS_SDK_AVAILABLE and OPENHANDS_TOOLS_AVAILABLE and OPENHANDS_WORKSPACE_AVAILABLE:
        return "sdk_workspace"
    elif OPENHANDS_WORKSPACE_AVAILABLE:
        return "workspace"
    elif OPENHANDS_SDK_AVAILABLE and OPENHANDS_TOOLS_AVAILABLE:
        return "sdk"
    elif OPENHANDS_LEGACY_AVAILABLE:
        return "legacy"
    return "none"


# ============================================================================
# OpenHands Pool Manager - Agent-Level Runtime Allocation
# ============================================================================

class OpenHandsPool(SingletonMixin):
    """Pool manager for OpenHands runtimes with agent-level allocation.

    Features:
        - Persistent runtime allocation per agent (no re-setup overhead)
        - Automatic cleanup on release or program exit
        - Thread-safe allocation tracking via ``SingletonMixin``

    Usage:
        pool = get_openhands_pool()

        # Allocate runtime for an agent
        runtime = await pool.allocate("agent-123")

        # Use runtime...
        result = await runtime.run_command("python script.py")

        # Release when done (optional, auto-cleanup on exit)
        await pool.release("agent-123")
    """

    def __init__(self):
        import atexit
        import signal
        import threading

        self._runtimes: dict[str, OpenHandsAdapter] = {}
        self._lock = threading.Lock()
        self._shutdown = False

        # Register cleanup handlers
        atexit.register(lambda: sync_async_cleanup(self._async_cleanup_all, "OpenHands pool cleanup"))

        # Handle SIGINT and SIGTERM gracefully
        self._original_sigint = signal.getsignal(signal.SIGINT)
        self._original_sigterm = signal.getsignal(signal.SIGTERM)
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        logger.debug("OpenHands pool initialized with cleanup handlers")

    def _signal_handler(self, signum, frame):
        """Handle shutdown signals by cleaning up runtimes before exiting."""
        logger.info(f"Received signal {signum}, cleaning up OpenHands runtimes...")
        self._sync_cleanup_all()

        import signal
        if signum == signal.SIGINT and callable(self._original_sigint):
            self._original_sigint(signum, frame)
        elif signum == signal.SIGTERM and callable(self._original_sigterm):
            self._original_sigterm(signum, frame)

    async def _async_cleanup_all(self):
        """Async cleanup -- disconnect all runtimes."""
        if self._shutdown:
            return
        self._shutdown = True
        with self._lock:
            if not self._runtimes:
                return
            logger.info(f"Cleaning up {len(self._runtimes)} OpenHands runtimes...")
            for agent_id, runtime in list(self._runtimes.items()):
                try:
                    await runtime.disconnect()
                    logger.debug(f"Cleaned up runtime for agent: {agent_id}")
                except Exception as exc:
                    logger.debug(f"Error cleaning up runtime for {agent_id}: {exc}")
            self._runtimes.clear()
            logger.info("OpenHands pool cleanup complete")

    def _sync_cleanup_all(self):
        """Synchronous cleanup fallback for signal handlers."""
        sync_async_cleanup(self._async_cleanup_all, "OpenHands pool cleanup")

    async def allocate(
        self,
        agent_id: str,
        config: OpenHandsConfig = None,
    ) -> OpenHandsAdapter | None:
        """
        Allocate or get existing runtime for an agent.

        If the agent already has an allocated runtime, returns it.
        Otherwise creates a new one.

        Args:
            agent_id: Unique agent identifier
            config: Optional custom configuration

        Returns:
            OpenHandsAdapter instance or None if unavailable
        """
        if self._shutdown:
            logger.debug("OpenHands pool is shutting down, cannot allocate")
            return None

        with self._lock:
            # Return existing runtime if allocated
            if agent_id in self._runtimes:
                runtime = self._runtimes[agent_id]
                if runtime.is_connected:
                    logger.debug(f"Returning existing runtime for agent: {agent_id}")
                    return runtime
                else:
                    # Clean up disconnected runtime
                    del self._runtimes[agent_id]

        # Create new runtime
        try:
            cfg = config or OpenHandsConfig()
            runtime = OpenHandsAdapter(cfg, sid=agent_id)

            success = await runtime.connect()
            if not success:
                if runtime.last_connect_kind == "environment" and runtime.last_connect_error:
                    logger.info(
                        "OpenHands runtime unavailable for agent %s: %s",
                        agent_id,
                        runtime.last_connect_error,
                    )
                else:
                    logger.warning(f"Failed to connect runtime for agent: {agent_id}")
                return None

            with self._lock:
                self._runtimes[agent_id] = runtime

            logger.info(f"Allocated OpenHands runtime for agent: {agent_id}")
            return runtime

        except Exception as e:
            logger.error(f"Failed to allocate runtime for {agent_id}: {e}")
            return None

    async def release(self, agent_id: str) -> bool:
        """
        Release runtime for an agent (cleanup when done).

        Args:
            agent_id: Agent identifier

        Returns:
            True if released successfully
        """
        with self._lock:
            runtime = self._runtimes.pop(agent_id, None)

        if runtime is None:
            return False

        try:
            await runtime.disconnect()
            logger.info(f"Released OpenHands runtime for agent: {agent_id}")
            return True
        except Exception as e:
            logger.warning(f"Error releasing runtime for {agent_id}: {e}")
            return False

    async def release_all(self):
        """Release all allocated runtimes."""
        with self._lock:
            agents = list(self._runtimes.keys())

        for agent_id in agents:
            await self.release(agent_id)

    def get(self, agent_id: str) -> OpenHandsAdapter | None:
        """
        Get runtime for an agent without allocating.

        Args:
            agent_id: Agent identifier

        Returns:
            Runtime if allocated, None otherwise
        """
        with self._lock:
            return self._runtimes.get(agent_id)

    def list_agents(self) -> list[str]:
        """Get list of agents with allocated runtimes."""
        with self._lock:
            return list(self._runtimes.keys())

    def get_status(self) -> dict[str, Any]:
        """Get pool status."""
        with self._lock:
            return {
                "allocated_count": len(self._runtimes),
                "agents": list(self._runtimes.keys()),
                "shutdown": self._shutdown,
            }


# Global pool instance
_openhands_pool: OpenHandsPool | None = None


def get_openhands_pool() -> OpenHandsPool:
    """Get the global OpenHands pool instance."""
    global _openhands_pool
    if _openhands_pool is None:
        _openhands_pool = OpenHandsPool.get_instance()
    return _openhands_pool


async def allocate_openhands(agent_id: str, config: OpenHandsConfig = None) -> OpenHandsAdapter | None:
    """Convenience function to allocate OpenHands runtime for an agent."""
    pool = get_openhands_pool()
    return await pool.allocate(agent_id, config)


async def release_openhands(agent_id: str) -> bool:
    """Convenience function to release OpenHands runtime for an agent."""
    pool = get_openhands_pool()
    return await pool.release(agent_id)


async def cleanup_all_openhands():
    """Cleanup all allocated OpenHands runtimes."""
    pool = get_openhands_pool()
    await pool.release_all()


__all__ = [
    "OpenHandsAdapter",
    "OpenHandsConfig",
    "create_openhands_runtime",
    "check_openhands_available",
    "get_openhands_mode",
    "OPENHANDS_AVAILABLE",
    "OPENHANDS_SDK_AVAILABLE",
    "OPENHANDS_TOOLS_AVAILABLE",
    "OPENHANDS_WORKSPACE_AVAILABLE",
    "OPENHANDS_LEGACY_AVAILABLE",
    # Pool management
    "OpenHandsPool",
    "get_openhands_pool",
    "allocate_openhands",
    "release_openhands",
    "cleanup_all_openhands",
]
