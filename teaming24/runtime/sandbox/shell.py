"""Shell Session Management.

Persistent shell sessions with environment and working directory state.
"""

from __future__ import annotations

import asyncio
import os
import shutil
from datetime import datetime
from pathlib import Path

from teaming24.runtime.types import (
    CommandResult,
    ProcessStatus,
    RuntimeConfig,
    SessionError,
    ShellSession,
)
from teaming24.runtime.types import (
    TimeoutError as RuntimeTimeout,
)
from teaming24.utils.ids import session_id as _make_session_id
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class ShellManager:
    """Shell session manager for command execution."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._sessions: dict[str, ShellSession] = {}
        self._locks: dict[str, asyncio.Lock] = {}

    async def create_session(
        self,
        session_id: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellSession:
        """Create a new shell session."""
        sid = session_id or _make_session_id()

        if sid in self._sessions:
            return self._sessions[sid]

        work_dir = cwd or str(self.config.workspace)
        work_path = Path(work_dir)
        if not work_path.exists():
            work_path.mkdir(parents=True, exist_ok=True)

        session_env = os.environ.copy()
        session_env["HOME"] = str(self.config.workspace)
        session_env["SHELL"] = shutil.which("bash") or shutil.which("sh") or "/bin/sh"
        if env:
            session_env.update(env)

        now = datetime.now()
        session = ShellSession(
            id=sid,
            cwd=work_dir,
            env=session_env,
            created_at=now,
            last_activity=now,
        )

        self._sessions[sid] = session
        self._locks[sid] = asyncio.Lock()

        logger.info("Session created", extra={"id": sid, "cwd": work_dir})
        return session

    async def get_session(self, session_id: str) -> ShellSession:
        """Get existing session by ID."""
        if session_id not in self._sessions:
            raise SessionError(f"Session not found: {session_id}")
        return self._sessions[session_id]

    async def execute(
        self,
        command: str,
        session_id: str | None = None,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute command in session."""
        if session_id and session_id in self._sessions:
            session = self._sessions[session_id]
        else:
            session = await self.create_session(session_id, cwd, env)

        async with self._locks[session.id]:
            return await self._run_command(session, command, cwd, timeout, env)

    async def _run_command(
        self,
        session: ShellSession,
        command: str,
        cwd: str | None,
        timeout: float | None,
        env: dict[str, str] | None,
    ) -> CommandResult:
        """Internal command execution."""
        work_dir = cwd or session.cwd
        cmd_timeout = timeout or self.config.timeout

        run_env = session.env.copy()
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

            session.last_activity = datetime.now()
            if cwd:
                session.cwd = cwd

            return CommandResult(
                exit_code=exit_code,
                stdout=stdout.decode("utf-8", errors="replace"),
                stderr=stderr.decode("utf-8", errors="replace"),
                status=ProcessStatus.COMPLETED if exit_code == 0 else ProcessStatus.FAILED,
                duration_ms=duration,
                command=command,
                cwd=work_dir,
            )

        except TimeoutError as e:
            duration = (datetime.now() - start_time).total_seconds() * 1000
            raise RuntimeTimeout(f"Command timed out after {cmd_timeout}s") from e
        except Exception as e:
            logger.exception("Shell execution failed for command=%r: %s", command, e)
            duration = (datetime.now() - start_time).total_seconds() * 1000
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                status=ProcessStatus.FAILED,
                duration_ms=duration,
                command=command,
                cwd=work_dir,
            )

    async def close_session(self, session_id: str) -> bool:
        """Close and cleanup a session."""
        if session_id not in self._sessions:
            return False

        session = self._sessions[session_id]
        session.is_active = False

        del self._sessions[session_id]
        del self._locks[session_id]

        return True

    async def cleanup_all(self) -> int:
        """Close all sessions."""
        count = len(self._sessions)
        for sid in list(self._sessions.keys()):
            await self.close_session(sid)
        return count


class SyncShellManager:
    """Synchronous wrapper for ShellManager."""

    def __init__(self, config: RuntimeConfig):
        self._async_manager = ShellManager(config)
        self._loop = None

    def _run(self, coro):
        try:
            return asyncio.get_running_loop().run_until_complete(coro)
        except RuntimeError:
            logger.debug("SyncShellManager._run using dedicated loop (no active loop in current thread)")
            if self._loop is None or self._loop.is_closed():
                self._loop = asyncio.new_event_loop()
            return self._loop.run_until_complete(coro)

    def execute(self, command: str, **kwargs) -> CommandResult:
        return self._run(self._async_manager.execute(command, **kwargs))

    def create_session(self, **kwargs) -> ShellSession:
        return self._run(self._async_manager.create_session(**kwargs))

    def cleanup_all(self) -> int:
        return self._run(self._async_manager.cleanup_all())


__all__ = ["ShellManager", "SyncShellManager"]
