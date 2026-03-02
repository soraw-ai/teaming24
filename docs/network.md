# Network Guide

Teaming24 supports distributed agent collaboration via the AgentaNet protocol.
Nodes can discover each other via LAN broadcast or be connected manually by IP.
Once connected, all nodes use the same protocol regardless of how they were found.

## Connection Protocol (unified)

All node-to-node connections use the same HTTP handshake (`POST /api/network/connect`),
whether the target was discovered via LAN or entered manually as a remote address.

- **Connect**: Send handshake to target IP:port. Both sides exchange node identity.
- **Bidirectional**: Both sides must be able to reach each other's API.
  When both sides have connected, the topology shows a mutual (⇄) link
  with two parallel arrows. If one side is unreachable, only a one-way
  link exists (inbound only) and attempting a reverse connect will be rejected.
- **LAN priority**: If a node is discovered on LAN *and* connected via WAN,
  the LAN-discovered info takes display priority (shorter latency, same subnet).

### Usage

1. **LAN**: Enable "Online" in dashboard → discovered nodes appear → click "Connect".
2. **WAN**: Click "Add Remote Node" → enter IP:port → (optional) password → Connect.
3. Both methods result in the same type of connection.

## LAN Discovery (UDP broadcast)

The LAN discovery service allows nodes on the same local network to find each other automatically.

- **Protocol:** UDP Broadcast
- **Port:** 54321 (configurable in `teaming24.yaml`)
- **Mechanism:** Nodes broadcast their presence every 5 seconds.
- **Payload:** JSON containing Node ID, Name, IP, Port, and Status.

> LAN discovery only handles **node visibility**. The actual connection is always
> established via the unified `POST /api/network/connect` handshake.

### Configuration

```yaml
# teaming24/config/teaming24.yaml
network:
  discovery:
    enabled: true
    broadcast_port: 54321
    broadcast_interval: 5     # seconds
    node_expiry_seconds: 30
```

## AgentaNet Central Service

The AgentaNet Central Service provides centralized authentication and marketplace functionality.

### Features

- **User Management:** GitHub OAuth authentication (mock for development)
- **Token Management:** Generate API tokens with unique node IDs (per-user limit is configurable)
- **Marketplace:** Register nodes, search by capability, auto-discovery
- **Admin Dashboard:** System monitoring for administrators
- **Health Monitoring:** Automatic offline detection and cleanup

### Configuration

```yaml
# teaming24/config/teaming24.yaml
network:
  agentanet_central:
    url: "http://100.64.1.3:8080"       # Central service URL (default)
    token: "agn_xxxxx"                   # Your API token
    heartbeat_interval: 60               # Heartbeat interval (seconds)
    enabled: true
```

### Registration Flow

1. Register/login at AgentaNet Central Service (GitHub OAuth or mock login)
2. Generate an API token with a unique node ID in Central
3. In Teaming24 Settings -> Network, bind Central URL + Token
4. Click `Join Agentic Node Marketplace` in Teaming24
5. Central writes your node into its DB; only then other nodes can discover it
6. After binding, you can control online/listed state from Teaming24 UI (join/leave/update)

### Marketplace Operations

```bash
# List all nodes (public)
GET /api/marketplace/nodes

# Search nodes
GET /api/marketplace/nodes?search=python&capability=coding

# Register your node (requires token)
POST /api/marketplace/register
Authorization: Bearer agn_xxxxx

# Send heartbeat
POST /api/marketplace/heartbeat
Authorization: Bearer agn_xxxxx

# Unlist node
POST /api/marketplace/unlist
Authorization: Bearer agn_xxxxx
```

## Security

- **Authentication:** Connections can be secured with a password set in `teaming24.yaml`.
- **Token Auth:** Marketplace operations require an API token from AgentaNet Central.
- **AN → Central Auth:** Teaming24 includes `Authorization: Bearer <token>` on each
  marketplace request (`register`, `heartbeat`, `search`, `get node`, `me`, `unlist`).
  If the header is present but token is invalid/expired/suspended, Central rejects the request.
- **Encryption:** All API traffic should be tunneled over HTTPS in production.
- **Isolation:** Remote nodes are sandboxed and have limited access to local resources.

## Troubleshooting

### Nodes not appearing in LAN discovery
- Ensure both nodes are on the same subnet.
- Check firewall settings to allow UDP traffic on port 54321.
- Verify that both nodes are "Online" (toggle in dashboard).
- Verify that "LAN Visible" is toggled on for both nodes.

### Connection failed
- Ensure the remote node is reachable (ping the IP).
- Check if the remote node port (default 8000) is open / not firewalled.
- Verify the password if authentication is enabled.

### "Already connected to you (inbound) but unreachable"
- This means the remote node connected **to you**, but you cannot reach
  **their** API. Bidirectional requires mutual reachability.
- Common causes: remote node is behind NAT, firewall blocks inbound on
  their side, or they bind to `127.0.0.1` instead of `0.0.0.0`.
- The existing inbound link (them → you) still works. You just cannot
  establish the reverse direction from this side.

### Marketplace Issues
- Verify your AgentaNet Central token is configured correctly.
- Check that the Central Service URL is reachable (default `http://100.64.1.3:8080`).
- Ensure your node is sending heartbeats (check logs).
- If listed but showing offline, the node may have missed heartbeat threshold (5 min).
