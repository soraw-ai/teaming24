"""
Agent tools module for Teaming24.

Provides custom tools for CrewAI agents including:
- Network delegation tools for remote task execution
- OpenHands-based sandbox tools for code execution (CrewAI adapter)
- Framework-agnostic sandbox tools (native runtime)
"""

from teaming24.agent.tools.network_tools import (
    DelegateToNetworkTool,
    SearchNetworkTool,
    create_delegation_tool,
    get_organizer_tools,
)
from teaming24.agent.tools.openhands_tools import (
    BrowserTool,
    FileReadTool,
    FileWriteTool,
    PythonInterpreterTool,
    ShellCommandTool,
    bind_task_output_dir,
    check_openhands_tools_available,
    create_openhands_tools,
    get_tool_by_name,
)
from teaming24.agent.tools.sandbox_tools import (
    get_sandbox_registry,
    get_sandbox_tool_specs,
)

__all__ = [
    # Network tools
    "DelegateToNetworkTool",
    "SearchNetworkTool",
    "create_delegation_tool",
    "get_organizer_tools",
    # OpenHands tools (CrewAI adapter)
    "ShellCommandTool",
    "FileReadTool",
    "FileWriteTool",
    "PythonInterpreterTool",
    "BrowserTool",
    "create_openhands_tools",
    "get_tool_by_name",
    "bind_task_output_dir",
    "check_openhands_tools_available",
    # Framework-agnostic sandbox tools
    "get_sandbox_registry",
    "get_sandbox_tool_specs",
]
