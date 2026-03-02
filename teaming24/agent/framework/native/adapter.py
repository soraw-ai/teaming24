"""
NativeAdapter — FrameworkAdapter backed by the teaming24 native runtime.

Uses litellm directly for LLM calls.  No external agent framework required.
"""

from __future__ import annotations

from typing import Any

from teaming24.agent.framework.base import (
    AgentSpec,
    FrameworkAdapter,
    StepCallback,
)
from teaming24.agent.framework.native.runner import HierarchicalRunner, SequentialRunner
from teaming24.agent.framework.native.runtime import AgentRuntime
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class NativeAdapter(FrameworkAdapter):
    """FrameworkAdapter using the teaming24 native agentic loop."""

    def __init__(
        self,
        max_iterations: int = 25,
        planning_model: str = "",
        planning_llm_call_params: dict[str, Any] | None = None,
        **_extra,
    ):
        self.runtime = AgentRuntime(max_iterations=max_iterations)
        self.hierarchical = HierarchicalRunner(
            runtime=self.runtime,
            planning_model=planning_model,
            planning_llm_call_params=planning_llm_call_params,
        )
        self.sequential = SequentialRunner(runtime=self.runtime)

    async def execute_hierarchical(
        self,
        prompt: str,
        manager: AgentSpec,
        workers: list[AgentSpec],
        step_callback: StepCallback | None = None,
        task_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        logger.info(
            "[NativeAdapter] execute_hierarchical  task_id=%s  manager=%s  workers=%d",
            task_id, manager.role, len(workers),
        )
        return await self.hierarchical.run(
            manager, workers, prompt, step_callback, context=context
        )

    async def execute_sequential(
        self,
        prompt: str,
        agents: list[AgentSpec],
        step_callback: StepCallback | None = None,
        task_id: str = "",
        context: dict[str, Any] | None = None,
    ) -> str:
        logger.info(
            "[NativeAdapter] execute_sequential  task_id=%s  agents=%d",
            task_id, len(agents),
        )
        return await self.sequential.run(agents, prompt, step_callback)
