from __future__ import annotations

import teaming24.config as config_module


def test_legacy_bindings_and_session_fields_are_supported() -> None:
    cfg = config_module._build_config_from_dict(
        {
            "bindings": [
                {
                    "channel": "telegram",
                    "account_id": "bot_a",
                    "peer": "12345678",
                    "agent_id": "researcher",
                },
                {
                    "agent_id": "main",
                    "match": {
                        "channel": "slack",
                        "peer": {"kind": "group", "id": "C123"},
                    },
                },
            ],
            "session": {
                "idle_timeout_s": 1800,
                "max_history": 150,
                "store_path": "~/.teaming24/test-sessions.db",
                "reset_triggers": ["/new"],
            },
        }
    )

    assert len(cfg.bindings) == 2
    first = cfg.bindings[0]
    assert first.match.channel == "telegram"
    assert first.match.account_id == "bot_a"
    assert first.match.peer is not None
    assert first.match.peer.id == "12345678"
    assert first.match.peer.kind == ""

    second = cfg.bindings[1]
    assert second.match.channel == "slack"
    assert second.match.peer is not None
    assert second.match.peer.kind == "group"
    assert second.match.peer.id == "C123"

    assert cfg.session.idle_minutes == 30
    assert cfg.session.idle_timeout_s == 1800
    assert cfg.session.max_history == 150
    assert cfg.session.store_path == "~/.teaming24/test-sessions.db"
    assert cfg.session.reset_triggers == ["/new"]


def test_session_parser_is_resilient_to_invalid_types() -> None:
    cfg = config_module._build_config_from_dict(
        {
            "session": {
                "idle_minutes": "bad-int",
                "idle_timeout_s": "bad-seconds",
                "max_history": "NaN",
                "store_path": 1234,
                "reset_triggers": "/restart",
            },
            "channels": {
                "webchat": {"enabled": False},
            },
        }
    )

    assert cfg.session.idle_minutes == 120
    assert cfg.session.idle_timeout_s is None
    assert cfg.session.max_history == 200
    assert cfg.session.store_path == "1234"
    assert cfg.session.reset_triggers == ["/restart"]
    assert cfg.channels.webchat.enabled is True


def test_config_to_dict_includes_runtime_routing_and_session_sections() -> None:
    cfg = config_module._build_config_from_dict(
        {
            "framework": {"backend": "native"},
            "an_router": {"strategy": "organizer_llm", "min_pool_members": 1},
            "channels": {"webchat": {"enabled": True}},
            "bindings": [{"channel": "webchat", "agent_id": "main"}],
            "session": {"dm_scope": "main", "max_history": 50},
            "scheduler": {"auto_start": True, "jobs": [{"name": "j1", "prompt": "hello"}]},
        }
    )

    exported = cfg.to_dict()

    assert exported["framework"]["backend"] == "native"
    assert exported["an_router"]["min_pool_members"] == 1
    assert "webchat" in exported["channels"]
    assert exported["bindings"][0]["match"]["channel"] == "webchat"
    assert exported["session"]["max_history"] == 50
    assert exported["scheduler"]["auto_start"] is True


def test_legacy_local_password_maps_to_connection_password() -> None:
    cfg = config_module._build_config_from_dict(
        {"security": {"local_password": "secret"}}
    )
    assert cfg.security.connection_password == "secret"
