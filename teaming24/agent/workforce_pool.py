"""
AN Workforce Pool — full set of all AN-level execution targets.

This is the AN-level pool. The pool contains ALL: local team coordinator + all
connected Remote ANs. Before each execution, the ANRouter selects a SUBSET
from this pool. The Organizer assigns subtasks only to the selected members.

The local-level pool (Workers) is LocalAgentWorkforcePool; it uses
LocalAgentRouter for selection.

Pool contents:
  - Local: the Coordinator (gateway to local Workers). Always present.
  - Remote: all connected Agentic Nodes. Dynamic — appear/disappear with network state.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from teaming24.agent.routing_strategy import RoutingStrategy
from teaming24.communication.discovery import NodeInfo
from teaming24.utils.ids import LOCAL_COORDINATOR_ID
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

@dataclass
class AgenticNodeEntry:
    """One member of the Agentic Node Workforce Pool."""

    id: str
    name: str
    entry_type: str          # "local" or "remote"
    capabilities: list[str] = field(default_factory=list)
    status: str = "online"   # online / offline / busy
    cost: str | None = None
    description: str | None = None
    endpoint: str | None = None
    region: str | None = None
    wallet_address: str | None = None
    agent_id: str | None = None
    source: str | None = None  # lan / wan / marketplace / unknown
    # For remote entries — the raw NodeInfo needed by the delegation tool
    node_info: Any = None


# ---------------------------------------------------------------------------
# Pool implementation
# ---------------------------------------------------------------------------

class AgenticNodeWorkforcePool:
    """AN Workforce Pool — full set of local team coordinator + all Remote ANs.

    The pool contains ALL AN-level members. Before each execution, the
    ANRouter selects a subset from this pool. get_pool() returns the full snapshot.

    Args:
        local_crew: The local ``LocalCrew`` instance (provides capabilities
            and worker descriptions).
        network_manager: Optional ``NetworkManager`` for remote node discovery.
        strategy: Optional ``RoutingStrategy`` for ranking / selecting pool
            members.  A default instance is created if omitted.
    """

    def __init__(
        self,
        local_crew: Any,
        network_manager: Any = None,
        strategy: RoutingStrategy | None = None,
        extra_remote_nodes_provider: Callable[[], list[Any]] | None = None,
    ):
        self._local_crew = local_crew
        self._network_manager = network_manager
        self.strategy = strategy or RoutingStrategy()
        self._extra_remote_nodes_provider = extra_remote_nodes_provider

    # -- public API --------------------------------------------------------

    def get_pool(self) -> list[AgenticNodeEntry]:
        """Build and return the current pool snapshot.

        Returns one local entry (Coordinator) plus one entry per reachable
        remote AN that is online.
        """
        entries: list[AgenticNodeEntry] = []

        # 1. local team coordinator entry — aggregate Worker capabilities + descriptions
        local_caps: list[str] = []
        local_desc = "Local team"
        coord_name = "local team coordinator"
        if self._local_crew:
            local_caps = self._local_crew.get_capabilities()
            local_desc = self._build_local_description()
            coord = getattr(self._local_crew, "coordinator", None)
            if coord:
                coord_name = getattr(coord, "role", None) or coord_name

        entries.append(AgenticNodeEntry(
            id=LOCAL_COORDINATOR_ID,
            name=coord_name,
            entry_type="local",
            capabilities=self._filter_system_capabilities(local_caps),
            status="online",
            description=local_desc,
            source="local",
        ))

        # 2. Remote AN entries: connected nodes + marketplace/cache nodes.
        # Keep connected nodes first so they win during de-duplication.
        remote_candidates: list[Any] = []
        if self._network_manager:
            try:
                remote_candidates.extend(self._network_manager.get_nodes())
            except Exception as exc:
                logger.warning("Failed to load connected AN nodes: %s", exc, exc_info=True)
        if self._extra_remote_nodes_provider:
            try:
                remote_candidates.extend(self._extra_remote_nodes_provider() or [])
            except Exception as exc:
                logger.warning("Failed to load marketplace AN nodes: %s", exc, exc_info=True)

        seen_identity: set[str] = set()
        seen_entry_ids: set[str] = set()
        for raw_node in remote_candidates:
            node = self._coerce_node(raw_node)
            if not node:
                continue

            status = (getattr(node, "status", "online") or "online").lower()
            if status != "online":
                continue

            entry_id = self._pick_entry_id(node)
            if not entry_id or entry_id == LOCAL_COORDINATOR_ID:
                continue

            identity_tokens = self._identity_tokens(node, fallback_id=entry_id)
            if entry_id in seen_entry_ids or bool(identity_tokens & seen_identity):
                continue

            seen_entry_ids.add(entry_id)
            seen_identity.update(identity_tokens)

            caps = self._extract_capabilities(node)
            entries.append(AgenticNodeEntry(
                id=entry_id,
                name=(getattr(node, "name", None) or entry_id),
                entry_type="remote",
                capabilities=caps,
                status=status,
                cost=getattr(node, "price", None),
                description=getattr(node, "description", None),
                endpoint=self._build_endpoint(node),
                region=str(getattr(node, "region", "") or "").strip() or None,
                wallet_address=str(getattr(node, "wallet_address", "") or "").strip() or None,
                agent_id=str(getattr(node, "agent_id", "") or "").strip() or None,
                source=str(getattr(node, "type", "") or "").strip() or "unknown",
                node_info=node,
            ))

        return entries

    def search(self, capabilities: list[str]) -> list[AgenticNodeEntry]:
        """Return pool entries whose capabilities are a superset of *capabilities*.

        If *capabilities* is empty, returns the full pool.
        """
        if not capabilities:
            return self.get_pool()

        required = set(capabilities)
        return [
            e for e in self.get_pool()
            if required.issubset(set(e.capabilities))
        ]

    def rank(self, capabilities: list[str] | None = None) -> list[AgenticNodeEntry]:
        """Return all pool members ranked best-first by the routing strategy."""
        return self.strategy.rank(self.get_pool(), capabilities)

    def select(self, capabilities: list[str] | None = None) -> AgenticNodeEntry | None:
        """Return the single best pool member for the given capabilities."""
        return self.strategy.select(self.get_pool(), capabilities)

    def is_local_only(self) -> bool:
        """Return True when the pool has no remote ANs (offline mode)."""
        return all(e.entry_type == "local" for e in self.get_pool())

    def describe(self) -> str:
        """Human-readable summary of the current pool.

        Suitable for injecting into the Organizer's prompt so the LLM
        can make an informed routing decision.
        """
        entries = self.get_pool()
        if not entries:
            return "Agentic Node Workforce Pool is empty."

        lines = [f"Agentic Node Workforce Pool ({len(entries)} member(s)):"]
        for i, e in enumerate(entries, 1):
            tag = "LOCAL" if e.entry_type == "local" else "REMOTE"
            caps = ", ".join(e.capabilities[:12]) if e.capabilities else "general"
            if len(e.capabilities) > 12:
                caps += f", +{len(e.capabilities) - 12} more"
            cost_str = f", cost={e.cost}" if e.cost else ""
            endpoint_str = f", endpoint={e.endpoint}" if e.endpoint else ""
            region_str = f", region={e.region}" if e.region else ""
            wallet_str = f", wallet={self._short_wallet(e.wallet_address)}" if e.wallet_address else ""
            source_str = f", source={e.source}" if e.source else ""
            desc_str = f"\n     {e.description}" if e.description else ""
            lines.append(
                f"  {i}. [{tag}] {e.name} ({e.status}): "
                f"capabilities=[{caps}]{cost_str}{endpoint_str}{region_str}{wallet_str}{source_str}{desc_str}"
            )
        return "\n".join(lines)

    # -- internals ---------------------------------------------------------

    def _build_local_description(self) -> str:
        """Build a rich description for the local Coordinator entry.

        Lists every Worker role and its capabilities so the Organizer sees
        what the local team can do without inspecting individual Workers.
        """
        if not self._local_crew:
            return "Local team"

        worker_descs = []
        if hasattr(self._local_crew, "get_worker_descriptions"):
            worker_descs = self._local_crew.get_worker_descriptions()

        if not worker_descs:
            return "Local team with specialized Workers"

        parts = ["Local team with Workers:"]
        for wd in worker_descs:
            role = wd.get("role", "Worker")
            goal = wd.get("goal", "")
            line = f"  - {role}"
            if goal:
                line += f": {goal}"
            parts.append(line)
        return "\n".join(parts)

    @staticmethod
    def _extract_capabilities(node: Any) -> list[str]:
        """Normalize capability names from NodeInfo/dict payloads."""
        raw_caps = getattr(node, "capabilities", None) or []
        caps: list[str] = []
        for c in raw_caps:
            if isinstance(c, str):
                if c and c not in caps:
                    caps.append(c)
            elif isinstance(c, dict):
                name = str(c.get("name", "") or "").strip()
                if name and name not in caps:
                    caps.append(name)

        main_cap = str(getattr(node, "capability", "") or "").strip()
        if main_cap and main_cap not in caps:
            caps.append(main_cap)
        return AgenticNodeWorkforcePool._filter_system_capabilities(caps)

    @staticmethod
    def _filter_system_capabilities(capabilities: list[str] | None) -> list[str]:
        """Drop internal coordinator/organizer capability tags from AN descriptions."""
        blocked_keywords = (
            "organizer",
            "coordinator",
            "task_decomposition",
            "worker_coordination",
            "task_routing",
            "network_delegation",
        )
        filtered: list[str] = []
        seen: set[str] = set()
        for cap in capabilities or []:
            cap_name = str(cap or "").strip()
            if not cap_name:
                continue
            lowered = cap_name.lower()
            if any(k in lowered for k in blocked_keywords):
                continue
            if lowered in seen:
                continue
            seen.add(lowered)
            filtered.append(cap_name)
        return filtered

    @staticmethod
    def _build_endpoint(node: Any) -> str | None:
        ip = str(getattr(node, "ip", "") or "").strip()
        port = getattr(node, "port", None)
        try:
            port_num = int(port) if port is not None else 0
        except (TypeError, ValueError):
            port_num = 0
        if ip and port_num > 0:
            return f"{ip}:{port_num}"
        return None

    @staticmethod
    def _short_wallet(wallet: str | None) -> str:
        raw = str(wallet or "").strip()
        if len(raw) <= 14:
            return raw
        return f"{raw[:8]}…{raw[-6:]}"

    @staticmethod
    def _pick_entry_id(node: Any) -> str:
        """Pick stable ID for remote pool entry."""
        for key in ("id", "an_id", "agent_id"):
            val = str(getattr(node, key, "") or "").strip()
            if val:
                return val

        wallet = str(getattr(node, "wallet_address", "") or "").strip()
        if wallet:
            return wallet

        ip = str(getattr(node, "ip", "") or "").strip()
        port = getattr(node, "port", None)
        try:
            port_num = int(port) if port is not None else 0
        except (TypeError, ValueError):
            port_num = 0
        if ip and port_num > 0:
            return f"{ip}:{port_num}"
        return ""

    @staticmethod
    def _identity_tokens(node: Any, fallback_id: str = "") -> set[str]:
        """Identity keys used for cross-source de-duplication."""
        tokens: set[str] = set()

        for key in ("id", "an_id", "agent_id"):
            value = str(getattr(node, key, "") or "").strip()
            if value:
                tokens.add(f"{key}:{value}")

        wallet = str(getattr(node, "wallet_address", "") or "").strip().lower()
        if wallet:
            tokens.add(f"wallet:{wallet}")

        ip = str(getattr(node, "ip", "") or "").strip().lower()
        port = getattr(node, "port", None)
        try:
            port_num = int(port) if port is not None else 0
        except (TypeError, ValueError):
            port_num = 0
        if ip and port_num > 0:
            tokens.add(f"endpoint:{ip}:{port_num}")

        if fallback_id:
            tokens.add(f"id:{fallback_id}")
        return tokens

    @staticmethod
    def _coerce_node(raw_node: Any) -> NodeInfo | None:
        """Convert marketplace/connected node payload into NodeInfo."""
        if isinstance(raw_node, NodeInfo):
            return raw_node

        if not isinstance(raw_node, dict):
            try:
                node_dict = dict(raw_node)
            except Exception:
                return None
        else:
            node_dict = raw_node

        node_id = str(
            node_dict.get("id")
            or node_dict.get("an_id")
            or node_dict.get("agent_id")
            or ""
        ).strip()
        node_name = str(node_dict.get("name") or node_id or "Remote AN").strip()
        ip = str(node_dict.get("ip", "") or "").strip()
        try:
            port = int(node_dict.get("port", 0) or 0)
        except (TypeError, ValueError):
            port = 0
        if not node_id or not ip or port <= 0:
            return None

        payload = {
            "id": node_id,
            "name": node_name,
            "ip": ip,
            "port": port,
            "role": str(node_dict.get("role", "worker") or "worker"),
            "status": str(node_dict.get("status", "online") or "online"),
            "type": str(node_dict.get("type", "wan") or "wan"),
            "capability": node_dict.get("capability"),
            "capabilities": node_dict.get("capabilities"),
            "price": node_dict.get("price"),
            "wallet_address": node_dict.get("wallet_address"),
            "agent_id": node_dict.get("agent_id") or node_dict.get("an_id"),
            "description": node_dict.get("description"),
            "region": node_dict.get("region"),
        }
        try:
            return NodeInfo(**payload)
        except Exception as exc:
            logger.warning("Invalid remote node payload skipped: %s", exc, exc_info=True)
            return None
