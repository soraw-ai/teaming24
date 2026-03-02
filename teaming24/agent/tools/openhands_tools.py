"""
Sandbox Tools for CrewAI Agents - OpenHands SDK Compatible.

This module provides CrewAI-compatible tools that enable AI agents to interact
with isolated sandbox environments. It aligns with the OpenHands SDK pattern
while leveraging Teaming24's native sandbox infrastructure.

Architecture:
    These tools use the Teaming24 RuntimeManager as the execution backend,
    which provides:

    ┌─────────────────────────────────────────────────────────────────────┐
    │                       CrewAI Agent Tools                            │
    │  ┌────────────┐ ┌────────────┐ ┌────────────┐ ┌────────────┐      │
    │  │   Shell    │ │ FileRead   │ │ FileWrite  │ │  Python    │      │
    │  │  Command   │ │   Tool     │ │   Tool     │ │ Interpreter│      │
    │  └─────┬──────┘ └─────┬──────┘ └─────┬──────┘ └─────┬──────┘      │
    │        │              │              │              │              │
    │        └──────────────┴──────────────┴──────────────┘              │
    │                              │                                      │
    │                    ┌─────────▼─────────┐                           │
    │                    │  RuntimeManager   │                           │
    │                    │   (Singleton)     │                           │
    │                    └─────────┬─────────┘                           │
    │                              │                                      │
    │           ┌──────────────────┼──────────────────┐                  │
    │           │                  │                  │                  │
    │    ┌──────▼──────┐   ┌───────▼───────┐  ┌──────▼──────┐          │
    │    │   Sandbox   │   │   OpenHands   │  │    Local    │          │
    │    │  (Default)  │   │    Runtime    │  │ (Dev Only)  │          │
    │    └─────────────┘   └───────────────┘  └─────────────┘          │
    └─────────────────────────────────────────────────────────────────────┘

Output Sandboxing:
    ALL file-write operations from agents are forced into a task-specific
    output directory:  {output.base_dir}/{task_id}/workspace/

    This prevents agents from modifying project files or the host system.
    Paths supplied by agents are resolved relative to this sandbox root;
    any ".." traversal or absolute path is stripped and normalized.

Sandbox-First Execution:
    By default, all tool execution happens inside isolated Docker containers.
    This ensures:
    - Security: Untrusted code cannot affect the host system
    - Isolation: Each agent gets its own execution environment
    - Reproducibility: Consistent environment across executions
    - Resource Control: Memory and CPU limits enforced

OpenHands SDK Alignment:
    These tools follow the OpenHands SDK interface patterns:
    - Same method signatures as OpenHands tools
    - Compatible event emission for monitoring
    - Shared workspace concept for file operations

    Reference: https://docs.openhands.dev/sdk

Usage in CrewAI:
    from teaming24.agent.tools import create_openhands_tools

    # Create all sandbox tools
    tools = create_openhands_tools()

    # Use in agent
    agent = Agent(
        role="Developer",
        tools=tools,
        ...
    )

Tool Capabilities:
    ShellCommandTool  : Execute bash commands in sandbox
    FileReadTool      : Read files from workspace
    FileWriteTool     : Write files to workspace (sandboxed to task output)
    PythonInterpreter : Execute Python code in IPython
    BrowserTool       : Browse web pages (requires browser capability)

Author: Teaming24 Team
"""

import asyncio
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from teaming24.utils.logger import get_logger
from teaming24.utils.paths import resolve_sandbox_path as _resolve_sandbox_path

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Sync-to-async bridge (shared by all tool _run methods)
# ---------------------------------------------------------------------------

def _run_async_from_sync(coro, timeout_budget: float = 120):
    """Run an async coroutine from a synchronous context.

    Handles two situations:
      1. Running inside an existing event loop (rare — should use _arun) —
         spawns a worker thread with its own loop to avoid blocking the
         caller's loop.  Note: asyncio primitives (Lock, Event) created on
         the caller's loop will NOT be shared; use threading primitives for
         cross-loop synchronization.
      2. No event loop (normal path for CrewAI worker threads) — calls
         asyncio.run() directly.
    """
    try:
        asyncio.get_running_loop()
        import concurrent.futures
        with concurrent.futures.ThreadPoolExecutor(1) as pool:
            future = pool.submit(asyncio.run, coro)
            return future.result(timeout=timeout_budget)
    except RuntimeError:
        logger.debug("No running loop in openhands sync bridge; using asyncio.run")
        return asyncio.run(coro)


# ---------------------------------------------------------------------------
# Path sandbox helper (shared with sandbox_tools via utils.paths)
# ---------------------------------------------------------------------------

