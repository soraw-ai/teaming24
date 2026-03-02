"""
Memory tools for agents — search and save to persistent memory.

These tools use the framework-agnostic @tool decorator so they work
with both the native runtime and CrewAI adapter.
"""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar

from teaming24.agent.tools.base import tool
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# Lazy-initialised singleton
_memory_manager = None
_memory_agent_id: ContextVar[str] = ContextVar("teaming24_memory_agent_id", default="main")
_memory_session_id: ContextVar[str] = ContextVar("teaming24_memory_session_id", default="")

try:
    from crewai.tools.base_tool import BaseTool
    from pydantic import BaseModel, Field

    CREWAI_AVAILABLE = True
except ImportError:
    BaseTool = object  # type: ignore[assignment]
    BaseModel = object  # type: ignore[assignment]
    Field = None  # type: ignore[assignment]
    CREWAI_AVAILABLE = False


def _get_mm():
    global _memory_manager
    if _memory_manager is None:
        from teaming24.memory.manager import MemoryManager
        _memory_manager = MemoryManager()
    return _memory_manager


def get_memory_agent_id(default: str = "main") -> str:
    value = str(_memory_agent_id.get() or "").strip()
    return value or default


def get_memory_session_id(default: str = "") -> str:
    value = str(_memory_session_id.get() or "").strip()
    return value or default


@contextmanager
def memory_tool_context(agent_id: str | None = None, session_id: str | None = None):
    """Bind agent/session scope for memory tools during a task."""
    token_agent = _memory_agent_id.set(str(agent_id or "").strip() or get_memory_agent_id())
    token_session = _memory_session_id.set(str(session_id or "").strip() or get_memory_session_id())
    try:
        yield
    finally:
        _memory_agent_id.reset(token_agent)
        _memory_session_id.reset(token_session)


def _memory_scope() -> str:
    """Session-scoped when session_id present; else agent-scoped. Each chat session = independent memory."""
    session_id = get_memory_session_id()
    agent_id = get_memory_agent_id()
    if session_id:
        return f"session:{session_id}"
    return agent_id


@tool(
    name="memory_search",
    description=(
        "Search the agent's long-term memory for relevant information. "
        "Returns the top matching memories with their content and scores. "
        "Use this before starting a task to recall past context."
    ),
)
def memory_search(query: str, top_k: int = 5) -> str:
    mm = _get_mm()
    scope = _memory_scope()
    results = mm.search(query, agent_id=scope, top_k=top_k)
    if not results:
        return "No relevant memories found."
    lines = []
    for i, entry in enumerate(results, 1):
        tags_str = f" [{', '.join(entry.tags)}]" if entry.tags else ""
        lines.append(f"{i}. (score={entry.score:.2f}{tags_str}) {entry.content[:500]}")
    return "\n".join(lines)


@tool(
    name="memory_save",
    description=(
        "Save important information to the agent's long-term memory. "
        "Use this to remember facts, user preferences, or key findings "
        "that should persist across conversations."
    ),
)
def memory_save(content: str, tags: str = "") -> str:
    mm = _get_mm()
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []
    scope = _memory_scope()
    session_id = get_memory_session_id()
    metadata = {"session_id": session_id} if session_id else {}
    mem_id = mm.save(agent_id=scope, content=content, tags=tag_list, source="agent", metadata=metadata)
    return f"Memory saved (id={mem_id})"


if CREWAI_AVAILABLE:
    class MemorySearchInput(BaseModel):
        query: str = Field(..., description="What to recall from long-term memory")
        top_k: int = Field(default=5, description="Maximum number of memory hits to return")


    class MemorySaveInput(BaseModel):
        content: str = Field(..., description="Important information worth persisting")
        tags: str = Field(default="", description="Comma-separated memory tags")


    class MemorySearchTool(BaseTool):
        name: str = "memory_search"
        description: str = (
            "Search this agent's long-term memory for relevant past facts, user preferences, "
            "prior decisions, or earlier task outcomes."
        )
        args_schema: type[BaseModel] = MemorySearchInput
        handle_tool_error: bool = True

        def _run(self, query: str, top_k: int = 5) -> str:
            return memory_search(query=query, top_k=top_k)


    class MemorySaveTool(BaseTool):
        name: str = "memory_save"
        description: str = (
            "Save durable facts, decisions, user preferences, or important findings to this "
            "agent's long-term memory for future recall."
        )
        args_schema: type[BaseModel] = MemorySaveInput
        handle_tool_error: bool = True

        def _run(self, content: str, tags: str = "") -> str:
            return memory_save(content=content, tags=tags)
