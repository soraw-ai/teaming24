from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import HTTPException

from teaming24.api.routes import db as db_routes


def test_set_security_config_writes_canonical_password_key(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(db_routes, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(db_routes, "UNIFIED_CONFIG_FILE", "teaming24.yaml")
    old_password = db_routes.config.security.connection_password
    local_request = SimpleNamespace(client=SimpleNamespace(host="127.0.0.1"))

    try:
        result = asyncio.run(
            db_routes.set_security_config(
                db_routes.SecurityUpdate(password="p@ss"),
                request=local_request,
            )
        )

        data = yaml.safe_load((tmp_path / "teaming24.yaml").read_text())

        assert result["status"] == "saved"
        assert data["security"]["connection_password"] == "p@ss"
        assert data["security"]["local_password"] == "p@ss"
        assert db_routes.config.security.connection_password == "p@ss"
    finally:
        db_routes.config.security.connection_password = old_password


def test_set_security_config_denies_remote_without_override(monkeypatch) -> None:
    monkeypatch.delenv("TEAMING24_ALLOW_REMOTE_ADMIN", raising=False)
    fake_request = SimpleNamespace(client=SimpleNamespace(host="8.8.8.8"))

    with pytest.raises(HTTPException) as exc:
        asyncio.run(
            db_routes.set_security_config(
                db_routes.SecurityUpdate(password="x"),
                request=fake_request,
            )
        )
    assert exc.value.status_code == 403


def test_reset_all_data_denies_remote_without_override(monkeypatch) -> None:
    monkeypatch.delenv("TEAMING24_ALLOW_REMOTE_ADMIN", raising=False)

    class _Req:
        client = SimpleNamespace(host="8.8.8.8")

        @staticmethod
        async def json():
            return {"confirm": "DELETE ALL DATA"}

    with pytest.raises(HTTPException) as exc:
        asyncio.run(db_routes.reset_all_data(_Req()))
    assert exc.value.status_code == 403
