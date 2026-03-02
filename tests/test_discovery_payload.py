from __future__ import annotations

import json
from types import SimpleNamespace

from teaming24.communication.discovery import LANDiscovery, NodeInfo


def _cfg() -> SimpleNamespace:
    return SimpleNamespace(
        broadcast_port=54321,
        udp_payload_target_bytes=700,
        udp_recv_buffer_size=65535,
    )


def test_discovery_payload_is_compacted_under_target_size() -> None:
    caps = [
        {
            "name": f"capability-{i}",
            "description": "x" * 500,
        }
        for i in range(40)
    ]
    node = NodeInfo(
        id="node-1",
        name="node-1",
        ip="192.168.1.8",
        port=54321,
        capability="general",
        capabilities=caps,
        description="y" * 2000,
    )
    d = LANDiscovery(node, _cfg())

    payload = d._build_node_payload()

    assert len(payload) <= 700
    data = json.loads(payload.decode())
    assert data["id"] == "node-1"
    assert data["name"] == "node-1"
    assert data["port"] == 54321
    assert "capabilities" not in data
    assert "wallet_address" not in data
    assert "agent_id" not in data
    assert "description" not in data
    assert "region" not in data
