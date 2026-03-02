"""
Native multi-agent runtime for Teaming24.

Uses litellm directly for LLM calls with OpenAI-compatible tool calling.
No external agent framework dependency required.

Imports are lazy to avoid hard-failing when litellm is not installed.
"""


def __getattr__(name: str):
    if name == "AgentRuntime":
        from teaming24.agent.framework.native.runtime import AgentRuntime
        return AgentRuntime
    if name == "HierarchicalRunner":
        from teaming24.agent.framework.native.runner import HierarchicalRunner
        return HierarchicalRunner
    if name == "SequentialRunner":
        from teaming24.agent.framework.native.runner import SequentialRunner
        return SequentialRunner
    if name == "NativeAdapter":
        from teaming24.agent.framework.native.adapter import NativeAdapter
        return NativeAdapter
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = [
    "AgentRuntime",
    "HierarchicalRunner",
    "NativeAdapter",
    "SequentialRunner",
]
