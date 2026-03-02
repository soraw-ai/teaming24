"""Process Management - Background process lifecycle."""

from __future__ import annotations

import asyncio
import os
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

from teaming24.runtime.types import ProcessInfo, ProcessStatus, RuntimeConfig
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class ManagedProcess:
    """A managed subprocess."""
    pid: int
    name: str
    command: str
    process: asyncio.subprocess.Process
    started_at: datetime
    status: ProcessStatus = ProcessStatus.RUNNING
    exit_code: int | None = None
    output_buffer: list[str] = field(default_factory=list)
    error_buffer: list[str] = field(default_factory=list)


class ProcessManager:
    """Background process manager."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._processes: dict[int, ManagedProcess] = {}
        self._name_index: dict[str, int] = {}
        self._output_handlers: dict[int, Callable] = {}

    async def start(
        self,
        command: str,
        name: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        on_output: Callable[[str], None] | None = None,
    ) -> ProcessInfo:
        """Start a new process."""
        work_dir = cwd or str(self.config.workspace)

        proc_env = os.environ.copy()
        if env:
            proc_env.update(env)

        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=proc_env,
            start_new_session=True,
        )

        pid = proc.pid
        proc_name = name or f"proc-{pid}"

        managed = ManagedProcess(
            pid=pid,
            name=proc_name,
            command=command,
            process=proc,
            started_at=datetime.now(),
        )

        self._processes[pid] = managed
        self._name_index[proc_name] = pid

        if on_output:
            self._output_handlers[pid] = on_output

        asyncio.create_task(self._read_output(managed))
        asyncio.create_task(self._read_error(managed))
        asyncio.create_task(self._wait_process(managed))

        logger.info("Process started", extra={"pid": pid, "name": proc_name})
        return self._to_info(managed)

    async def _read_output(self, managed: ManagedProcess):
        if managed.process.stdout is None:
            return

        while True:
            try:
                line = await managed.process.stdout.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                managed.output_buffer.append(decoded)

                if len(managed.output_buffer) > 1000:
                    managed.output_buffer = managed.output_buffer[-500:]

                handler = self._output_handlers.get(managed.pid)
                if handler:
                    try:
                        handler(decoded)
                    except Exception as e:
                        logger.debug(f"Error in output handler callback: {e}")
            except Exception as e:
                logger.debug("Output handler loop: %s", e)
                break

    async def _read_error(self, managed: ManagedProcess):
        if managed.process.stderr is None:
            return

        while True:
            try:
                line = await managed.process.stderr.readline()
                if not line:
                    break
                decoded = line.decode("utf-8", errors="replace").rstrip()
                managed.error_buffer.append(decoded)

                if len(managed.error_buffer) > 1000:
                    managed.error_buffer = managed.error_buffer[-500:]
            except Exception as e:
                logger.debug("Error read loop: %s", e)
                break

    async def _wait_process(self, managed: ManagedProcess):
        try:
            exit_code = await managed.process.wait()
            managed.exit_code = exit_code
            managed.status = ProcessStatus.COMPLETED if exit_code == 0 else ProcessStatus.FAILED
        except asyncio.CancelledError:
            logger.debug("Process wait cancelled for pid=%s", managed.pid)
            managed.status = ProcessStatus.KILLED

    async def stop(
        self,
        pid: int | None = None,
        name: str | None = None,
        timeout: float = 5.0,
    ) -> bool:
        """Stop a process gracefully."""
        resolved_pid = self._resolve_pid(pid, name)
        if resolved_pid is None:
            return False

        managed = self._processes.get(resolved_pid)
        if managed is None or managed.status != ProcessStatus.RUNNING:
            return True

        try:
            managed.process.terminate()
            await asyncio.wait_for(managed.process.wait(), timeout=timeout)
            managed.status = ProcessStatus.KILLED
            return True
        except TimeoutError:
            logger.warning("Process stop timed out for pid=%s; forcing kill", managed.pid)
            managed.process.kill()
            managed.status = ProcessStatus.KILLED
            return True
        except Exception as e:
            logger.debug("Process stop failed: %s", e)
            return False

    async def kill(self, pid: int | None = None, name: str | None = None) -> bool:
        """Forcefully kill a process."""
        resolved_pid = self._resolve_pid(pid, name)
        if resolved_pid is None:
            return False

        managed = self._processes.get(resolved_pid)
        if managed is None:
            return False

        try:
            managed.process.kill()
            managed.status = ProcessStatus.KILLED
            return True
        except Exception as e:
            logger.debug("Process kill failed: %s", e)
            return False

    async def get_output(
        self,
        pid: int | None = None,
        name: str | None = None,
        lines: int = 100,
    ) -> list[str]:
        """Get recent output."""
        resolved_pid = self._resolve_pid(pid, name)
        if resolved_pid is None:
            return []

        managed = self._processes.get(resolved_pid)
        return managed.output_buffer[-lines:] if managed else []

    async def get_errors(
        self,
        pid: int | None = None,
        name: str | None = None,
        lines: int = 100,
    ) -> list[str]:
        """Get recent stderr."""
        resolved_pid = self._resolve_pid(pid, name)
        if resolved_pid is None:
            return []

        managed = self._processes.get(resolved_pid)
        return managed.error_buffer[-lines:] if managed else []

    async def info(
        self,
        pid: int | None = None,
        name: str | None = None,
    ) -> ProcessInfo | None:
        """Get process information."""
        resolved_pid = self._resolve_pid(pid, name)
        if resolved_pid is None:
            return None

        managed = self._processes.get(resolved_pid)
        if managed is None:
            return None

        if managed.process.returncode is not None:
            if managed.status == ProcessStatus.RUNNING:
                managed.exit_code = managed.process.returncode
                managed.status = (
                    ProcessStatus.COMPLETED
                    if managed.process.returncode == 0
                    else ProcessStatus.FAILED
                )

        return self._to_info(managed)

    async def list_processes(self) -> list[ProcessInfo]:
        """List all processes."""
        for managed in self._processes.values():
            if managed.process.returncode is not None:
                if managed.status == ProcessStatus.RUNNING:
                    managed.exit_code = managed.process.returncode
                    managed.status = (
                        ProcessStatus.COMPLETED
                        if managed.process.returncode == 0
                        else ProcessStatus.FAILED
                    )

        return [self._to_info(p) for p in self._processes.values()]

    async def cleanup(self, kill_running: bool = True) -> int:
        """Cleanup all processes."""
        count = 0
        for pid, managed in list(self._processes.items()):
            if managed.status == ProcessStatus.RUNNING:
                if kill_running:
                    await self.kill(pid=pid)
                    count += 1
            else:
                del self._processes[pid]
                if managed.name in self._name_index:
                    del self._name_index[managed.name]
        return count

    def _resolve_pid(self, pid: int | None, name: str | None) -> int | None:
        if pid is not None:
            return pid
        if name is not None:
            return self._name_index.get(name)
        return None

    def _to_info(self, managed: ManagedProcess) -> ProcessInfo:
        return ProcessInfo(
            pid=managed.pid,
            name=managed.name,
            status=managed.status,
            command=managed.command,
            started_at=managed.started_at,
        )


__all__ = ["ProcessManager", "ManagedProcess"]
