"""Teaming24 Sandbox Runtime - Isolated Execution for Agentic Networks.

This module provides the core sandbox execution environment for Teaming24,
enabling AI agents to safely execute code, automate browsers, and interact
with the filesystem in isolated Docker containers.

Architecture:
    The Sandbox runtime uses AIO Sandbox (ghcr.io/agent-infra/sandbox) as
    the default Docker image, providing:

    ┌─────────────────────────────────────────────────────────────┐
    │                    Sandbox Class                            │
    │  (High-level API for agents)                                │
    ├─────────────────────────────────────────────────────────────┤
    │  ShellManager    │  FileSystem  │  BrowserManager           │
    │  ProcessManager  │  Interpreter │  MetricsCollector         │
    ├─────────────────────────────────────────────────────────────┤
    │           Backend (DockerBackend or APIBackend)             │
    ├─────────────────────────────────────────────────────────────┤
    │            AIO Sandbox Docker Container                     │
    │  ┌─────────────┐ ┌──────────────┐ ┌─────────────────────┐  │
    │  │ Shell/Bash  │ │  Chromium    │ │  Python/Node.js     │  │
    │  │             │ │  (Playwright)│ │  (Interpreters)     │  │
    │  └─────────────┘ └──────────────┘ └─────────────────────┘  │
    └─────────────────────────────────────────────────────────────┘

Hot Sandbox Design:
    Sandboxes are "hot" by default - they run as persistent containers that
    can be reused across multiple tasks. Benefits:

    - Fast Execution: No container startup overhead between commands
    - State Persistence: Variables, files, and browser sessions persist
    - Resource Efficiency: Single container serves multiple operations
    - Clean Shutdown: Proper cleanup when sandbox is explicitly deleted

Backends:
    DOCKER (default):
        - Executes commands via `docker exec`
        - Direct container access
        - Lower latency for command execution

    API:
        - Executes commands via HTTP API
        - VNC streaming for visual monitoring
        - CDP URL for Playwright connection
        - Better for remote/distributed deployments

Components:
    Sandbox          - Main entry point with full feature set
    ShellManager     - Shell command execution with sessions
    FileSystem       - Secure file operations within workspace
    BrowserManager   - Playwright-based browser automation
    ProcessManager   - Background process lifecycle management
    CodeInterpreter  - Python/JavaScript/Bash code execution
    MetricsCollector - System resource monitoring
    HealthManager    - Sandbox lifecycle and health checks

Usage:
    from teaming24.runtime.sandbox import Sandbox, SandboxBackend

    # Basic usage (Docker backend)
    async with Sandbox() as sandbox:
        # Execute shell commands
        result = await sandbox.execute("ls -la")
        print(result.stdout)

        # File operations
        await sandbox.write_file("hello.py", "print('Hello!')")
        content = await sandbox.read_file("hello.py")

        # Browser automation
        await sandbox.goto("https://example.com")
        screenshot = await sandbox.screenshot()

        # Code execution
        result = await sandbox.run_code("print(1+1)", Language.PYTHON)

    # API backend with VNC monitoring
    async with Sandbox(backend=SandboxBackend.API) as sandbox:
        await sandbox.goto("https://google.com")
        print(f"Watch live: {sandbox.vnc_url}")

Cleanup:
    When a sandbox is deleted (via pool.stop() or explicit deletion):
    1. Browser sessions are closed
    2. Background processes are terminated
    3. Docker container is stopped and removed
    4. Workspace files are cleaned up (optional)

See Also:
    - teaming24.runtime.sandbox.pool: Hot sandbox pool management
    - teaming24.runtime.sandbox.client: AIO Sandbox HTTP client
    - teaming24.runtime.sandbox.streaming: Real-time monitoring
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from pathlib import Path

from teaming24.utils.logger import get_logger

from ..types import (
    BrowserType,
    CommandResult,
    FileInfo,
    HealthStatus,
    Language,
    PageInfo,
    ProcessInfo,
    RuntimeConfig,
    RuntimeError,
    RuntimeMode,
    SandboxBackend,
    SandboxState,
    ScreenshotResult,
    SysMetrics,
)
from .api import APIBackend
from .browser import BrowserManager
from .client import AIOSandboxClient
from .docker import DockerBackend, get_container_metrics
from .fs import FileSystem
from .health import HealthManager
from .interpreter import CodeInterpreter, ExecResult
from .metrics import MetricsCollector
from .pool import SandboxPool, get_pool
from .process import ProcessManager
from .shell import ShellManager
from .streaming import (
    EventStreamer,
    Screenshot,
    ScreenshotStreamer,
    StreamEvent,
    StreamEventType,
)

logger = get_logger(__name__)


class Sandbox:
    """Sandbox execution environment.

    High-level interface for sandboxed code execution with:
    - Shell command execution
    - File system operations
    - Browser automation
    - Code interpreter
    - System metrics
    - Health checks

    Backends:
        - Docker: Uses `docker exec` for command execution
        - API: Uses HTTP API (supports VNC/CDP)

    Example:
        # Docker backend (default)
        async with Sandbox() as sandbox:
            result = await sandbox.execute("echo hello")
            print(result.stdout)

        # API backend with VNC
        async with Sandbox(backend=SandboxBackend.API) as sandbox:
            result = await sandbox.execute("echo hello")
            print(f"VNC: {sandbox.vnc_url}")
    """

    def __init__(
        self,
        workspace: str | Path | None = None,
        runtime: RuntimeMode = RuntimeMode.SANDBOX,
        backend: SandboxBackend | None = None,
        timeout: float = None,
        max_memory_mb: int = None,
        docker_image: str = None,
        api_url: str = None,
        allow_network: bool = True,
        allowed_paths: list[str] | None = None,
        browser_type: BrowserType = BrowserType.CHROMIUM,
        browser_headless: bool = True,
    ):
        """Initialize sandbox.

        Args:
            workspace: Working directory path
            runtime: Runtime mode (SANDBOX or LOCAL)
            backend: Backend type for sandbox (DOCKER or API)
            timeout: Default command timeout in seconds
            max_memory_mb: Memory limit for sandbox
            docker_image: Docker image for sandbox
            api_url: HTTP API URL for API backend
            allow_network: Allow network access
            allowed_paths: Additional allowed paths
            browser_type: Browser type for automation
            browser_headless: Run browser headless
        """
        # Resolve defaults from YAML config (runtime.sandbox section)
        try:
            from teaming24.config import get_config as _get_cfg
            _sb = _get_cfg().runtime.sandbox
        except Exception as exc:
            logger.debug(
                "Failed to load runtime.sandbox defaults; using built-ins: %s",
                exc,
                exc_info=True,
            )
            _sb = None
        if timeout is None:
            timeout = _sb.default_timeout if _sb else 300.0
        if max_memory_mb is None:
            max_memory_mb = _sb.max_memory_mb if _sb else 2048
        if docker_image is None:
            docker_image = _sb.docker_image if _sb else "ghcr.io/agent-infra/sandbox:latest"
        if api_url is None:
            api_url = _sb.api_url if _sb else "http://localhost:8080"

        self.config = RuntimeConfig(
            mode=runtime,
            workspace=Path(workspace) if workspace else None,
            timeout=timeout,
            sandbox_backend=backend or SandboxBackend.DOCKER,
            docker_image=docker_image,
            api_url=api_url,
            max_memory_mb=max_memory_mb,
            allow_network=allow_network,
            allowed_paths=allowed_paths or [],
            browser_type=browser_type,
            browser_headless=browser_headless,
        )

        self._backend = None
        self._shell: ShellManager | None = None
        self._fs: FileSystem | None = None
        self._process: ProcessManager | None = None
        self._browser: BrowserManager | None = None
        self._interpreter: CodeInterpreter | None = None
        self._metrics: MetricsCollector | None = None
        self._health: HealthManager | None = None
        self._started = False

    async def start(
        self,
        ready_timeout: float = None,
        skip_health_check: bool = False,
    ) -> Sandbox:
        if ready_timeout is None:
            try:
                from teaming24.config import get_config as _get_cfg
                ready_timeout = _get_cfg().runtime.sandbox.ready_timeout
            except Exception as exc:
                logger.debug(
                    "Failed to load runtime.sandbox.ready_timeout; using default: %s",
                    exc,
                    exc_info=True,
                )
                ready_timeout = 30.0
        """Start the sandbox environment."""
        if self._started:
            return self

        # Initialize health manager
        self._health = HealthManager(self.config)

        # Start backend
        if self.config.mode == RuntimeMode.LOCAL:
            from ..local import LocalRuntime

            self._backend = LocalRuntime(self.config)
        elif self.config.sandbox_backend == SandboxBackend.API:
            self._backend = APIBackend(self.config)
        else:
            self._backend = DockerBackend(self.config)

        await self._backend.start()

        # Initialize components
        self._shell = ShellManager(self.config)
        self._fs = FileSystem(self.config)
        self._process = ProcessManager(self.config)
        self._browser = BrowserManager(self.config)
        self._interpreter = CodeInterpreter(self.config)
        self._metrics = MetricsCollector(self.config)

        self._health.state = SandboxState.RUNNING
        self._started = True

        if not skip_health_check:
            await self._health.wait_ready(timeout=ready_timeout)

        return self

    async def stop(self, remove_container: bool = True) -> None:
        """Stop and cleanup sandbox.

        Args:
            remove_container: If True (default), fully cleanup including container removal.
                              If False (hot mode), disconnect but keep container running.
                              Container can be cleaned up later via GUI or cleanup utilities.
        """
        if not self._started:
            return

        if self._browser and self._browser.is_running:
            await self._browser.stop()

        if self._process:
            await self._process.cleanup(kill_running=True)

        if self._shell:
            await self._shell.cleanup_all()

        if self._backend:
            # Pass through remove_container flag
            if hasattr(self._backend, 'stop'):
                import inspect
                sig = inspect.signature(self._backend.stop)
                if 'remove_container' in sig.parameters:
                    await self._backend.stop(remove_container=remove_container)
                else:
                    await self._backend.stop()

        self._started = False

    async def __aenter__(self) -> Sandbox:
        return await self.start()

    async def __aexit__(self, *args) -> None:
        await self.stop()

    # ========================================================================
    # Shell
    # ========================================================================

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        session_id: str | None = None,
    ) -> CommandResult:
        """Execute shell command."""
        self._ensure_started()
        return await self._shell.execute(
            command=command,
            session_id=session_id,
            cwd=cwd,
            timeout=timeout,
            env=env,
        )

    # ========================================================================
    # File System
    # ========================================================================

    async def read_file(self, path: str, **kwargs) -> str:
        """Read file content."""
        self._ensure_started()
        return self._fs.read(path, **kwargs)

    async def write_file(self, path: str, content: str, **kwargs) -> int:
        """Write text file."""
        self._ensure_started()
        return self._fs.write(path, content, **kwargs)

    async def write_bytes(self, path: str, content: bytes, **kwargs) -> int:
        """Write binary file."""
        self._ensure_started()
        return self._fs.write_bytes(path, content, **kwargs)

    async def list_dir(self, path: str = ".", **kwargs) -> list[FileInfo]:
        """List directory contents."""
        self._ensure_started()
        return self._fs.list_dir(path, **kwargs)

    # ========================================================================
    # Process
    # ========================================================================

    async def start_process(
        self,
        command: str,
        name: str | None = None,
        **kwargs,
    ) -> ProcessInfo:
        """Start a background process."""
        self._ensure_started()
        return await self._process.start(command, name=name, **kwargs)

    async def stop_process(
        self,
        pid: int | None = None,
        name: str | None = None,
    ) -> bool:
        """Stop a running process."""
        self._ensure_started()
        return await self._process.stop(pid=pid, name=name)

    # ========================================================================
    # Browser
    # ========================================================================

    async def goto(self, url: str, **kwargs) -> PageInfo:
        """Navigate browser to URL."""
        self._ensure_started()
        if not self._browser.is_running:
            await self._browser.start()
        return await self._browser.goto(url, **kwargs)

    async def screenshot(self, **kwargs) -> ScreenshotResult:
        """Take browser screenshot."""
        self._ensure_started()
        if not self._browser.is_running:
            raise RuntimeError("Browser not started. Call goto() first.")
        return await self._browser.screenshot(**kwargs)

    async def click(self, selector: str, **kwargs) -> None:
        """Click element."""
        self._ensure_started()
        await self._browser.click(selector, **kwargs)

    async def type_text(self, selector: str, text: str, **kwargs) -> None:
        """Type text into element."""
        self._ensure_started()
        await self._browser.type(selector, text, **kwargs)

    async def get_page_content(self) -> str:
        """Get browser page HTML."""
        self._ensure_started()
        return await self._browser.get_content()

    # ========================================================================
    # Code Interpreter
    # ========================================================================

    async def run_code(
        self,
        code: str,
        language: Language = Language.PYTHON,
        session_id: str | None = None,
        timeout: float | None = None,
    ) -> ExecResult:
        """Execute code."""
        self._ensure_started()
        if not self._interpreter.is_running:
            await self._interpreter.start()
        return await self._interpreter.run(
            code=code,
            language=language,
            session_id=session_id,
            timeout=timeout,
        )

    async def create_code_session(
        self,
        language: Language = Language.PYTHON,
        name: str | None = None,
    ):
        """Create interpreter session."""
        self._ensure_started()
        if not self._interpreter.is_running:
            await self._interpreter.start()
        return await self._interpreter.create_session(language, name)

    # ========================================================================
    # Metrics
    # ========================================================================

    async def get_metrics(self) -> SysMetrics:
        """Get system metrics.

        For Docker backends, returns actual container metrics (CPU, memory, disk).
        For other backends, returns host system metrics.
        """
        self._ensure_started()

        # If using Docker backend, get container-specific metrics
        if hasattr(self._backend, 'get_docker_metrics') and hasattr(self._backend, '_container_id'):
            if self._backend._container_id:
                docker_metrics = await self._backend.get_docker_metrics()

                # Get base metrics for uptime and cpu_cores
                base_metrics = await self._metrics.snapshot()

                return SysMetrics(
                    ts=base_metrics.ts,
                    uptime_sec=base_metrics.uptime_sec,
                    cpu_pct=docker_metrics.get("cpu_pct", 0),
                    cpu_cores=base_metrics.cpu_cores,
                    mem_total_mb=base_metrics.mem_total_mb,  # Container limit not easily available
                    mem_used_mb=docker_metrics.get("mem_used_mb", 0),
                    mem_pct=docker_metrics.get("mem_pct", 0),
                    disk_total_mb=base_metrics.disk_total_mb,
                    disk_used_mb=base_metrics.disk_used_mb,
                    disk_pct=docker_metrics.get("disk_pct", 0),
                )

        return await self._metrics.snapshot()

    # ========================================================================
    # Health
    # ========================================================================

    async def is_healthy(self) -> bool:
        """Check if sandbox is healthy."""
        self._ensure_started()
        return await self._health.ping()

    async def check_health(self) -> HealthStatus:
        """Get detailed health status."""
        self._ensure_started()
        return await self._health.check()

    async def pause(self) -> None:
        """Pause sandbox."""
        self._ensure_started()
        self._health.transition(SandboxState.PAUSED)

    async def resume(self) -> None:
        """Resume sandbox."""
        self._ensure_started()
        self._health.transition(SandboxState.RUNNING)

    async def renew(self, duration: timedelta) -> None:
        """Extend sandbox TTL."""
        self._ensure_started()
        self._health.renew(duration)

    # ========================================================================
    # Properties
    # ========================================================================

    @property
    def state(self) -> SandboxState:
        """Current sandbox state."""
        if self._health:
            return self._health.state
        return SandboxState.STOPPED

    @property
    def shell(self) -> ShellManager:
        self._ensure_started()
        return self._shell

    @property
    def fs(self) -> FileSystem:
        self._ensure_started()
        return self._fs

    @property
    def process(self) -> ProcessManager:
        self._ensure_started()
        return self._process

    @property
    def browser(self) -> BrowserManager:
        self._ensure_started()
        return self._browser

    @property
    def interpreter(self) -> CodeInterpreter:
        self._ensure_started()
        return self._interpreter

    @property
    def metrics(self) -> MetricsCollector:
        self._ensure_started()
        return self._metrics

    @property
    def health(self) -> HealthManager:
        self._ensure_started()
        return self._health

    @property
    def workspace(self) -> str:
        return str(self.config.workspace)

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def vnc_url(self) -> str | None:
        """VNC URL for visual monitoring.

        Available for both Docker and API backends when ports are exposed.
        """
        if isinstance(self._backend, APIBackend):
            return self._backend.vnc_url
        if isinstance(self._backend, DockerBackend):
            return self._backend.vnc_url
        return None

    @property
    def cdp_url(self) -> str | None:
        """CDP URL for Playwright connection."""
        if isinstance(self._backend, APIBackend):
            return self._backend.cdp_url
        if isinstance(self._backend, DockerBackend):
            return self._backend.cdp_url
        return None

    @property
    def api_url(self) -> str | None:
        """Container API URL."""
        if isinstance(self._backend, APIBackend):
            return self._backend.config.api_url
        if isinstance(self._backend, DockerBackend):
            return self._backend.api_url
        return None

    @property
    def backend_type(self) -> SandboxBackend:
        return self.config.sandbox_backend

    @property
    def container_name(self) -> str | None:
        """Docker container name (if using Docker backend)."""
        if isinstance(self._backend, DockerBackend):
            return self._backend.container_name
        return None

    @property
    def container_id(self) -> str | None:
        """Docker container ID (if using Docker backend)."""
        if isinstance(self._backend, DockerBackend):
            return self._backend.container_id
        return None

    def _ensure_started(self) -> None:
        if not self._started:
            raise RuntimeError("Sandbox not started")

    @classmethod
    def sync(cls, **kwargs) -> SyncSandbox:
        """Create synchronous sandbox instance."""
        return SyncSandbox(**kwargs)


class SyncSandbox:
    """Synchronous wrapper for Sandbox."""

    def __init__(self, **kwargs):
        self._async_sandbox = Sandbox(**kwargs)
        self._loop = None

    def _run(self, coro):
        try:
            asyncio.get_running_loop()
            # Already inside an event loop — can't call run_until_complete.
            # Spawn a new thread with its own loop to avoid blocking.
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor(1) as pool:
                future = pool.submit(asyncio.run, coro)
                return future.result()
        except RuntimeError:
            logger.debug("SyncSandbox._run using dedicated loop (no active loop in current thread)")
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(coro)

    def start(self) -> SyncSandbox:
        self._run(self._async_sandbox.start())
        return self

    def stop(self) -> None:
        self._run(self._async_sandbox.stop())
        if self._loop and not self._loop.is_closed():
            self._loop.close()

    def __enter__(self) -> SyncSandbox:
        return self.start()

    def __exit__(self, *args) -> None:
        self.stop()

    def execute(self, command: str, **kwargs) -> CommandResult:
        return self._run(self._async_sandbox.execute(command, **kwargs))

    def read_file(self, path: str, **kwargs) -> str:
        return self._run(self._async_sandbox.read_file(path, **kwargs))

    def write_file(self, path: str, content: str, **kwargs) -> int:
        return self._run(self._async_sandbox.write_file(path, content, **kwargs))

    def list_dir(self, path: str = ".", **kwargs) -> list[FileInfo]:
        return self._run(self._async_sandbox.list_dir(path, **kwargs))

    def start_process(self, command: str, **kwargs) -> ProcessInfo:
        return self._run(self._async_sandbox.start_process(command, **kwargs))

    def stop_process(self, **kwargs) -> bool:
        return self._run(self._async_sandbox.stop_process(**kwargs))

    def goto(self, url: str, **kwargs) -> PageInfo:
        return self._run(self._async_sandbox.goto(url, **kwargs))

    def screenshot(self, **kwargs) -> ScreenshotResult:
        return self._run(self._async_sandbox.screenshot(**kwargs))

    @property
    def workspace(self) -> str:
        return self._async_sandbox.workspace

    @property
    def is_running(self) -> bool:
        return self._async_sandbox.is_running


__all__ = [
    # Main classes
    "Sandbox",
    "SyncSandbox",
    # Backends
    "DockerBackend",
    "APIBackend",
    # Docker utilities
    "get_container_metrics",
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
]
