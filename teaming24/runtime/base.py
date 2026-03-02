"""Teaming24 Runtime Base - Abstract Foundation for Execution Backends.

This module defines the abstract Runtime base class that all execution
backends must implement. It establishes the contract for sandbox and
local runtimes in the Teaming24 framework.

Design Pattern:
    Template Method Pattern - Concrete implementations override abstract
    methods while inheriting common functionality.

Abstract Methods:
    start()   - Initialize and start the runtime environment
    stop()    - Clean up and stop the runtime
    execute() - Execute a shell command and return results

Properties:
    workspace   - Path to the working directory
    is_running  - Whether the runtime is currently active

Usage:
    # Runtime is an abstract class, use concrete implementations:
    from teaming24.runtime import Sandbox, LocalRuntime

    # Or create custom runtime:
    from teaming24.runtime.base import Runtime

    class MyRuntime(Runtime):
        async def start(self) -> None:
            # Initialize your runtime
            pass

        async def stop(self) -> None:
            # Clean up your runtime
            pass

        async def execute(self, command, ...) -> CommandResult:
            # Execute command in your runtime
            pass

See Also:
    - teaming24.runtime.sandbox.DockerBackend: Docker container runtime
    - teaming24.runtime.sandbox.APIBackend: HTTP API runtime
    - teaming24.runtime.local.LocalRuntime: Direct subprocess runtime
"""

from __future__ import annotations

from abc import ABC, abstractmethod

from teaming24.runtime.types import CommandResult, RuntimeConfig


class Runtime(ABC):
    """Abstract base class for runtime backends.

    All runtime implementations must inherit from this class and implement
    the required abstract methods.
    """

    def __init__(self, config: RuntimeConfig):
        """Initialize runtime with configuration.

        Args:
            config: RuntimeConfig instance
        """
        self.config = config
        self._started = False

    @abstractmethod
    async def start(self) -> None:
        """Initialize and start the runtime."""
        pass

    @abstractmethod
    async def stop(self) -> None:
        """Stop and cleanup the runtime."""
        pass

    @abstractmethod
    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute a shell command.

        Args:
            command: Shell command to execute
            cwd: Working directory
            timeout: Command timeout in seconds
            env: Environment variables

        Returns:
            CommandResult with exit code, stdout, stderr
        """
        pass

    @property
    def workspace(self) -> str:
        """Get workspace path."""
        return str(self.config.workspace)

    @property
    def is_running(self) -> bool:
        """Check if runtime is active."""
        return self._started

    async def __aenter__(self) -> Runtime:
        """Async context manager entry."""
        await self.start()
        return self

    async def __aexit__(self, *args) -> None:
        """Async context manager exit."""
        await self.stop()


__all__ = ["Runtime"]
