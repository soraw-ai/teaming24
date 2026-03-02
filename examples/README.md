# Teaming24 Examples

Demo scripts showcasing Teaming24 capabilities including sandbox, browser automation, and multi-agent collaboration.

## Quick Start

```bash
# 1. Pull Docker sandbox image
docker pull ghcr.io/agent-infra/sandbox:latest

# 2. Start API server
uv run python -m teaming24.server.cli

# 3. Run demo (in another terminal)
uv run python examples/sandbox_demo.py
```

## Examples

### 1. Sandbox Demo (`sandbox_demo.py`)

Basic sandbox functionality demonstration.

```bash
uv run python examples/sandbox_demo.py

# Without Docker
uv run python examples/sandbox_demo.py --local
```

**Features:**
- Shell command execution
- Code interpreter (Python, Bash)
- File system operations
- Process management
- System metrics
- Health checks
- Browser automation

### 2. Browser Automation Demo (`browser_automation_demo.py`)

Browser automation with **VNC live view** - watch the browser in real-time!

```bash
# Install Playwright
uv run playwright install chromium

# Run demo (with VNC)
uv run python examples/browser_automation_demo.py

# Keep container running after demo
uv run python examples/browser_automation_demo.py --hot
```

**Features:**
- Connect to container's browser via CDP
- **All actions visible in VNC!**
- Page navigation
- Form filling
- Screenshot capture
- JavaScript execution
- Hot mode (container stays running)

**VNC Live View:**
1. Run the demo
2. Open http://localhost:8000 → Sandbox tab
3. Click "Fullscreen" button to watch browser actions in real-time

### 3. Multi-Agent Demo (`multi_agent_demo.py`)

Multi-agent collaboration using CrewAI integration.

```bash
# Mock mode (no API key required)
uv run python examples/multi_agent_demo.py --mock

# Real execution (requires OPENAI_API_KEY)
export OPENAI_API_KEY=your-key
uv run python examples/multi_agent_demo.py
```

**Features:**
- Local crew execution with scenario-based agents
- Remote task delegation via AgentaNet
- x402 payment protocol (mock mode)
- Task tracking with unique IDs
- Cost calculation and display
- Streaming progress updates

**Scenarios:**
- **Frontend Team**: Product Manager + React Developer
- **Backend Team**: Senior Python Dev + DB Architect

**Delegation Flow:**
1. User submits task via chat
2. Local Coordinator checks if workers can handle it
3. If not, Organizer searches AgentaNet for capable nodes
4. Task delegated with x402 payment
5. Remote node executes and returns result
6. Cost displayed in final response

## Runtime Modes

| Mode | Description | Use Case |
|------|-------------|----------|
| **SANDBOX** (default) | Docker container isolation | Production |
| **LOCAL** | Direct execution on host | Development without Docker |

```python
from teaming24.runtime import Sandbox, RuntimeMode

# Docker mode (default, recommended)
async with Sandbox() as s:
    result = await s.execute("echo hello")

# Local mode (no Docker)
async with Sandbox(runtime=RuntimeMode.LOCAL) as s:
    result = await s.execute("python script.py")
```

### 4. CrewAI + OpenHands Demo (`crewai_openhands_demo.py`)

Integration demo showing CrewAI agents using OpenHands for code execution.

```bash
# Mock mode (no dependencies)
uv run python examples/crewai_openhands_demo.py --mock

# With CrewAI
uv run python examples/crewai_openhands_demo.py --crewai-only

# With OpenHands
uv run python examples/crewai_openhands_demo.py --openhands-only

# Full integration
uv run python examples/crewai_openhands_demo.py
```

**Features:**
- CrewAI agent creation with OpenHands tools
- Sandboxed code execution
- File read/write operations
- Python interpreter access
- Multi-agent collaboration with sandbox

## Requirements

```bash
# Docker (for sandbox mode)
docker pull ghcr.io/agent-infra/sandbox:latest

# Playwright (for browser demos)
uv run playwright install chromium

# CrewAI (for multi-agent demos)
uv pip install crewai

# OpenHands (for advanced sandbox)
uv pip install openhands-ai
```
