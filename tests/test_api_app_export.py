from __future__ import annotations

from fastapi import FastAPI

from teaming24.api import app as app_module


def test_api_app_module_exports_default_asgi_app() -> None:
    assert isinstance(app_module.app, FastAPI)
