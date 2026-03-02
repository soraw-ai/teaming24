"""Teaming24 Sandbox Pool - Hot Sandbox Management for Agentic Networks.

This module manages a pool of "hot" (persistent) sandboxes that remain running
across multiple tasks. Each agent gets a dedicated sandbox that persists until
explicitly deleted or program exit.

Design Philosophy:
    Hot Sandboxes:
        - Containers remain running between tasks
        - Fast execution (no startup overhead)
        - State persists across commands
        - Automatic cleanup on program exit

    One Sandbox Per Agent:
        - Each agent ID maps to exactly one sandbox
        - Sandboxes can be acquired and released
        - Released sandboxes stay running (hot)
        - Only explicit deletion removes the container

Lifecycle:
    1. acquire(agent_id) - Get or create sandbox for agent
    2. Use sandbox for task execution
    3. release(agent_id) - Release back to pool (still running)
    4. (Later) acquire(agent_id) - Reuse same running sandbox
    5. stop(agent_id) - Delete sandbox, container, and files

Cleanup on Deletion:
    When stop(agent_id) is called:
    - Browser sessions are closed
    - Background processes are terminated
    - Docker container is stopped and removed
    - Workspace files are cleaned up
    - All history is cleared

Usage:
    from teaming24.runtime import get_pool

    pool = get_pool()

    # Acquire sandbox for an agent
    sandbox = await pool.acquire("agent-001")

    # Execute commands
    await sandbox.execute("ls -la")

    # Release back to pool (sandbox stays running)
    await pool.release("agent-001")

    # Later, reuse the same sandbox
    sandbox = await pool.acquire("agent-001")  # Instant, no startup

    # Delete sandbox completely
    await pool.stop("agent-001")  # Container removed, files cleaned

See Also:
    - teaming24.runtime.sandbox.Sandbox: Main sandbox class
    - teaming24.runtime.sandbox.docker.DockerBackend: Container management
"""

from __future__ import annotations

import asyncio
import atexit
import shutil
import time
from pathlib import Path
from typing import TYPE_CHECKING

from teaming24.runtime.types import RuntimeConfig, RuntimeMode, SandboxBackend
from teaming24.utils.logger import get_logger
from teaming24.utils.shared import SingletonMixin, sync_async_cleanup

if TYPE_CHECKING:
    from . import Sandbox

logger = get_logger(__name__)


