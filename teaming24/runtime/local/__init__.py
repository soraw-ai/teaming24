"""Teaming24 Local Runtime - Direct Execution Without Isolation.

This module provides the LocalRuntime class for executing commands directly
on the host machine without Docker isolation. This is intended for development,
debugging, and scenarios where Docker is unavailable or unnecessary.

⚠️  SECURITY WARNING:
    Local mode provides NO ISOLATION. Commands have full access to the host
    filesystem, network, and system resources. Only use with trusted code
    in development environments.

When to Use Local Mode:
    ✓ Development and debugging
    ✓ Environments without Docker
    ✓ Trusted code execution
    ✓ Quick prototyping

    ✗ Production deployments
    ✗ Untrusted code execution
    ✗ Multi-tenant environments
    ✗ Security-sensitive operations

Features:
    - Direct subprocess execution via asyncio
    - No container startup overhead
    - Full access to host environment
    - Simple file operations
    - Workspace directory management

Comparison with Sandbox Mode:
    Feature              | Local       | Sandbox (Docker)
    ---------------------|-------------|------------------
    Isolation            | None        | Container
    Startup Time         | Instant     | ~1-2 seconds
    Resource Limits      | None        | Configurable
    Security             | Low         | High
    Docker Required      | No          | Yes

Usage:
    from teaming24.runtime import LocalRuntime, RuntimeConfig, RuntimeMode

    config = RuntimeConfig(mode=RuntimeMode.LOCAL)

    async with LocalRuntime(config) as runtime:
        # Execute commands directly on host
        result = await runtime.execute("python script.py")
        print(result.stdout)

        # File operations
        content = runtime.read_file("data.txt")
        runtime.write_file("output.txt", "Hello!")

See Also:
    - teaming24.runtime.sandbox: Isolated sandbox execution
    - teaming24.runtime.base.Runtime: Abstract base class
"""

from __future__ import annotations

import asyncio
import os
from datetime import datetime
from pathlib import Path

from teaming24.runtime.base import Runtime
from teaming24.runtime.types import (
    CommandResult,
    FileInfo,
    FileType,
    ProcessStatus,
    RuntimeConfig,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class LocalRuntime(Runtime):
    """Local subprocess-based runtime (no isolation).

    Executes commands directly on the host machine using asyncio subprocesses.
    """

    async def start(self) -> None:
        """Initialize local workspace directory."""
        if not self.config.workspace.exists():
            self.config.workspace.mkdir(parents=True, exist_ok=True)
        self._started = True
        logger.info("LocalRuntime started", extra={"workspace": self.workspace})

    async def stop(self) -> None:
        """Cleanup local runtime."""
        self._started = False
        logger.info("LocalRuntime stopped")

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute command in local subprocess."""
        work_dir = cwd or self.workspace
        cmd_timeout = timeout or self.config.timeout

        run_env = os.environ.copy()
        if env:
            run_env.update(env)

        start_time = datetime.now()

        try:
            proc = await asyncio.create_subprocess_shell(
                command,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
                env=run_env,
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=cmd_timeout,
            )

            duration = (datetime.now() - start_time).total_seconds() * 1000
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
            logger.warning("Local runtime command timed out after %ss: %r", cmd_timeout, command)
            duration = (datetime.now() - start_time).total_seconds() * 1000
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=f"Command timed out after {cmd_timeout}s",
                status=ProcessStatus.TIMEOUT,
                duration_ms=duration,
                command=command,
                cwd=work_dir,
            )

    def read_file(self, path: str, encoding: str = "utf-8") -> str:
        """Read file content."""
        file_path = self._resolve_path(path)
        return file_path.read_text(encoding=encoding)

    def write_file(self, path: str, content: str, encoding: str = "utf-8") -> int:
        """Write file content."""
        file_path = self._resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding=encoding)
        return len(content)

    def list_dir(self, path: str = ".") -> list[FileInfo]:
        """List directory contents."""
        dir_path = self._resolve_path(path)
        results = []

        for entry in dir_path.iterdir():
            stat = entry.stat()
            file_type = FileType.DIRECTORY if entry.is_dir() else FileType.FILE
            if entry.is_symlink():
                file_type = FileType.SYMLINK

            results.append(FileInfo(
                name=entry.name,
                path=str(entry),
                type=file_type,
                size=stat.st_size,
                modified=datetime.fromtimestamp(stat.st_mtime),
            ))

        return results

    def exists(self, path: str) -> bool:
        """Check if file/directory exists."""
        return self._resolve_path(path).exists()

    def _resolve_path(self, path: str) -> Path:
        """Resolve path relative to workspace."""
        p = Path(path)
        if p.is_absolute():
            return p
        return self.config.workspace / path


__all__ = ["LocalRuntime", "RuntimeConfig"]
