from __future__ import annotations

from pathlib import Path

from teaming24.config import SessionConfig
from teaming24.session.manager import SessionManager


def test_session_manager_honors_store_path_and_max_history(tmp_path: Path) -> None:
    db_path = tmp_path / "sessions.db"
    manager = SessionManager(
        session_config=SessionConfig(
            dm_scope="per-channel-peer",
            idle_minutes=120,
            max_history=2,
            store_path=str(db_path),
            reset_triggers=["/reset"],
        )
    )

    session = manager.get_or_create(
        channel="webchat",
        peer_id="peer-1",
        agent_id="main",
        peer_kind="direct",
    )
    manager.record_message(session.id, "user", "first")
    manager.record_message(session.id, "assistant", "second")
    manager.record_message(session.id, "user", "third")

    transcript = manager.get_transcript(session.id, limit=10)

    assert manager.store.db_path == db_path.expanduser()
    assert [m.content for m in transcript] == ["second", "third"]