class SandboxPool(SingletonMixin):
    """Pool of hot (persistent) sandboxes for agents.

    Thread-safe singleton via ``SingletonMixin``.
    All sandboxes are "hot" by default -- they persist until explicitly deleted.
    """

    def __init__(self):
        self._sandboxes: dict[str, Sandbox] = {}
        self._in_use: dict[str, bool] = {}
        self._async_lock = asyncio.Lock()
        self._runtime = RuntimeMode.SANDBOX
        self._backend = SandboxBackend.DOCKER
        self._config: RuntimeConfig | None = None
        self._docker_failure_reason: str = ""
        self._docker_failure_logged_at: float = 0.0
        self._docker_failure_cooldown: float = 15.0

    def _on_first_init(self):
        """Register atexit handler so sandboxes are cleaned up on process exit."""
        atexit.register(lambda: sync_async_cleanup(self.shutdown, "sandbox pool shutdown"))

    def configure(
        self,
        runtime: RuntimeMode = RuntimeMode.SANDBOX,
        config: RuntimeConfig | None = None,
    ):
        """Configure default settings."""
        self._runtime = runtime
        self._config = config

    async def acquire(
        self,
        agent_id: str,
        runtime: RuntimeMode | None = None,
        force_new: bool = False,
        workspace: str | Path | None = None,
    ) -> Sandbox:
        """Acquire or create a hot sandbox for an agent.

        If the agent already has a running sandbox, it is returned immediately.
        Otherwise a new sandbox is created and started.

        Args:
            agent_id: Unique agent identifier.
            runtime: Override the default runtime mode.
            force_new: Force creation even if a sandbox already exists.

        Returns:
            A started ``Sandbox`` instance.

        Raises:
            Exception: If sandbox creation or startup fails.
        """
        from . import Sandbox

        async with self._async_lock:
            if agent_id in self._sandboxes and not force_new:
                sandbox = self._sandboxes[agent_id]
                if sandbox._started:
                    self._in_use[agent_id] = True
                    return sandbox
                else:
                    del self._sandboxes[agent_id]

            actual_runtime = runtime or self._runtime
            if actual_runtime == RuntimeMode.SANDBOX:
                from teaming24.runtime.sandbox.docker import get_docker_availability

                docker_ok, docker_reason = get_docker_availability()
                if not docker_ok:
                    now = time.time()
                    should_log = (
                        docker_reason != self._docker_failure_reason
                        or (now - self._docker_failure_logged_at) >= self._docker_failure_cooldown
                    )
                    if should_log:
                        logger.warning(
                            "Docker sandbox unavailable for %s: %s",
                            agent_id,
                            docker_reason,
                        )
                        self._docker_failure_reason = docker_reason
                        self._docker_failure_logged_at = now
                    else:
                        logger.debug("Docker sandbox still unavailable for %s", agent_id)
                    raise RuntimeError(docker_reason)

            logger.info(f"Creating sandbox for {agent_id}", extra={
                "runtime": actual_runtime.value,
            })

            try:
                from teaming24.config import get_config
                _creation_timeout = get_config().runtime.sandbox_pool.creation_timeout
            except Exception as exc:
                logger.debug(
                    "Failed to load runtime.sandbox_pool.creation_timeout; using default: %s",
                    exc,
                    exc_info=True,
                )
                _creation_timeout = 60.0
            sandbox = Sandbox(
                runtime=actual_runtime,
                timeout=_creation_timeout,
                workspace=workspace,
            )

            try:
                await sandbox.start()
            except Exception as e:
                logger.warning(f"Failed to create sandbox for {agent_id}: {e}")
                raise

            self._sandboxes[agent_id] = sandbox
            self._in_use[agent_id] = True

            return sandbox

    async def release(self, agent_id: str):
        """Release sandbox back to pool."""
        async with self._async_lock:
            if agent_id in self._in_use:
                self._in_use[agent_id] = False

    async def stop(self, agent_id: str, cleanup_workspace: bool = True):
        """Stop and delete a specific agent's sandbox.

        This performs a complete cleanup:
        1. Stops the sandbox (closes browser, processes)
        2. Removes the Docker container
        3. Cleans up workspace files (if cleanup_workspace=True)

        Args:
            agent_id: Agent identifier
            cleanup_workspace: If True, delete workspace files
        """
        async with self._async_lock:
            if agent_id in self._sandboxes:
                sandbox = self._sandboxes[agent_id]
                workspace_path = sandbox.config.workspace

                try:
                    # Stop sandbox (handles browser, processes, container)
                    await sandbox.stop()
                    logger.info(f"Stopped sandbox for {agent_id}")
                except Exception as e:
                    logger.warning(f"Error stopping sandbox for {agent_id}: {e}")

                # Cleanup workspace files
                if cleanup_workspace and workspace_path:
                    try:
                        workspace = Path(workspace_path)
                        if workspace.exists():
                            shutil.rmtree(workspace, ignore_errors=True)
                            logger.debug(f"Cleaned up workspace: {workspace}")
                    except Exception as e:
                        logger.warning(f"Error cleaning workspace for {agent_id}: {e}")

                # Remove from tracking
                del self._sandboxes[agent_id]
                self._in_use.pop(agent_id, None)

                logger.info(f"Sandbox {agent_id} deleted completely")

    async def shutdown(self):
        """Stop all sandboxes."""
        async with self._async_lock:
            if not self._sandboxes:
                return

            logger.info(f"Shutting down sandbox pool ({len(self._sandboxes)} sandboxes)")

            tasks = []
            for agent_id, sandbox in list(self._sandboxes.items()):
                tasks.append(self._stop_sandbox(agent_id, sandbox))

            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

            self._sandboxes.clear()
            self._in_use.clear()

    async def _stop_sandbox(self, agent_id: str, sandbox: Sandbox):
        """Stop a single sandbox, logging warnings on failure."""
        try:
            await sandbox.stop()
        except Exception as e:
            logger.warning(f"Error stopping sandbox {agent_id}: {e}")

    def get_sandbox(self, agent_id: str) -> Sandbox | None:
        """Return the sandbox for *agent_id* without marking it as in-use."""
        return self._sandboxes.get(agent_id)

    def is_available(self, agent_id: str) -> bool:
        """Check whether the agent's sandbox exists and is not currently in use."""
        return (
            agent_id in self._sandboxes
            and not self._in_use.get(agent_id, False)
        )

    @property
    def active_count(self) -> int:
        """Number of sandboxes currently in the pool (running or idle)."""
        return len(self._sandboxes)

    @property
    def in_use_count(self) -> int:
        """Number of sandboxes currently acquired by agents."""
        return sum(1 for v in self._in_use.values() if v)

    def status(self) -> dict:
        """Get pool status."""
        return {
            "active": self.active_count,
            "in_use": self.in_use_count,
            "available": self.active_count - self.in_use_count,
            "agents": list(self._sandboxes.keys()),
            "runtime": self._runtime.value,
        }


def get_pool() -> SandboxPool:
    """Get the global sandbox pool instance."""
    return SandboxPool.get_instance()


__all__ = ["SandboxPool", "get_pool"]
