from __future__ import annotations

import asyncio
import json

from teaming24.api.routes import config as config_routes
from teaming24.channels.manager import ChannelManager
from teaming24.channels.webchat import WebChatAdapter
from teaming24.gateway import gateway as gateway_module


def test_channel_manager_start_stop_updates_adapter_running_state() -> None:
    manager = ChannelManager()
    adapter = WebChatAdapter()
    manager._adapters = {"webchat:default": adapter}

    asyncio.run(manager.start())
    assert adapter._running is True

    asyncio.run(manager.stop())
    assert adapter._running is False


def test_list_channels_reports_connected_from_running_adapters(monkeypatch) -> None:
    class _FakeAdapter:
        def __init__(self, running: bool):
            self._running = running

    class _FakeCM:
        _adapters = {
            "webchat:default": _FakeAdapter(True),
        }

    class _FakeGW:
        channel_manager = _FakeCM()

    monkeypatch.setattr(gateway_module, "_gateway", _FakeGW())

    response = asyncio.run(config_routes.list_channels())
    payload = json.loads(response.body.decode("utf-8"))
    by_id = {c["id"]: c for c in payload["channels"]}
    assert by_id["webchat"]["connected"] is True
