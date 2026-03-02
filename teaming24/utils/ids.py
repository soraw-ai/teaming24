"""
Centralized ID generation and well-known ID constants for Teaming24.

All agent IDs, task IDs, sandbox IDs, and other entity identifiers
should be generated or referenced through this module to ensure
consistency across backend and frontend.

ID Format Rules:
  - Agent IDs:    organizer-1, coordinator-1, worker-{registry_name}, system
  - Node UIDs:    node-{sha256_16}  (stable per machine+port, used in delegation chain)
  - Task IDs:     task_{uuid16}
  - Sandbox IDs:  sbx-task-{hash12}, openhands-{agent}, sandbox-{uuid8}, demo-{uuid8}
  - Step IDs:     {task_id}_{timestamp_ms}
  - Session IDs:  session_{uuid8}
"""

import hashlib
import socket
import time
import uuid

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# ============================================================================
# Node UID — Unique identifier for THIS node across the network.
# ============================================================================
# Uses hostname + MAC address + port to produce a deterministic, globally
# unique string.  Unlike config.local_node.name (human-readable, defaults to
# "Local Agentic Node" on every machine) or config.local_node.host:port
# (defaults to 127.0.0.1:8000 on every machine), this value is different
# on each physical/virtual machine, making it safe for delegation-chain
# loop detection.
#
# Cached after first call; the value is stable for the lifetime of the
# process.
# ============================================================================

_NODE_UID_CACHE: str | None = None


def uuid_str() -> str:
    """Generate a canonical UUID string."""
    return str(uuid.uuid4())


def random_hex(length: int = 12) -> str:
    """Generate a lowercase random hex string with exact length."""
    if length <= 0:
        return ""

    chunks: list[str] = []
    remaining = length
    while remaining > 0:
        block = uuid.uuid4().hex
        chunks.append(block[:remaining])
        remaining -= len(block[:remaining])
    return "".join(chunks)


def prefixed_id(prefix: str, length: int = 12, separator: str = "-") -> str:
    """Generate ``{prefix}{separator}{random_hex}`` with a stable helper."""
    core = random_hex(length)
    if not prefix:
        return core
    if not separator:
        return f"{prefix}{core}"
    if prefix.endswith(separator):
        return f"{prefix}{core}"
    return f"{prefix}{separator}{core}"


def get_node_uid() -> str:
    """Return the globally unique ``an_id`` for this node.

    Format: ``{wallet_address}-{6 hex}``, e.g.
    ``0x23489ac55f2cab16ddb872c1e2711616c5cf5c6e-a3f1b2``.

    The random suffix ensures uniqueness even if two machines share the
    same wallet address.  Generated once at startup (config post-init)
    and cached for the lifetime of the process.

    This value is used in:
      - Delegation chains (loop prevention)
      - ``requester_id`` in remote task requests
      - ``is_remote`` detection on the receiving end
    """
    global _NODE_UID_CACHE
    if _NODE_UID_CACHE is not None:
        return _NODE_UID_CACHE

    try:
        from teaming24.config import get_config
        cfg = get_config()
        an_id = cfg.local_node.an_id
        if an_id:
            _NODE_UID_CACHE = an_id
            return an_id
    except Exception as e:
        logger.debug(f"Failed to load an_id from config: {e}")

    # Fallback: generate from machine identity + random suffix
    try:
        hostname = socket.gethostname()
        mac = uuid.getnode()
        seed = f"{hostname}-{mac}"
    except Exception as e:
        logger.debug("Failed to get hostname/mac for node uid: %s", e)
        seed = random_hex(32)

    wallet = "0x" + hashlib.sha256(f"{seed}:wallet".encode()).hexdigest()[:40]
    suffix = random_hex(6)
    uid = f"{wallet}-{suffix}"
    _NODE_UID_CACHE = uid
    return uid

# ============================================================================
# Well-known Agent IDs (singleton roles)
# ============================================================================

ORGANIZER_ID = "organizer-1"
COORDINATOR_ID = "coordinator-1"
LOCAL_COORDINATOR_ID = COORDINATOR_ID  # unified — same agent in pool and agent store
SYSTEM_ID = "system"

# ============================================================================
# Agent ID Generators
# ============================================================================

def worker_id(index: int) -> str:
    """Generate a positional worker agent ID (legacy).

    .. deprecated::
        Prefer :func:`worker_id_from_name` which produces stable,
        name-based IDs that don't shift when the worker list changes.

    Args:
        index: 1-based index of the worker (worker-1, worker-2, ...).
    """
    return f"worker-{index}"


def worker_id_from_name(registered_name: str) -> str:
    """Generate a **stable** worker ID from the registry name.

    The ID is deterministic and will not change if the list order changes.

    Format: ``worker-{name}`` where *name* is the snake_case registry key
    (e.g. ``fullstack_dev`` → ``worker-fullstack_dev``).

    Args:
        registered_name: The worker's unique registry key (e.g. ``"fullstack_dev"``).

    Returns:
        Stable worker ID string.
    """
    slug = registered_name.strip().lower().replace(" ", "_").replace("-", "_")
    return f"worker-{slug}"


