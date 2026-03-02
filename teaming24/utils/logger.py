"""Production-level Logging for Teaming24.

Features:
    - Colored console output (development)
    - JSON structured logs (production)
    - File rotation with configurable size/count
    - Module-level loggers with easy access
    - Context support (request_id, user_id, etc.)
    - **Source categorization** with agent identity tagging

Log Source Categories:
    SYSTEM  -- Server startup, config, lifecycle
    AGENT   -- Local agent execution (organizer, coordinator, workers)
    SANDBOX -- Sandbox/Docker/OpenHands runtime
    NETWORK -- P2P, discovery, central server (AN nodes)
    API     -- HTTP endpoints, SSE
    TASK    -- Task management
    PAYMENT -- x402, wallet

Log Level Guidelines:
    DEBUG    -- Internal state changes, variable dumps, tool input/output
    INFO     -- Lifecycle events (agent created, sandbox started, task completed)
    WARNING  -- Recoverable issues (retry, fallback, heartbeat timeout)
    ERROR    -- Failures that affect functionality (tool failure, sandbox crash)
    CRITICAL -- System-level failures (cannot start server, DB corruption)

Usage:
    from teaming24.utils.logger import get_logger, get_agent_logger, setup_logging

    # Plain module logger (uses module path as source)
    logger = get_logger(__name__)
    logger.info("Hello world")

    # Agent-aware logger with identity tag
    agent_logger = get_agent_logger(LogSource.AGENT, "organizer")
    agent_logger.info("Creating task plan...")
    # Output: 2026-02-05 14:23:01 INFO    [AGENT:organizer]    Creating task plan...

    # Network node logger
    net_logger = get_agent_logger(LogSource.NETWORK, "node-alice")
    net_logger.warning("Heartbeat timeout, reconnecting")
    # Output: 2026-02-05 14:23:06 WARNING [AN:node-alice]      Heartbeat timeout...

    # Sandbox logger
    sbx_logger = get_agent_logger(LogSource.SANDBOX, "sbx-abc123")
    sbx_logger.info("Container started")
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

# =============================================================================
# Log Source Categories
# =============================================================================


class LogSource:
    """Log source category constants.

    Use these when calling ``get_agent_logger`` to tag log lines by subsystem.
    """

    SYSTEM = "SYSTEM"    # Server startup, config, lifecycle
    AGENT = "AGENT"      # Local agent execution
    SANDBOX = "SANDBOX"  # Sandbox/Docker/OpenHands runtime
    NETWORK = "AN"       # P2P, discovery, central server (Agentic Network)
    API = "API"          # HTTP endpoints, SSE
    TASK = "TASK"        # Task management
    PAYMENT = "PAYMENT"  # x402, wallet


# =============================================================================
# Context Management
# =============================================================================


_request_context: ContextVar[dict[str, Any] | None] = ContextVar("request_context", default=None)


def set_context(**kwargs) -> None:
    """Set context variables for current async context.

    Example:
        set_context(request_id="abc123", user_id="user1")
    """
    ctx = (_request_context.get() or {}).copy()
    ctx.update(kwargs)
    _request_context.set(ctx)


def clear_context() -> None:
    """Clear all context variables."""
    _request_context.set(None)


def get_context() -> dict[str, Any]:
    """Get current context dictionary."""
    return (_request_context.get() or {}).copy()


# =============================================================================
# Configuration
# =============================================================================


@dataclass
class LogConfig:
    """Logging configuration."""

    level: str = "INFO"
    format: str = "text"  # "text" (colored) or "json" (structured)
    file: str | None = None
    file_max_bytes: int = 10 * 1024 * 1024  # 10 MB
    file_backup_count: int = 5
    console: bool = True
    include_context: bool = True

    @classmethod
    def from_env(cls) -> LogConfig:
        """Load config from environment variables."""
        return cls(
            level=os.getenv("LOG_LEVEL", "INFO"),
            format=os.getenv("LOG_FORMAT", "text"),
            file=os.getenv("LOG_FILE"),
            console=os.getenv("LOG_CONSOLE", "true").lower() == "true",
        )


_config: LogConfig | None = None
_initialized: bool = False


# =============================================================================
# Formatters
# =============================================================================


class ColorFormatter(logging.Formatter):
    """Colored formatter with source/identity tag support.

    Output format::

        DATETIME LEVEL [SOURCE:identity] MESSAGE
        DATETIME LEVEL module_name       MESSAGE   (when no identity tag)
    """

    COLORS = {
        "DEBUG": "\033[36m",     # Cyan
        "INFO": "\033[32m",      # Green
        "WARNING": "\033[33m",   # Yellow
        "ERROR": "\033[31m",     # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"
    GRAY = "\033[90m"
    BOLD = "\033[1m"

    # Color for source tags
    SOURCE_COLORS = {
        LogSource.SYSTEM:  "\033[37m",   # White
        LogSource.AGENT:   "\033[36m",   # Cyan
        LogSource.SANDBOX: "\033[34m",   # Blue
        LogSource.NETWORK: "\033[35m",   # Magenta
        LogSource.API:     "\033[33m",   # Yellow
        LogSource.TASK:    "\033[32m",   # Green
        LogSource.PAYMENT: "\033[33m",   # Yellow
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.COLORS.get(record.levelname, "")
        ts = datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S")

        # Build source tag: [SOURCE:identity] or plain module name
        source = getattr(record, "log_source", "")
        identity = getattr(record, "log_identity", "")

        if source:
            src_color = self.SOURCE_COLORS.get(source, self.GRAY)
            if identity:
                tag = f"{src_color}[{source}:{identity}]{self.RESET}"
            else:
                tag = f"{src_color}[{source}]{self.RESET}"
            tag_plain_len = len(f"[{source}:{identity}]") if identity else len(f"[{source}]")
            # Pad to 25 chars for alignment
            pad = max(0, 25 - tag_plain_len)
            tag = tag + " " * pad
        else:
            # Fallback: short module name
            name = record.name
            if name.startswith("teaming24."):
                name = name[10:]
            elif name == "root":
                name = "system"
            if len(name) > 20:
                name = name[-20:]
            tag = f"{self.GRAY}{name:20}{self.RESET}"

        msg = f"{self.GRAY}{ts}{self.RESET} {color}{record.levelname:7}{self.RESET} {tag} {record.getMessage()}"

        # Append file:line for DEBUG in teaming24 modules
        if record.levelno <= logging.DEBUG and "teaming24" in getattr(record, "pathname", ""):
            filename = os.path.basename(record.pathname)
            msg = f"{msg} {self.GRAY}[{filename}:{record.lineno}]{self.RESET}"

        if record.exc_info:
            msg = f"{msg}\n{self.formatException(record.exc_info)}"

        return msg


class JsonFormatter(logging.Formatter):
    """JSON formatter for structured logging, includes source/identity fields."""

    def format(self, record: logging.LogRecord) -> str:
        log_data: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }

        # Source/identity
        source = getattr(record, "log_source", "")
        identity = getattr(record, "log_identity", "")
        if source:
            log_data["source"] = source
        if identity:
            log_data["identity"] = identity

        # Location
        if record.pathname:
            log_data["location"] = {
                "file": record.pathname,
                "line": record.lineno,
                "function": record.funcName,
            }

        # Context + extra
        ctx = getattr(record, "context", {})
        skip_keys = {"message", "context", "taskName", "log_source", "log_identity"}
        extra_data = {
            k: v for k, v in record.__dict__.items()
            if k not in logging.LogRecord.__dict__
            and not k.startswith("_")
            and k not in skip_keys
        }
        ctx.update(extra_data)
        if ctx:
            log_data["context"] = ctx

        if record.exc_info:
            log_data["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_data, default=str, ensure_ascii=False)


# =============================================================================
# Filters
# =============================================================================


class ContextFilter(logging.Filter):
    """Injects context variables into every log record."""

    def filter(self, record: logging.LogRecord) -> bool:
        record.context = get_context()  # type: ignore[attr-defined]
        return True


class VerboseFilter(logging.Filter):
    """Suppresses noisy third-party log messages."""

    SUPPRESS_PATTERNS = [
        "OpenAI API usage",
        "Anthropic API usage",
        "Token usage",
        "prompt_tokens",
        "completion_tokens",
        "total_tokens",
        "Usage:",
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        if record.name.startswith("teaming24"):
            return True
        msg = record.getMessage()
        return not any(p in msg for p in self.SUPPRESS_PATTERNS)


class NoisyAccessFilter(logging.Filter):
    """Suppresses high-frequency polling endpoints from uvicorn access logs.

    Install on the ``uvicorn.access`` logger *after* uvicorn has initialised
    its own logging (e.g. inside the FastAPI lifespan startup handler), so
    that uvicorn does not override the filter.

    Example::

        from teaming24.utils.logger import NoisyAccessFilter, get_logger
        get_logger("uvicorn.access").addFilter(NoisyAccessFilter())
    """

    SUPPRESS_PATHS: list[str] = [
        "/api/bash/bash_events/search",
        "/api/bash/",  # All bash/OpenHands polling endpoints
    ]

    def filter(self, record: logging.LogRecord) -> bool:
        """Return False to suppress the log record."""
        if record.name != "uvicorn.access":
            return True
        msg = record.getMessage()
        return not any(p in msg for p in self.SUPPRESS_PATHS)


class DockerStdoutFilter:
    """Wraps sys.stdout to suppress noisy ``[DOCKER]`` container log lines.

    OpenHands' docker workspace streams the sandbox container's stdout via
    ``docker logs -f``, prefixing every line with ``[DOCKER]``.  Most of
    these are high-frequency uvicorn HTTP access logs that add noise without
    value.  This wrapper drops them while passing everything else through.
    """

    # Substrings that identify a [DOCKER] line as suppressible
    _SUPPRESS: tuple[str, ...] = (
        '"name": "uvicorn.access"',
        "uvicorn.access",
    )

    def __init__(self, stream: Any) -> None:
        self._stream = stream

    def write(self, s: str) -> int:
        if s.startswith("[DOCKER]") and any(p in s for p in self._SUPPRESS):
            return len(s)
        return self._stream.write(s)

    def flush(self) -> None:
        self._stream.flush()

    def __getattr__(self, name: str) -> Any:
        return getattr(self._stream, name)


# =============================================================================
# Logger Setup
# =============================================================================


def setup_logging(
    level: str | None = None,
    format: str | None = None,
    file: str | None = None,
    console: bool = True,
    config: LogConfig | None = None,
) -> None:
    """Initialize the logging system.

    Args:
        level: Log level string or ``LogConfig`` instance (for backward compat).
        format: Output format (``"text"`` or ``"json"``).
        file: Log file path (enables rotating file handler).
        console: Enable console output.
        config: ``LogConfig`` object (overrides individual args).

    Example::

        # Development
        setup_logging(level="DEBUG", format="text")

        # Production
        setup_logging(level="INFO", format="json", file="/var/log/teaming24/app.log")
    """
    global _config, _initialized

    # Support passing LogConfig as first positional arg
    if isinstance(level, LogConfig):
        config = level
        level = None

    if config:
        _config = config
    else:
        _config = LogConfig(
            level=level or os.getenv("LOG_LEVEL", "INFO"),
            format=format or os.getenv("LOG_FORMAT", "text"),
            file=file or os.getenv("LOG_FILE"),
            console=console,
        )

    root = logging.getLogger()
    root.setLevel(getattr(logging, _config.level.upper(), logging.INFO))
    root.handlers.clear()

    ctx_filter = ContextFilter()
    verbose_filter = VerboseFilter()

    # Console handler
    if _config.console:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.addFilter(ctx_filter)
        console_handler.addFilter(verbose_filter)
        if _config.format == "json":
            console_handler.setFormatter(JsonFormatter())
        else:
            console_handler.setFormatter(ColorFormatter())
        root.addHandler(console_handler)

    # File handler
    if _config.file:
        log_path = Path(_config.file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            _config.file,
            maxBytes=_config.file_max_bytes,
            backupCount=_config.file_backup_count,
            encoding="utf-8",
        )
        file_handler.addFilter(ctx_filter)
        file_handler.setFormatter(JsonFormatter())
        root.addHandler(file_handler)

    # Suppress noisy third-party loggers
    for name in (
        "httpx", "httpcore", "urllib3", "asyncio",
        "crewai", "openai", "anthropic", "litellm",
        "langchain", "langsmith", "chromadb",
        "websockets", "websockets.client", "websockets.server", "websockets.protocol",
        "multipart", "watchfiles", "uvicorn.access",
    ):
        logging.getLogger(name).setLevel(logging.WARNING)

    # Root logger only shows warnings+ (third-party)
    root.setLevel(logging.WARNING)

    # Teaming24 loggers use the configured level
    teaming24_logger = logging.getLogger("teaming24")
    teaming24_logger.setLevel(getattr(logging, _config.level.upper(), logging.INFO))

    # Suppress noisy [DOCKER] lines written directly to sys.stdout by
    # OpenHands' docker workspace log-streaming thread.
    if not isinstance(sys.stdout, DockerStdoutFilter):
        sys.stdout = DockerStdoutFilter(sys.stdout)  # type: ignore[assignment]

    _initialized = True


# =============================================================================
# Logger Access
# =============================================================================


def get_logger(name: str) -> logging.Logger:
    """Get a module-level logger (uses module path as the name column).

    Args:
        name: Logger name, typically ``__name__``.

    Returns:
        Standard ``logging.Logger`` instance.

    Example::

        logger = get_logger(__name__)
        logger.info("Server started")
    """
    if not _initialized:
        setup_logging()
    return logging.getLogger(name)


def get_agent_logger(source: str, identity: str = "") -> logging.LoggerAdapter:
    """Get a logger tagged with a source category and identity.

    The returned ``LoggerAdapter`` injects ``log_source`` and ``log_identity``
    into every record so the formatter can render ``[SOURCE:identity]``.

    Args:
        source: One of ``LogSource.*`` constants (e.g. ``LogSource.AGENT``).
        identity: Agent role/name, node name, or sandbox ID.

    Returns:
        ``logging.LoggerAdapter`` that auto-tags all messages.

    Examples::

        agent_log = get_agent_logger(LogSource.AGENT, "organizer")
        agent_log.info("Creating task plan...")
        # -> 2026-02-05 14:23:01 INFO    [AGENT:organizer]    Creating task plan...

        net_log = get_agent_logger(LogSource.NETWORK, "node-alice")
        net_log.warning("Heartbeat timeout")
        # -> 2026-02-05 14:23:06 WARNING [AN:node-alice]       Heartbeat timeout

        sys_log = get_agent_logger(LogSource.SYSTEM)
        sys_log.info("Config reloaded")
        # -> 2026-02-05 14:23:07 INFO    [SYSTEM]              Config reloaded
    """
    if not _initialized:
        setup_logging()

    # Use a sub-logger under teaming24 so level config applies
    logger_name = f"teaming24.{source.lower()}"
    if identity:
        logger_name = f"{logger_name}.{identity}"

    base_logger = logging.getLogger(logger_name)
    return logging.LoggerAdapter(base_logger, {"log_source": source, "log_identity": identity})


# =============================================================================
# Convenience Functions
# =============================================================================


def debug(msg: str, **kwargs) -> None:
    """Log debug message to the root teaming24 logger."""
    get_logger("teaming24").debug(msg, extra=kwargs)


def info(msg: str, **kwargs) -> None:
    """Log info message to the root teaming24 logger."""
    get_logger("teaming24").info(msg, extra=kwargs)


def warning(msg: str, **kwargs) -> None:
    """Log warning message to the root teaming24 logger."""
    get_logger("teaming24").warning(msg, extra=kwargs)


def error(msg: str, **kwargs) -> None:
    """Log error message to the root teaming24 logger."""
    get_logger("teaming24").error(msg, extra=kwargs)


def critical(msg: str, **kwargs) -> None:
    """Log critical message to the root teaming24 logger."""
    get_logger("teaming24").critical(msg, extra=kwargs)


def exception(msg: str, **kwargs) -> None:
    """Log exception with traceback."""
    get_logger("teaming24").exception(msg, extra=kwargs)


# =============================================================================
# Exports
# =============================================================================


__all__ = [
    # Source categories
    "LogSource",
    # Config
    "LogConfig",
    "setup_logging",
    # Logger access
    "get_logger",
    "get_agent_logger",
    # Filters
    "NoisyAccessFilter",
    "DockerStdoutFilter",
    # Context management
    "set_context",
    "clear_context",
    "get_context",
    # Convenience functions
    "debug",
    "info",
    "warning",
    "error",
    "critical",
    "exception",
]
