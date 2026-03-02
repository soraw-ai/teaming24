"""Code Interpreter - Multi-language code execution."""

from __future__ import annotations

import asyncio
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from io import StringIO
from typing import Any

from teaming24.runtime.types import Language, RuntimeConfig, RuntimeError
from teaming24.utils.ids import generic_id
from teaming24.utils.ids import session_id as _make_session_id
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class Output:
    """Single output line from execution."""
    text: str
    stream: str = "stdout"
    timestamp: datetime = field(default_factory=datetime.now)


@dataclass
class ExecError:
    """Execution error details."""
    type: str
    message: str
    trace: list[str] = field(default_factory=list)


@dataclass
class ExecResult:
    """Code execution result."""
    id: str
    output: str = ""
    error: ExecError | None = None
    outputs: list[Output] = field(default_factory=list)
    return_value: Any = None
    duration_ms: int = 0

    @property
    def success(self) -> bool:
        return self.error is None


@dataclass
class Session:
    """Interpreter session for stateful execution."""
    id: str
    language: Language
    created: datetime = field(default_factory=datetime.now)
    exec_count: int = 0
    globals: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecHandlers:
    """Callbacks for execution events."""
    on_output: Callable[[Output], None] | None = None
    on_error: Callable[[ExecError], None] | None = None
    on_done: Callable[[ExecResult], None] | None = None


class CodeInterpreter:
    """Multi-language code interpreter."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._sessions: dict[str, Session] = {}
        self._running = False

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._sessions.clear()
        self._running = False

    async def create_session(
        self,
        language: Language = Language.PYTHON,
        name: str | None = None,
    ) -> Session:
        """Create execution session."""
        self._check_running()

        session_id = name or _make_session_id()
        session = Session(id=session_id, language=language)
        self._sessions[session_id] = session

        return session

    async def close_session(self, session_id: str) -> bool:
        if session_id in self._sessions:
            del self._sessions[session_id]
            return True
        return False

    def get_session(self, session_id: str) -> Session | None:
        return self._sessions.get(session_id)

    async def run(
        self,
        code: str,
        language: Language | None = None,
        session_id: str | None = None,
        timeout: float | None = None,
        handlers: ExecHandlers | None = None,
    ) -> ExecResult:
        """Execute code and return result."""
        self._check_running()

        exec_id = generic_id()[:8]
        exec_timeout = timeout or self.config.timeout
        start = datetime.now()

        session = self._sessions.get(session_id) if session_id else None
        lang = session.language if session else (language or Language.PYTHON)
        exec_globals = session.globals if session else {}

        result = ExecResult(id=exec_id)

        try:
            if lang == Language.PYTHON:
                result = await self._run_python(code, exec_globals, exec_timeout, handlers)
            elif lang == Language.BASH:
                result = await self._run_bash(code, exec_timeout, handlers)
            elif lang == Language.JAVASCRIPT:
                result = await self._run_node(code, exec_timeout, handlers)
            else:
                raise RuntimeError(f"Unsupported language: {lang}")

            if session:
                session.exec_count += 1
                session.globals = exec_globals

        except TimeoutError:
            logger.warning("Interpreter execution timed out after %ss", exec_timeout)
            result.error = ExecError(
                type="TimeoutError",
                message=f"Execution timed out after {exec_timeout}s"
            )
        except Exception as e:
            logger.exception("Interpreter execution failed (%s): %s", lang, e)
            result.error = ExecError(type=type(e).__name__, message=str(e))

        result.id = exec_id
        result.duration_ms = int((datetime.now() - start).total_seconds() * 1000)

        if handlers and handlers.on_done:
            handlers.on_done(result)

        return result

    async def _run_python(
        self,
        code: str,
        exec_globals: dict[str, Any],
        timeout: float,
        handlers: ExecHandlers | None,
    ) -> ExecResult:
        result = ExecResult(id="")

        old_stdout, old_stderr = sys.stdout, sys.stderr
        sys.stdout, sys.stderr = StringIO(), StringIO()

        try:
            compiled = compile(code, "<sandbox>", "exec")
            exec(compiled, exec_globals)

            stdout_val = sys.stdout.getvalue()
            stderr_val = sys.stderr.getvalue()

            if stdout_val:
                output = Output(text=stdout_val.rstrip(), stream="stdout")
                result.outputs.append(output)
                result.output = stdout_val.rstrip()
                if handlers and handlers.on_output:
                    handlers.on_output(output)

            if stderr_val:
                output = Output(text=stderr_val.rstrip(), stream="stderr")
                result.outputs.append(output)
                if handlers and handlers.on_output:
                    handlers.on_output(output)

        except Exception as e:
            import traceback
            logger.exception("Python execution failed: %s", e)
            result.error = ExecError(
                type=type(e).__name__,
                message=str(e),
                trace=traceback.format_exc().split('\n'),
            )
            if handlers and handlers.on_error:
                handlers.on_error(result.error)
        finally:
            sys.stdout, sys.stderr = old_stdout, old_stderr

        return result

    async def _run_bash(
        self,
        code: str,
        timeout: float,
        handlers: ExecHandlers | None,
    ) -> ExecResult:
        result = ExecResult(id="")

        try:
            proc = await asyncio.create_subprocess_shell(
                code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=str(self.config.workspace),
            )

            stdout, stderr = await asyncio.wait_for(
                proc.communicate(),
                timeout=timeout
            )

            if stdout:
                text = stdout.decode().rstrip()
                output = Output(text=text, stream="stdout")
                result.outputs.append(output)
                result.output = text
                if handlers and handlers.on_output:
                    handlers.on_output(output)

            if stderr:
                text = stderr.decode().rstrip()
                output = Output(text=text, stream="stderr")
                result.outputs.append(output)
                if handlers and handlers.on_output:
                    handlers.on_output(output)

            if proc.returncode != 0:
                result.error = ExecError(
                    type="ProcessError",
                    message=f"Exit code: {proc.returncode}"
                )
        except Exception as e:
            logger.exception("Bash execution failed: %s", e)
            result.error = ExecError(type=type(e).__name__, message=str(e))

        return result

    async def _run_node(
        self,
        code: str,
        timeout: float,
        handlers: ExecHandlers | None,
    ) -> ExecResult:
        escaped = code.replace('"', '\\"').replace('`', '\\`')
        cmd = f'node -e "{escaped}"'
        return await self._run_bash(cmd, timeout, handlers)

    def _check_running(self) -> None:
        if not self._running:
            raise RuntimeError("Interpreter not started")

    @property
    def is_running(self) -> bool:
        return self._running


__all__ = [
    "CodeInterpreter",
    "Output",
    "ExecError",
    "ExecResult",
    "Session",
    "ExecHandlers",
]
