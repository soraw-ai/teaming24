"""
Agent module for Teaming24.

Provides multi-agent framework integration with a native runtime (default)
and optional CrewAI backend.
"""

from teaming24.agent.an_router import (
    AN_ROUTER_REGISTRY,
    ROUTER_REGISTRY,
    AlgorithmicRouter,
    ANRouter,
    # AN Routing — Protocol & Factory (preferred API)
    BaseANRouter,
    # Backward compatibility aliases
    BaseRouter,
    # LLM Routing — stub (future)
    LLMRouter,
    RoutingDecision,
    # AN Routing — Data Models
    RoutingPlan,
    RoutingSubtask,
    ScoringANRouter,
    create_an_router,
    create_router,
    register_an_router,
    register_router,
)
from teaming24.agent.core import (
    LocalCrew,
    check_agent_framework_available,
    check_crewai_available,
    create_local_crew,
)
from teaming24.agent.crew_wrapper import CrewWrapper
from teaming24.agent.events import AgentConfig, CrewConfig, StepCallback
from teaming24.agent.factory import AgentFactory
from teaming24.agent.local_agent_pool import LocalAgentEntry, LocalAgentWorkforcePool
from teaming24.agent.local_agent_router import (
    BaseLocalAgentRouter,
    LocalAgentAssignment,
    LocalAgentRouter,
    LocalAgentRoutingPlan,
    create_local_agent_router,
)
from teaming24.agent.routing_strategy import RoutingStrategy
from teaming24.agent.streaming import (
    CostTracker,
    StreamEvent,
    StreamingCallback,
    format_cost_display,
)
from teaming24.agent.workforce_pool import AgenticNodeEntry, AgenticNodeWorkforcePool

__all__ = [
    # Core
    "AgentConfig",
    "AgentFactory",
    "CrewConfig",
    "CrewWrapper",
    "LocalCrew",
    "StepCallback",
    "check_agent_framework_available",
    "check_crewai_available",
    "create_local_crew",
    # Streaming
    "CostTracker",
    "StreamEvent",
    "StreamingCallback",
    "format_cost_display",
    # AN Routing — Protocol & Factory (preferred new API)
    "BaseANRouter",
    "ANRouter",
    "ScoringANRouter",
    "create_an_router",
    "register_an_router",
    "AN_ROUTER_REGISTRY",
    # AN Routing — Data Models
    "RoutingPlan",
    "RoutingSubtask",
    "RoutingDecision",
    # LLM Routing — stub (future)
    "LLMRouter",
    # Capability scoring
    "RoutingStrategy",
    # Agentic Node Workforce Pool (AN-level)
    "AgenticNodeEntry",
    "AgenticNodeWorkforcePool",
    # Local Agent Pool & Router (Worker-level)
    "LocalAgentEntry",
    "LocalAgentWorkforcePool",
    "BaseLocalAgentRouter",
    "LocalAgentRouter",
    "LocalAgentRoutingPlan",
    "LocalAgentAssignment",
    "create_local_agent_router",
    # Backward compatibility aliases (deprecated)
    "BaseRouter",
    "AlgorithmicRouter",
    "create_router",
    "register_router",
    "ROUTER_REGISTRY",
]
