from typing import TYPE_CHECKING

from teaming24.communication.discovery import LANDiscovery, NodeInfo
from teaming24.utils.logger import get_logger

if TYPE_CHECKING:
    from teaming24.config import DiscoveryConfig

logger = get_logger(__name__)

class NetworkManager:
    """Manages network connections and node discovery."""

    def __init__(self, local_node: NodeInfo, config: 'DiscoveryConfig', on_event=None):
        """
        Initialize Network Manager.

        Args:
            local_node: Information about the local node
            config: Discovery configuration from agentanet.yaml
            on_event: Callback for network events
        """
        self.local_node = local_node
        self.config = config
        self.on_event = on_event
        self.discovery = LANDiscovery(
            local_node, config=config,
            on_discovery=self._on_discovery,
            on_node_seen=self._on_node_seen,
            get_connected_ids=self._get_connected_ids,
            get_is_scanning=lambda: self._is_scanning,
        )
        self.wan_nodes: dict[str, NodeInfo] = {}
        self.inbound_peers: dict[str, NodeInfo] = {}
        self._is_scanning: bool = False

    async def _on_discovery(self, node: NodeInfo):
        if self.on_event:
            await self.on_event("node_discovered", node.model_dump())

    async def _on_node_seen(self, node: NodeInfo):
        """Called when an existing LAN node re-broadcasts (heartbeat).

        If this node is a known inbound peer, fire a ``node_seen`` event so
        the health-check timer can be refreshed.
        """
        if node.id in self.inbound_peers:
            if self.on_event:
                await self.on_event("node_seen", {"node_id": node.id})

    def _get_connected_ids(self) -> set:
        """Return IDs of all actively connected peers (outbound + inbound).

        Used by the discovery cleanup loop to avoid removing nodes that
        are still connected, even if their UDP broadcast is temporarily
        missed during heavy task execution.
        """
        ids = set()
        ids.update(self.wan_nodes.keys())
        ids.update(self.inbound_peers.keys())
        return ids

    @property
    def is_running(self) -> bool:
        return self.discovery.running

    @property
    def is_discoverable(self) -> bool:
        return self.discovery.discoverable

    def set_discoverable(self, discoverable: bool):
        """Set whether this node can be discovered by others on LAN."""
        self.discovery.set_discoverable(discoverable)

    @property
    def is_scanning(self) -> bool:
        """True when the user has explicitly enabled LAN scan (Scan toggle ON)."""
        return self._is_scanning

    def set_scanning(self, scanning: bool):
        """Set whether active LAN scan (Scan toggle) is on."""
        self._is_scanning = scanning

    @property
    def known_nodes(self) -> dict[str, NodeInfo]:
        """Get all known nodes (LAN + WAN outbound)."""
        nodes = self.discovery.known_nodes.copy()
        nodes.update(self.wan_nodes)
        return nodes

    @property
    def all_reachable_nodes(self) -> dict[str, NodeInfo]:
        """Get all reachable nodes: LAN + WAN outbound + inbound peers.

        This is the full Agentic Node Workforce Pool that the Organizer can delegate to.
        Inbound peers (remote ANs that connected to us) are included so
        they are visible as delegation targets alongside outbound peers.
        """
        nodes = self.known_nodes.copy()
        nodes.update(self.inbound_peers)
        return nodes

    async def start(self):
        """Start network services."""
        await self.discovery.start()
        logger.info("Network Manager started")

    async def stop(self):
        """Stop network services."""
        self._is_scanning = False
        await self.discovery.stop()
        logger.info("Network Manager stopped")

    def get_nodes(self) -> list[NodeInfo]:
        """Get the full list of reachable nodes (Agentic Node Workforce Pool).

        Includes LAN-discovered, WAN-connected, and inbound peers.
        """
        return list(self.all_reachable_nodes.values())

    async def connect_node(self, ip: str, port: int, password: str) -> NodeInfo:
        """Connect to a node by IP:port (unified for LAN and WAN).

        Performs an outbound HTTP handshake.  If the target is unreachable the
        call fails — we intentionally do NOT silently "promote" an inbound peer,
        because a failed outbound handshake means we cannot actually reach the
        remote API, so a bidirectional link would be fake.
        """
        import httpx

        # Block if the target is already an inbound peer we can't reach outbound.
        inbound_match = self._find_inbound_peer_by_endpoint(ip, port)

        # Check capacity
        if len(self.wan_nodes) >= self.config.max_wan_nodes:
            raise Exception(f"Maximum connected nodes ({self.config.max_wan_nodes}) reached")

        url = f"http://{ip}:{port}/api/network/handshake"
        try:
            from teaming24.config import get_config
            _conn_cfg = get_config().connection
            async with httpx.AsyncClient(timeout=_conn_cfg.handshake_timeout) as client:
                # Include our node info so the peer can track inbound connections.
                response = await client.post(
                    url,
                    json={
                        "password": password,
                        "peer": {
                            "id": self.local_node.id,
                            "name": self.local_node.name,
                            "port": self.local_node.port,
                            "role": self.local_node.role,
                            "capability": self.local_node.capability,
                            "capabilities": self.local_node.capabilities,
                            "wallet_address": self.local_node.wallet_address,
                            "agent_id": self.local_node.agent_id,
                            "description": self.local_node.description,
                            "region": self.local_node.region,
                        },
                    },
                )

                if response.status_code == 401:
                    raise Exception("Invalid password")
                if response.status_code != 200:
                    raise Exception(f"Connection failed: {response.status_code}")

                data = response.json()
                # Ensure type is wan
                data['type'] = 'wan'
                # Use provided IP/Port for connection, but trust other info
                data['ip'] = ip
                data['port'] = port

                node = NodeInfo(**data)
                self.wan_nodes[node.id] = node
                logger.info(f"Connected to node: {node.name} ({node.ip}:{node.port})")
                return node
        except httpx.RequestError as e:
            logger.warning(f"Outbound handshake to {ip}:{port} failed: {e}")

            if inbound_match:
                # The remote node already connected to us, but we can't reach
                # them back.  A "bidirectional" link would be fake — reject.
                raise Exception(
                    f"Node {inbound_match.name} ({ip}:{port}) is already connected to you (inbound), "
                    f"but its API is not reachable from this side. "
                    f"Bidirectional connection is not possible."
                ) from e

            raise Exception(f"Failed to connect to {ip}:{port}: {e}") from e

    def _find_inbound_peer_by_endpoint(self, ip: str, port: int) -> NodeInfo | None:
        """Find an inbound peer matching the given IP:port."""
        for peer in self.inbound_peers.values():
            if peer.ip == ip and peer.port == port:
                return peer
        return None

    def disconnect_node(self, node_id: str) -> bool:
        """Disconnect a connected node by ID."""
        if node_id in self.wan_nodes:
            node = self.wan_nodes.pop(node_id)
            logger.info(f"Disconnected node: {node.name} ({node.ip})")
            return True
        return False

    def disconnect_node_by_endpoint(self, ip: str, port: int) -> bool:
        """Disconnect a connected node by its IP:port."""
        for node_id, node in list(self.wan_nodes.items()):
            if node.ip == ip and node.port == port:
                self.wan_nodes.pop(node_id, None)
                logger.info(f"Disconnected node: {node.name} ({node.ip}:{node.port})")
                return True
        return False

    def mark_node_offline(self, peer_id: str) -> bool:
        node = self.wan_nodes.get(peer_id)
        if not node:
            return False
        node.status = "offline"
        return True

    def clear_wan_nodes(self):
        """Clear all WAN nodes."""
        count = len(self.wan_nodes)
        self.wan_nodes.clear()
        logger.info(f"Cleared {count} WAN nodes")

    def register_inbound_peer(self, peer: NodeInfo):
        """Register/update a peer that connected to us (via handshake)."""
        self.inbound_peers[peer.id] = peer

    def get_inbound_peers(self) -> list[NodeInfo]:
        """Get list of peers that have connected to us."""
        return list(self.inbound_peers.values())

    def mark_inbound_peer_offline(self, peer_id: str) -> bool:
        peer = self.inbound_peers.get(peer_id)
        if not peer:
            return False
        peer.status = "offline"
        return True

    def remove_inbound_peer(self, peer_id: str) -> bool:
        """Remove an inbound peer from tracking."""
        if peer_id in self.inbound_peers:
            self.inbound_peers.pop(peer_id, None)
            return True
        return False

    def update_local_capabilities(
        self,
        capabilities: list | None = None,
        description: str | None = None,
    ):
        """Refresh the local node's advertised capabilities and description.

        Call this whenever the local worker pool changes (e.g. a worker
        goes offline/online) so the next LAN broadcast and WAN handshake
        carry accurate information.

        Args:
            capabilities: New capabilities list (same format as NodeInfo.capabilities).
            description: New human-readable node description.
        """
        if capabilities is not None:
            self.local_node.capabilities = capabilities
        if description is not None:
            self.local_node.description = description
        logger.info("Local node capabilities updated for next broadcast")
