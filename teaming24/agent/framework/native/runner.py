"""
Multi-agent runners for the native framework.

HierarchicalRunner: Manager plans subtasks, delegates to workers, aggregates.
SequentialRunner:   Agents execute in order; each output feeds the next.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any

try:
    import litellm
except ImportError:  # pragma: no cover - depends on optional dependency
    import logging as _logging

    _logging.getLogger(__name__).debug("litellm is not installed for native runner")
    litellm = None

from teaming24.agent.framework.base import AgentSpec, StepCallback, StepOutput
from teaming24.agent.framework.native.runtime import AgentRuntime
from teaming24.config import get_config
from teaming24.llm.model_resolver import resolve_model_and_call_params
from teaming24.prompting import render_prompt
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)
if litellm is None:
    logger.debug("litellm is not installed; native runner planning will be unavailable")


def _require_litellm() -> None:
    if litellm is None:
        raise RuntimeError(
            "Native runner planning requires 'litellm'. Install with: uv pip install litellm"
        )


# ---------------------------------------------------------------------------
# Internal data models
# ---------------------------------------------------------------------------

@dataclass
class SubtaskAssignment:
    """One subtask assigned to a specific worker."""
    worker_role: str
    description: str


@dataclass
class DelegationPlan:
    """The manager's plan for distributing work."""
    reasoning: str = ""
    assignments: list[SubtaskAssignment] = field(default_factory=list)


# ---------------------------------------------------------------------------
# HierarchicalRunner
# ---------------------------------------------------------------------------

class HierarchicalRunner:
    """Manager (Organizer/Coordinator) plans → workers execute → manager aggregates.

    The manager NEVER executes tasks directly — only delegates and synthesizes.
    When plan is empty, delegates full task to first worker. When no workers,
    uses a fallback TaskExecutor (derived from manager spec).
    """

    def __init__(
        self,
        runtime: AgentRuntime | None = None,
        planning_model: str = "",
        planning_llm_call_params: dict[str, Any] | None = None,
        local_agent_router: Any | None = None,
        local_agent_pool: Any | None = None,
    ):
        self.runtime = runtime or AgentRuntime()
        self.planning_model = planning_model
        self.planning_llm_call_params = dict(planning_llm_call_params or {})
        self._local_agent_router = local_agent_router
        self._local_agent_pool = local_agent_pool

    def _fallback_executor(self, manager: AgentSpec) -> AgentSpec:
        """Create a TaskExecutor spec when no workers exist (manager never executes)."""
        return AgentSpec(
            role="TaskExecutor",
            goal="Execute the given task precisely. Produce a complete, usable result.",
            backstory="",
            capabilities=["general"],
            model=manager.model,
            tools=manager.tools,
            system_prompt="You are a task executor. Execute the given task precisely.",
        )

    async def run(
        self,
        manager: AgentSpec,
        workers: list[AgentSpec],
        prompt: str,
        step_callback: StepCallback | None = None,
        context: dict[str, Any] | None = None,
    ) -> str:
        # Manager never executes. When no workers, use fallback TaskExecutor.
        if not workers:
            fallback = self._fallback_executor(manager)
            workers = [fallback]
            logger.info(
                "[HierarchicalRunner] No workers — using fallback TaskExecutor "
                "(manager never executes)"
            )

        # 1. Plan: use LocalAgentRouter if provided, else Manager LLM plans
        ctx = context or {}
        local_router = ctx.get("local_agent_router") or getattr(self, "_local_agent_router", None)
        local_pool = ctx.get("local_agent_pool") or getattr(self, "_local_agent_pool", None)
        if local_router and local_pool:
            plan = self._route_via_local_agent_router(prompt, workers, local_router, local_pool)
        else:
            plan = await self._plan(manager, workers, prompt, step_callback)
        if not plan.assignments:
            # Don't run manager directly — delegate full task to first worker
            logger.info(
                "[HierarchicalRunner] Empty plan — delegating full task to first worker "
                "(manager never executes)"
            )
            first = workers[0]
            plan = DelegationPlan(
                reasoning="Fallback: assign full task to first available worker",
                assignments=[SubtaskAssignment(worker_role=first.role, description=prompt)],
            )

        # 2. Workers execute in parallel
        results = await self._execute_workers(workers, plan, step_callback)

        # 3. Manager aggregates and synthesizes (validation/summary — allowed)
        return await self._aggregate(manager, prompt, results, step_callback)

    def _route_via_local_agent_router(
        self,
        prompt: str,
        workers: list[AgentSpec],
        router: Any,
        pool: Any,
    ) -> DelegationPlan:
        """Use LocalAgentRouter to select Workers and assign sub-subtasks."""
        plan = router.route(prompt, pool)
        assignments = [
            SubtaskAssignment(worker_role=a.worker_role, description=a.description)
            for a in plan.assignments
        ]
        logger.info(
            "[HierarchicalRunner] LocalAgentRouter selected %d Worker(s): %s",
            len(assignments),
            [a.worker_role for a in assignments],
        )
        return DelegationPlan(reasoning=plan.reasoning, assignments=assignments)

    async def _plan(
        self,
        manager: AgentSpec,
        workers: list[AgentSpec],
        prompt: str,
        step_callback: StepCallback | None,
    ) -> DelegationPlan:
        workers_desc = "\n".join(
            f"- {w.role}: {w.goal} (capabilities: {', '.join(w.capabilities) or 'general'})"
            for w in workers
        )
        planning_prompt = render_prompt(
            "native.hierarchical.planning",
            manager_role=manager.role,
            prompt=prompt,
            workers_desc=workers_desc,
        )
        model = self.planning_model or manager.model

        if step_callback:
            step_callback(StepOutput(
                agent=manager.role, action="thought",
                content=f"Planning subtask delegation across {len(workers)} workers...",
            ))

        try:
            if self.planning_llm_call_params:
                resolved_model = model
                resolved_params = dict(self.planning_llm_call_params)
            else:
                resolved_model, resolved_params, _provider = resolve_model_and_call_params(
                    model,
                    get_config().llm,
                )
            metadata_params = {}
            if isinstance(manager.metadata, dict) and (model == manager.model):
                raw_params = manager.metadata.get("llm_call_params")
                if isinstance(raw_params, dict):
                    metadata_params = raw_params
            call_params = {**resolved_params, **metadata_params}
            _require_litellm()
            response = await litellm.acompletion(
                model=resolved_model,
                messages=[
                    {"role": "system", "content": f"You are {manager.role}. {manager.goal}"},
                    {"role": "user", "content": planning_prompt},
                ],
                temperature=0.2,
                response_format={"type": "json_object"},
                **call_params,
            )
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
        except Exception as exc:
            logger.error("[HierarchicalRunner] planning failed: %s", exc, exc_info=True)
            return DelegationPlan(reasoning=f"Planning error: {exc}")

        assignments = []
        for item in data.get("assignments", []):
            assignments.append(SubtaskAssignment(
                worker_role=item.get("worker_role", ""),
                description=item.get("description", ""),
            ))

        plan = DelegationPlan(
            reasoning=data.get("reasoning", ""),
            assignments=assignments,
        )
        logger.info("[HierarchicalRunner] plan: %d assignments — %s",
                     len(assignments), plan.reasoning[:120])
        return plan

    async def _execute_workers(
        self,
        workers: list[AgentSpec],
        plan: DelegationPlan,
        step_callback: StepCallback | None,
    ) -> dict[str, str]:
        worker_map = {w.role: w for w in workers}
        tasks = []
        labels = []

        for assignment in plan.assignments:
            worker = worker_map.get(assignment.worker_role)
            if worker is None:
                # Fuzzy match: try case-insensitive
                for w in workers:
                    if w.role.lower() == assignment.worker_role.lower():
                        worker = w
                        break
            if worker is None:
                logger.warning("[HierarchicalRunner] worker '%s' not found, skipping",
                               assignment.worker_role)
                continue

            if step_callback:
                step_callback(StepOutput(
                    agent=worker.role, action="delegation",
                    content=f"Assigned: {assignment.description[:200]}",
                ))

            tasks.append(self.runtime.run(worker, assignment.description, step_callback))
            labels.append(assignment.worker_role)

        if not tasks:
            return {}

        raw_results = await asyncio.gather(*tasks, return_exceptions=True)
        results: dict[str, str] = {}
        for label, res in zip(labels, raw_results, strict=False):
            if isinstance(res, Exception):
                results[label] = f"[error] {res}"
            else:
                results[label] = str(res)
        return results

    async def _aggregate(
        self,
        manager: AgentSpec,
        original_prompt: str,
        results: dict[str, str],
        step_callback: StepCallback | None,
    ) -> str:
        if not results:
            return ""
        if len(results) == 1:
            return next(iter(results.values()))

        parts = []
        for role, text in results.items():
            parts.append(f"### Result from {role}\n{text}")
        combined = "\n\n".join(parts)

        # Lead with the answer, not the plan
        agg_prompt = render_prompt(
            "native.hierarchical.aggregate",
            manager_role=manager.role,
            original_prompt=original_prompt,
            combined_results=combined,
        )

        if step_callback:
            step_callback(StepOutput(
                agent=manager.role, action="thought",
                content="Aggregating worker results into final response...",
            ))

        return await self.runtime.run(manager, agg_prompt, step_callback)


