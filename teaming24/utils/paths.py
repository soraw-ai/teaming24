"""
Path utilities for Teaming24.

Centralizes path resolution and sandbox path sanitization to avoid
duplication and ensure consistent security guarantees across tools.
"""

from __future__ import annotations

import os
from pathlib import PurePosixPath


def resolve_sandbox_path(agent_path: str, root: str) -> str:
    """Resolve an agent-supplied path to a safe absolute path under *root*.

    Security guarantees:
      - Strips leading ``/`` — absolute paths become relative.
      - Collapses ``..`` components — no escape above the root.
      - Returns a normalised absolute path under *root*.

    Args:
        agent_path: The raw path the agent passed (e.g. ``"pyproject.toml"``,
                    ``"../../../etc/passwd"``).
        root: Absolute path to the sandbox/workspace root directory.

    Returns:
        Absolute path that is guaranteed to be within *root*.
    """
    cleaned = PurePosixPath(agent_path.replace("\\", "/"))
    parts = [p for p in cleaned.parts if p != "/"]
    safe: list[str] = []
    for p in parts:
        if p == "..":
            if safe:
                safe.pop()
        elif p not in (".", ""):
            safe.append(p)
    relative = os.path.join(*safe) if safe else ""
    resolved = os.path.normpath(os.path.join(root, relative))
    if not resolved.startswith(os.path.normpath(root)):
        resolved = os.path.join(root, os.path.basename(agent_path))
    return resolved
