# Runtime & Sandbox

Teaming24 provides isolated execution environments for AI agents through the RuntimeManager, which aligns with the OpenHands SDK pattern while leveraging native sandbox infrastructure.

## Overview

The runtime layer ensures all agent code execution happens in secure, isolated containers by default:

```
┌─────────────────────────────────────────────────────────────────────┐
│                      RuntimeManager (Singleton)                      │
├─────────────────────────────────────────────────────────────────────┤
│                    Configuration Layer                               │
│  - Reads from teaming24.yaml and environment variables              │
│  - Determines default runtime (openhands, sandbox, local)            │
│  - Manages OpenHands integration settings                           │
├─────────────────────────────────────────────────────────────────────┤
│                    Runtime Selection                                 │
│  ┌──────────────┐  ┌──────────────┐  ┌──────────────┐              │
│  │  OpenHands   │  │   Sandbox    │  │    Local     │              │
│  │  (Default)   │  │   Runtime    │  │  (Dev Only)  │              │
│  └──────────────┘  └──────────────┘  └──────────────┘              │
├─────────────────────────────────────────────────────────────────────┤
│                    Agent Interface                                   │
│  - get_runtime()     : Get configured runtime instance              │
│  - execute()         : Run shell commands                           │
│  - run_code()        : Execute code (Python, JS, Bash)              │
│  - run_tests()       : Execute test scripts                         │
│  - get_capabilities(): Query available runtime features             │
└─────────────────────────────────────────────────────────────────────┘
```

## RuntimeManager

The `RuntimeManager` is a centralized singleton that manages all runtime operations.

### Getting Started

```python
from teaming24.runtime import get_runtime_manager

# Get the global runtime manager
manager = get_runtime_manager()

# Execute a shell command in sandbox
result = await manager.execute("python script.py")
print(result["stdout"])

# Run Python code
result = await manager.run_code("print(1+1)", language="python")

# Run tests with extended timeout
result = await manager.run_tests("pytest tests/", timeout=300)

# Check available capabilities
caps = manager.get_capabilities()
if caps["browser"]:
    await manager.browse("https://example.com")
```

### Synchronous Usage

For use in CrewAI tools or other synchronous contexts:

```python
manager = get_runtime_manager()
result = manager.execute_sync("ls -la")
```

### Runtime Backends

| Backend | Description | Use Case |
|---------|-------------|----------|
| `openhands` | OpenHands SDK runtime (Docker) | Default, isolated execution |
| `sandbox` | Teaming24 native Docker sandbox | Fallback, production use |
| `local` | Direct host execution | Development only |

## Sandbox Architecture

The sandbox provides isolated execution with hot container support:

```
┌─────────────────────────────────────────────────────────────────┐
│                    Sandbox Class                                 │
│  (High-level API for agents)                                    │
├─────────────────────────────────────────────────────────────────┤
│  ShellManager    │  FileSystem  │  BrowserManager               │
│  ProcessManager  │  Interpreter │  MetricsCollector             │
├─────────────────────────────────────────────────────────────────┤
│           Backend (DockerBackend or APIBackend)                  │
├─────────────────────────────────────────────────────────────────┤
│            AIO Sandbox Docker Container                          │
│  ┌─────────────┐ ┌──────────────┐ ┌─────────────────────┐      │
│  │ Shell/Bash  │ │  Chromium    │ │  Python/Node.js     │      │
│  │             │ │  (Playwright)│ │  (Interpreters)     │      │
│  └─────────────┘ └──────────────┘ └─────────────────────┘      │
└─────────────────────────────────────────────────────────────────┘
```

### Hot Sandbox Pool

Sandboxes are "hot" by default - they persist as long-running containers:

- **Fast Execution**: No container startup overhead between commands
- **State Persistence**: Variables, files, and browser sessions persist
- **Resource Efficiency**: Single container serves multiple operations
- **Clean Shutdown**: Proper cleanup when sandbox is explicitly deleted

### Basic Sandbox Usage

```python
from teaming24.runtime.sandbox import Sandbox, SandboxBackend

# Docker backend (default)
async with Sandbox() as sandbox:
    result = await sandbox.execute("echo hello")
    print(result.stdout)

# API backend with VNC monitoring
async with Sandbox(backend=SandboxBackend.API) as sandbox:
    await sandbox.goto("https://google.com")
    print(f"Watch live: {sandbox.vnc_url}")
```

### Sandbox Pool for Agents

```python
from teaming24.runtime import get_pool

pool = get_pool()

# Acquire sandbox for an agent
sandbox = await pool.acquire("agent-001")
await sandbox.execute("ls -la")

# Release back to pool (sandbox stays running)
await pool.release("agent-001")
```

## Runtime Capabilities

Agents can query available capabilities before using specific features:

