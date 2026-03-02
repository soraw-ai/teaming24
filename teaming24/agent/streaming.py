"""
Streaming utilities for CrewAI agent execution.

Provides callbacks and formatters for streaming agent progress to SSE.
"""

import asyncio
import json
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from teaming24.config import get_config
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


@dataclass
class StreamEvent:
    """Event data for streaming."""
    type: str
    timestamp: float = field(default_factory=time.time)
    task_id: str | None = None
    agent: str | None = None
    action: str | None = None
    content: str | None = None
    thought: str | None = None
    observation: str | None = None
    cost: dict | None = None
    error: str | None = None

    def to_dict(self) -> dict:
        return {k: v for k, v in {
            "type": self.type,
            "timestamp": self.timestamp,
            "task_id": self.task_id,
            "agent": self.agent,
            "action": self.action,
            "content": self.content,
            "thought": self.thought,
            "observation": self.observation,
            "cost": self.cost,
            "error": self.error,
        }.items() if v is not None}

    def to_sse(self) -> str:
        """Format as Server-Sent Event."""
        return f"data: {json.dumps(self.to_dict())}\n\n"


class StreamingCallback:
    """
    Streaming callback for CrewAI execution.

    Converts CrewAI step outputs to SSE events and broadcasts them
    via the subscription manager.
    """

    def __init__(self, task_id: str,
                 on_event: Callable[[StreamEvent], None] = None,
                 broadcast: Callable[[str, dict], Any] = None):
        """
        Initialize streaming callback.

        Args:
            task_id: Task ID for event tracking
            on_event: Optional sync callback for events
            broadcast: Optional async broadcast function (e.g., SSE manager)
        """
        self.task_id = task_id
        self.on_event = on_event
        self.broadcast = broadcast
        self._loop = None
        self._events: list[StreamEvent] = []

    def _get_loop(self):
        """Get or create event loop for async operations."""
        if self._loop is None:
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                logger.debug("No running loop in stream callback; creating a dedicated event loop")
                self._loop = asyncio.new_event_loop()
                asyncio.set_event_loop(self._loop)
        return self._loop

    def __call__(self, output: Any) -> None:
        """
        Handle CrewAI step output.

        Args:
            output: CrewAI step output (AgentAction, AgentFinish, etc.)
        """
        # Extract agent info
        agent_name = "unknown"
        if hasattr(output, 'agent'):
            agent_name = getattr(output.agent, 'role', str(output.agent))

        # Extract step type and content
        action = "step"
        content = ""
        thought = None
        observation = None

        # Handle different output types
        if hasattr(output, 'thought'):
            thought = str(output.thought) if output.thought else None
            action = "think"

        if hasattr(output, 'action'):
            action = str(output.action)
            content = str(getattr(output, 'action_input', ''))

        if hasattr(output, 'observation'):
            observation = str(output.observation) if output.observation else None

        if hasattr(output, 'return_values'):
            action = "finish"
            content = str(output.return_values)

        # If no specific content, use string representation
        cfg = get_config()
        str_max = cfg.api.step_event_string_repr_max_chars
        content_max = cfg.api.step_event_content_max_chars
        thought_max = cfg.api.step_event_thought_max_chars
        obs_max = cfg.api.step_event_observation_max_chars
        if not content and not thought:
            content = str(output)[:str_max]

        # Create event
        event = StreamEvent(
            type="agent_step",
            task_id=self.task_id,
            agent=agent_name,
            action=action,
            content=content[:content_max] if content else None,
            thought=thought[:thought_max] if thought else None,
            observation=observation[:obs_max] if observation else None,
        )

        self._events.append(event)

        # Notify listeners
        if self.on_event:
            try:
                self.on_event(event)
            except Exception as e:
                logger.error(f"Event callback error: {e}")

        # Broadcast via SSE
        if self.broadcast:
            try:
                loop = self._get_loop()
                if loop.is_running():
                    asyncio.ensure_future(
                        self.broadcast("agent_step", event.to_dict())
                    )
                else:
                    loop.run_until_complete(
                        self.broadcast("agent_step", event.to_dict())
                    )
            except Exception as e:
                logger.error(f"Broadcast error: {e}")

    def emit_start(self):
        """Emit task start event."""
        event = StreamEvent(type="task_started", task_id=self.task_id)
        self._emit(event)

    def emit_complete(self, result: str, cost: dict = None, duration: float = None):
        """Emit task complete event."""
        event = StreamEvent(
            type="task_completed",
            task_id=self.task_id,
            content=result,
            cost=cost,
        )
        self._emit(event)

    def emit_error(self, error: str):
        """Emit task error event."""
        event = StreamEvent(
            type="task_error",
            task_id=self.task_id,
            error=error,
        )
        self._emit(event)

    def emit_delegation(self, node_id: str, node_name: str):
        """Emit task delegation event."""
        event = StreamEvent(
            type="task_delegated",
            task_id=self.task_id,
            content=f"Delegating to {node_name}",
            observation=node_id,
        )
        self._emit(event)

    def _emit(self, event: StreamEvent):
        """Internal emit helper."""
        self._events.append(event)
        if self.on_event:
            self.on_event(event)
        if self.broadcast:
            try:
                loop = self._get_loop()
                if loop.is_running():
                    asyncio.ensure_future(self.broadcast(event.type, event.to_dict()))
            except Exception as e:
                logger.debug(f"Emit broadcast error: {e}")

    def get_events(self) -> list[StreamEvent]:
        """Get all recorded events."""
        return self._events.copy()


