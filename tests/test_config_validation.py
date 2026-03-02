from __future__ import annotations

from teaming24.config.validation import validate_config


def test_validation_flags_invalid_an_router_values() -> None:
    errors = validate_config(
        {
            "an_router": {
                "routing_temperature": 3.1,
                "min_pool_members": 0,
            }
        }
    )
    joined = "\n".join(errors)
    assert "an_router → routing_temperature" in joined
    assert "an_router → min_pool_members" in joined


def test_validation_flags_invalid_session_scope() -> None:
    errors = validate_config(
        {
            "session": {
                "dm_scope": "invalid-scope",
            }
        }
    )
    assert any("session → dm_scope" in e for e in errors)
