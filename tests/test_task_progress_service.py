from __future__ import annotations

from teaming24.api.services.task_progress import (
    remote_stage_default_pct,
    should_emit_remote_milestone,
)


def test_remote_stage_default_pct_defaults() -> None:
    assert remote_stage_default_pct("running") == 35
    assert remote_stage_default_pct("completed") == 100
    assert remote_stage_default_pct("unknown") == 0


def test_should_emit_remote_milestone_deduplicates_same_remote_progress() -> None:
    tracker: dict[str, tuple[object, ...]] = {}
    step = {
        "agent_type": "remote",
        "action": "remote_progress",
        "agent": "remote-node-1",
        "remote_progress": {
            "stage": "running",
            "percentage": 25,
            "phase_label": "Executing with 4 workers",
            "transport": "sse",
        },
    }

    assert should_emit_remote_milestone(step, tracker) is True
    assert should_emit_remote_milestone(step, tracker) is False
