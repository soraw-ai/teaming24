"""
Framework-agnostic tool protocol for Teaming24.

Defines a ``@tool`` decorator and a ``ToolRegistry`` so that tool
implementations are written once and automatically work with both
the native runtime and the CrewAI adapter.

Usage:

    from teaming24.agent.tools.base import tool, ToolRegistry

    @tool(name="shell_exec", description="Run a shell command")
    async def shell_exec(command: str, timeout: int = 60) -> str:
        ...

    registry = ToolRegistry()
    registry.register(shell_exec)
    specs = registry.get_specs()          # list[ToolSpec]
"""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Callable
from typing import Any, get_type_hints

from teaming24.agent.framework.base import ToolSpec
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# JSON-Schema extraction helpers
# ---------------------------------------------------------------------------

_PY_TO_JSON = {
    str: "string",
    int: "integer",
    float: "number",
    bool: "boolean",
    list: "array",
    dict: "object",
}


def _python_type_to_json(tp: Any) -> str:
    """Map a Python type annotation to a JSON Schema type string."""
    origin = getattr(tp, "__origin__", None)
    if origin is list or origin is list:
        return "array"
    if origin is dict or origin is dict:
        return "object"
    return _PY_TO_JSON.get(tp, "string")


def extract_json_schema(fn: Callable) -> dict[str, Any]:
    """Derive an OpenAI-compatible JSON-Schema from a function signature.

    Skips ``self`` / ``cls`` and the ``return`` annotation.
    """
    sig = inspect.signature(fn)
    try:
        hints = get_type_hints(fn)
    except Exception as e:
        import logging
        logging.getLogger(__name__).debug("Tool hints extraction failed: %s", e)
        hints = {}

    properties: dict[str, Any] = {}
    required: list[str] = []

    for name, param in sig.parameters.items():
        if name in ("self", "cls"):
            continue
        prop: dict[str, Any] = {}
        if name in hints:
            prop["type"] = _python_type_to_json(hints[name])
        else:
            prop["type"] = "string"

        if param.default is not inspect.Parameter.empty:
            prop["default"] = param.default
        else:
            required.append(name)

        properties[name] = prop

    schema: dict[str, Any] = {
        "type": "object",
        "properties": properties,
    }
    if required:
        schema["required"] = required
    return schema


# ---------------------------------------------------------------------------
# @tool decorator
# ---------------------------------------------------------------------------

def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
) -> Callable:
    """Decorator that attaches a ``ToolSpec`` to a function.

    The decorated function keeps its original behaviour but gains a
    ``_tool_spec`` attribute that adapters can read.

    Args:
        name: Tool name exposed to the LLM.
        description: Human-readable description for the LLM.
        parameters: Explicit JSON-Schema override.  If ``None`` the
            schema is auto-derived from the function signature.
    """
    def decorator(fn: Callable) -> Callable:
        schema = parameters if parameters is not None else extract_json_schema(fn)
        fn._tool_spec = ToolSpec(  # type: ignore[attr-defined]
            name=name,
            description=description,
            parameters=schema,
            handler=fn,
        )
        return fn
    return decorator


def get_tool_spec(fn: Callable) -> ToolSpec | None:
    """Return the ToolSpec attached by ``@tool``, or ``None``."""
    return getattr(fn, "_tool_spec", None)


# ---------------------------------------------------------------------------
# ToolRegistry — collect and convert tool specs
# ---------------------------------------------------------------------------

class ToolRegistry:
    """Collects tool functions and exposes them as ``ToolSpec`` objects."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolSpec] = {}

    def register(self, fn_or_spec) -> None:
        """Register a ``@tool``-decorated function or a ``ToolSpec``."""
        if isinstance(fn_or_spec, ToolSpec):
            self._tools[fn_or_spec.name] = fn_or_spec
            return
        spec = get_tool_spec(fn_or_spec)
        if spec is None:
            raise ValueError(
                f"{fn_or_spec!r} is not decorated with @tool and is not a ToolSpec"
            )
        self._tools[spec.name] = spec

    def get(self, name: str) -> ToolSpec | None:
        return self._tools.get(name)

    def get_specs(self) -> list[ToolSpec]:
        return list(self._tools.values())

    def names(self) -> list[str]:
        return list(self._tools.keys())

    def __len__(self) -> int:
        return len(self._tools)


# ---------------------------------------------------------------------------
# Tool execution helper
# ---------------------------------------------------------------------------

async def execute_tool(spec: ToolSpec, arguments: dict[str, Any]) -> str:
    """Invoke a tool handler (sync or async) and return its string result."""
    if spec.handler is None:
        return f"[error] tool '{spec.name}' has no handler"
    try:
        if asyncio.iscoroutinefunction(spec.handler):
            result = await spec.handler(**arguments)
        else:
            loop = asyncio.get_running_loop()
            result = await loop.run_in_executor(None, lambda: spec.handler(**arguments))
        return str(result) if result is not None else ""
    except Exception as exc:
        logger.warning("Tool execution failed: %s args=%s err=%s", spec.name, arguments, exc, exc_info=True)
        return f"[error] {spec.name}: {exc}"


# ---------------------------------------------------------------------------
# CrewAI BaseTool → ToolSpec bridge
# ---------------------------------------------------------------------------

def crewai_tool_to_spec(crewai_tool) -> ToolSpec:
    """Convert a CrewAI BaseTool instance to a framework-agnostic ToolSpec.

    This allows existing CrewAI tools (network_tools, openhands_tools) to
    be used seamlessly with the native runtime without rewriting them.
    """
    name = getattr(crewai_tool, "name", "unknown")
    description = getattr(crewai_tool, "description", "")

    # Extract JSON-Schema from Pydantic args_schema if available
    schema_cls = getattr(crewai_tool, "args_schema", None)
    if schema_cls is not None and hasattr(schema_cls, "model_json_schema"):
        try:
            raw = schema_cls.model_json_schema()
            parameters = {
                "type": "object",
                "properties": raw.get("properties", {}),
                "required": raw.get("required", []),
            }
        except Exception as e:
            logger.debug("Tool parameters extraction failed: %s", e, exc_info=True)
            parameters = {"type": "object", "properties": {}}
    else:
        parameters = {"type": "object", "properties": {}}

    def _handler(**kwargs) -> str:
        run_fn = getattr(crewai_tool, "_run", None)
        if run_fn is None:
            return f"[error] CrewAI tool '{name}' has no _run method"
        return str(run_fn(**kwargs))

    return ToolSpec(
        name=name,
        description=description,
        parameters=parameters,
        handler=_handler,
    )
