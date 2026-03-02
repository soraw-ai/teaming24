"""
Framework-agnostic sandbox tools for Teaming24 agents.

These tools wrap the ``teaming24.runtime.sandbox`` layer and are
registered via the ``@tool`` decorator so they work with both the
native runtime and the CrewAI adapter (via ``crewai_tool_to_spec``).

Features:
  - Output limits and truncation for large results
  - Structured error returns
  - Rich parameters (timeout, cwd, line ranges, regex, etc.)
  - Secure path sandboxing

Usage::

    from teaming24.agent.tools.sandbox_tools import get_sandbox_tool_specs

    specs = get_sandbox_tool_specs()  # list[ToolSpec]
"""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

from teaming24.agent.tools.base import ToolRegistry, tool
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

MAX_OUTPUT_CHARS = 200_000
TRUNCATION_MSG = "\n\n... [output truncated — {total} chars, showing first {shown}]"


def _truncate(text: str, limit: int = MAX_OUTPUT_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + TRUNCATION_MSG.format(total=len(text), shown=limit)


def _host_fallback_allowed() -> bool:
    return os.getenv("TEAMING24_ALLOW_HOST_TOOLS", "").lower() in ("1", "true", "yes")


def _host_fallback_error() -> str:
    return (
        "ERROR: Sandbox unavailable; host fallback disabled. "
        "Set TEAMING24_ALLOW_HOST_TOOLS=1 to enable."
    )


def _error_with_log(context: str, exc: Exception) -> str:
    logger.warning("[sandbox_tools] %s failed: %s", context, exc, exc_info=True)
    return f"ERROR: {exc}"


def _extract_missing_module(error_text: str) -> str:
    match = re.search(r"No module named ['\"]([^'\"]+)['\"]", str(error_text or ""))
    return str(match.group(1)).strip() if match else ""


def _build_execution_recovery_hint(error_text: str) -> str:
    text = str(error_text or "")
    lower = text.lower()
    hints: list[str] = []
    missing_module = _extract_missing_module(text)
    if missing_module:
        hints.append(
            f"- Missing Python package '{missing_module}'. Install then rerun: "
            f"`shell_exec(\"python3 -m pip install {missing_module}\")`"
        )
    if "command not found" in lower:
        hints.append(
            "- Command/tool missing in environment. Install dependency or use an available alternative command, then rerun."
        )
    if "permission denied" in lower:
        hints.append(
            "- Permission denied. Write outputs to the task workspace/output directory and rerun."
        )
    if not hints:
        return ""
    return "\n\n[recovery_hint]\n" + "\n".join(hints)


# ---------------------------------------------------------------------------
# Lazy sandbox accessors
# ---------------------------------------------------------------------------

async def _get_sandbox_async():
    """Return a Sandbox from the pool (async context)."""
    try:
        from teaming24.runtime.sandbox.pool import SandboxPool
        pool = SandboxPool.get_instance()
        sb = pool.acquire("tools")
        if hasattr(sb, "__await__"):
            sb = await sb
        return sb
    except Exception as exc:
        logger.debug(
            "[sandbox_tools] failed to acquire sandbox from pool, falling back: %s",
            exc,
            exc_info=True,
        )
    try:
        from teaming24.runtime.sandbox import Sandbox
        from teaming24.runtime.types import RuntimeConfig
        return Sandbox(RuntimeConfig())
    except Exception as exc:
        logger.debug("[sandbox_tools] no async sandbox: %s", exc)
        return None


def _get_sandbox_sync():
    """Return a Sandbox for sync tools, or None."""
    try:
        from teaming24.runtime.sandbox import Sandbox
        from teaming24.runtime.types import RuntimeConfig
        return Sandbox(RuntimeConfig())
    except Exception as exc:
        logger.debug("[sandbox_tools] no sync sandbox: %s", exc)
        return None


# ===================================================================
# Shell Execution
# ===================================================================

@tool(
    name="shell_exec",
    description=(
        "Execute a shell command in the sandbox environment. "
        "Returns stdout/stderr with exit code. Supports timeout, "
        "working directory, and environment variables."
    ),
)
async def shell_exec(
    command: str,
    timeout: int = 120,
    cwd: str = "",
    env: dict | None = None,
) -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        if not _host_fallback_allowed():
            return _host_fallback_error()
        return _shell_exec_local(command, timeout, cwd, env)

    try:
        result = await sandbox.execute(command, timeout=timeout)
        stdout = result.get("stdout", "") or ""
        stderr = result.get("stderr", "") or ""
        code = result.get("exit_code", -1)

        output = stdout
        if stderr:
            output += f"\n[stderr]\n{stderr}" if output else stderr

        output = _truncate(output)
        if code != 0:
            return f"[exit code {code}]\n{output}{_build_execution_recovery_hint(output)}"
        return output or "(no output)"
    except Exception as exc:
        return _error_with_log("shell_exec", exc)


def _shell_exec_local(command: str, timeout: int, cwd: str, env: dict) -> str:
    """Fallback: run command on the host via subprocess."""
    try:
        proc = subprocess.run(
            command,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=cwd or None,
            env={**os.environ, **env} if env else None,
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        combined = out + (f"\n[stderr]\n{err}" if err else "")
        combined = _truncate(combined)
        if proc.returncode != 0:
            return f"[exit code {proc.returncode}]\n{combined}"
        return combined or "(no output)"
    except subprocess.TimeoutExpired:
        logger.warning("[sandbox_tools] shell_exec local timed out after %ss", timeout)
        return f"ERROR: Command timed out after {timeout}s"
    except Exception as exc:
        return _error_with_log("shell_exec_local", exc)


# ===================================================================
# File Read
# ===================================================================

@tool(
    name="file_read",
    description=(
        "Read the contents of a file. Supports reading specific line "
        "ranges for large files. Returns the file content with line numbers."
    ),
)
def file_read(
    path: str,
    start_line: int = 0,
    end_line: int = 0,
) -> str:
    sandbox = _get_sandbox_sync()
    if sandbox is not None and hasattr(sandbox, "_fs"):
        try:
            content = sandbox._fs.read(
                path,
                start_line=start_line if start_line > 0 else None,
                end_line=end_line if end_line > 0 else None,
            )
            return _truncate(content)
        except Exception as exc:
            return _error_with_log("file_read (sandbox)", exc)

    if not _host_fallback_allowed():
        return _host_fallback_error()

    try:
        p = Path(path).expanduser()
        if not p.is_file():
            return f"ERROR: File not found: {path}"
        text = p.read_text(encoding="utf-8", errors="replace")
        if start_line > 0 or end_line > 0:
            lines = text.splitlines(keepends=True)
            s = max(start_line - 1, 0)
            e = end_line if end_line > 0 else len(lines)
            text = "".join(lines[s:e])
        return _truncate(text)
    except Exception as exc:
        return _error_with_log("file_read (host)", exc)


# ===================================================================
# File Write
# ===================================================================

@tool(
    name="file_write",
    description=(
        "Write content to a file. Creates parent directories automatically. "
        "Set append=true to append instead of overwrite."
    ),
)
def file_write(
    path: str,
    content: str,
    append: bool = False,
) -> str:
    sandbox = _get_sandbox_sync()
    if sandbox is not None and hasattr(sandbox, "_fs"):
        try:
            sandbox._fs.write(path, content, append=append)
            return f"Wrote {len(content)} chars to {path}"
        except Exception as exc:
            return _error_with_log("file_write (sandbox)", exc)

    if not _host_fallback_allowed():
        return _host_fallback_error()

    try:
        p = Path(path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        if append:
            with p.open("a", encoding="utf-8") as f:
                f.write(content)
        else:
            p.write_text(content, encoding="utf-8")
        return f"Wrote {len(content)} chars to {path}"
    except Exception as exc:
        return _error_with_log("file_write (host)", exc)


# ===================================================================
# File Edit (search & replace)
# ===================================================================

@tool(
    name="file_edit",
    description=(
        "Edit a file by replacing occurrences of old_text with new_text. "
        "Set regex=true to treat old_text as a regular expression."
    ),
)
def file_edit(
    path: str,
    old_text: str,
    new_text: str,
    regex: bool = False,
) -> str:
    sandbox = _get_sandbox_sync()
    if sandbox is not None and hasattr(sandbox, "_fs"):
        try:
            count = sandbox._fs.replace(path, old_text, new_text, regex=regex)
            return f"Replaced {count} occurrence(s) in {path}"
        except Exception as exc:
            return _error_with_log("file_edit (sandbox)", exc)

    if not _host_fallback_allowed():
        return _host_fallback_error()

    try:
        import re as _re
        p = Path(path).expanduser()
        if not p.is_file():
            return f"ERROR: File not found: {path}"
        text = p.read_text(encoding="utf-8")
        if regex:
            new, count = _re.subn(old_text, new_text, text)
        else:
            count = text.count(old_text)
            new = text.replace(old_text, new_text)
        if count == 0:
            return f"No matches found for '{old_text}' in {path}"
        p.write_text(new, encoding="utf-8")
        return f"Replaced {count} occurrence(s) in {path}"
    except Exception as exc:
        return _error_with_log("file_edit (host)", exc)


# ===================================================================
# File Search (grep-like)
# ===================================================================

@tool(
    name="file_search",
    description=(
        "Search for a pattern in file contents (grep-like). "
        "Searches a file or directory recursively. "
        "Returns matching lines with file paths and line numbers."
    ),
)
def file_search(
    pattern: str,
    path: str = ".",
    regex: bool = True,
    max_results: int = 50,
) -> str:
    sandbox = _get_sandbox_sync()
    if sandbox is not None and hasattr(sandbox, "_fs"):
        try:
            matches = sandbox._fs.search(pattern, path, regex=regex, max_results=max_results)
            if not matches:
                return "No matches found."
            lines = [f"{m.path}:{m.line_number}: {m.line.rstrip()}" for m in matches]
            return _truncate("\n".join(lines))
        except Exception as exc:
            return _error_with_log("file_search (sandbox)", exc)

    if not _host_fallback_allowed():
        return _host_fallback_error()

    try:
        import re as _re
        p = Path(path).expanduser().resolve()
        results: list[str] = []
        files = [p] if p.is_file() else sorted(p.rglob("*"))
        compiled = _re.compile(pattern) if regex else None
        for f in files:
            if not f.is_file() or f.stat().st_size > 2_000_000:
                continue
            try:
                for i, line in enumerate(f.read_text(errors="replace").splitlines(), 1):
                    matched = (compiled.search(line) if compiled else pattern in line)
                    if matched:
                        results.append(f"{f}:{i}: {line.rstrip()}")
                        if len(results) >= max_results:
                            break
            except Exception:
                logger.debug(
                    "[sandbox_tools] file_search skipped unreadable file: %s",
                    f,
                    exc_info=True,
                )
                continue
            if len(results) >= max_results:
                break
        if not results:
            return "No matches found."
        return _truncate("\n".join(results))
    except Exception as exc:
        return _error_with_log("file_search (host)", exc)


# ===================================================================
# File List (ls / tree)
# ===================================================================

@tool(
    name="file_list",
    description=(
        "List files and directories. Set recursive=true for a tree view. "
        "Returns file names with types and sizes."
    ),
)
def file_list(
    path: str = ".",
    recursive: bool = False,
    show_hidden: bool = False,
) -> str:
    sandbox = _get_sandbox_sync()
    if sandbox is not None and hasattr(sandbox, "_fs"):
        try:
            entries = sandbox._fs.list_dir(path, recursive=recursive, show_hidden=show_hidden)
            if not entries:
                return "(empty directory)"
            lines = []
            for e in entries:
                prefix = "d " if e.file_type.value == "directory" else "- "
                size = f" ({e.size}B)" if e.size and e.file_type.value != "directory" else ""
                lines.append(f"{prefix}{e.path}{size}")
            return "\n".join(lines)
        except Exception as exc:
            return _error_with_log("file_list (sandbox)", exc)

    if not _host_fallback_allowed():
        return _host_fallback_error()

    try:
        p = Path(path).expanduser().resolve()
        if not p.exists():
            return f"ERROR: Path not found: {path}"
        if p.is_file():
            return f"- {p.name} ({p.stat().st_size}B)"
        entries = sorted(p.rglob("*") if recursive else p.iterdir())
        lines: list[str] = []
        for e in entries:
            if not show_hidden and e.name.startswith("."):
                continue
            rel = e.relative_to(p)
            prefix = "d " if e.is_dir() else "- "
            size = f" ({e.stat().st_size}B)" if e.is_file() else ""
            lines.append(f"{prefix}{rel}{size}")
        return "\n".join(lines) if lines else "(empty directory)"
    except Exception as exc:
        return _error_with_log("file_list (host)", exc)


# ===================================================================
# File Find (glob)
# ===================================================================

@tool(
    name="file_find",
    description=(
        "Find files matching a glob pattern (e.g. '*.py', '**/*.json'). "
        "Returns matching file paths."
    ),
)
def file_find(
    pattern: str,
    path: str = ".",
    max_results: int = 100,
) -> str:
    sandbox = _get_sandbox_sync()
    if sandbox is not None and hasattr(sandbox, "_fs"):
        try:
            matches = sandbox._fs.find(pattern, path, max_results=max_results)
            if not matches:
                return "No files found."
            return "\n".join(matches)
        except Exception as exc:
            return _error_with_log("file_find (sandbox)", exc)

    if not _host_fallback_allowed():
        return _host_fallback_error()

    try:
        p = Path(path).expanduser().resolve()
        matches = sorted(p.glob(pattern))[:max_results]
        if not matches:
            return "No files found."
        return "\n".join(str(m.relative_to(p)) for m in matches)
    except Exception as exc:
        return _error_with_log("file_find (host)", exc)


# ===================================================================
# Python Execution
# ===================================================================

@tool(
    name="python_exec",
    description=(
        "Execute Python code and return the output. "
        "Code runs in an isolated interpreter session."
    ),
)
async def python_exec(code: str) -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is not None:
        try:
            result = await sandbox.run_code(code, language="python")
            output = result.get("output", "") or ""
            error = result.get("error", "") or ""
            if error:
                return f"ERROR:\n{error}{_build_execution_recovery_hint(error)}"
            return _truncate(output) or "(no output)"
        except Exception as exc:
            return _error_with_log("python_exec (sandbox)", exc)

    try:
        proc = subprocess.run(
            ["python3", "-c", code],
            capture_output=True, text=True, timeout=60,
        )
        out = proc.stdout or ""
        err = proc.stderr or ""
        if proc.returncode != 0:
            combined_err = err or out
            return (
                f"ERROR (exit {proc.returncode}):\n"
                f"{combined_err}{_build_execution_recovery_hint(combined_err)}"
            )
        return _truncate(out) or "(no output)"
    except subprocess.TimeoutExpired:
        logger.warning("[sandbox_tools] python_exec local timed out")
        return "ERROR: Execution timed out (60s)"
    except Exception as exc:
        return _error_with_log("python_exec (host)", exc)


# ===================================================================
# Browser Navigate
# ===================================================================

@tool(
    name="browser_navigate",
    description=(
        "Navigate to a URL and return the page text content. "
        "Use this to fetch web pages, API responses, or documentation."
    ),
)
async def browser_navigate(url: str) -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        return "ERROR: No sandbox with browser capability available"
    try:
        caps = sandbox.get_capabilities() if hasattr(sandbox, "get_capabilities") else {}
        if isinstance(caps, dict) and not caps.get("browser", True):
            return "ERROR: Browser not available in sandbox"
        result = await sandbox.goto(url)
        content = result.get("content", "") or result.get("text", "")
        return _truncate(content) if content else f"Navigated to {url} (no text content)"
    except Exception as exc:
        return _error_with_log("browser_navigate", exc)


# ===================================================================
# Browser Screenshot
# ===================================================================

@tool(
    name="browser_screenshot",
    description="Take a screenshot of the current browser page. Returns the file path.",
)
async def browser_screenshot(path: str = "screenshot.png") -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        return "ERROR: No sandbox available"
    try:
        result = await sandbox.screenshot()
        img_data = result.get("data") or result.get("image")
        if img_data:
            import base64
            p = Path(path)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(base64.b64decode(img_data))
            return f"Screenshot saved to {path}"
        return "Screenshot captured (no file saved)"
    except Exception as exc:
        return _error_with_log("browser_screenshot", exc)


# ===================================================================
# Browser Action (click, type, fill, etc.)
# ===================================================================

@tool(
    name="browser_action",
    description=(
        "Perform a browser action: click, type, fill, select, scroll, "
        "wait_for, or evaluate JavaScript. Specify the action and target selector."
    ),
)
async def browser_action(
    action: str,
    selector: str = "",
    value: str = "",
    timeout: int = 30,
) -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        return "ERROR: No sandbox with browser available"

    try:
        if action == "click":
            result = await sandbox.click(selector)
        elif action == "type":
            result = await sandbox.type_text(selector, value)
        elif action == "fill":
            result = await sandbox.fill(selector, value) if hasattr(sandbox, "fill") else await sandbox.type_text(selector, value)
        elif action == "select":
            result = await sandbox.select(selector, value) if hasattr(sandbox, "select") else {"error": "select not supported"}
        elif action == "scroll":
            direction = value or "down"
            result = await sandbox.scroll(direction) if hasattr(sandbox, "scroll") else {"error": "scroll not supported"}
        elif action == "wait_for":
            result = await sandbox.wait_for(selector, timeout=timeout) if hasattr(sandbox, "wait_for") else {"error": "wait_for not supported"}
        elif action == "evaluate":
            result = await sandbox.evaluate(value) if hasattr(sandbox, "evaluate") else {"error": "evaluate not supported"}
        elif action == "get_text":
            result = await sandbox.get_text(selector) if hasattr(sandbox, "get_text") else {"error": "get_text not supported"}
        elif action == "get_content":
            result = await sandbox.get_content() if hasattr(sandbox, "get_content") else {"error": "get_content not supported"}
        else:
            return f"ERROR: Unknown action '{action}'. Supported: click, type, fill, select, scroll, wait_for, evaluate, get_text, get_content"

        if isinstance(result, dict):
            if result.get("error"):
                return f"ERROR: {result['error']}"
            return str(result.get("result", result.get("content", "Action completed")))
        return str(result) if result else "Action completed"
    except Exception as exc:
        return _error_with_log("browser_action", exc)


# ===================================================================
# Process Management
# ===================================================================

@tool(
    name="process_start",
    description="Start a long-running background process. Returns the process ID.",
)
async def process_start(command: str, name: str = "") -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        return "ERROR: No sandbox available"
    try:
        result = await sandbox.start_process(command, name=name or command.split()[0])
        pid = result.get("pid", "?")
        return f"Process started: PID={pid}, name={name or command.split()[0]}"
    except Exception as exc:
        return _error_with_log("process_start", exc)


@tool(
    name="process_list",
    description="List all running background processes in the sandbox.",
)
async def process_list() -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        return "ERROR: No sandbox available"
    try:
        procs = await sandbox.list_processes() if hasattr(sandbox, "list_processes") else []
        if not procs:
            return "No running processes."
        lines = []
        for p in procs:
            if isinstance(p, dict):
                lines.append(f"PID={p.get('pid','?')}  {p.get('name','')}  [{p.get('status','?')}]")
            else:
                lines.append(str(p))
        return "\n".join(lines)
    except Exception as exc:
        return _error_with_log("process_list", exc)


@tool(
    name="process_stop",
    description="Stop a background process by PID or name.",
)
async def process_stop(pid: str = "", name: str = "") -> str:
    sandbox = await _get_sandbox_async()
    if sandbox is None:
        return "ERROR: No sandbox available"
    try:
        target = pid or name
        if not target:
            return "ERROR: Provide either pid or name"
        await sandbox.stop_process(target)
        return f"Process {target} stopped"
    except Exception as exc:
        return _error_with_log("process_stop", exc)


# ===================================================================
# Registry
# ===================================================================

_registry: ToolRegistry | None = None


def get_sandbox_registry() -> ToolRegistry:
    """Return a ToolRegistry pre-loaded with all sandbox tools."""
    global _registry
    if _registry is not None:
        return _registry

    _registry = ToolRegistry()
    for fn in [
        shell_exec,
        file_read,
        file_write,
        file_edit,
        file_search,
        file_list,
        file_find,
        python_exec,
        browser_navigate,
        browser_screenshot,
        browser_action,
        process_start,
        process_list,
        process_stop,
    ]:
        _registry.register(fn)
    return _registry


def get_sandbox_tool_specs():
    """Return all sandbox tools as a list of ToolSpec objects."""
    return get_sandbox_registry().get_specs()
