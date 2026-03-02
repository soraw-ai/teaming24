"""Teaming24 Docker Backend - Container Execution via docker exec.

This module provides the DockerBackend class, the default execution backend
for Teaming24 sandboxes. It uses Docker containers with the AIO Sandbox image
to provide isolated, secure execution environments.

How It Works:
    1. Creates a Docker container from AIO Sandbox image
    2. Mounts workspace directory as a volume
    3. Executes commands via `docker exec`
    4. Captures stdout/stderr and exit codes
    5. Cleans up container on stop

Container Configuration:
    Image:     ghcr.io/agent-infra/sandbox:latest (customizable)
    Security:  seccomp=unconfined (for browser support)
    Network:   Bridge mode (configurable)
    Resources: Memory and CPU limits enforced
    Volumes:   Workspace mounted at /workspace

AIO Sandbox Features:
    The AIO Sandbox container includes:
    - Ubuntu-based environment
    - Python 3, Node.js, and common tools
    - Chromium browser with Playwright
    - VNC server for visual monitoring
    - HTTP API for remote access

Lifecycle:
    start() → Container created and started
    execute() → Commands run via docker exec
    stop() → Container stopped and removed

Usage:
    from teaming24.runtime.sandbox.docker import DockerBackend
    from teaming24.runtime import RuntimeConfig

    config = RuntimeConfig(
        docker_image="ghcr.io/agent-infra/sandbox:latest",
        max_memory_mb=2048,
    )

    async with DockerBackend(config) as backend:
        result = await backend.execute("ls -la")
        print(result.stdout)
        print(f"Container: {backend.container_id}")

See Also:
    - teaming24.runtime.sandbox.api: HTTP API backend alternative
    - teaming24.runtime.sandbox.Sandbox: High-level sandbox interface
"""

from __future__ import annotations

import asyncio
import shutil
import subprocess
import time
from datetime import datetime

from teaming24.runtime.base import Runtime
from teaming24.runtime.types import (
    TEAMING24_CONTAINER_PREFIX,
    TEAMING24_LABEL_CREATED,
    TEAMING24_LABEL_MANAGED,
    TEAMING24_LABEL_TYPE,
    CommandResult,
    ProcessStatus,
    RuntimeConfig,
    RuntimeError,
)
from teaming24.utils.ids import OPENHANDS_PREFIX, SANDBOX_PREFIX, prefixed_id
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

_DOCKER_AVAILABILITY_CACHE: dict[str, object] = {
    "ok": None,
    "reason": "Docker availability has not been checked yet.",
    "checked_at": 0.0,
}
_DOCKER_AVAILABILITY_TTL = 5.0


