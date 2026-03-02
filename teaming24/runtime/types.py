"""Teaming24 Runtime Types - Core Type Definitions for Execution Environments.

This module defines all types, enums, dataclasses, and exceptions used throughout
the Teaming24 Runtime system. These types provide a consistent interface for
sandbox execution, file operations, browser automation, and error handling.

Constants:
    TEAMING24_PREFIX: Identifier prefix for all teaming24 resources
    TEAMING24_LABEL: Docker label for managed containers
    TEAMING24_USER_AGENT: Default user agent string
    TEAMING24_WORKSPACE_BASE: Base directory for workspaces

Design Philosophy:
    - Type Safety: All data structures are well-typed dataclasses
    - Immutability: Configuration objects are immutable after creation
    - Clear Semantics: Enum values have explicit string representations
    - Backward Compatibility: Legacy aliases maintained for smooth migration

Type Categories:
    Enums:
        RuntimeMode     - Execution mode (SANDBOX, LOCAL)
        SandboxBackend  - Backend type (DOCKER, API)
        ProcessStatus   - Process lifecycle states
        FileType        - File system entry types
        BrowserType     - Browser automation targets
        Language        - Code interpreter languages
        SandboxState    - Sandbox lifecycle states

    Configuration:
        RuntimeConfig   - Main configuration for all runtime modes

    Results:
        CommandResult   - Shell command execution result
        FileInfo        - File metadata
        ProcessInfo     - Background process information
        ScreenshotResult - Browser screenshot data
        SysMetrics      - System resource metrics

    Exceptions:
        RuntimeError    - Base exception for all runtime errors
        CommandError    - Command execution failures
        TimeoutError    - Operation timeouts
        FileAccessError - File access denied
        BrowserError    - Browser automation errors

Usage:
    from teaming24.runtime.types import (
        RuntimeConfig,
        RuntimeMode,
        SandboxBackend,
        CommandResult,
        RuntimeError,
    )

    # Create configuration
    config = RuntimeConfig(
        mode=RuntimeMode.SANDBOX,
        sandbox_backend=SandboxBackend.API,
        timeout=60.0,
    )

    # Handle errors
    try:
        result = await sandbox.execute("command")
    except TimeoutError:
        print("Command timed out")
    except CommandError as e:
        print(f"Command failed: {e.result.stderr}")
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path

# ============================================================================
# Teaming24 Constants
# ============================================================================

# Identifiers and prefixes
TEAMING24_PREFIX = "teaming24"
TEAMING24_SANDBOX_PREFIX = "teaming24-sandbox"
TEAMING24_CONTAINER_PREFIX = "teaming24-sandbox"

# Docker labels for identification
TEAMING24_LABEL_MANAGED = "teaming24.managed"
TEAMING24_LABEL_TYPE = "teaming24.type"
TEAMING24_LABEL_CREATED = "teaming24.created"

# Default paths
TEAMING24_HOME = Path.home() / ".teaming24"
TEAMING24_WORKSPACE_BASE = TEAMING24_HOME / "sandboxes"
TEAMING24_LOGS_DIR = TEAMING24_HOME / "logs"
TEAMING24_CACHE_DIR = TEAMING24_HOME / "cache"

# HTTP headers and user agent
TEAMING24_USER_AGENT = "Teaming24-Runtime/1.0"
TEAMING24_HEADER_PREFIX = "X-Teaming24"

# Environment variable prefix
TEAMING24_ENV_PREFIX = "TEAMING24_"


# ============================================================================
# Enums
# ============================================================================

class RuntimeMode(str, Enum):
    """Runtime execution mode."""
    SANDBOX = "sandbox"  # Docker container isolation
    LOCAL = "local"      # Direct execution (no isolation)


class SandboxBackend(str, Enum):
    """Sandbox backend type."""
    DOCKER = "docker"  # Execute via docker exec
    API = "api"        # Execute via HTTP API (VNC/CDP)


class ProcessStatus(str, Enum):
    """Process execution status."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    KILLED = "killed"


class FileType(str, Enum):
    """File type enumeration."""
    FILE = "file"
    DIRECTORY = "directory"
    SYMLINK = "symlink"