def agentic_node_id(short_hash: str) -> str:
    """Generate an Agentic Node ID from a node identifier.

    Args:
        short_hash: First 8 chars of the node id or name slug.
    """
    return f"agent-{short_hash[:8]}"


# ============================================================================
# Agent Name → ID Mapping Helpers
# ============================================================================

# Names that should map to Organizer (case-insensitive check)
_ORGANIZER_ALIASES = {"organizer", "assistant", "unknown", ""}

# Names that should map to Coordinator (case-insensitive substring check)
_COORDINATOR_KEYWORDS = {"coordinator"}

LOCAL_COORDINATOR_NAME = "local team coordinator"


def normalize_agent_name(agent_name: str) -> str:
    """Normalize user-facing agent labels to canonical display names.

    This keeps UI labels, task history, and progress tracking aligned while
    preserving canonical IDs untouched.
    """
    value = str(agent_name or "").strip()
    if not value:
        return value

    name_lower = value.lower()
    # Preserve canonical IDs and generated identifiers.
    if name_lower in {ORGANIZER_ID, COORDINATOR_ID, LOCAL_COORDINATOR_ID, SYSTEM_ID}:
        return value
    if name_lower.startswith(("worker-", "remote-", "agent-", "anrouter-", SANDBOX_PREFIX)):
        return value

    if (
        name_lower == "coordinator"
        or "local coordinator" in name_lower
        or "local team coordinator" in name_lower
    ):
        return LOCAL_COORDINATOR_NAME

    if name_lower == "organizer":
        return "Organizer"

    if name_lower == "anrouter":
        return "ANRouter"

    return value


def resolve_agent_id(
    agent_name: str,
    worker_name_to_id: dict | None = None,
) -> tuple[str, str]:
    """Resolve an agent name to (agent_id, agent_type).

    This is the single source of truth for mapping CrewAI agent names
    to the canonical IDs used throughout the system.

    Args:
        agent_name: The agent's display name / role from CrewAI.
        worker_name_to_id: Optional lookup mapping worker names to IDs.

    Returns:
        (agent_id, agent_type) tuple.
    """
    normalized_name = normalize_agent_name(agent_name)
    name_lower = normalized_name.strip().lower()

    # Normalize known aliases to Organizer
    if name_lower in _ORGANIZER_ALIASES:
        return ORGANIZER_ID, "organizer"

    # Check for organizer keyword
    if "organizer" in name_lower:
        return ORGANIZER_ID, "organizer"

    # Check for coordinator keyword
    if "coordinator" in name_lower:
        return COORDINATOR_ID, "coordinator"

    # Check for ANRouter / router keyword
    if "router" in name_lower or "anrouter" in name_lower:
        return "anrouter-1", "router"

    # Check for remote AN / Agentic Node keyword
    if "remote" in name_lower or "agentic node" in name_lower:
        return f"remote-{generic_id()}", "remote"

    # Worker — use lookup if available
    if worker_name_to_id:
        wid = (
            worker_name_to_id.get(normalized_name)
            or worker_name_to_id.get(name_lower)
            or worker_name_to_id.get(agent_name)
        )
        if wid:
            return wid, "worker"

    # Fallback: first worker
    return worker_id(1), "worker"


def build_worker_lookup(
    workers: list,
    worker_configs: list | None = None,
) -> dict:
    """Build a worker name → ID lookup dict from a crew's worker list.

    When *worker_configs* is provided (list of dicts from the worker
    registry, same order as *workers*), the **stable name-based ID**
    (``worker-{registered_name}``) is used.  Otherwise falls back to the
    legacy positional ``worker-{index}`` ID.

    Args:
        workers: List of CrewAI Agent objects with ``role`` / ``name`` attrs.
        worker_configs: Optional parallel list of worker config dicts, each
            containing at least ``{"name": "registry_key", ...}``.

    Returns:
        Dict mapping name/role (and lowercase variants) to worker IDs.
    """
    lookup: dict[str, str] = {}
    for i, w in enumerate(workers):
        # Prefer stable name-based ID from the registry config
        if worker_configs and i < len(worker_configs):
            reg_name = worker_configs[i].get("name", "")
            wid = worker_id_from_name(reg_name) if reg_name else worker_id(i + 1)
        else:
            wid = worker_id(i + 1)

        role = getattr(w, "role", None)
        name = getattr(w, "name", None)
        if role:
            lookup[role] = wid
            lookup[role.lower()] = wid
        if name and name != role:
            lookup[name] = wid
            lookup[name.lower()] = wid
    return lookup


# ============================================================================
# Task ID Generators
# ============================================================================