```python
manager = get_runtime_manager()
caps = manager.get_capabilities()

# Returns:
{
    "shell": True,           # Shell command execution
    "file_read": True,       # Read files from workspace
    "file_write": True,      # Write files to workspace
    "python": True,          # Python code execution
    "javascript": True,      # JavaScript/Node.js execution
    "browser": True,         # Browser automation (Playwright)
    "code_interpreter": True,# IPython/Jupyter support
    "vnc": False,            # VNC screen sharing
    "cdp": True,             # Chrome DevTools Protocol
    "metrics": True,         # System metrics collection
    "isolation": True,       # Container isolation
}

# Get list of available tool names
tools = manager.get_available_tools()
# Returns: ["shell_command", "shell", "file_read", ...]
```

## Agent Tools

CrewAI agents have access to sandbox tools via the RuntimeManager:

| Tool | Description |
|------|-------------|
| `shell_command` | Execute shell commands in sandbox |
| `file_read` | Read files from workspace |
| `file_write` | Write files to workspace |
| `python_interpreter` | Execute Python code via IPython |
| `browser` | Browse web pages (requires browser capability) |

### Tool Example

```python
from teaming24.agent.tools import create_openhands_tools

# Create all sandbox tools
tools = create_openhands_tools()

# Use in CrewAI agent
from crewai import Agent

agent = Agent(
    role="Developer",
    goal="Write and test code",
    tools=tools,
    verbose=True
)
```

## OpenHands Integration

Teaming24 can use OpenHands as an alternative runtime backend:

```yaml
# teaming24/config/teaming24.yaml
runtime:
  default: "openhands"  # Use OpenHands instead of native sandbox
  
  openhands:
    enabled: true
    runtime_type: "docker"
    container_image: "ghcr.io/openhands/agent-server:latest-python"
    workspace_path: "/workspace"
    timeout: 120
```

### OpenHands SDK Alignment

The RuntimeManager follows OpenHands SDK patterns:

1. **Same Interface**: Same methods work regardless of backend
2. **Event-Driven Design**: Runtime events can be captured and streamed
3. **Sandbox-First**: All untrusted code runs in containers

Reference: [OpenHands SDK Documentation](https://docs.openhands.dev/sdk)

## Configuration

### Runtime Configuration (teaming24.yaml)

```yaml
runtime:
  # Default runtime backend
  default: "openhands"        # openhands (Docker isolated), sandbox, local
  
  # Sandbox pool settings
  sandbox_pool:
    min_size: 0               # Minimum sandboxes to keep warm
    max_size: 10              # Maximum concurrent sandboxes
    idle_timeout: 300         # Remove idle sandboxes after (seconds)
  
  # OpenHands runtime (optional)
  openhands:
    enabled: true
    runtime_type: "docker"
    container_image: "ghcr.io/openhands/agent-server:latest-python"
    workspace_path: "/workspace"
    timeout: 120
    enable_auto_lint: true
    enable_jupyter: true
    headless_mode: true
```

### Environment Variables

```bash
# Override default runtime
TEAMING24_RUNTIME_DEFAULT=sandbox

# OpenHands settings
OPENHANDS_RUNTIME_TYPE=docker
OPENHANDS_CONTAINER_IMAGE=ghcr.io/openhands/agent-server:latest-python
```

## Task Output Management

When tasks complete, outputs are saved to organized directories:

```
~/.teaming24/outputs/
├── task_20260205_143025_abc123/
│   ├── README.md           # Task summary and run instructions
│   ├── snake.py            # Extracted code files
│   └── requirements.txt    # Dependencies (if any)
```

### Output Structure

Each task output includes:

- **README.md**: Task name, duration, token usage, run instructions
- **Code files**: Extracted from agent response with proper extensions
- **Run instructions**: How to execute the generated code

### Configuration

```yaml
# In frontend settings or teaming24.yaml
task_output:
  enabled: true
  output_dir: "~/.teaming24/outputs"
```

## API Reference

### RuntimeManager Methods

| Method | Description |
|--------|-------------|
| `execute(command, timeout, cwd)` | Execute shell command |
| `execute_sync(command, timeout)` | Synchronous execution |
| `run_code(code, language, timeout)` | Execute code in language |
| `run_tests(command, timeout)` | Run test scripts |
| `read_file(path)` | Read file from workspace |
| `write_file(path, content)` | Write file to workspace |
| `browse(url)` | Browse URL and get content |
| `get_capabilities()` | Get runtime capabilities |
| `get_runtime_info()` | Get runtime information |

### Events

The RuntimeManager emits events for monitoring:

```python
manager = get_runtime_manager()

def on_runtime_event(event_type, data):
    print(f"Event: {event_type}, Data: {data}")

manager.on_event(on_runtime_event)

# Events:
# - runtime_initialized
# - sandbox_acquired
# - sandbox_released
# - command_start
# - command_complete
# - tests_start
# - tests_complete
```

## Cleanup Utilities

```python
from teaming24.runtime import (
    list_teaming24_containers,
    cleanup_teaming24_containers,
    cleanup_teaming24_workspaces,
)

# List all managed containers
containers = list_teaming24_containers()

# Clean up old containers
cleanup_teaming24_containers(older_than_hours=24)

# Clean up workspace files
cleanup_teaming24_workspaces()
```