class BrowserType(str, Enum):
    """Browser type for automation."""
    CHROMIUM = "chromium"
    FIREFOX = "firefox"
    WEBKIT = "webkit"


class Language(str, Enum):
    """Supported programming languages."""
    PYTHON = "python"
    JAVASCRIPT = "javascript"
    BASH = "bash"


class SandboxState(str, Enum):
    """Sandbox lifecycle states.

    Must stay in sync with frontend SandboxInfo.state type in SandboxMonitor.tsx.
    """
    INIT = "init"
    RUNNING = "running"
    PAUSED = "paused"
    STOPPING = "stopping"
    STOPPED = "stopped"
    COMPLETED = "completed"
    DISCONNECTED = "disconnected"
    ERROR = "error"


# Legacy aliases for backwards compatibility
RuntimeType = RuntimeMode


# ============================================================================
# Data Classes
# ============================================================================

@dataclass
class RuntimeConfig:
    """Runtime configuration.

    Attributes:
        mode: Runtime mode (SANDBOX or LOCAL)
        workspace: Working directory path
        timeout: Default command timeout
        sandbox_backend: Backend for sandbox mode
        docker_image: Docker image for sandbox
        api_url: HTTP API URL for API backend
        expose_ports: Expose container ports for VNC/API access
        api_port: Port for container's HTTP API (default 8080)
        max_memory_mb: Memory limit in MB
        max_cpu_percent: CPU limit
        allow_network: Allow network access
        browser_type: Browser for automation
        browser_headless: Run browser headless
        browser_timeout: Browser operation timeout
    """
    mode: RuntimeMode = RuntimeMode.SANDBOX
    workspace: Path | None = None
    timeout: float = 300.0

    # Sandbox settings
    sandbox_backend: SandboxBackend = SandboxBackend.DOCKER
    docker_image: str = "ghcr.io/agent-infra/sandbox:latest"
    api_url: str = "http://localhost:8080"

    # Docker port configuration
    expose_ports: bool = True  # Expose container API port for VNC/CDP
    api_port: int = 8080       # Container's internal API port
    host_port: int | None = None  # Host port (None = auto-assign)

    # Resource limits
    max_memory_mb: int = 2048
    max_cpu_percent: float = 200.0
    max_disk_gb: int = 10

    # Network
    allow_network: bool = True
    proxy_url: str | None = None

    # Security
    allowed_paths: list[str] = field(default_factory=list)

    # Browser
    browser_type: BrowserType = BrowserType.CHROMIUM
    browser_headless: bool = True
    browser_timeout: float = 30.0

    def __post_init__(self):
        if self.workspace is None:
            # Default workspace under TEAMING24_WORKSPACE_BASE
            from teaming24.utils.ids import sandbox_id_generic
            sandbox_id = sandbox_id_generic()
            self.workspace = TEAMING24_WORKSPACE_BASE / sandbox_id
        elif isinstance(self.workspace, str):
            self.workspace = Path(self.workspace)

    @property
    def is_sandbox(self) -> bool:
        return self.mode == RuntimeMode.SANDBOX

    @property
    def is_local(self) -> bool:
        return self.mode == RuntimeMode.LOCAL


# Legacy alias
SandboxConfig = RuntimeConfig


@dataclass
class CommandResult:
    """Result of command execution."""
    exit_code: int
    stdout: str
    stderr: str
    status: ProcessStatus
    duration_ms: float
    command: str
    cwd: str = ""

    @property
    def success(self) -> bool:
        return self.exit_code == 0

    @property
    def output(self) -> str:
        return self.stdout


@dataclass
class ShellSession:
    """Shell session state."""
    id: str
    cwd: str
    env: dict[str, str]
    created_at: datetime
    last_activity: datetime
    is_active: bool = True


