"""Centralized ID generation helpers for AgentaNet Central."""

from __future__ import annotations

import uuid


def new_id() -> str:
    """Return a canonical UUID string ID."""
    return str(uuid.uuid4())


def random_hex(length: int = 12) -> str:
    """Return lowercase random hex with exact length."""
    if length <= 0:
        return ""

    out: list[str] = []
    remain = length
    while remain > 0:
        chunk = uuid.uuid4().hex
        out.append(chunk[:remain])
        remain -= len(chunk[:remain])
    return "".join(out)


def prefixed_id(prefix: str, length: int = 12, separator: str = "-") -> str:
    """Return ``{prefix}{separator}{random_hex}``."""
    core = random_hex(length)
    if not prefix:
        return core
    if not separator:
        return f"{prefix}{core}"
    if prefix.endswith(separator):
        return f"{prefix}{core}"
    return f"{prefix}{separator}{core}"