def get_docker_availability(force: bool = False) -> tuple[bool, str]:
    """Return whether Docker CLI and daemon are available.

    Uses a short TTL cache to avoid spamming `docker info` on every tool call.
    """
    now = time.time()
    cached_ok = _DOCKER_AVAILABILITY_CACHE.get("ok")
    cached_checked_at = float(_DOCKER_AVAILABILITY_CACHE.get("checked_at") or 0.0)
    if not force and cached_ok is not None and (now - cached_checked_at) < _DOCKER_AVAILABILITY_TTL:
        return bool(cached_ok), str(_DOCKER_AVAILABILITY_CACHE.get("reason") or "")

    docker_bin = shutil.which("docker")
    if docker_bin is None:
        reason = "Docker CLI is not installed or not on PATH."
        _DOCKER_AVAILABILITY_CACHE.update({"ok": False, "reason": reason, "checked_at": now})
        return False, reason

    try:
        proc = subprocess.run(
            [docker_bin, "info"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=2.5,
            check=False,
        )
    except subprocess.TimeoutExpired:
        reason = "Docker daemon check timed out."
        _DOCKER_AVAILABILITY_CACHE.update({"ok": False, "reason": reason, "checked_at": now})
        return False, reason
    except Exception as exc:
        reason = f"Docker check failed: {exc}"
        _DOCKER_AVAILABILITY_CACHE.update({"ok": False, "reason": reason, "checked_at": now})
        return False, reason

    if proc.returncode == 0:
        reason = "Docker daemon is available."
        _DOCKER_AVAILABILITY_CACHE.update({"ok": True, "reason": reason, "checked_at": now})
        return True, reason

    raw_reason = (proc.stderr or proc.stdout or "").strip().replace("\n", " ")
    reason = raw_reason[:240] if raw_reason else "Docker daemon is not reachable."
    _DOCKER_AVAILABILITY_CACHE.update({"ok": False, "reason": reason, "checked_at": now})
    return False, reason


class DockerBackend(Runtime):
    """Docker container-based sandbox backend.

    Runs commands inside an AIO Sandbox Docker container using `docker exec`.
    Provides process isolation and resource limits.

    The container is started automatically and kept alive for the duration
    of the runtime. Commands are executed via `docker exec`.

    VNC/API Access:
        When expose_ports=True (default), the container's API port is exposed,
        enabling VNC streaming and CDP access:
        - VNC: http://localhost:{port}/vnc/index.html?autoconnect=true
        - CDP: Available via /v1/browser/info endpoint
    """

    def __init__(self, config: RuntimeConfig):
        """Initialize Docker backend.

        Args:
            config: RuntimeConfig with Docker settings
        """
        super().__init__(config)
        self._container_id: str | None = None
        self._container_name: str | None = None
        self._container_workspace = "/workspace"
        self._host_port: int | None = None  # Assigned port on host
        # Load sandbox-specific config from YAML
        try:
            from teaming24.config import get_config
            _sb = get_config().runtime.sandbox
            self._shm_size = _sb.shm_size
            self._api_ready_timeout = _sb.api_ready_timeout
            self._api_ready_check_interval = _sb.api_ready_check_interval
            self._health_check_timeout = _sb.health_check_timeout
            self._stop_timeout = _sb.stop_timeout
        except Exception as e:
            logger.debug(f"Failed to load sandbox config, using defaults: {e}")
            self._shm_size = "512m"
            self._api_ready_timeout = 30.0
            self._api_ready_check_interval = 0.5
            self._health_check_timeout = 2.0
            self._stop_timeout = 3

    async def start(self) -> None:
        """Start Docker container.

        Creates a new container with:
        - Volume mount for workspace
        - Resource limits
        - Security options for browser support
        """
        if not self._is_docker_available():
            raise RuntimeError(
                "Docker is not available. Install Docker or use RuntimeMode.LOCAL"
            )

        # Ensure workspace exists with teaming24 marker
        self.config.workspace.mkdir(parents=True, exist_ok=True)

        # Container name with teaming24 prefix for easy identification
        self._container_name = prefixed_id(TEAMING24_CONTAINER_PREFIX, 8)

        # Build docker run command with teaming24 labels
        cmd = [
            "docker", "run", "-d",
            "--name", self._container_name,
            # Labels for identification and filtering
            "--label", f"{TEAMING24_LABEL_MANAGED}=true",
            "--label", f"{TEAMING24_LABEL_TYPE}=sandbox",
            "--label", f"{TEAMING24_LABEL_CREATED}={int(asyncio.get_event_loop().time())}",
            "--security-opt", "seccomp=unconfined",
            "-v", f"{self.config.workspace}:{self._container_workspace}",
            "-w", self._container_workspace,
            "--memory", f"{self.config.max_memory_mb}m",
            "--cpus", str(self.config.max_cpu_percent / 100),
            "--shm-size", self._shm_size,
        ]

        # Network configuration
        if self.config.allow_network:
            cmd.extend(["--network", "bridge"])
        else:
            cmd.extend(["--network", "none"])

        # Port exposure for VNC/API access
        if self.config.expose_ports:
            if self.config.host_port:
                # Use specified host port
                cmd.extend(["-p", f"{self.config.host_port}:{self.config.api_port}"])
                self._host_port = self.config.host_port
            else:
                # Auto-assign host port
                cmd.extend(["-p", f"{self.config.api_port}"])

        # Reduce log noise: disable uvicorn access logs (bash_events polling floods logs).
        # extra_env from config can override these.
        default_env = {"UVICORN_ACCESS_LOG": "false", "LOG_LEVEL": "WARNING"}
        try:
            from teaming24.config import get_config
            extra = get_config().runtime.sandbox.extra_env
            default_env = {**default_env, **extra}
        except Exception as exc:
            logger.debug(
                "Failed to load runtime.sandbox.extra_env; using defaults: %s",
                exc,
                exc_info=True,
            )
        for k, v in default_env.items():
            cmd.extend(["-e", f"{k}={v}"])

        # Image (let container run its default entrypoint for API server)
        cmd.append(self.config.docker_image)

        logger.debug("Starting container", extra={"image": self.config.docker_image})

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()

        if proc.returncode != 0:
            error_msg = stderr.decode()[:300]
            raise RuntimeError(f"Failed to start container: {error_msg}")

        self._container_id = stdout.decode().strip()

        # Get assigned host port if auto-assigned
        if self.config.expose_ports and not self._host_port:
            self._host_port = await self._get_container_port()

        # Wait for API server to be ready
        if self.config.expose_ports and self._host_port:
            await self._wait_for_api_ready()

        self._started = True

        logger.info("Docker backend started", extra={
            "container_name": self._container_name,
            "container_id": self._container_id[:12],
            "host_port": self._host_port,
            "vnc_url": self.vnc_url if self._host_port else None,
        })

    @property
    def container_name(self) -> str | None:
        """Get the Docker container name."""
        return self._container_name

    @property
    def container_id(self) -> str | None:
        """Get the Docker container ID."""
        return self._container_id

    async def _get_container_port(self) -> int | None:
        """Get the host port assigned to the container."""
        if not self._container_id:
            return None

        proc = await asyncio.create_subprocess_exec(
            "docker", "port", self._container_id, str(self.config.api_port),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode == 0 and stdout:
            # Output format: "0.0.0.0:32768" or "[::]:32768"
            output = stdout.decode().strip()
            if ":" in output:
                port_str = output.split(":")[-1]
                try:
                    return int(port_str)
                except ValueError as e:
                    logger.debug(f"Failed to parse container port: {e}")
        return None

    async def _wait_for_api_ready(self, max_wait: float = None) -> None:
        """Wait for the container's API server to be ready."""
        if max_wait is None:
            max_wait = self._api_ready_timeout
        import httpx

        if not self._host_port:
            return

        url = f"http://localhost:{self._host_port}/v1/health"
        start_time = asyncio.get_event_loop().time()

        async with httpx.AsyncClient(timeout=self._health_check_timeout) as client:
            while asyncio.get_event_loop().time() - start_time < max_wait:
                try:
                    response = await client.get(url)
                    if response.status_code == 200:
                        logger.debug("Container API ready", extra={"port": self._host_port})
                        return
                except Exception as e:
                    logger.debug(f"API health check attempt failed: {e}")
                await asyncio.sleep(self._api_ready_check_interval)

        logger.warning("Container API not ready after timeout", extra={"port": self._host_port})

    @property
    def vnc_url(self) -> str | None:
        """Get VNC URL for visual monitoring.

        Returns:
            VNC viewer URL if port is exposed, None otherwise
        """
        if self._host_port:
            return f"http://localhost:{self._host_port}/vnc/index.html?autoconnect=true"
        return None

    @property
    def cdp_url(self) -> str | None:
        """Get CDP URL for Playwright connection.

        Note: Use get_browser_info() for async access with actual CDP URL.
        """
        return None

    @property
    def api_url(self) -> str | None:
        """Get the container's API URL."""
        if self._host_port:
            return f"http://localhost:{self._host_port}"
        return None

    async def stop(self, remove_container: bool = True) -> None:
        """Stop container and optionally remove it.

        Args:
            remove_container: If True (default), stop and remove the container.
                              If False (hot mode), just disconnect but keep container running.
        """
        if not self._container_id:
            return

        container_short = self._container_id[:12]

        if remove_container:
            # Full cleanup - stop and remove container
            logger.debug("Stopping and removing container", extra={"container": container_short})

            # Stop with timeout
            await asyncio.create_subprocess_exec(
                "docker", "stop", "-t", str(self._stop_timeout), self._container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            # Remove container
            await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", self._container_id,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )

            self._container_id = None
            self._host_port = None
            self._started = False

            logger.info("Docker backend stopped and removed", extra={"container": container_short})
        else:
            # Hot mode - just disconnect, keep container running
            logger.info("Docker backend disconnected (container still running)", extra={
                "container": container_short,
                "vnc_url": self.vnc_url,
                "api_url": self.api_url,
            })
            # Don't clear container_id so it can be reconnected
            self._started = False

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute command inside container via docker exec.

        Args:
            command: Shell command to execute
            cwd: Working directory inside container
            timeout: Command timeout in seconds
            env: Environment variables

        Returns:
            CommandResult with exit code, stdout, stderr
        """
        if not self._container_id:
            raise RuntimeError("Container not running")

        work_dir = cwd or self._container_workspace
        cmd_timeout = timeout or self.config.timeout

        # Build docker exec command
        exec_cmd = ["docker", "exec"]

        if env:
            for k, v in env.items():
                exec_cmd.extend(["-e", f"{k}={v}"])

        exec_cmd.extend(["-w", work_dir, self._container_id, "sh", "-c", command])

        start = datetime.now()

        try:
            proc = await asyncio.create_subprocess_exec(
                *exec_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=cmd_timeout,
            )

            duration = (datetime.now() - start).total_seconds() * 1000
            exit_code = proc.returncode or 0

            return CommandResult(
                exit_code=exit_code,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                status=ProcessStatus.COMPLETED if exit_code == 0 else ProcessStatus.FAILED,
                duration_ms=duration,
                command=command,
                cwd=work_dir,
            )
        except TimeoutError:
            logger.warning("Docker command timed out after %ss: %r", cmd_timeout, command)
            duration = (datetime.now() - start).total_seconds() * 1000
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {cmd_timeout}s",
                status=ProcessStatus.TIMEOUT,
                duration_ms=duration,
                command=command,
                cwd=work_dir,
            )

    @property
    def workspace(self) -> str:
        """Get workspace path inside container."""
        return self._container_workspace if self._container_id else str(self.config.workspace)

    def _is_docker_available(self) -> bool:
        """Check if Docker is available."""
        available, _ = get_docker_availability()
        return available

    async def get_docker_metrics(self) -> dict[str, float]:
        """Get Docker container resource metrics.

        Uses `docker stats` to get real container metrics.

        Returns:
            Dict with cpu_pct, mem_pct, mem_used_mb, disk_pct
        """
        if not self._container_id:
            return {"cpu_pct": 0, "mem_pct": 0, "mem_used_mb": 0, "disk_pct": 0}

        return await get_container_metrics(self._container_id)


# ============================================================================
# Docker Container Metrics Utilities
# ============================================================================

async def get_container_metrics(container_id: str) -> dict[str, float]:
    """Get metrics for a Docker container using docker stats.

    Args:
        container_id: Docker container ID or name

    Returns:
        Dict with cpu_pct, mem_pct, mem_used_mb, disk_pct
    """
    metrics = {"cpu_pct": 0, "mem_pct": 0, "mem_used_mb": 0, "disk_pct": 0}

    try:
        # Get CPU and memory stats
        proc = await asyncio.create_subprocess_exec(
            "docker", "stats", "--no-stream", "--format",
            '{"cpu":"{{.CPUPerc}}","mem":"{{.MemPerc}}","mem_usage":"{{.MemUsage}}"}',
            container_id,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode == 0 and stdout:
            import json
            import re

            try:
                data = json.loads(stdout.decode().strip())

                # Parse CPU percentage (e.g., "12.50%")
                cpu_str = data.get("cpu", "0%").replace("%", "")
                metrics["cpu_pct"] = round(float(cpu_str), 1) if cpu_str else 0

                # Parse memory percentage (e.g., "35.20%")
                mem_str = data.get("mem", "0%").replace("%", "")
                metrics["mem_pct"] = round(float(mem_str), 1) if mem_str else 0

                # Parse memory usage (e.g., "512MiB / 2GiB")
                mem_usage = data.get("mem_usage", "0MiB / 0GiB")
                match = re.match(r'([\d.]+)([GMK]i?B)', mem_usage)
                if match:
                    value = float(match.group(1))
                    unit = match.group(2).upper()
                    if 'G' in unit:
                        metrics["mem_used_mb"] = int(value * 1024)
                    elif 'K' in unit:
                        metrics["mem_used_mb"] = int(value / 1024)
                    else:  # MiB
                        metrics["mem_used_mb"] = int(value)
            except (json.JSONDecodeError, ValueError) as e:
                logger.debug(f"Error parsing docker stats: {e}")

        # Get disk usage inside container (run df command)
        proc = await asyncio.create_subprocess_exec(
            "docker", "exec", container_id, "df", "-h", "/workspace",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()

        if proc.returncode == 0 and stdout:
            lines = stdout.decode().strip().split("\n")
            if len(lines) >= 2:
                # Parse df output: "Filesystem Size Used Avail Use% Mounted on"
                parts = lines[1].split()
                if len(parts) >= 5:
                    use_pct = parts[4].replace("%", "")
                    try:
                        metrics["disk_pct"] = round(float(use_pct), 1)
                    except ValueError as e:
                        logger.debug(f"Failed to parse disk usage percentage: {e}")

    except Exception as e:
        logger.debug(f"Error getting container metrics: {e}")

    return metrics


# ============================================================================
# Teaming24 Container Management Utilities
# ============================================================================

async def list_teaming24_containers() -> list:
    """List all Docker containers with teaming24 labels.

    Returns:
        List of container info dicts with id, name, status, created, ports
    """
    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "-a",
        "--filter", f"label={TEAMING24_LABEL_MANAGED}=true",
        "--format", '{"id":"{{.ID}}","name":"{{.Names}}","status":"{{.Status}}","created":"{{.CreatedAt}}","ports":"{{.Ports}}"}',
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()

    containers = []
    if proc.returncode == 0 and stdout:
        import json
        for line in stdout.decode().strip().split("\n"):
            if line:
                try:
                    containers.append(json.loads(line))
                except json.JSONDecodeError as e:
                    logger.debug(f"Failed to parse container JSON: {e}")

    return containers


async def remove_container(container_id_or_name: str, force: bool = True) -> bool:
    """Remove a single Docker container by ID or name.

    Tries direct removal first, then falls back to label-based and
    prefix-based lookup strategies.

    Args:
        container_id_or_name: Container ID, name, or sandbox ID to remove.
        force: Force-remove running containers.

    Returns:
        True if a container was removed, False otherwise.
    """
    # Strategy 1: Direct removal by name/ID
    cmd = ["docker", "rm"]
    if force:
        cmd.append("-f")
    cmd.append(container_id_or_name)
    proc = await asyncio.create_subprocess_exec(
        *cmd, stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
    )
    await proc.wait()
    if proc.returncode == 0:
        return True

    # Strategy 2: Search by teaming24 label + name filter
    proc = await asyncio.create_subprocess_exec(
        "docker", "ps", "-aq",
        "--filter", f"label={TEAMING24_LABEL_MANAGED}=true",
        "--filter", f"name={container_id_or_name}",
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL,
    )
    stdout, _ = await proc.communicate()
    if stdout:
        for cid in stdout.decode().strip().split("\n"):
            if cid:
                rm = await asyncio.create_subprocess_exec(
                    "docker", "rm", "-f", cid,
                    stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
                )
                await rm.wait()
                if rm.returncode == 0:
                    return True

    # Strategy 3: OpenHands prefix patterns
    if container_id_or_name.startswith(OPENHANDS_PREFIX):
        suffix = container_id_or_name[len(OPENHANDS_PREFIX):]
        for prefix in ("openhands_", "oh_", "openhands-"):
            rm = await asyncio.create_subprocess_exec(
                "docker", "rm", "-f", f"{prefix}{suffix}",
                stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
            )
            await rm.wait()
            if rm.returncode == 0:
                return True

    return False


async def cleanup_teaming24_containers(force: bool = False) -> int:
    """Remove all teaming24 Docker containers.

    Args:
        force: Force remove running containers

    Returns:
        Number of containers removed
    """
    containers = await list_teaming24_containers()
    removed = 0

    for container in containers:
        cmd = ["docker", "rm"]
        if force:
            cmd.append("-f")
        cmd.append(container["id"])

        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        await proc.wait()

        if proc.returncode == 0:
            removed += 1
            logger.info(f"[{TEAMING24_CONTAINER_PREFIX}] Removed container: {container['name']}")

    return removed


async def cleanup_teaming24_workspaces() -> int:
    """Clean up orphaned teaming24 workspace directories.

    Returns:
        Number of directories removed
    """
    import shutil as shutil_mod

    from teaming24.runtime.types import TEAMING24_WORKSPACE_BASE

    if not TEAMING24_WORKSPACE_BASE.exists():
        return 0

    removed = 0
    for workspace in TEAMING24_WORKSPACE_BASE.iterdir():
        if workspace.is_dir() and workspace.name.startswith(SANDBOX_PREFIX):
            try:
                shutil_mod.rmtree(workspace)
                removed += 1
                logger.info(f"[{TEAMING24_CONTAINER_PREFIX}] Removed workspace: {workspace}")
            except Exception as e:
                logger.warning(f"[{TEAMING24_CONTAINER_PREFIX}] Failed to remove {workspace}: {e}")

    return removed


__all__ = [
    "DockerBackend",
    "remove_container",
    "list_teaming24_containers",
    "cleanup_teaming24_containers",
    "cleanup_teaming24_workspaces",
]
