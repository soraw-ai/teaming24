from __future__ import annotations

import asyncio
from dataclasses import dataclass

from teaming24.api.routes import db as db_routes


@dataclass
class _Node:
    id: str
    name: str
    ip: str
    port: int
    capability: str | None = None
    description: str | None = None
    status: str = "online"
    type: str = "lan"
    region: str | None = None


class _Manager:
    def __init__(self, nodes):
        self._nodes = nodes

    def get_nodes(self):
        return self._nodes


def test_network_search_uses_real_manager_nodes(monkeypatch) -> None:
    nodes = [
        _Node(id="a1", name="Alpha", ip="10.0.0.2", port=8001, capability="AI Research"),
        _Node(id="b1", name="Beta", ip="10.0.0.3", port=8002, capability="Backend"),
    ]
    monkeypatch.setattr(db_routes, "get_network_manager", lambda: _Manager(nodes))

    result = asyncio.run(db_routes.search_nodes("research"))

    assert len(result["results"]) == 1
    assert result["results"][0]["id"] == "a1"
    assert result["results"][0]["capability"] == "AI Research"


def test_network_search_falls_back_to_db_when_manager_unavailable(monkeypatch) -> None:
    class _DB:
        @staticmethod
        def get_known_nodes():
            return [
                {
                    "id": "db-1",
                    "name": "Stored Node",
                    "ip": "10.0.0.9",
                    "port": 9000,
                    "capability": "Storage",
                    "description": "from db",
                    "status": "offline",
                    "node_type": "stored",
                }
            ]

    def _boom():
        raise RuntimeError("network manager down")

    monkeypatch.setattr(db_routes, "get_network_manager", _boom)
    monkeypatch.setattr(db_routes, "get_database", lambda: _DB())

    result = asyncio.run(db_routes.search_nodes("stored"))

    assert len(result["results"]) == 1
    assert result["results"][0]["id"] == "db-1"
    assert result["results"][0]["type"] == "stored"
