"""
Typed error codes and centralized exception handling for the Teaming24 API.

Replaces scattered ``JSONResponse(status_code=500, ...)`` patterns with
structured ``raise AppError(...)`` calls.  The FastAPI exception handler
ensures every error response follows the same shape::

    {"error": "<error_code>", "message": "<human-readable detail>"}

Architecture Overview
--------------------
Error flow from route to JSON response::

    1. Route handler raises AppError(code, message, status=...)
    2. FastAPI catches the exception and invokes the registered handler
    3. register_error_handlers() provides @app.exception_handler(AppError)
    4. Handler calls exc.to_dict() and returns JSONResponse(status_code=exc.status, content=...)
    5. Client receives a consistent JSON body: {"error": "...", "message": "..."}

All Available ErrorCode Values (with typical HTTP status)
--------------------------------------------------------
+----------------------+--------+
| ErrorCode            | Status |
+----------------------+--------+
| INVALID_REQUEST      | 400    |
| VALIDATION_ERROR     | 400    |
| NOT_FOUND            | 404    |
| UNAUTHORIZED         | 401    |
| FORBIDDEN            | 403    |
| CONFLICT             | 409    |
| AGENT_UNAVAILABLE    | 503    |
| LLM_ERROR            | 502    |
| TOOL_ERROR           | 500    |
| AGENT_TIMEOUT        | 504    |
| TIMEOUT              | 504    |
| RATE_LIMITED         | 429    |
| SERVICE_UNAVAILABLE  | 503    |
| INTERNAL_ERROR       | 500    |
| CONFIG_ERROR         | 500    |
| DEPENDENCY_ERROR     | 503    |
+----------------------+--------+

How to Add a New Error Code
---------------------------
1. Extend the ErrorCode enum with a new member (e.g. ``QUOTA_EXCEEDED = "quota_exceeded"``).
2. Add a convenience factory if the code is used often::

    def quota_exceeded(message: str = "Quota exceeded", **kw) -> AppError:
        return AppError(ErrorCode.QUOTA_EXCEEDED, message, status=429, **kw)

3. Use in handlers: ``raise quota_exceeded("User limit reached")``.

How to Use in Route Handlers
----------------------------
Raise the exception from any async or sync handler; FastAPI will propagate it::

    from teaming24.api.errors import AppError, ErrorCode, not_found

    @router.get("/api/widgets/{id}")
    async def get_widget(id: str):
        widget = await db.get(id)
        if not widget:
            raise not_found("Widget not found")
        return widget

    # Or with explicit AppError:
    raise AppError(ErrorCode.FORBIDDEN, "Access denied", status=403)

How register_error_handlers Works with FastAPI
----------------------------------------------
1. Call ``register_error_handlers(app)`` once during app creation.
2. Registers two handlers in order of specificity:
   - AppError: catches all AppError instances, returns JSONResponse with exc.status and exc.to_dict().
   - Exception: catches any unhandled exception; re-raises StarletteHTTPException and
     RequestValidationError so FastAPI's built-in handlers can process them; for all other
     exceptions, logs the traceback and returns a generic 500 JSON response.
3. FastAPI matches handlers by exception type (most specific first).

Thread Safety Notes
-------------------
- AppError instances are immutable after construction; safe to raise from any thread.
- register_error_handlers modifies the app's exception handler registry; call it once
  during startup before any requests are served. The registry itself is not thread-safe
  for concurrent modification, but reading/handling is safe after registration.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


class ErrorCode(str, Enum):
    """Typed error codes covering common API failure modes.

    All codes and their typical HTTP status codes:

    +----------------------+--------+------------------------------------------+
    | Code                 | Status | Description                              |
    +----------------------+--------+------------------------------------------+
    | INVALID_REQUEST      | 400    | Malformed or invalid request body/params |
    | VALIDATION_ERROR     | 400    | Pydantic/request validation failed       |
    | NOT_FOUND            | 404    | Resource does not exist                  |
    | UNAUTHORIZED         | 401    | Authentication required or failed        |
    | FORBIDDEN            | 403    | Authenticated but not authorized         |
    | CONFLICT             | 409    | State conflict (e.g. duplicate)          |
    | AGENT_UNAVAILABLE    | 503    | Agent service not available              |
    | LLM_ERROR            | 502    | LLM provider returned error              |
    | TOOL_ERROR           | 500    | Tool execution failed                    |
    | AGENT_TIMEOUT        | 504    | Agent did not respond in time            |
    | TIMEOUT              | 504    | Generic operation timeout                |
    | RATE_LIMITED         | 429    | Too many requests                        |
    | SERVICE_UNAVAILABLE  | 503    | Upstream service down                     |
    | INTERNAL_ERROR       | 500    | Unexpected server error                   |
    | CONFIG_ERROR         | 500    | Configuration invalid or missing         |
    | DEPENDENCY_ERROR     | 503    | External dependency failed               |
    +----------------------+--------+------------------------------------------+
    """

    # Client errors
    INVALID_REQUEST = "invalid_request"
    VALIDATION_ERROR = "validation_error"
    NOT_FOUND = "not_found"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    CONFLICT = "conflict"

    # Agent / LLM errors
    AGENT_UNAVAILABLE = "agent_unavailable"
    LLM_ERROR = "llm_error"
    TOOL_ERROR = "tool_error"
    AGENT_TIMEOUT = "agent_timeout"

    # Infrastructure
    TIMEOUT = "timeout"
    RATE_LIMITED = "rate_limited"
    SERVICE_UNAVAILABLE = "service_unavailable"

    # System
    INTERNAL_ERROR = "internal_error"
    CONFIG_ERROR = "config_error"
    DEPENDENCY_ERROR = "dependency_error"


class AppError(Exception):
    """Structured application exception that maps to an HTTP error response.

    Usage examples::

        # Basic usage with ErrorCode and explicit status
        raise AppError(ErrorCode.NOT_FOUND, "User not found", status=404)

        # With optional detail for structured error payloads
        raise AppError(
            ErrorCode.VALIDATION_ERROR,
            "Invalid input",
            status=400,
            detail={"field": "email", "reason": "invalid format"}
        )

        # Via convenience factories (recommended for common cases)
        raise not_found("Widget not found")
        raise bad_request("Missing required field: name")
        raise unauthorized("Token expired")
        raise forbidden("Insufficient permissions")
        raise internal_error("Database connection failed")

    Attributes:
        code: The ErrorCode enum value (e.g. ErrorCode.NOT_FOUND).
        message: Human-readable error message returned in the JSON response.
        status: HTTP status code (default 500).
        detail: Optional extra payload; if not None, included as "detail" in the JSON.
    """

    def __init__(
        self,
        code: ErrorCode,
        message: str,
        status: int = 500,
        detail: Any | None = None,
    ):
        self.code = code
        self.message = message
        self.status = status
        self.detail = detail
        super().__init__(f"[{code.value}] {message}")

    def to_dict(self) -> dict:
        d: dict = {"error": self.code.value, "message": self.message}
        if self.detail is not None:
            d["detail"] = self.detail
        return d


# ---------------------------------------------------------------------------
# Convenience factories
# ---------------------------------------------------------------------------

def not_found(message: str = "Resource not found", **kw) -> AppError:
    """Return an AppError for 404 Not Found (ErrorCode.NOT_FOUND)."""
    return AppError(ErrorCode.NOT_FOUND, message, status=404, **kw)


def bad_request(message: str = "Invalid request", **kw) -> AppError:
    """Return an AppError for 400 Bad Request (ErrorCode.INVALID_REQUEST)."""
    return AppError(ErrorCode.INVALID_REQUEST, message, status=400, **kw)


def unauthorized(message: str = "Authentication required", **kw) -> AppError:
    """Return an AppError for 401 Unauthorized (ErrorCode.UNAUTHORIZED)."""
    return AppError(ErrorCode.UNAUTHORIZED, message, status=401, **kw)


def forbidden(message: str = "Access denied", **kw) -> AppError:
    """Return an AppError for 403 Forbidden (ErrorCode.FORBIDDEN)."""
    return AppError(ErrorCode.FORBIDDEN, message, status=403, **kw)


def agent_unavailable(message: str = "Agent is not available", **kw) -> AppError:
    """Return an AppError for 503 Agent Unavailable (ErrorCode.AGENT_UNAVAILABLE)."""
    return AppError(ErrorCode.AGENT_UNAVAILABLE, message, status=503, **kw)


def llm_error(message: str = "LLM request failed", **kw) -> AppError:
    """Return an AppError for 502 LLM Error (ErrorCode.LLM_ERROR)."""
    return AppError(ErrorCode.LLM_ERROR, message, status=502, **kw)


def timeout(message: str = "Operation timed out", **kw) -> AppError:
    """Return an AppError for 504 Gateway Timeout (ErrorCode.TIMEOUT)."""
    return AppError(ErrorCode.TIMEOUT, message, status=504, **kw)


def internal_error(message: str = "Internal server error", **kw) -> AppError:
    """Return an AppError for 500 Internal Server Error (ErrorCode.INTERNAL_ERROR)."""
    return AppError(ErrorCode.INTERNAL_ERROR, message, status=500, **kw)


# ---------------------------------------------------------------------------
# FastAPI exception handler registration
# ---------------------------------------------------------------------------

def register_error_handlers(app: FastAPI) -> None:
    """Register centralized exception handlers on the FastAPI app.

    Call once during app creation (e.g. in ``app.py`` or ``server.py``).

    Exception Handler Chain
    -----------------------
    Two handlers are registered, and FastAPI invokes them by exception type
    (most specific match first):

    1. **AppError handler**: Catches all ``AppError`` instances raised from
       route handlers. Returns ``JSONResponse(status_code=exc.status,
       content=exc.to_dict())``, producing responses like::

           {"error": "not_found", "message": "Widget not found"}

       Optional ``detail`` is included when set.

    2. **Generic Exception handler**: Catches any unhandled exception.
       - Re-raises ``StarletteHTTPException`` and ``RequestValidationError``
         so FastAPI's built-in handlers can return proper 4xx responses.
       - For all other exceptions: logs the full traceback via
         ``logging.exception()`` and returns a generic 500 response::

           {"error": "internal_error", "message": "Internal server error"}

       This ensures no uncaught exception leaks to the client.
    """

    @app.exception_handler(AppError)
    async def _handle_app_error(_request: Request, exc: AppError) -> JSONResponse:
        return JSONResponse(status_code=exc.status, content=exc.to_dict())

    @app.exception_handler(Exception)
    async def _handle_generic_error(_request: Request, exc: Exception) -> JSONResponse:
        from fastapi.exceptions import RequestValidationError
        from starlette.exceptions import HTTPException as StarletteHTTPException
        if isinstance(exc, (StarletteHTTPException, RequestValidationError)):
            raise exc
        import logging
        logging.getLogger("teaming24.api.errors").exception("Unhandled error: %s", exc)
        return JSONResponse(
            status_code=500,
            content={
                "error": ErrorCode.INTERNAL_ERROR.value,
                "message": "Internal server error",
            },
        )
