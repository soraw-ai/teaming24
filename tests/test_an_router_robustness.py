from __future__ import annotations

import json
from dataclasses import dataclass, field

from teaming24.agent.an_router import (
    ANRouter,
    BaseANRouter,
    RoutingSubtask,
    create_an_router,
)


@dataclass
class _PoolEntry:
    id: str
    name: str
    entry_type: str
    status: str = "online"
    capabilities: list[str] = field(default_factory=list)


def test_parse_routing_response_skips_duplicate_an_id() -> None:
    router = ANRouter(pool=None, task_id="task-1", model="openai/gpt-4o-mini", min_pool_members=1)
    entries = [
        _PoolEntry(id="local-1", name="Local Coordinator", entry_type="local"),
        _PoolEntry(id="remote-1", name="Remote A", entry_type="remote"),
    ]
    raw = json.dumps(
        {
            "execution_mode": "parallel",
            "subtasks": [
                {"description": "first remote", "assigned_to": "remote-1", "order": 1},
                {"description": "duplicate remote", "assigned_to": "remote-1", "order": 2},
            ],
        }
    )

    plan = router._parse_routing_response(raw, "test prompt", entries)

    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].is_remote is True
    assert plan.subtasks[0].target_node_id == "remote-1"


def test_deduplicate_subtasks_uses_target_node_id_for_remote_members() -> None:
    subtasks = [
        RoutingSubtask(
            description="task a",
            assigned_to="same-name",
            is_remote=True,
            target_node_id="remote-1",
        ),
        RoutingSubtask(
            description="task b",
            assigned_to="same-name",
            is_remote=True,
            target_node_id="remote-2",
        ),
    ]

    deduped = BaseANRouter._deduplicate_subtasks(subtasks)

    assert len(deduped) == 2
    assert {s.target_node_id for s in deduped} == {"remote-1", "remote-2"}


def test_factory_allows_min_pool_members_one() -> None:
    router = create_an_router(pool=None, task_id="task-2", min_pool_members=1)
    assert router._min_pool_members == 1


def test_parse_routing_response_handles_non_dict_subtasks() -> None:
    router = ANRouter(pool=None, task_id="task-3", model="openai/gpt-4o-mini", min_pool_members=1)
    entries = [
        _PoolEntry(id="local-1", name="Local Coordinator", entry_type="local"),
        _PoolEntry(id="remote-1", name="Remote A", entry_type="remote"),
    ]
    raw = json.dumps(
        {
            "execution_mode": "parallel",
            "subtasks": [
                "bad-item",
                {"description": "", "assigned_to": "remote-1", "order": "x"},
            ],
        }
    )

    plan = router._parse_routing_response(raw, "fallback prompt", entries)

    assert len(plan.subtasks) == 1
    assert plan.subtasks[0].description == "fallback prompt"
    assert plan.subtasks[0].target_node_id == "remote-1"
