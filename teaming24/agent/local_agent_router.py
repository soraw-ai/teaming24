"""
Local Agent Router — selects Workers from the Local Agent Workforce Pool.

Before each subtask, the LocalAgentRouter selects a SUBSET from the
LocalAgentWorkforcePool and assigns sub-subtasks to the selected Workers.
The Coordinator uses this router instead of planning directly.

This is the local counterpart to the ANRouter (which selects from the
AN Workforce Pool).
"""

from __future__ import annotations

import abc
import json
from dataclasses import dataclass, field
from typing import Any

from teaming24.prompting import render_prompt
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class LocalAgentAssignment:
    """A sub-subtask assigned to a specific Worker."""

    worker_role: str
    description: str


@dataclass
class LocalAgentRoutingPlan:
    """The result of LocalAgentRouter.route() — which Workers handle which sub-subtasks."""

    assignments: list[LocalAgentAssignment] = field(default_factory=list)
    reasoning: str = ""
    execution_mode: str = "parallel"


# ---------------------------------------------------------------------------
# Abstract protocol
# ---------------------------------------------------------------------------

class BaseLocalAgentRouter(abc.ABC):
    """Abstract base class for Local Agent routing implementations.

    Local Agent routers decide which Workers from the LocalAgentWorkforcePool
    should handle which sub-subtasks of a given subtask.

    Minimal contract: route(subtask_prompt, pool) → LocalAgentRoutingPlan
    """

    @abc.abstractmethod
    def route(self, subtask_prompt: str, pool: Any) -> LocalAgentRoutingPlan:
        """Select Workers from the pool and assign sub-subtasks.

        Args:
            subtask_prompt: The subtask the Coordinator received.
            pool: LocalAgentWorkforcePool instance.

        Returns:
            LocalAgentRoutingPlan with assignments to selected Workers.
        """
        pass


# ---------------------------------------------------------------------------
# Default implementation (LLM-assisted)
# ---------------------------------------------------------------------------

class LocalAgentRouter(BaseLocalAgentRouter):
    """Default Local Agent Router — selects Workers via LLM decision."""

    def __init__(
        self,
        model: str = "flock/gpt-5.2",
        temperature: float = 0.2,
        max_tokens: int = 1000,
        llm_call_params: dict[str, Any] | None = None,
    ):
        self._model = model
        self._temperature = temperature
        self._max_tokens = max_tokens
        self._llm_call_params = dict(llm_call_params or {})

    def route(self, subtask_prompt: str, pool: Any) -> LocalAgentRoutingPlan:
        """Select Workers from the pool and assign sub-subtasks via LLM."""
        entries = pool.get_pool() if pool else []

        if not entries:
            logger.warning("[LocalAgentRouter] Pool is empty — no Workers to select")
            return LocalAgentRoutingPlan(reasoning="Pool empty")

        workers_desc = "\n".join(
            f"- {e.role}: {e.goal} (capabilities: {', '.join(e.capabilities) or 'general'})"
            for e in entries
        )

        prompt = render_prompt(
            "local_agent_router.user.routing",
            subtask_prompt=subtask_prompt,
            workers_desc=workers_desc,
        )

        try:
            import litellm
            response = litellm.completion(
                model=self._model,
                messages=[
                    {"role": "system", "content": render_prompt("local_agent_router.system.routing")},
                    {"role": "user", "content": prompt},
                ],
                temperature=self._temperature,
                response_format={"type": "json_object"},
                **self._llm_call_params,
            )
            raw = response.choices[0].message.content or "{}"
            data = json.loads(raw)
        except Exception as exc:
            logger.error("[LocalAgentRouter] Routing failed: %s", exc)
            # Fallback: assign full subtask to first Worker
            first = entries[0]
            return LocalAgentRoutingPlan(
                assignments=[LocalAgentAssignment(worker_role=first.role, description=subtask_prompt)],
                reasoning=f"Fallback: routing failed ({exc})",
            )

        assignments = []
        for item in data.get("assignments", []):
            assignments.append(LocalAgentAssignment(
                worker_role=item.get("worker_role", ""),
                description=item.get("description", ""),
            ))

        if not assignments:
            first = entries[0]
            assignments = [LocalAgentAssignment(worker_role=first.role, description=subtask_prompt)]
            logger.info("[LocalAgentRouter] Empty assignments — fallback to first Worker")

        plan = LocalAgentRoutingPlan(
            assignments=assignments,
            reasoning=data.get("reasoning", ""),
        )
        logger.info(
            "[LocalAgentRouter] Selected %d Worker(s): %s",
            len(assignments),
            [a.worker_role for a in assignments],
        )
        return plan


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------

def create_local_agent_router(
    model: str = "",
    temperature: float = 0.2,
    max_tokens: int = 1000,
    **kwargs: Any,
) -> BaseLocalAgentRouter:
    """Create a LocalAgentRouter from config."""
    model = model or "flock/gpt-5.2"
    return LocalAgentRouter(model=model, temperature=temperature, max_tokens=max_tokens, **kwargs)