@dataclass
class FileInfo:
    """File metadata."""
    path: str
    name: str
    type: FileType
    size: int
    modified: datetime
    permissions: str = ""

    @classmethod
    def from_path(cls, path: Path) -> FileInfo:
        stat = path.stat()
        if path.is_symlink():
            ftype = FileType.SYMLINK
        elif path.is_dir():
            ftype = FileType.DIRECTORY
        else:
            ftype = FileType.FILE
        return cls(
            path=str(path),
            name=path.name,
            type=ftype,
            size=stat.st_size if ftype == FileType.FILE else 0,
            modified=datetime.fromtimestamp(stat.st_mtime),
            permissions=oct(stat.st_mode)[-3:],
        )

    @property
    def is_dir(self) -> bool:
        return self.type == FileType.DIRECTORY


@dataclass
class ProcessInfo:
    """Running process information."""
    pid: int
    name: str
    status: ProcessStatus
    command: str
    started_at: datetime
    cpu_percent: float = 0.0
    memory_mb: float = 0.0


@dataclass
class BrowserContext:
    """Browser context information."""
    id: str
    browser_type: BrowserType
    viewport_width: int = 1280
    viewport_height: int = 720
    user_agent: str | None = None
    locale: str = "en-US"


@dataclass
class PageInfo:
    """Browser page information."""
    url: str
    title: str
    viewport_width: int
    viewport_height: int


@dataclass
class ScreenshotResult:
    """Screenshot result."""
    data: bytes
    format: str = "png"
    width: int = 0
    height: int = 0


@dataclass
class ElementInfo:
    """DOM element information."""
    selector: str
    tag: str
    text: str
    attributes: dict[str, str] = field(default_factory=dict)
    visible: bool = True
    bbox: dict[str, float] | None = None


@dataclass
class HealthStatus:
    """Health check result."""
    ok: bool
    state: SandboxState
    message: str
    ts: datetime
    latency_ms: float = 0.0


@dataclass
class SysMetrics:
    """System resource metrics."""
    ts: datetime
    uptime_sec: float
    cpu_pct: float
    cpu_cores: int
    mem_total_mb: int
    mem_used_mb: int
    mem_pct: float
    disk_total_mb: int
    disk_used_mb: int
    disk_pct: float

    def to_dict(self) -> dict:
        return {
            "timestamp": self.ts.isoformat(),
            "uptime_sec": round(self.uptime_sec, 1),
            "cpu": {"percent": self.cpu_pct, "cores": self.cpu_cores},
            "memory": {
                "total_mb": self.mem_total_mb,
                "used_mb": self.mem_used_mb,
                "percent": self.mem_pct,
            },
            "disk": {
                "total_mb": self.disk_total_mb,
                "used_mb": self.disk_used_mb,
                "percent": self.disk_pct,
            },
        }


# ============================================================================
# Exceptions
# ============================================================================

class RuntimeError(Exception):
    """Base runtime error."""
    pass


# Alias for backwards compatibility
SandboxError = RuntimeError


class CommandError(RuntimeError):
    """Command execution error."""
    def __init__(self, message: str, result: CommandResult | None = None):
        super().__init__(message)
        self.result = result


class TimeoutError(RuntimeError):
    """Operation timed out."""
    pass


class ConnectionError(RuntimeError):
    """Connection failed."""
    pass


class FileAccessError(RuntimeError):
    """File access denied or not found."""
    pass


class SessionError(RuntimeError):
    """Shell session error."""
    pass


class BrowserError(RuntimeError):
    """Browser automation error."""
    pass


class ResourceLimitError(RuntimeError):
    """Resource limit exceeded."""
    pass


class ReadyTimeout(RuntimeError):
    """Sandbox not ready within timeout."""
    pass


class VMError(RuntimeError):
    """VM runtime error."""
    pass


# ============================================================================
# Exports
# ============================================================================

__all__ = [
    # Enums
    "RuntimeMode",
    "RuntimeType",  # Legacy alias
    "SandboxBackend",
    "ProcessStatus",
    "FileType",
    "BrowserType",
    "Language",
    "SandboxState",
    # Data classes
    "RuntimeConfig",
    "SandboxConfig",  # Legacy alias
    "CommandResult",
    "ShellSession",
    "FileInfo",
    "ProcessInfo",
    "BrowserContext",
    "PageInfo",
    "ScreenshotResult",
    "ElementInfo",
    "HealthStatus",
    "SysMetrics",
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
