"""Teaming24 Runtime - Isolated Execution Environments for Agentic Networks.

This module is the core execution layer of the Teaming24 framework, providing
secure, isolated runtime environments for AI agents to execute code, automate
browsers, and interact with the filesystem.

Architecture Overview:
    Teaming24 Runtime follows a layered architecture:

    ┌─────────────────────────────────────────────────────────────┐
    │                     Application Layer                        │
    │  (Agent Tasks, Code Execution, Browser Automation, etc.)    │
    ├─────────────────────────────────────────────────────────────┤
    │                      Runtime Layer                           │
    │  ┌─────────────────────┐  ┌─────────────────────────────┐   │
    │  │   Sandbox Mode      │  │      Local Mode              │   │
    │  │  (Docker/AIO API)   │  │  (Direct Subprocess)         │   │
    │  └─────────────────────┘  └─────────────────────────────┘   │
    ├─────────────────────────────────────────────────────────────┤
    │                    Backend Layer                             │
    │  ┌──────────────┐  ┌───────────────┐  ┌────────────────┐   │
    │  │DockerBackend │  │  APIBackend   │  │ LocalRuntime   │   │
    │  │(docker exec) │  │ (HTTP/VNC/CDP)│  │ (subprocess)   │   │
    │  └──────────────┘  └───────────────┘  └────────────────┘   │
    └─────────────────────────────────────────────────────────────┘

Runtime Modes:
    SANDBOX (default, recommended for production):
        - Docker container isolation using AIO Sandbox image
        - Resource limits (CPU, memory, disk)
        - Process isolation and security
        - Two backends available:
            * DOCKER: Commands via `docker exec` (default)
            * API: Commands via HTTP API with VNC/CDP support

    LOCAL (for development and debugging):
        - Direct subprocess execution on host
        - No isolation or resource limits
        - No Docker required
        - Only use with trusted code

Hot Sandbox Pool:
    Sandboxes are "hot" by default - they persist as long-running containers
    that can be reused across multiple tasks. This provides:
    - Fast task execution (no container startup overhead)
    - State persistence between commands
    - Efficient resource utilization

    When a sandbox is deleted:
    - The Docker container is stopped and removed
    - Workspace files are cleaned up
    - All history is cleared

Key Components:
    Sandbox         - Main entry point for sandboxed execution
    SandboxPool     - Hot sandbox management for persistent containers
    LocalRuntime    - Direct execution without isolation
    AIOSandboxClient - HTTP client for AIO Sandbox API

Usage Examples:
    # Basic sandbox usage
    from teaming24.runtime import Sandbox

    async with Sandbox() as sandbox:
        result = await sandbox.execute("echo hello")
        print(result.stdout)

    # Sandbox with VNC/CDP monitoring
    from teaming24.runtime import Sandbox, SandboxBackend

    async with Sandbox(backend=SandboxBackend.API) as sandbox:
        result = await sandbox.execute("echo hello")
        print(f"VNC URL: {sandbox.vnc_url}")

    # Hot sandbox pool for agents
    from teaming24.runtime import get_pool

    pool = get_pool()
    sandbox = await pool.acquire("agent-001")
    await sandbox.execute("ls -la")
    await pool.release("agent-001")  # Sandbox stays running

    # Local mode (development only)
    from teaming24.runtime import LocalRuntime, RuntimeConfig, RuntimeMode

    config = RuntimeConfig(mode=RuntimeMode.LOCAL)
    async with LocalRuntime(config) as local:
        result = await local.execute("python script.py")

See Also:
    - teaming24.runtime.sandbox: Sandbox implementation details
    - teaming24.runtime.local: Local runtime implementation
    - teaming24.runtime.types: Type definitions and exceptions
"""

