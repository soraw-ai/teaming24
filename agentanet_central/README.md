# AgentaNet Central Service

Central authentication and marketplace service for AgentaNet network.

## Features

- **User Management**: GitHub OAuth authentication (mock for development)
- **Token Management**: Generate API tokens per user (configurable limit)
- **Marketplace**: Register and discover agentic nodes
- **Admin Dashboard**: System monitoring for administrators
- **Health Monitoring**: Automatic offline detection and cleanup

## Quick Start

### Prerequisites

- Python 3.11+
- [uv](https://github.com/astral-sh/uv) - Fast Python package manager
- Node.js 18+ (for frontend)

### Backend

```bash
# Install dependencies with uv
cd agentanet_central
uv sync

# Run the backend server
uv run python backend/run.py --reload

# Or use the entry point (after uv sync)
uv run agentanet-central --reload
```

API available at: http://localhost:8080
Docs at: http://localhost:8080/docs

### Frontend

```bash
cd frontend
npm install
npm run dev
```

UI available at: http://localhost:5173
Default frontend listen host is `0.0.0.0` (configured in `agentanet_central/config.yaml`).

## Configuration

All configuration is in `config.yaml`. Key settings:

```yaml
server:
  host: "0.0.0.0"
  port: 8080

frontend:
  host: "0.0.0.0"
  port: 5173
  backend_url: "http://127.0.0.1:8080"

security:
  secret_key: null  # Set via AGENTANET_SECRET_KEY env var in production
  session_expire_hours: 24
  token:
    max_per_user: 5

health_check:
  interval: 60
  offline_threshold: 300    # 5 minutes
  delist_threshold: 3600    # 1 hour
```

Teaming24 default Central endpoint is `http://100.64.1.3:8080`.
Only nodes that call `/api/marketplace/register` with a valid token are written into Central DB and discoverable by other nodes.

### Environment Variables

```bash
# Required in production
export AGENTANET_SECRET_KEY="your-secure-secret-key"

# Optional overrides
export AGENTANET_PORT=8080
export AGENTANET_HOST=0.0.0.0
export AGENTANET_DB_PATH=/var/data/agentanet.db
export AGENTANET_LOG_LEVEL=INFO

# Frontend dev server overrides (optional)
export AGENTANET_FRONTEND_HOST=0.0.0.0
export AGENTANET_FRONTEND_PORT=5173
export AGENTANET_FRONTEND_BACKEND_URL=http://127.0.0.1:8080
```

## Mock Users

For development, use these mock GitHub users:

| Username | Role | Description |
|----------|------|-------------|
| `admin` | Admin | Full dashboard access |
| `demo` | User | Regular user |
| `alice` | User | Regular user |
| `bob` | User | Regular user |

## Admin Dashboard

Login as `admin` to access the admin dashboard with:

- **Overview**: System statistics (users, tokens, nodes)
- **Users**: List and manage all users
- **Tokens**: View all API tokens across users
- **Nodes**: Monitor and manage marketplace nodes

## API Overview

### Authentication

```bash
# Login (mock)
POST /auth/login
{"username": "demo"}

# Logout
POST /auth/logout

# Get current user
GET /api/user/me
```

### Token Management

```bash
# List tokens
GET /api/tokens

# Create token (limit enforced by config.security.token.max_per_user)
POST /api/tokens
{"node_id": "my-unique-node", "description": "My agent"}

# Refresh token
POST /api/tokens/{id}/refresh

# Revoke token
DELETE /api/tokens/{id}
```

### Marketplace (requires API token)

```bash
# Register/update node
POST /api/marketplace/register
Authorization: Bearer agn_xxx...
{"name": "My Node", "capability": "Data Analysis", ...}

# Heartbeat (keep-alive)
POST /api/marketplace/heartbeat
Authorization: Bearer agn_xxx...

# Unlist node
POST /api/marketplace/unlist
Authorization: Bearer agn_xxx...

# Search nodes (public)
GET /api/marketplace/nodes?search=xxx&capability=xxx

# Get node by ID
GET /api/marketplace/nodes/{node_id}

# Get current token owner's node state
GET /api/marketplace/me
```

### Admin API (admin only)

```bash
# System statistics
GET /api/admin/stats

# List all users
GET /api/admin/users

# List all tokens
GET /api/admin/tokens

# List all nodes
GET /api/admin/nodes

# Delete user
DELETE /api/admin/users/{user_id}

# Delete node
DELETE /api/admin/nodes/{node_id}
```

## Database

SQLite database stored at: `data/agentanet.db` (configurable)

Tables:
- `users` - GitHub-linked user accounts
- `tokens` - API tokens (per-user limit is configurable)
- `revoked_tokens` - Historical record of revoked tokens
- `nodes` - Registered marketplace nodes
- `sessions` - User login sessions

## Health Monitoring

Background task runs every minute (configurable):
- Nodes without heartbeat for 5+ minutes → marked offline
- Listed nodes offline for 1+ hour → automatically unlisted

## Docker Deployment

```bash
# Build and run with docker-compose
docker-compose up -d

# View logs
docker-compose logs -f
```

## Development

```bash
# Install with dev dependencies
uv sync --all-extras

# Run linter
uv run ruff check backend/

# Run type checker
uv run mypy backend/

# Run tests
uv run pytest
```
