"""Node identity helpers extracted from config bootstrap code."""

from __future__ import annotations

import hashlib
import socket
import uuid as _uuid_mod
from typing import Any

from teaming24.utils.ids import random_hex


def resolve_node_identity(local_node: Any, *, logger: Any) -> None:
    """Populate ``wallet_address``, ``an_id``, and ``name`` in-place."""
    if not local_node.wallet_address:
        try:
            hostname = socket.gethostname()
            mac = _uuid_mod.getnode()
            seed = f"{hostname}-{mac}"
        except Exception as exc:
            logger.warning(
                "Failed to derive wallet seed from hostname/MAC; using random seed: %s",
                exc,
                exc_info=True,
            )
            seed = random_hex(32)
        local_node.wallet_address = "0x" + hashlib.sha256(
            f"{seed}:wallet".encode()
        ).hexdigest()[:40]

    suffix = random_hex(6)
    local_node.an_id = f"{local_node.wallet_address}-{suffix}"

    if local_node.name:
        return

    try:
        hostname = socket.gethostname().split(".")[0]
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.connect(("8.8.8.8", 80))
            lan_ip = sock.getsockname()[0]
        finally:
            sock.close()
        local_node.name = f"{hostname}@{lan_ip}"
    except Exception as exc:
        logger.warning(
            "Failed to derive LAN display name; falling back to host:port: %s",
            exc,
            exc_info=True,
        )
        local_node.name = f"{local_node.host}:{local_node.port}"