from .base import Runtime
from .local import LocalRuntime
from .manager import (
    RuntimeBackend,
    RuntimeCapabilities,
    RuntimeManager,
    check_openhands_available,
    check_sandbox_available,
    get_runtime_manager,
    initialize_runtime,
    set_runtime_manager,
    shutdown_runtime,
)
from .manager import (
    RuntimeConfig as ManagerConfig,
)
from .sandbox import (
    AIOSandboxClient,
    APIBackend,
    BrowserManager,
    CodeInterpreter,
    DockerBackend,
    EventStreamer,
    FileSystem,
    HealthManager,
    MetricsCollector,
    ProcessManager,
    Sandbox,
    SandboxPool,
    Screenshot,
    ScreenshotStreamer,
    ShellManager,
    StreamEvent,
    StreamEventType,
    SyncSandbox,
    get_pool,
)
from .sandbox.docker import (
    cleanup_teaming24_containers,
    cleanup_teaming24_workspaces,
    list_teaming24_containers,
)
from .sandbox.fs import FileMatch
from .sandbox.health import LifecycleConfig
from .sandbox.interpreter import ExecError, ExecHandlers, ExecResult, Output, Session
from .types import (
    TEAMING24_CACHE_DIR,
    TEAMING24_CONTAINER_PREFIX,
    TEAMING24_ENV_PREFIX,
    TEAMING24_HEADER_PREFIX,
    TEAMING24_HOME,
    TEAMING24_LABEL_CREATED,
    TEAMING24_LABEL_MANAGED,
    TEAMING24_LABEL_TYPE,
    TEAMING24_LOGS_DIR,
    # Constants
    TEAMING24_PREFIX,
    TEAMING24_SANDBOX_PREFIX,
    TEAMING24_USER_AGENT,
    TEAMING24_WORKSPACE_BASE,
    BrowserContext,
    BrowserError,
    BrowserType,
    CommandError,
    CommandResult,
    ConnectionError,
    ElementInfo,
    FileAccessError,
    FileInfo,
    FileType,
    HealthStatus,
    Language,
    PageInfo,
    ProcessInfo,
    ProcessStatus,
    ReadyTimeout,
    ResourceLimitError,
    # Data classes
    RuntimeConfig,
    # Exceptions
    RuntimeError,
    # Enums
    RuntimeMode,
    RuntimeType,  # Legacy alias
    SandboxBackend,
    SandboxConfig,  # Legacy alias
    SandboxError,
    SandboxState,
    ScreenshotResult,
    SessionError,
    ShellSession,
    SysMetrics,
    TimeoutError,
    VMError,
)


def create_runtime(config: RuntimeConfig) -> Runtime:
    """Factory function to create runtime based on configuration.

    Args:
        config: RuntimeConfig with mode and settings

    Returns:
        Appropriate runtime instance
    """
    if config.mode == RuntimeMode.LOCAL:
        return LocalRuntime(config)

    if config.sandbox_backend == SandboxBackend.API:
        return APIBackend(config)

    return DockerBackend(config)


__all__ = [
    # Teaming24 Constants
    "TEAMING24_PREFIX",
    "TEAMING24_SANDBOX_PREFIX",
    "TEAMING24_CONTAINER_PREFIX",
    "TEAMING24_LABEL_MANAGED",
    "TEAMING24_LABEL_TYPE",
    "TEAMING24_LABEL_CREATED",
    "TEAMING24_HOME",
    "TEAMING24_WORKSPACE_BASE",
    "TEAMING24_LOGS_DIR",
    "TEAMING24_CACHE_DIR",
    "TEAMING24_USER_AGENT",
    "TEAMING24_HEADER_PREFIX",
    "TEAMING24_ENV_PREFIX",
    # Main classes
    "Sandbox",
    "SyncSandbox",
    "LocalRuntime",
    # Backends
    "DockerBackend",
    "APIBackend",
    # Abstract
    "Runtime",
    # Factory
    "create_runtime",
    # RuntimeManager (OpenHands-aligned)
    "RuntimeManager",
    "RuntimeCapabilities",
    "RuntimeBackend",
    "ManagerConfig",
    "get_runtime_manager",
    "set_runtime_manager",
    "initialize_runtime",
    "shutdown_runtime",
    "check_sandbox_available",
    "check_openhands_available",
    # Components
    "ShellManager",
    "FileSystem",
    "ProcessManager",
    "BrowserManager",
    "CodeInterpreter",
    "MetricsCollector",
    "HealthManager",
    # Pool
    "SandboxPool",
    "get_pool",
    # Streaming
    "ScreenshotStreamer",
    "EventStreamer",
    "Screenshot",
    "StreamEvent",
    "StreamEventType",
    # Client
    "AIOSandboxClient",
    # Cleanup utilities
    "list_teaming24_containers",
    "cleanup_teaming24_containers",
    "cleanup_teaming24_workspaces",
    # Enums
    "RuntimeMode",
    "RuntimeType",
    "SandboxBackend",
    "ProcessStatus",
    "FileType",
    "BrowserType",
    "Language",
    "SandboxState",
    # Data classes
    "RuntimeConfig",
    "SandboxConfig",
    "CommandResult",
    "ShellSession",
    "FileInfo",
    "FileMatch",
    "ProcessInfo",
    "BrowserContext",
    "PageInfo",
    "ScreenshotResult",
    "ElementInfo",
    "HealthStatus",
    "SysMetrics",
    # Interpreter types
    "ExecResult",
    "ExecError",
    "ExecHandlers",
    "Output",
    "Session",
    # Config
    "LifecycleConfig",
    # Exceptions
    "RuntimeError",
    "SandboxError",
    "CommandError",
    "TimeoutError",
    "ConnectionError",
    "FileAccessError",
    "SessionError",
    "BrowserError",
    "ResourceLimitError",
    "ReadyTimeout",
    "VMError",
]
