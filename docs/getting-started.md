# Getting Started

Get Teaming24 running in under 5 minutes.

## System Requirements

### Supported Platforms

| Platform | Support Level | Notes |
|----------|---------------|-------|
| macOS (Apple Silicon M1/M2/M3) | ✅ Full | Primary development platform |
| Linux (Ubuntu 22.04+) | ✅ Full | Tested on Ubuntu 22.04/24.04 |
| macOS (Intel) | ⚠️ Limited | May have performance issues |
| Windows | ⚠️ WSL2 | Use WSL2 with Ubuntu |

### Required Dependencies

| Component | Minimum Version | Purpose |
|-----------|-----------------|---------|
| Python | 3.12+ | Backend runtime |
| Node.js | 18+ | GUI frontend |
| Docker | 24.0+ | Sandbox containers |
| Docker CLI | Included | Container management |

## Prerequisites Installation

### 1. Docker (Required for Sandbox)

Docker is required for running isolated sandbox environments.

**macOS (Apple Silicon / Intel):**
```bash
# Install Docker Desktop via Homebrew
brew install --cask docker

# Start Docker Desktop from Applications folder
# Wait for Docker to start (whale icon in menu bar)

# Verify installation
docker --version
docker ps  # Should show empty table, not error
```

**Linux (Ubuntu 22.04+):**
```bash
# Install Docker Engine
curl -fsSL https://get.docker.com -o get-docker.sh
sudo sh get-docker.sh

# Add current user to docker group (avoid sudo)
sudo usermod -aG docker $USER

# IMPORTANT: Logout and login again for group changes
# Then verify:
docker --version
docker ps  # Should work without sudo
```

**Troubleshooting Docker:**
```bash
# macOS: If Docker daemon not running
open -a Docker  # Start Docker Desktop

# Linux: Start Docker daemon
sudo systemctl start docker
sudo systemctl enable docker

# Check Docker status
docker info
```

### 2. Python (via uv - Recommended)

```bash
# Install uv (fast Python package manager)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Verify
uv --version
```

### 3. Node.js

```bash
# macOS
brew install node@18

# Linux (Ubuntu)
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt-get install -y nodejs

# Verify
node --version  # Should be 18.x or higher
npm --version
```

## Installation

### 1. Clone and install dependencies

```bash
git clone https://github.com/teaming24/teaming24.git
cd teaming24

# Python dependencies (using uv - recommended)
uv sync

# Or with pip
pip install -r requirements.txt

# Frontend dependencies
cd teaming24/gui && npm install
cd ..  # Return to project root
```

### 2. Pull AIO Sandbox Docker Image

```bash
# Pull the sandbox image (required for first run)
docker pull ghcr.io/agent-infra/sandbox:latest

# Verify
docker images | grep sandbox
```

### 3. Configure environment

```bash
cp .env.example .env
# Edit .env with your settings
```

Key settings:

```bash
TEAMING24_LOG_LEVEL=INFO                 # DEBUG, INFO, WARNING, ERROR
TEAMING24_RPC_URL=https://sepolia.base.org  # For x402 payments
TEAMING24_WALLET_ADDRESS=0x...           # Your wallet address
TEAMING24_WALLET_PRIVATE_KEY=0x...       # For signing x402 payments
TEAMING24_WALLET_NETWORK=base-sepolia    # base or base-sepolia
```

## Running

### Development (recommended)

Use the dev script to start both backend and frontend:

```bash
./scripts/start_dev.sh
```

### Manual startup

```bash
# Terminal 1 - Backend
uv run python main.py --reload

# Terminal 2 - Frontend
cd teaming24/gui && npm run dev
```

### CLI Options

```bash
python main.py --help

Options:
  --config, -c PATH    Path to config file
  --host HOST          Override server host
  --port, -p PORT      Override server port  
  --reload, -r         Enable auto-reload
  --workers, -w N      Number of workers
```

## Verify Installation

| Service | URL | Expected |
|---------|-----|----------|
| Dashboard (dev server, typical\*) | http://localhost:8088 | React app loads |
| Dashboard (built frontend) | http://localhost:8000 | React app loads |
| API | http://localhost:8000/api | `{"status": "running"}` |
| API Docs | http://localhost:8000/docs | Swagger UI |
| Health | http://localhost:8000/api/health | `{"status": "healthy"}` |

\* Vite prefers `8088`, then falls back to another available port when occupied.

## Sandbox Runtime

The sandbox runtime provides isolated Docker containers for safe code execution.

### Quick Test

```bash
# Run browser automation demo
uv run python examples/browser_automation_demo.py

# Open GUI to watch VNC live view
# If running frontend dev server:
open http://localhost:8088
# If using built frontend only:
open http://localhost:8000
```

### VNC Live View

When running sandboxes, you can monitor browser activity in real-time:

1. Start backend: `uv run python -m teaming24.server.cli`
2. Run a sandbox demo: `uv run python examples/browser_automation_demo.py`
3. Open GUI: http://localhost:8088 (dev) or http://localhost:8000 (built)
4. Navigate to **Sandbox** tab
5. Select the running sandbox to see VNC stream

### Cleanup Commands

```bash
# List all teaming24 containers
docker ps -a --filter "label=teaming24.managed=true"

# Remove all teaming24 containers
docker rm -f $(docker ps -aq --filter "label=teaming24.managed=true")

# Clean workspace directories
rm -rf ~/.teaming24/sandboxes/*
```

### Troubleshooting

**"Container API not ready after timeout":**
- This is normal on first run (image needs to initialize)
- If persistent, check Docker is running: `docker ps`

**VNC not showing in GUI:**
- Ensure sandbox registered with `vnc_url`
- Check browser console for errors
- Try refreshing the page

## Next Steps

- [Configuration](configuration.md) — Customize settings
- [Architecture](architecture.md) — Understand the system
- [x402 Payments](x402-payments.md) — Enable crypto payments