class CostTracker:
    """
    Tracks execution cost for streaming display.

    Accumulates token usage and x402 payments.
    """

    # Default pricing (can be overridden via config)
    DEFAULT_PRICING = {
        "gpt-4": {"input": 0.03, "output": 0.06},
        "gpt-4-turbo": {"input": 0.01, "output": 0.03},
        "gpt-3.5-turbo": {"input": 0.0005, "output": 0.0015},
        "claude-3-opus": {"input": 0.015, "output": 0.075},
        "claude-3-sonnet": {"input": 0.003, "output": 0.015},
    }

    def __init__(self, model: str = "gpt-4", pricing: dict = None):
        """
        Initialize cost tracker.

        Args:
            model: LLM model name for pricing
            pricing: Optional custom pricing dict
        """
        self.model = model
        self.pricing = pricing or self.DEFAULT_PRICING

        self.input_tokens = 0
        self.output_tokens = 0
        self.x402_payments = 0.0

    def add_tokens(self, input_tokens: int = 0, output_tokens: int = 0):
        """Add token usage."""
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def add_payment(self, amount: float):
        """Add x402 payment."""
        self.x402_payments += amount

    @property
    def total_tokens(self) -> int:
        """Get total tokens used."""
        return self.input_tokens + self.output_tokens

    @property
    def llm_cost(self) -> float:
        """Calculate LLM cost based on token usage."""
        model_pricing = self.pricing.get(self.model, {"input": 0.01, "output": 0.03})
        input_cost = (self.input_tokens / 1000) * model_pricing["input"]
        output_cost = (self.output_tokens / 1000) * model_pricing["output"]
        return input_cost + output_cost

    @property
    def total_cost(self) -> float:
        """Get total cost including x402 payments."""
        return self.llm_cost + self.x402_payments

    def to_dict(self) -> dict:
        """Convert to dictionary for API response."""
        return {
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "llm_cost_usd": round(self.llm_cost, 6),
            "x402_payment": round(self.x402_payments, 6),
            "total_cost_usd": round(self.total_cost, 6),
            "model": self.model,
        }


def format_cost_display(cost: dict) -> str:
    """
    Format cost dictionary for display.

    Args:
        cost: Cost dictionary from CostTracker.to_dict()

    Returns:
        Human-readable cost string
    """
    total = cost.get("total_cost_usd", 0)
    tokens = cost.get("total_tokens", 0)
    x402 = cost.get("x402_payment", 0)

    def _fmt(v: float) -> str:
        s = f"{v:.6f}".rstrip("0").rstrip(".")
        return s

    parts = [f"${_fmt(total)}"]
    if tokens:
        parts.append(f"{tokens} tokens")
    if x402 > 0:
        parts.append(f"(incl. ${_fmt(x402)} x402)")

    return " | ".join(parts)