def task_id() -> str:
    """Generate a unique task ID.

    Format: ``task_{YYYYMMDDHHmmss}_{8hex}``

    The datetime segment makes task IDs human-readable and sortable by
    creation time, while the random hex suffix prevents collisions.

    Example: ``task_20260209143025_a3f8b1c2``
    """
    from datetime import datetime as _dt
    ts = _dt.now().strftime("%Y%m%d%H%M%S")
    rand = random_hex(8)
    return f"task_{ts}_{rand}"


def main_task_id(task_id_val: str) -> str:
    """Extract the root/main task ID from a hierarchical task ID.

    Format: ``{main_id}/{subtask_1}/{subtask_2}/...``
    Main = first segment (before first ``/``). Subtasks append segments.

    Example:
        main_task_id("task_20260224_abc123")         → "task_20260224_abc123"
        main_task_id("task_20260224_abc123/r1/s1")   → "task_20260224_abc123"
    """
    if not task_id_val:
        return ""
    idx = task_id_val.find("/")
    return task_id_val[:idx] if idx >= 0 else task_id_val


def subtask_id(parent_id: str, segment: str) -> str:
    """Append a subtask segment to parent ID: ``parent_id/segment``.

    Works for any depth — parent can be main or a subtask.
    Avoids duplicate: if parent already ends with ``/segment``, returns parent.

    Example:
        subtask_id("task_20260224_abc123", "round_1")
        → "task_20260224_abc123/round_1"
        subtask_id("task_20260224_abc123/round_1", "remote_0")
        → "task_20260224_abc123/round_1/remote_0"
        subtask_id("task_xxx/round_1", "round_1")  # duplicate avoided
        → "task_xxx/round_1"
    """
    if not parent_id or not segment:
        return parent_id or segment or ""
    base = parent_id.rstrip("/")
    # Avoid duplicate: parent already ends with this segment
    if base.endswith("/" + segment) or base == segment:
        return base
    return f"{base}/{segment}"


# ============================================================================
# Sandbox ID Generators
# ============================================================================

def sandbox_id_for_task(task_id_val: str) -> str:
    """Generate a deterministic sandbox ID for a task execution.

    Same task_id always maps to the same sandbox_id so events and metrics
    aggregate correctly (register_task_sandbox, on_step, etc. must share one ID).

    Args:
        task_id_val: The task ID this sandbox is executing.
    """
    suffix = (task_id_val or "").replace("/", "_")
    h = hashlib.sha256(suffix.encode()).hexdigest()[:12]
    return f"sbx-task-{h}"


def sandbox_id_for_openhands(agent_id_val: str) -> str:
    """Generate a sandbox ID for an OpenHands runtime.

    Args:
        agent_id_val: The agent/session ID for the OpenHands runtime.
    """
    return f"openhands-{agent_id_val}"


def sandbox_id_from_container(container_id_val: str) -> str:
    """Use Docker container ID as sandbox ID when available.

    Args:
        container_id_val: Docker container ID (full or short).
    """
    # Docker IDs are 64 hex chars; short form is 12 chars
    clean = (container_id_val or "").strip()[:64]
    if clean and all(c in "0123456789abcdef" for c in clean.lower()):
        return f"docker-{clean[:12]}"  # Prefix for identification
    return sandbox_id_generic()


def sandbox_id_generic() -> str:
    """Generate a generic sandbox ID."""
    return prefixed_id("sandbox", 8)


def sandbox_id_demo() -> str:
    """Generate a demo sandbox ID."""
    return prefixed_id("demo", 8)


# ============================================================================
# Step / Session / Misc ID Generators
# ============================================================================

def step_id(task_id_val: str) -> str:
    """Generate a step ID scoped to a task."""
    return f"{task_id_val}_{int(time.time() * 1000)}"


def session_id(name: str | None = None) -> str:
    """Generate a session ID."""
    if name:
        return name
    return prefixed_id("session_", 8, separator="")


def generic_id() -> str:
    """Generate a short random ID for frontend entities (messages, logs, etc.)."""
    return random_hex(12)


# ============================================================================
# Prefixes (for filtering / detection)
# ============================================================================

DEMO_PREFIX = "demo-"
OPENHANDS_PREFIX = "openhands-"
SANDBOX_PREFIX = "sandbox-"
TASK_SANDBOX_PREFIX = "task-"
WORKER_PREFIX = "worker-"

def is_demo_id(entity_id: str) -> bool:
    """Check if an ID belongs to demo data."""
    return entity_id.startswith(DEMO_PREFIX)


def is_openhands_sandbox(sandbox_id_val: str) -> bool:
    """Check if a sandbox ID is an OpenHands runtime."""
    return sandbox_id_val.startswith(OPENHANDS_PREFIX)


def extract_agent_id_from_openhands_sandbox(sandbox_id_val: str) -> str | None:
    """Extract the agent ID from an OpenHands sandbox ID."""
    if sandbox_id_val.startswith(OPENHANDS_PREFIX):
        return sandbox_id_val[len(OPENHANDS_PREFIX):]
    return None