# Try to import CrewAI BaseTool
try:
    from crewai.tools.base_tool import BaseTool
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    BaseTool = object
    logger.debug("CrewAI BaseTool unavailable for openhands tools")

# Try to import RuntimeManager (primary)
try:
    from teaming24.runtime.manager import RuntimeManager, get_runtime_manager
    RUNTIME_MANAGER_AVAILABLE = True
except ImportError:
    RUNTIME_MANAGER_AVAILABLE = False
    RuntimeManager = None
    logger.debug("RuntimeManager unavailable for openhands tools")

# Try to import OpenHands adapter (fallback)
try:
    from teaming24.runtime.openhands import (
        OPENHANDS_AVAILABLE as OH_AVAILABLE,
    )
    from teaming24.runtime.openhands import (
        OpenHandsAdapter,
        check_openhands_available,
        create_openhands_runtime,
        get_openhands_mode,
    )
    OPENHANDS_AVAILABLE = OH_AVAILABLE
except ImportError:
    OPENHANDS_AVAILABLE = False
    OpenHandsAdapter = None
    logger.debug("OpenHands adapter unavailable for openhands tools")
    def check_openhands_available():
        return False
    def get_openhands_mode():
        return "none"


# Shared runtime manager instance
_runtime_manager: Any | None = None


def get_shared_runtime() -> Any | None:
    """Get the shared RuntimeManager instance.

    Returns RuntimeManager if available, falls back to OpenHands adapter.

    Returns:
        RuntimeManager or OpenHandsAdapter instance
    """
    global _runtime_manager

    if _runtime_manager is not None:
        return _runtime_manager

    # Try RuntimeManager first (preferred)
    if RUNTIME_MANAGER_AVAILABLE:
        try:
            _runtime_manager = get_runtime_manager()
            logger.debug("Using RuntimeManager for tool execution")
            return _runtime_manager
        except Exception as e:
            logger.warning(f"Failed to get RuntimeManager: {e}")

    # Fall back to OpenHands adapter
    if OPENHANDS_AVAILABLE:
        try:
            _runtime_manager = create_openhands_runtime()
            logger.debug("Using OpenHands adapter for tool execution")
            return _runtime_manager
        except Exception as e:
            logger.warning(f"Failed to create OpenHands runtime: {e}")

    return None


def set_shared_runtime(runtime: Any):
    """Set the shared runtime instance.

    Args:
        runtime: RuntimeManager or OpenHandsAdapter instance
    """
    global _runtime_manager
    _runtime_manager = runtime


def is_runtime_manager(runtime: Any) -> bool:
    """Check if runtime is a RuntimeManager instance."""
    if RuntimeManager is None:
        return False
    return isinstance(runtime, RuntimeManager)


# ============================================================================
# Shell Tool
# ============================================================================

def _oh_config():
    """Get OpenHands tool config from YAML."""
    from teaming24.config import get_config
    return get_config().tools.openhands_tools


class ShellCommandInput(BaseModel):
    """Input schema for shell command tool."""
    command: str = Field(..., description="The shell command to execute")
    timeout: int = Field(default=None, description="Timeout in seconds")


class ShellCommandTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for executing shell commands in sandboxed environment.

    Uses RuntimeManager to execute commands in OpenHands (default) or
    Teaming24 sandbox. All commands run in isolated Docker containers.

    Example:
        result = tool._run("ls -la")
        result = tool._run("python --version")
        result = tool._run("pip install requests", timeout=120)
    """

    name: str = "shell_command"
    description: str = (
        "Execute a shell command in a sandboxed environment. "
        "Use this for running bash commands, installing packages, running scripts, etc.\n"
        "Input MUST be a JSON dict: {\"command\": \"<shell command>\", \"timeout\": 60}\n"
        "Example: {\"command\": \"ls -la\"}\n"
        "Returns: Command output or error message."
    )
    args_schema: type[BaseModel] = ShellCommandInput
    handle_tool_error: bool = True

    def _run(self, command: str, timeout: int = None) -> str:
        """Execute shell command synchronously."""
        _cfg = _oh_config()
        if timeout is None:
            timeout = _cfg.shell_timeout
        return _run_async_from_sync(
            self._arun(command, timeout),
            timeout_budget=timeout + _cfg.shell_sync_timeout_buffer,
        )

    async def _arun(self, command: str, timeout: int = None) -> str:
        """Execute shell command asynchronously."""
        if timeout is None:
            timeout = _oh_config().shell_timeout
        runtime = get_shared_runtime()
        if runtime is None:
            return "ERROR: No runtime available. Check sandbox or OpenHands configuration."

        try:
            # Use RuntimeManager interface (preferred)
            if is_runtime_manager(runtime):
                result = await runtime.execute(command, timeout=timeout)
                if result["exit_code"] == 0:
                    return result["stdout"] or "Command completed successfully"
                else:
                    return f"ERROR (exit code {result['exit_code']}): {result['stderr'] or result['stdout']}"

            # Fallback to OpenHands adapter
            result = await runtime.run_command(command, timeout=timeout)
            if result["exit_code"] == 0:
                return result["output"] or "Command completed successfully"
            else:
                return f"ERROR (exit code {result['exit_code']}): {result['error'] or result['output']}"

        except Exception as e:
            logger.error(f"Shell command error: {e}")
            return f"ERROR: {str(e)}"


# ============================================================================
# File Read Tool
# ============================================================================

class FileReadInput(BaseModel):
    """Input schema for file read tool."""
    path: str = Field(..., description="Relative path within the task output workspace to read")


class FileReadTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for reading files from the task output workspace.

    When a task_output_dir is set, reads are resolved within that sandbox.
    Falls back to the runtime backend (sandbox/OpenHands) for paths outside.
    """

    name: str = "file_read"
    description: str = (
        "Read the contents of a file from the task output workspace.\n"
        "Input MUST be a JSON dict: {\"path\": \"<relative_path>\"}\n"
        "Example: {\"path\": \"main.py\"}\n"
        "Returns: File contents or error message."
    )
    args_schema: type[BaseModel] = FileReadInput
    handle_tool_error: bool = True

    # Set by core.py before each task execution (same as FileWriteTool).
    _task_output_dir: str | None = None

    def _run(self, path: str) -> str:
        """Read file synchronously."""
        return _run_async_from_sync(
            self._arun(path),
            timeout_budget=_oh_config().file_read_timeout,
        )

    async def _arun(self, path: str) -> str:
        """Read file asynchronously — sandbox-aware."""
        # If task_output_dir is set, try reading from the sandbox first
        if self._task_output_dir:
            resolved = _resolve_sandbox_path(path, self._task_output_dir)
            if os.path.isfile(resolved):
                try:
                    return Path(resolved).read_text(encoding="utf-8")
                except Exception as e:
                    logger.warning(f"[FileRead] sandbox read failed: {e}")

        runtime = get_shared_runtime()
        if runtime is None:
            return "ERROR: No runtime available"

        try:
            if is_runtime_manager(runtime):
                content = await runtime.read_file(path)
                return content

            result = await runtime.read_file(path)
            if result.get("error"):
                return f"ERROR: {result['error']}"
            return result.get("content", "")

        except Exception as e:
            logger.error(f"File read error: {e}")
            return f"ERROR: {str(e)}"


# ============================================================================
# File Write Tool
# ============================================================================

class FileWriteInput(BaseModel):
    """Input schema for file write tool."""
    path: str = Field(..., description="Relative path within the task output workspace (e.g. 'main.py', 'src/utils.py')")
    content: str = Field(..., description="Content to write to the file")


class FileWriteTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for writing files to the task output workspace.

    IMPORTANT: All files are written to a sandboxed task output directory,
    NOT to the project source tree.  You cannot modify project files.
    Paths are always relative — absolute paths and ".." traversals are
    stripped automatically.
    """

    name: str = "file_write"
    description: str = (
        "Write content to a file in your task output workspace. "
        "Files are saved to a sandboxed output directory — you CANNOT write to the project source tree.\n"
        "Input MUST be a JSON dict: {\"path\": \"<relative_path>\", \"content\": \"<file_content>\"}\n"
        "Example: {\"path\": \"main.py\", \"content\": \"print('hello')\"}\n"
        "Returns: Success message with the actual file path, or error."
    )
    args_schema: type[BaseModel] = FileWriteInput
    handle_tool_error: bool = True

    # Set by core.py before each task execution.
    # When set, ALL writes are forced into this directory.
    _task_output_dir: str | None = None

    def _run(self, path: str, content: str) -> str:
        """Write file synchronously."""
        return _run_async_from_sync(
            self._arun(path, content),
            timeout_budget=_oh_config().file_write_timeout,
        )

    async def _arun(self, path: str, content: str) -> str:
        """Write file asynchronously — all paths sandboxed to task output dir."""
        # ------------------------------------------------------------------
        # 1. Resolve the write path within the task output sandbox
        # ------------------------------------------------------------------
        sandbox_root = self._task_output_dir
        if not sandbox_root:
            # Fallback: use the global output base_dir + "unsandboxed"
            try:
                from teaming24.config import get_config
                base = os.path.expanduser(get_config().output.base_dir)
            except Exception as e:
                logger.debug(f"Config error for output dir, using default: {e}")
                base = os.path.expanduser("~/.teaming24/outputs")
            sandbox_root = os.path.join(base, "_unsandboxed_writes")
            logger.warning(
                f"[FileWrite] _task_output_dir not set — "
                f"writing to fallback: {sandbox_root}"
            )

        resolved_path = _resolve_sandbox_path(path, sandbox_root)

        # Ensure parent directories exist in the sandbox
        parent_dir = os.path.dirname(resolved_path)
        if parent_dir:
            os.makedirs(parent_dir, exist_ok=True)

        # ------------------------------------------------------------------
        # 2. Try runtime backend first (sandbox / OpenHands container)
        # ------------------------------------------------------------------
        runtime = get_shared_runtime()

        try:
            if runtime is not None and is_runtime_manager(runtime):
                success = await runtime.write_file(resolved_path, content)
                if success:
                    logger.info(f"[FileWrite] Wrote via RuntimeManager: {resolved_path}")
                    return f"Successfully wrote to {path} (saved at {resolved_path})"
                return f"ERROR: Failed to write to {path}"

            if runtime is not None:
                result = await runtime.write_file(resolved_path, content)
                if result.get("success"):
                    logger.info(f"[FileWrite] Wrote via adapter: {resolved_path}")
                    return f"Successfully wrote to {path} (saved at {resolved_path})"
                return f"ERROR: {result.get('error', 'Unknown error')}"
        except Exception as e:
            logger.debug(f"[FileWrite] Runtime write failed, using local fallback: {e}")

        # ------------------------------------------------------------------
        # 3. Local fallback — write directly (path is already sandboxed)
        # ------------------------------------------------------------------
        try:
            Path(resolved_path).write_text(content, encoding="utf-8")
            logger.info(f"[FileWrite] Wrote locally: {resolved_path}")
            return f"Successfully wrote to {path} (saved at {resolved_path})"
        except Exception as e:
            logger.error(f"File write error: {e}")
            return f"ERROR: {str(e)}"


# ============================================================================
# Python Code Interpreter Tool
# ============================================================================

class PythonCodeInput(BaseModel):
    """Input schema for Python code execution."""
    code: str = Field(..., description="Python code to execute")


class PythonInterpreterTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for executing Python code in sandboxed environment.

    Uses RuntimeManager to execute Python code in OpenHands (default) or sandbox.
    Code runs in an isolated IPython/Python environment.
    """

    name: str = "python_interpreter"
    description: str = (
        "Execute Python code in an IPython environment.\n"
        "Input MUST be a JSON dict: {\"code\": \"<python_code>\"}\n"
        "Example: {\"code\": \"import math\\nprint(math.pi)\"}\n"
        "Returns: Code output or error message."
    )
    args_schema: type[BaseModel] = PythonCodeInput
    handle_tool_error: bool = True

    def _run(self, code: str) -> str:
        """Execute Python code synchronously."""
        return _run_async_from_sync(
            self._arun(code),
            timeout_budget=_oh_config().python_timeout,
        )

    async def _arun(self, code: str) -> str:
        """Execute Python code asynchronously."""
        runtime = get_shared_runtime()
        if runtime is None:
            return "ERROR: No runtime available"

        try:
            # Use RuntimeManager interface (preferred)
            if is_runtime_manager(runtime):
                result = await runtime.run_code(code, language="python")
                if result.get("error"):
                    return f"ERROR: {result['error']}"
                return result.get("output") or "Code executed successfully (no output)"

            # Fallback to OpenHands adapter
            result = await runtime.run_python(code)
            if result.get("error"):
                return f"ERROR: {result['error']}"
            return result.get("output") or "Code executed successfully (no output)"

        except Exception as e:
            logger.error(f"Python execution error: {e}")
            return f"ERROR: {str(e)}"


# ============================================================================
# Browser Tool
# ============================================================================

class BrowseURLInput(BaseModel):
    """Input schema for browser tool."""
    url: str = Field(..., description="URL to browse")


class BrowserTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for browsing web pages in sandboxed browser.

    Uses RuntimeManager to browse pages via Playwright in sandbox.
    Browser runs in isolated container with optional VNC monitoring.
    """

    name: str = "browser"
    description: str = (
        "Browse a URL and retrieve its content.\n"
        "Input MUST be a JSON dict: {\"url\": \"<url>\"}\n"
        "Example: {\"url\": \"https://example.com\"}\n"
        "Returns: Page content or error message."
    )
    args_schema: type[BaseModel] = BrowseURLInput
    handle_tool_error: bool = True

    def _run(self, url: str) -> str:
        """Browse URL synchronously."""
        return _run_async_from_sync(
            self._arun(url),
            timeout_budget=_oh_config().browser_timeout,
        )

    async def _arun(self, url: str) -> str:
        """Browse URL asynchronously."""
        runtime = get_shared_runtime()
        if runtime is None:
            return "ERROR: No runtime available"

        try:
            # Use RuntimeManager interface (preferred)
            if is_runtime_manager(runtime):
                # Check if browser capability is available
                caps = runtime.get_capabilities()
                if not caps.get("browser"):
                    return "ERROR: Browser capability not available in current runtime"

                result = await runtime.browse(url)
                if result.get("error"):
                    return f"ERROR: {result['error']}"
                return result.get("content", "")

            # Fallback to OpenHands adapter
            result = await runtime.browse_url(url)
            if result.get("error"):
                return f"ERROR: {result['error']}"
            return result.get("content", "")

        except Exception as e:
            logger.error(f"Browser error: {e}")
            return f"ERROR: {str(e)}"


# ============================================================================
# Tool Factory
# ============================================================================

def create_openhands_tools(
    runtime: Any = None,
    task_output_dir: str | None = None,
) -> list[Any]:
    """
    Create all OpenHands-based tools.

    Args:
        runtime: Optional OpenHands runtime to use. If not provided,
                 tools will use a shared runtime.
        task_output_dir: Absolute path to the task-specific output workspace.
                         When set, all file writes are forced into this directory.

    Returns:
        List of CrewAI-compatible tool instances
    """
    if runtime:
        set_shared_runtime(runtime)

    if not CREWAI_AVAILABLE:
        logger.warning("CrewAI not available, returning empty tool list")
        return []

    file_read = FileReadTool()
    file_write = FileWriteTool()

    if task_output_dir:
        file_read._task_output_dir = task_output_dir
        file_write._task_output_dir = task_output_dir
        logger.info(f"[Tools] File tools sandboxed to: {task_output_dir}")

    return [
        ShellCommandTool(),
        file_read,
        file_write,
        PythonInterpreterTool(),
        BrowserTool(),
    ]


def get_tool_by_name(name: str, runtime: Any = None) -> Any | None:
    """
    Get a specific OpenHands tool by name.

    Args:
        name: Tool name (shell_command, file_read, file_write,
              python_interpreter, browser)
        runtime: Optional runtime instance

    Returns:
        Tool instance or None
    """
    if runtime:
        set_shared_runtime(runtime)

    tools = {
        "shell_command": ShellCommandTool,
        "shell": ShellCommandTool,
        "file_read": FileReadTool,
        "read_file": FileReadTool,
        "file_write": FileWriteTool,
        "write_file": FileWriteTool,
        "python_interpreter": PythonInterpreterTool,
        "python": PythonInterpreterTool,
        "code": PythonInterpreterTool,
        "browser": BrowserTool,
        "browse": BrowserTool,
    }

    tool_cls = tools.get(name.lower())
    if tool_cls and CREWAI_AVAILABLE:
        return tool_cls()
    return None


def bind_task_output_dir(tools_registry: dict, task_output_dir: str) -> None:
    """Bind a task-specific output directory to all file tools in the registry.

    Called by ``core.py`` before each task execution to ensure agents
    cannot write outside the designated output sandbox.

    Args:
        tools_registry: Dict mapping tool names to tool instances.
        task_output_dir: Absolute path to the task output workspace.
    """
    os.makedirs(task_output_dir, exist_ok=True)
    for _name, tool in tools_registry.items():
        if isinstance(tool, (FileWriteTool, FileReadTool)):
            tool._task_output_dir = task_output_dir
    logger.info(f"[Tools] Bound task output dir: {task_output_dir}")


# Export availability check
def check_openhands_tools_available() -> bool:
    """Check if sandbox tools are available.

    Returns True if either RuntimeManager or OpenHands is available.
    """
    return CREWAI_AVAILABLE and (RUNTIME_MANAGER_AVAILABLE or OPENHANDS_AVAILABLE)


def get_runtime_info() -> dict:
    """Get information about current runtime configuration.

    Useful for agents to understand available capabilities.

    Returns:
        Dict with runtime information
    """
    runtime = get_shared_runtime()
    if runtime is None:
        return {"available": False, "backend": None, "capabilities": {}}

    if is_runtime_manager(runtime):
        return runtime.get_runtime_info()

    # OpenHands fallback
    return {
        "available": True,
        "backend": "openhands",
        "capabilities": {
            "shell": True,
            "file_read": True,
            "file_write": True,
            "python": True,
            "browser": True,
        },
    }
