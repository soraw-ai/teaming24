from __future__ import annotations

from teaming24.config.loader import apply_env_overrides


class _LoggerStub:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def error(self, message: str, *args) -> None:
        self.errors.append(message % args if args else message)


def test_apply_env_overrides_updates_server_and_local_node_port() -> None:
    data = {
        "system": {
            "server": {"host": "0.0.0.0", "port": 8000},
            "api": {"base_url": "http://localhost:8000"},
            "logging": {},
            "database": {},
        },
        "network": {"local_node": {"port": 8000}},
    }
    logger = _LoggerStub()

    updated = apply_env_overrides(
        data,
        environ={"TEAMING24_PORT": "9001", "TEAMING24_HOST": "127.0.0.1"},
        logger=logger,
    )

    assert updated["system"]["server"]["host"] == "127.0.0.1"
    assert updated["system"]["server"]["port"] == 9001
    assert updated["network"]["local_node"]["port"] == 9001
    assert updated["system"]["api"]["base_url"] == "http://127.0.0.1:9001"
    assert logger.errors == []


def test_apply_env_overrides_ignores_invalid_port() -> None:
    data = {"system": {"server": {}, "api": {}, "logging": {}, "database": {}}, "network": {"local_node": {}}}
    logger = _LoggerStub()

    updated = apply_env_overrides(
        data,
        environ={"TEAMING24_PORT": "not-a-number"},
        logger=logger,
    )

    assert "port" not in updated["system"]["server"]
    assert logger.errors
