"""
LAN Discovery service using UDP broadcast.

Based on patterns from DAAN-mcp_services a2a/node.py implementation.
Uses UDP broadcast for peer discovery with proper socket options for reliability.
"""
import asyncio
import json
import socket
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from pydantic import BaseModel, Field

from teaming24.utils.logger import get_logger

if TYPE_CHECKING:
    from teaming24.config import DiscoveryConfig

logger = get_logger(__name__)


class NodeInfo(BaseModel):
    """Information about a network node."""
    id: str
    name: str
    ip: str
    port: int
    role: str = "worker"
    status: str = "online"
    last_seen: float = Field(default_factory=time.time)
    type: str = "lan"  # lan or wan
    capability: str | None = None
    capabilities: list | None = None  # List of {name, description}
    price: str | None = None
    # Extended info
    wallet_address: str | None = None
    agent_id: str | None = None
    description: str | None = None
    region: str | None = None


class LANDiscovery:
    """
    LAN Discovery service using UDP broadcast.

    Features:
    - Automatic node discovery via UDP broadcasts
    - Configurable broadcast port from teaming24.yaml
    - SO_REUSEPORT for reliable broadcast reception on macOS/Linux
    - Periodic cleanup of stale nodes
    - Manual broadcast trigger support
    """

    def __init__(self, local_node: NodeInfo, config: 'DiscoveryConfig',
                 on_discovery: Callable = None, on_node_seen: Callable = None,
                 get_connected_ids: Callable = None, get_is_scanning: Callable = None):
        """
        Initialize LAN Discovery service.

        Args:
            local_node: Information about the local node
            config: Discovery configuration from teaming24.yaml (network.discovery)
            on_discovery: Callback when a new node is discovered
            on_node_seen: Callback when an existing node re-broadcasts (heartbeat)
            get_connected_ids: Callback returning set of node IDs that are
                               actively connected (outbound + inbound).  Nodes
                               with these IDs are never removed by cleanup even
                               if their broadcast is temporarily missed.
        """
        self.local_node = local_node
        self.config = config
        self.on_discovery = on_discovery
        self.on_node_seen = on_node_seen
        self.get_connected_ids = get_connected_ids
        self.get_is_scanning = get_is_scanning or (lambda: False)
        self.running = False
        self.discoverable = True  # LAN Visible by default: respond to discover requests and announce presence
        self.known_nodes: dict[str, NodeInfo] = {}

        # Get all local IPs to filter out self
        self._local_ips: set[str] = self._get_all_local_ips()

        # Socket and tasks
        self._udp_socket: socket.socket | None = None
        self._broadcast_task: asyncio.Task | None = None
        self._listener_task: asyncio.Task | None = None
        self._cleanup_task: asyncio.Task | None = None

        # (bind_ip, broadcast_addr) to try; bind_ip '' means 0.0.0.0 (populated on start)
        self._broadcast_tuples: list = []
        # Suppress repeated broadcast errors after the first notice
        self._broadcast_error_logged: bool = False
        # Consecutive send failures — used for exponential backoff
        self._broadcast_fail_count: int = 0
        # Per-destination unicast route-failure tracking (VPN / AP isolation / route flaps)
        self._unicast_block_until: dict[str, float] = {}
        self._unicast_fail_count: dict[str, int] = {}
        self._unicast_last_warn_at: dict[str, float] = {}
        self._unicast_warn_cooldown_s: float = 30.0
        self._unicast_route_errnos: set[int] = {51, 65, 113}  # network unreachable / no route
        # De-duplicate repeated discover requests received via multiple broadcast
        # targets during a single scan cycle.
        self._recent_discover_seen: dict[str, float] = {}
        self._discover_dedupe_window_s: float = max(
            0.1, float(getattr(self.config, "discover_dedupe_window_s", 1.0))
        )
        # UDP datagram handling: use large receive buffer and compact outbound payload
        self._udp_recv_buffer_size: int = max(
            4096, int(getattr(self.config, "udp_recv_buffer_size", 65535))
        )
        # Keep payload near MTU to avoid fragmentation losses on LAN/Wi-Fi
        self._udp_payload_target_bytes: int = max(
            512, int(getattr(self.config, "udp_payload_target_bytes", 1200))
        )

    def _get_primary_lan_ip(self) -> str | None:
        """Return primary LAN IP (not 127.x) for use in discovery responses."""
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            ip = s.getsockname()[0]
            s.close()
            return ip if ip and not ip.startswith('127.') else None
        except Exception as exc:
            logger.debug("Failed to determine primary LAN IP: %s", exc, exc_info=True)
            return None

    def _get_all_local_ips(self) -> set[str]:
        """Get all local IP addresses to filter out self-discovery."""
        local_ips = {'127.0.0.1', '::1', '0.0.0.0'}

        try:
            hostname = socket.gethostname()
            # Get all addresses for hostname
            for info in socket.getaddrinfo(hostname, None):
                local_ips.add(info[4][0])
        except socket.gaierror:
            logger.debug("Hostname lookup failed while collecting local IPs")
            pass

        # Try to get the primary local IP
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            local_ips.add(s.getsockname()[0])
            s.close()
        except Exception as e:
            logger.debug("Failed to get primary local IP: %s", e, exc_info=True)

        return local_ips

    def set_discoverable(self, discoverable: bool):
        """Set whether this node can be discovered by others."""
        self.discoverable = discoverable
        logger.info(f"LAN discoverability set to: {discoverable}")

    def _build_node_payload(self, advertised_ip: str | None = None) -> bytes:
        """Build minimal LAN payload (discovery only).

        LAN UDP packets intentionally carry only identity/routing essentials.
        Full AN metadata is exchanged via HTTP handshake when a connection is
        explicitly established.
        """
        node_ip = advertised_ip or self.local_node.ip
        if node_ip in ("0.0.0.0", "127.0.0.1", "localhost", None):
            node_ip = self._get_primary_lan_ip() or "0.0.0.0"

        minimal = {
            "id": self.local_node.id,
            "name": self.local_node.name,
            "ip": node_ip,
            "port": self.local_node.port,
            "role": self.local_node.role,
            "status": self.local_node.status,
            "type": self.local_node.type or "lan",
            "capability": self.local_node.capability,
        }
        encoded = json.dumps(minimal, separators=(",", ":"), ensure_ascii=False).encode()
        if len(encoded) > self._udp_payload_target_bytes:
            logger.warning(
                "Minimal LAN payload still exceeds target size (size=%d target=%d)",
                len(encoded),
                self._udp_payload_target_bytes,
            )
        return encoded

    def _is_unicast_temporarily_blocked(self, dest_ip: str) -> bool:
        """Return True when recent route failures suggest unicast should be skipped."""
        until = self._unicast_block_until.get(dest_ip, 0.0)
        return until > time.time()

    def _record_unicast_failure(self, dest_ip: str, dest_port: int, err: OSError | Exception) -> None:
        """Record unicast failure and apply short backoff for route-related errors."""
        now = time.time()
        errno = getattr(err, "errno", None)
        count = self._unicast_fail_count.get(dest_ip, 0) + 1
        self._unicast_fail_count[dest_ip] = count

        # Route-level failures are usually persistent while VPN/AP state is unchanged.
        if isinstance(errno, int) and errno in self._unicast_route_errnos:
            block_s = min(300.0, float(2 ** min(count, 6)))  # 2s .. 64s .. capped
            self._unicast_block_until[dest_ip] = now + block_s

        last_warn = self._unicast_last_warn_at.get(dest_ip, 0.0)
        if now - last_warn >= self._unicast_warn_cooldown_s:
            logger.warning(
                "Unicast sendto failed dest=%s:%d errno=%s err=%s (fail_count=%d, blocked_until=%.0f)",
                dest_ip,
                dest_port,
                errno,
                err,
                count,
                self._unicast_block_until.get(dest_ip, 0.0),
            )
            self._unicast_last_warn_at[dest_ip] = now
        else:
            logger.debug(
                "Unicast sendto failed (suppressed) dest=%s:%d errno=%s err=%s",
                dest_ip,
                dest_port,
                errno,
                err,
            )

    def _record_unicast_success(self, dest_ip: str) -> None:
        """Clear failure backoff state when unicast succeeds."""
        self._unicast_fail_count.pop(dest_ip, None)
        self._unicast_block_until.pop(dest_ip, None)
        self._unicast_last_warn_at.pop(dest_ip, None)

    def _initialize_udp_socket(self) -> bool:
        """
        Initialize and bind the UDP socket for discovery.
        Uses proper socket options for reliable broadcast reception.

        Returns:
            bool: True if successful, False otherwise
        """
        try:
            # Clean up any existing socket
            if self._udp_socket:
                try:
                    self._udp_socket.close()
                except Exception as e:
                    logger.debug("Error closing existing UDP socket: %s", e, exc_info=True)

            # Create a new UDP socket
            self._udp_socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

            # Enable address reuse
            self._udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)

            # Enable port reuse (important for macOS to receive broadcast packets)
            try:
                self._udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
                logger.debug("SO_REUSEPORT enabled for UDP socket")
            except (AttributeError, OSError) as e:
                logger.debug("SO_REUSEPORT not available: %s", e, exc_info=True)

            # Enable broadcast
            self._udp_socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

            # Get port from config
            discovery_port = self.config.broadcast_port
            logger.info(f"UDP discovery using port {discovery_port} (from config)")

            # Bind to all interfaces (required for receiving broadcasts from any interface)
            self._udp_socket.bind(('0.0.0.0', discovery_port))
            logger.info(f"UDP socket bound to 0.0.0.0:{discovery_port}")

            # Set to non-blocking for async operation
            self._udp_socket.setblocking(False)

            return True

        except Exception as e:
            logger.error("Error initializing UDP socket: %s", e, exc_info=True)
            self._udp_socket = None
            return False

    def _get_broadcast_tuples(self) -> list:
        """Build list of (bind_ip, broadcast_addr) to try.

        Binding to the interface's local IP before sending fixes "no route to host"
        on macOS/Wi-Fi where a socket bound to 0.0.0.0 cannot send to broadcast.
        """
        result: list = []  # (bind_ip, broadcast_addr); bind_ip '' means 0.0.0.0
        seen_bcast: set = set()
        skip_prefixes = ('lo', 'docker', 'veth', 'br-')

        # 1. Primary route: bind to that interface's IP, send to its subnet broadcast
        primary_local: str | None = None
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(('8.8.8.8', 80))
            primary_local = s.getsockname()[0]
            s.close()
            if primary_local and not primary_local.startswith('127.'):
                parts = primary_local.split('.')
                if len(parts) == 4:
                    bcast = f"{parts[0]}.{parts[1]}.{parts[2]}.255"
                    if bcast not in seen_bcast:
                        seen_bcast.add(bcast)
                        result.append((primary_local, bcast))
        except Exception as e:
            logger.debug("Failed to derive broadcast from primary route: %s", e, exc_info=True)

        # 2. From netifaces: bind to each interface's addr, send to its broadcast
        try:
            import netifaces  # type: ignore
            for iface in netifaces.interfaces():
                if any(iface.startswith(p) for p in skip_prefixes):
                    continue
                af_inet = netifaces.ifaddresses(iface).get(netifaces.AF_INET, [])
                for entry in af_inet:
                    addr = entry.get('addr')
                    bcast = entry.get('broadcast')
                    if addr and bcast and bcast not in seen_bcast:
                        seen_bcast.add(bcast)
                        result.append((addr, bcast))
        except ImportError:
            logger.debug("netifaces not installed; LAN discovery using limited broadcast only")
            pass

        # 3. Limited broadcast; bind to primary local or 0.0.0.0
        if '255.255.255.255' not in seen_bcast:
            result.append((primary_local or '', '255.255.255.255'))
        return result

    async def start(self):
        """Start discovery service (listener and broadcaster)."""
        if self.running:
            logger.debug("Discovery already running")
            return

        self.running = True
        self._broadcast_error_logged = False
        self._broadcast_fail_count = 0

        # Compute (bind_ip, broadcast_addr) once at start
        self._broadcast_tuples = self._get_broadcast_tuples()
        addrs_log = [b for _, b in self._broadcast_tuples]
        logger.info(f"Broadcast targets: {addrs_log}")

        # Initialize UDP socket
        if not self._initialize_udp_socket():
            logger.error("Failed to initialize UDP socket, discovery disabled")
            self.running = False
            return

        # Start listener task
        self._listener_task = asyncio.create_task(self._listener_loop())

        # Start broadcast task
        self._broadcast_task = asyncio.create_task(self._broadcast_loop())

        # Start cleanup task
        self._cleanup_task = asyncio.create_task(self._cleanup_loop())

        logger.info(f"LAN Discovery started on port {self.config.broadcast_port}")

    async def stop(self):
        """Stop discovery service."""
        self.running = False

        # Cancel tasks
        for task in [self._broadcast_task, self._listener_task, self._cleanup_task]:
            if task:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug("LAN discovery task cancelled during stop")
                    pass

        # Close socket
        if self._udp_socket:
            try:
                self._udp_socket.close()
            except Exception as e:
                logger.debug("Error closing UDP socket during stop: %s", e, exc_info=True)
            self._udp_socket = None

        # Clear known nodes
        self.known_nodes.clear()

        logger.info("LAN Discovery stopped")

    async def _listener_loop(self):
        """
        UDP listener loop that receives node broadcasts.
        Runs continuously while discovery is active.
        """
        if not self._udp_socket:
            logger.error("UDP socket not initialized")
            return

        logger.debug(f"UDP listener started on port {self.config.broadcast_port}")

        while self.running:
            try:
                # Use asyncio to wait for data without blocking
                try:
                    loop = asyncio.get_event_loop()
                    data, addr = await asyncio.wait_for(
                        loop.run_in_executor(
                            None,
                            lambda: self._udp_socket.recvfrom(self._udp_recv_buffer_size),
                        ),
                        timeout=self.config.udp_receive_timeout
                    )
                    await self._handle_udp_message(data, addr)
                except TimeoutError:
                    # Timeout is normal, continue
                    logger.debug("LAN discovery UDP receive timeout")
                    pass
                except BlockingIOError:
                    logger.debug("LAN discovery UDP socket temporarily unavailable (BlockingIOError)")
                    await asyncio.sleep(0.1)
                except OSError as e:
                    if e.errno == 9:  # Bad file descriptor - socket closed
                        break
                    logger.debug("UDP receive error: %s", e, exc_info=True)
                    await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                logger.debug("LAN discovery listener loop cancelled")
                break
            except Exception as e:
                logger.warning("Error in UDP listener: %s", e, exc_info=True)
                await asyncio.sleep(1.0)

    async def _handle_udp_message(self, data: bytes, addr: tuple):
        """
        Handle incoming UDP broadcast message.

        Args:
            data: Received message data
            addr: Sender address tuple (ip, port)
        """
        sender_ip, sender_port = addr[0], addr[1]
        try:
            message = json.loads(data.decode())

            # Skip messages from ourselves (match 7c95b68)
            if sender_ip in self._local_ips:
                return

            msg_type = message.get("type", "node_info") if isinstance(message, dict) else "unknown"
            logger.info("Received LAN message from %s:%d, type=%s", sender_ip, sender_port, msg_type)

            # Handle discover requests: respond when LAN Visible (discoverable)
            if isinstance(message, dict) and message.get("type") == "discover":
                if not self.discoverable:
                    return
                if not self._udp_socket:
                    return
                now = time.time()
                reply_port = message.get("reply_port")
                nonce = str(message.get("nonce", "") or "")
                dedupe_key = f"{sender_ip}:{sender_port}:{reply_port}:{nonce}"
                last_seen = self._recent_discover_seen.get(dedupe_key, 0.0)
                if now - last_seen < self._discover_dedupe_window_s:
                    logger.debug(
                        "Duplicate discover suppressed from %s:%d (nonce=%s)",
                        sender_ip,
                        sender_port,
                        nonce or "-",
                    )
                    return
                self._recent_discover_seen[dedupe_key] = now
                if len(self._recent_discover_seen) > 2048:
                    cutoff = now - (self._discover_dedupe_window_s * 4.0)
                    self._recent_discover_seen = {
                        k: ts for k, ts in self._recent_discover_seen.items()
                        if ts >= cutoff
                    }

                # Respond with a broadcast so the reply reaches the scanner even
                # when the Wi-Fi AP has client isolation enabled (which blocks
                # direct unicast between wireless clients on the same subnet).
                # Try unicast first (more efficient); fall back to broadcast.
                try:
                    self.local_node.last_seen = time.time()
                    payload = self._build_node_payload(advertised_ip=self._get_primary_lan_ip())
                    dest_port = sender_port
                    if isinstance(message.get("reply_port"), int) and 1 <= message["reply_port"] <= 65535:
                        dest_port = message["reply_port"]
                    if self._is_unicast_temporarily_blocked(sender_ip):
                        logger.debug(
                            "Skip unicast reply to %s:%d due to recent route failures; using broadcast fallback",
                            sender_ip,
                            dest_port,
                        )
                        await self._send_broadcast()
                        logger.info("Responded to discover from %s:%d via broadcast (unicast backoff)", sender_ip, dest_port)
                    elif self._send_unicast(payload, sender_ip, dest_port):
                        logger.info("Responded to discover from %s:%d via unicast", sender_ip, dest_port)
                    else:
                        # Unicast blocked (AP client isolation) — broadcast our NodeInfo
                        # so the scanner receives it on its 0.0.0.0:54321 listener.
                        await self._send_broadcast()
                        logger.info("Responded to discover from %s:%d via broadcast (unicast blocked)", sender_ip, dest_port)
                except Exception as e:
                    logger.warning(
                        "Failed to respond to discover from %s:%d: %s",
                        sender_ip,
                        addr[1],
                        e,
                        exc_info=True,
                    )
                return

            # Handle node info messages (discover response or periodic broadcast)
            if "id" in message and "name" in message:
                if sender_ip not in self._local_ips:
                    logger.info(
                        "Received discover response from peer %s:%d: %s (id=%s)",
                        sender_ip, sender_port, message.get("name", "?"), message.get("id", "?"),
                    )
                node_data = message.copy()
                if node_data.get('id') == self.local_node.id:
                    return  # Ignore our own node
                # Allow node info from same-machine instances (different ports)

                if node_data.get('ip') in ('0.0.0.0', '127.0.0.1', 'localhost', None):
                    node_data['ip'] = sender_ip
                node_data['type'] = 'lan'
                node_data['last_seen'] = time.time()

                try:
                    node = NodeInfo(**node_data)
                except Exception as e:
                    logger.warning(
                        "Invalid node data from %s: %s (keys=%s)",
                        sender_ip,
                        e,
                        list(node_data.keys()),
                        exc_info=True,
                    )
                    return

                # Check if new or existing
                is_new = node.id not in self.known_nodes

                # Prevent unbounded growth
                if is_new and len(self.known_nodes) >= self.config.max_lan_nodes:
                    logger.warning(f"Max LAN nodes ({self.config.max_lan_nodes}) reached")
                    return

                # Store/update node
                self.known_nodes[node.id] = node

                if is_new:
                    logger.info(f"Discovered new LAN node: {node.name} ({sender_ip}:{node.port})")

                    # Call discovery callback
                    if self.on_discovery:
                        try:
                            if asyncio.iscoroutinefunction(self.on_discovery):
                                await self.on_discovery(node)
                            else:
                                self.on_discovery(node)
                        except Exception as e:
                            logger.error("Error in discovery callback: %s", e, exc_info=True)
                else:
                    logger.debug(f"Updated LAN node: {node.name} ({sender_ip})")
                    # Notify listeners that an existing node re-broadcast (heartbeat)
                    if self.on_node_seen:
                        try:
                            if asyncio.iscoroutinefunction(self.on_node_seen):
                                await self.on_node_seen(node)
                            else:
                                self.on_node_seen(node)
                        except Exception as e:
                            logger.debug("Error in on_node_seen callback: %s", e, exc_info=True)

        except json.JSONDecodeError:
            if len(data) >= self._udp_recv_buffer_size:
                logger.warning(
                    "Truncated UDP payload from %s:%d (len=%d buf=%d); sender payload is too large",
                    sender_ip,
                    sender_port,
                    len(data),
                    self._udp_recv_buffer_size,
                )
            else:
                logger.info(
                    "Invalid JSON from %s:%d (len=%d), first 80 bytes: %r",
                    sender_ip,
                    sender_port,
                    len(data),
                    data[:80],
                )
        except Exception as e:
            logger.debug("Error handling UDP message from %s: %s", addr, e, exc_info=True)

    async def _broadcast_loop(self):
        """Periodic scan loop (active discovery only).

        Design:
          - Do NOT periodically announce node info from every node.
          - Only nodes with SCAN enabled actively broadcast discover requests.
          - Non-scanning nodes stay listen-only and respond when discovered.
        """
        logger.debug(f"Broadcast loop started, interval: {self.config.broadcast_interval}s")

        # Initial delay to allow startup
        await asyncio.sleep(self.config.broadcast_initial_delay)

        # Initial active scan only (if enabled)
        if self.running:
            if self.get_is_scanning():
                await self.discover_once()

        while self.running:
            try:
                await asyncio.sleep(self.config.broadcast_interval)
                if self.get_is_scanning():
                    await self.discover_once()

            except asyncio.CancelledError:
                logger.debug("LAN discovery broadcast loop cancelled")
                break
            except Exception as e:
                logger.error("Error in broadcast loop: %s", e, exc_info=True)
                await asyncio.sleep(self.config.broadcast_error_delay)

    def _send_via_bound_socket(self, msg: bytes, bind_ip: str, bcast_addr: str) -> bool:
        """Send msg to (bcast_addr, port) from a socket bound to bind_ip (or 0.0.0.0 if empty)."""
        port = self.config.broadcast_port
        bind_to = bind_ip or '0.0.0.0'
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.bind((bind_to, 0))
            sock.settimeout(2.0)
            sock.sendto(msg, (bcast_addr, port))
            sock.close()
            return True
        except OSError as e:
            logger.debug(
                "Broadcast send failed bind=%s bcast=%s errno=%s %s",
                bind_to, bcast_addr, getattr(e, 'errno', None), e,
            )
            return False

    def _send_unicast(self, msg: bytes, dest_ip: str, dest_port: int) -> bool:
        """Send unicast response; binds to the correct local LAN IP to fix Errno 65 on macOS.

        Uses a probe connect() to dest_ip to let the OS pick the outgoing interface,
        then binds a fresh socket to that local IP before sending.  This avoids the
        "No route to host" error that occurs when sending from an unbound 0.0.0.0 socket
        on macOS Wi-Fi, even when the peer is on the same subnet.
        """
        # Step 1: ask the OS which local IP it would use to reach dest_ip
        local_ip: str | None = None
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            probe.settimeout(0.2)  # LAN probe only needs ~200ms
            probe.connect((dest_ip, dest_port))
            local_ip = probe.getsockname()[0]
            probe.close()
            logger.debug("Unicast probe: local_ip=%s for dest=%s:%d", local_ip, dest_ip, dest_port)
            if local_ip and local_ip.startswith('127.'):
                logger.debug("Unicast probe returned loopback %s, clearing", local_ip)
                local_ip = None  # don't bind to loopback for LAN unicast
        except Exception as e:
            logger.debug("Unicast probe connect(%s:%d) failed: %s", dest_ip, dest_port, e)
            local_ip = None

        # Step 2: send from that local IP (or unbound as last resort)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            sock.settimeout(2.0)
            if local_ip:
                sock.bind((local_ip, 0))
                logger.debug("Unicast send: bind=%s sendto=%s:%d len=%d", local_ip, dest_ip, dest_port, len(msg))
            else:
                logger.debug("Unicast send: no bind, sendto=%s:%d len=%d", dest_ip, dest_port, len(msg))
            sock.sendto(msg, (dest_ip, dest_port))
            sock.close()
            self._record_unicast_success(dest_ip)
            return True
        except OSError as e:
            self._record_unicast_failure(dest_ip, dest_port, e)
            try:
                sock.close()
            except Exception as close_exc:
                logger.debug("Failed to close UDP socket after sendto failure: %s", close_exc, exc_info=True)
            return False

    async def _send_broadcast(self):
        """Send a single broadcast of our node info (per DEVELOPMENT.md: UDP broadcast for zero-config LAN discovery).

        Resolves broadcast targets on each send. Uses a socket bound to each interface's IP.
        On "no route to host" we degrade gracefully: keep listening, stop logging after first notice.
        """
        if not self._udp_socket or not getattr(self.config, 'broadcast_enabled', True):
            return

        self.local_node.last_seen = time.time()
        msg = self._build_node_payload()
        tuples = self._get_broadcast_tuples()

        # Skip send when we have no usable LAN interface (only 0.0.0.0 -> 255.255.255.255, which often fails)
        if not tuples or (len(tuples) == 1 and (tuples[0][0] or '') == '' and tuples[0][1] == '255.255.255.255'):
            if not self._broadcast_error_logged:
                logger.debug(
                    "LAN broadcast skipped (no LAN interface); discovery listen-only. "
                    "Set network.discovery.broadcast_enabled: false to disable send."
                )
                self._broadcast_error_logged = True
            return

        for bind_ip, bcast_addr in tuples:
            try:
                ok = self._send_via_bound_socket(msg, bind_ip, bcast_addr)
                if ok:
                    logger.debug(f"Broadcast sent to {bcast_addr} (from {bind_ip or '0.0.0.0'})")
                    self._broadcast_error_logged = False
                    self._broadcast_fail_count = 0
                    return
            except Exception as e:
                logger.debug("Broadcast send exception: %s", e)
                continue

        addrs = [b for _, b in tuples]
        self._broadcast_fail_count += 1
        if not self._broadcast_error_logged:
            logger.debug(
                "LAN broadcast send unavailable (no route to host) on %s. "
                "Discovery will listen only; backoff applies. Set network.discovery.broadcast_enabled: false to disable send.",
                addrs,
            )
            self._broadcast_error_logged = True
        else:
            logger.debug(
                "LAN broadcast still unavailable (attempt %d): %s",
                self._broadcast_fail_count, addrs,
            )

    async def broadcast_once(self):
        """Manually trigger a single broadcast (announce our presence)."""
        if not self.running:
            logger.debug("Discovery not running, cannot broadcast")
            return

        await self._send_broadcast()
        logger.debug("Manual broadcast sent")

    async def discover_once(self) -> bool:
        """Broadcast discover request; peers reply to our listener port (reply_port).
        Uses main socket so source port is 54321 — response comes back to same socket.
        """
        if not self.running:
            logger.debug("Discovery not running, cannot discover")
            return False
        if not self._udp_socket:
            logger.debug("UDP socket not ready, cannot discover")
            return False

        msg = json.dumps(
            {
                "type": "discover",
                "reply_port": self.config.broadcast_port,
                "nonce": f"{int(time.time() * 1000):x}",
            }
        ).encode()
        port = self.config.broadcast_port
        tuples = self._get_broadcast_tuples()
        if not tuples:
            logger.debug("No broadcast targets (no LAN interface), cannot discover")
            return False

        success = False
        for _, bcast_addr in tuples:
            try:
                self._udp_socket.sendto(msg, (bcast_addr, port))
                logger.info("LAN scan: discover request sent to %s:%d", bcast_addr, port)
                success = True
            except OSError as e:
                logger.debug("Discover send failed bcast=%s: %s", bcast_addr, e)
                continue

        if not success:
            logger.warning("LAN scan: discover request failed on all addresses %s", [b for _, b in tuples])
        return success

    async def _cleanup_loop(self):
        """Periodically clean up stale nodes.

        Nodes that are actively connected as outbound or inbound peers
        are never removed — even if their UDP broadcast is temporarily
        missed (e.g. during heavy task execution).
        """
        while self.running:
            try:
                await asyncio.sleep(self.config.cleanup_interval)

                # Gather IDs of nodes that are actively connected
                # so we never expire them from LAN discovery.
                connected_ids: set[str] = set()
                if self.get_connected_ids:
                    try:
                        connected_ids = self.get_connected_ids()
                    except Exception as e:
                        logger.debug("Error getting connected IDs: %s", e, exc_info=True)

                now = time.time()
                stale_nodes = [
                    node_id for node_id, node in self.known_nodes.items()
                    if (now - node.last_seen) > self.config.node_expiry_seconds
                    and node_id not in connected_ids
                ]

                for node_id in stale_nodes:
                    node = self.known_nodes.pop(node_id, None)
                    if node:
                        logger.debug(f"Removed stale LAN node: {node.name} ({node.ip})")

                if stale_nodes:
                    logger.info(f"Cleaned up {len(stale_nodes)} stale LAN nodes")

            except asyncio.CancelledError:
                logger.debug("LAN discovery cleanup loop cancelled")
                break
            except Exception as e:
                logger.error("Cleanup error: %s", e, exc_info=True)
                await asyncio.sleep(self.config.cleanup_interval)