# ---------------------------------------------------------------------------
# SequentialRunner
# ---------------------------------------------------------------------------

class SequentialRunner:
    """Execute agents one after another; each receives the previous output."""

    def __init__(self, runtime: AgentRuntime | None = None):
        self.runtime = runtime or AgentRuntime()

    async def run(
        self,
        agents: list[AgentSpec],
        prompt: str,
        step_callback: StepCallback | None = None,
    ) -> str:
        if not agents:
            return ""

        current_output = ""
        last_non_empty_output = ""
        history: list[str] = []
        for i, agent in enumerate(agents):
            context_parts: list[str] = []
            if current_output:
                context_parts.append(f"Previous step output:\n{current_output}")
            elif last_non_empty_output:
                context_parts.append(
                    "Previous step output was empty; using the last non-empty output for continuity:\n"
                    f"{last_non_empty_output}"
                )
            if history:
                recent = "\n\n".join(
                    f"[Step {idx + 1}] {text}"
                    for idx, text in enumerate(history[-3:])
                )
                context_parts.append(f"Recent step history:\n{recent}")
            extra = "\n\n".join(context_parts)
            if step_callback:
                step_callback(StepOutput(
                    agent=agent.role, action="thought",
                    content=f"Step {i + 1}/{len(agents)}: starting execution...",
                ))
            step_output = await self.runtime.run(agent, prompt, step_callback, extra)
            step_output_text = str(step_output or "")
            if step_output_text.strip():
                current_output = step_output_text
                last_non_empty_output = step_output_text
            else:
                logger.warning(
                    "[SequentialRunner] Agent %s produced empty output at step %d/%d; preserving previous non-empty output",
                    agent.role,
                    i + 1,
                    len(agents),
                )
                current_output = last_non_empty_output or step_output_text
            history.append(current_output[:2000])

        return current_output
