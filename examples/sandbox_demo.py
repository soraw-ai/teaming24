#!/usr/bin/env python3
"""
Sandbox Demo - Complete AN Sandbox Operations Example.

This example demonstrates all sandbox capabilities:
- Shell command execution
- Code interpreter (Python, Bash)
- File system operations
- Browser automation
- System metrics monitoring
- Health checks

The demo registers with the API server for frontend monitoring.

Runtime Modes:
    SANDBOX (default): Docker container isolation (recommended)
    LOCAL: Direct execution on host (for development without Docker)

Usage:
    # Start the backend server first
    uv run python -m teaming24.server.cli
    
    # In another terminal, run this demo (Docker mode by default)
    uv run python examples/sandbox_demo.py
    
    # Run in local mode (no Docker required)
    uv run python examples/sandbox_demo.py --local
    
    # Open frontend to watch
    # http://localhost:3000
    # Navigate to Sandbox tab

Requirements:
    # For Docker mode (default)
    docker pull ghcr.io/agent-infra/sandbox:latest
"""

import argparse
import asyncio
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Optional

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx
from teaming24.runtime import (
    Sandbox,
    Language,
    RuntimeMode,
)

# Backward compatibility alias
RuntimeType = RuntimeMode
from teaming24.utils.logger import setup_logging, get_logger, LogConfig

# Setup logging
setup_logging(LogConfig(level="DEBUG", format="text", console=True))
logger = get_logger(__name__)

# API config
# Use 127.0.0.1 to avoid IPv6/Docker conflicts
API_BASE = "http://127.0.0.1:8000"

# Runtime mode (default: SANDBOX for Docker isolation)
USE_LOCAL_MODE = os.getenv("SANDBOX_LOCAL", "").lower() in ("1", "true", "yes")


class SandboxDemo:
    """Sandbox demo with API integration for frontend monitoring."""
    
    def __init__(self, name: str = "Demo Sandbox", role: str = "demo"):
        self.name = name
        self.role = role
        self.sandbox_id: Optional[str] = None
        self.sandbox: Optional[Sandbox] = None
        self.client = httpx.AsyncClient(timeout=30.0)
    
    async def register(self) -> bool:
        """Register sandbox with API server."""
        # Use frontend-provided ID if available, otherwise generate one
        self.sandbox_id = os.getenv("TEAMING24_DEMO_ID") or f"demo-{uuid.uuid4().hex[:8]}"
        runtime_name = getattr(self, 'runtime_name', 'docker')
        
        # Get container info for cleanup
        container_name = None
        container_id = None
        workspace = None
        if self.sandbox:
            container_name = getattr(self.sandbox, 'container_name', None)
            container_id = getattr(self.sandbox, 'container_id', None)
            workspace = str(self.sandbox.config.workspace) if self.sandbox.config.workspace else None
        
        try:
            resp = await self.client.post(
                f"{API_BASE}/api/sandbox/register",
                json={
                    "id": self.sandbox_id,
                    "name": self.name,
                    "runtime": runtime_name,
                    "role": self.role,
                    # Container info for cleanup
                    "container_name": container_name,
                    "container_id": container_id,
                    "workspace": workspace,
                    # Demo task info
                    "task_id": "demo-task-001",
                    "task_name": "Sandbox Feature Demo",
                }
            )
            resp.raise_for_status()
            logger.info("Registered with API", extra={
                "id": self.sandbox_id, 
                "runtime": runtime_name,
                "container": container_name,
            })
            return True
        except Exception as e:
            logger.warning(f"API registration failed (run server first): {e}")
            return False
    
    async def push_event(self, event_type: str, data: dict):
        """Push event to API for frontend display."""
        if not self.sandbox_id:
            return
        try:
            # Send event
            await self.client.post(
                f"{API_BASE}/api/sandbox/{self.sandbox_id}/event",
                json={
                    "type": event_type,
                    "timestamp": time.time(),
                    "data": data,
                }
            )
            # Also send heartbeat to keep sandbox alive
            await self.client.post(f"{API_BASE}/api/sandbox/{self.sandbox_id}/heartbeat")
        except Exception:
            pass  # Ignore API errors during demo
    
    async def start(self, use_local: bool = False):
        """Start sandbox and register with API.
        
        Args:
            use_local: If True, use LOCAL mode (no Docker).
                       If False (default), use SANDBOX mode (Docker isolation).
        """
        if use_local:
            # Local mode - direct execution without Docker
            runtime_name = "local"
            logger.info("Starting sandbox in LOCAL mode (no isolation)")
            self.sandbox = Sandbox(runtime=RuntimeType.LOCAL, timeout=30.0)
            await self.sandbox.start(skip_health_check=True)
        else:
            # Docker mode - requires Docker to be running
            runtime_name = "docker"
            logger.info("Starting sandbox in DOCKER mode (isolated)")
            self.sandbox = Sandbox(runtime=RuntimeType.SANDBOX, timeout=60.0)
            try:
                await self.sandbox.start()
            except Exception as e:
                print("\n" + "=" * 60)
                print("❌ ERROR: Docker is not available")
                print("=" * 60)
                print(f"\nError: {e}")
                print("\nTo fix this:")
                print("  1. Start Docker Desktop, OR")
                print("  2. Use --local flag: uv run python examples/sandbox_demo.py --local")
                print("\nNote: Local mode has NO isolation. Only use for development.")
                print("=" * 60 + "\n")
                raise RuntimeError(f"Docker not available: {e}") from e
        
        # Register with API after sandbox starts
        self.runtime_name = runtime_name
        await self.register()
        
        await self.push_event("command", {"cmd": "sandbox.start()", "status": "success"})
        return self.sandbox
    
    async def stop(self):
        """Stop sandbox and mark as completed."""
        if self.sandbox:
            await self.sandbox.stop()
        
        # Mark sandbox as completed in API (not delete, so history is preserved)
        if self.sandbox_id:
            try:
                await self.client.patch(
                    f"{API_BASE}/api/sandbox/{self.sandbox_id}/state",
                    params={"state": "completed", "completed": "true"}
                )
                logger.info("Sandbox marked as completed", extra={"id": self.sandbox_id})
            except Exception as e:
                logger.debug(f"Failed to update sandbox state: {e}")
                pass
        
        await self.client.aclose()
    
    # ========================================================================
    # Demo Functions
    # ========================================================================
    
    async def demo_shell(self):
        """Demo: Shell command execution."""
        print("\n" + "=" * 60)
        print("🖥️  SHELL COMMANDS DEMO")
        print("=" * 60)
        
        commands = [
            "echo 'Hello from AN Sandbox!'",
            "pwd",
            "ls -la",
            "whoami",
            "date",
            "python3 --version",
        ]
        
        for cmd in commands:
            print(f"\n$ {cmd}")
            await self.push_event("command", {"cmd": cmd, "status": "running"})
            
            result = await self.sandbox.execute(cmd)
            
            if result.stdout:
                output = result.stdout[:200]
                print(f"  {output}")
                await self.push_event("output", {"cmd": cmd, "stdout": output, "exit": result.exit_code})
            
            if result.exit_code != 0:
                print(f"  [exit: {result.exit_code}]")
                await self.push_event("error", {"cmd": cmd, "exit": result.exit_code})
        
        print("\n✅ Shell demo complete")
    
    async def demo_code(self):
        """Demo: Code interpreter."""
        print("\n" + "=" * 60)
        print("🐍 CODE INTERPRETER DEMO")
        print("=" * 60)
        
        # Python code
        python_code = """
import math
result = sum(range(1, 101))
print(f"Sum of 1-100: {result}")
print(f"Pi: {math.pi:.10f}")
"""
        print("\n📝 Running Python code...")
        await self.push_event("command", {"cmd": "python", "code": python_code[:50], "status": "running"})
        
        result = await self.sandbox.run_code(python_code, language=Language.PYTHON)
        if result.output:
            print(f"  Output: {result.output}")
            await self.push_event("output", {"lang": "python", "output": result.output})
        
        # Bash code
        bash_code = """
for i in 1 2 3; do
    echo "Iteration $i"
done
echo "Done!"
"""
        print("\n📝 Running Bash code...")
        await self.push_event("command", {"cmd": "bash", "code": bash_code[:50], "status": "running"})
        
        result = await self.sandbox.run_code(bash_code, language=Language.BASH)
        if result.output:
            print(f"  Output: {result.output}")
            await self.push_event("output", {"lang": "bash", "output": result.output})
        
        print("\n✅ Code demo complete")
    
    async def demo_files(self):
        """Demo: File system operations."""
        print("\n" + "=" * 60)
        print("📁 FILE SYSTEM DEMO")
        print("=" * 60)
        
        # Write file
        content = "Hello from AN Sandbox!\nThis is a test file.\n"
        await self.push_event("command", {"cmd": "write file", "path": "test.txt"})
        
        written = await self.sandbox.write_file("test.txt", content)
        print(f"\n📝 Wrote {written} bytes to test.txt")
        await self.push_event("output", {"action": "write", "path": "test.txt", "bytes": written})
        
        # Read file
        await self.push_event("command", {"cmd": "read file", "path": "test.txt"})
        
        read_content = await self.sandbox.read_file("test.txt")
        print(f"📖 Read content: {read_content[:50]}...")
        await self.push_event("output", {"action": "read", "path": "test.txt", "content": read_content[:50]})
        
        # List files
        await self.push_event("command", {"cmd": "list files", "path": "."})
        
        files = await self.sandbox.list_dir(".")
        print(f"📂 Files in workspace: {len(files)} items")
        for f in files[:5]:
            print(f"   - {f.name} ({'dir' if f.type.value == 'directory' else f'{f.size}b'})")
        await self.push_event("output", {"action": "list", "count": len(files)})
        
        print("\n✅ File system demo complete")
    
    async def demo_metrics(self):
        """Demo: System metrics."""
        print("\n" + "=" * 60)
        print("📊 METRICS DEMO")
        print("=" * 60)
        
        await self.push_event("command", {"cmd": "get_metrics"})
        
        for i in range(3):
            metrics = await self.sandbox.get_metrics()
            print(f"\n📈 Snapshot {i + 1}:")
            print(f"   CPU: {metrics.cpu_pct:.1f}%")
            print(f"   Memory: {metrics.mem_pct:.1f}% ({metrics.mem_used_mb}MB)")
            print(f"   Disk: {metrics.disk_pct:.1f}%")
            
            await self.push_event("metric", {
                "cpu_pct": metrics.cpu_pct,
                "mem_pct": metrics.mem_pct,
                "disk_pct": metrics.disk_pct,
            })
            await asyncio.sleep(1)
        
        print("\n✅ Metrics demo complete")
    
    async def demo_health(self):
        """Demo: Health checks."""
        print("\n" + "=" * 60)
        print("🏥 HEALTH CHECK DEMO")
        print("=" * 60)
        
        await self.push_event("command", {"cmd": "health_check"})
        
        health = await self.sandbox.check_health()
        print(f"\n🏥 Health Status:")
        print(f"   OK: {health.ok}")
        print(f"   State: {health.state.value}")
        print(f"   Message: {health.message}")
        print(f"   Latency: {health.latency_ms:.1f}ms")
        
        await self.push_event("output", {
            "ok": health.ok,
            "state": health.state.value,
            "message": health.message,
        })
        
        print("\n✅ Health demo complete")
    
    async def demo_process(self):
        """Demo: Background process management."""
        print("\n" + "=" * 60)
        print("🚀 PROCESS MANAGEMENT DEMO")
        print("=" * 60)
        
        print("\n🚀 Starting background process...")
        await self.push_event("command", {"cmd": "start_process ticker"})
        
        proc_info = await self.sandbox.start_process(
            command='python3 -c "import time; [print(f\'Tick {i}\', flush=True) or time.sleep(1) for i in range(10)]"',
            name="ticker",
        )
        print(f"   PID: {proc_info.pid}")
        print(f"   Status: {proc_info.status.value}")
        
        await self.push_event("output", {"pid": proc_info.pid, "status": proc_info.status.value})
        
        # Wait and check status
        print("\n⏳ Waiting for process...")
        await asyncio.sleep(2)
        
        # Stop process
        print("\n🛑 Stopping process...")
        await self.push_event("command", {"cmd": "stop_process ticker"})
        
        stopped = await self.sandbox.stop_process(name="ticker")
        print(f"   Stopped: {stopped}")
        
        await self.push_event("output", {"action": "stop", "stopped": stopped})
        
        print("\n✅ Process demo complete")
    
    async def demo_browser(self):
        """Demo: Browser automation (requires playwright)."""
        print("\n" + "=" * 60)
        print("🌐 BROWSER AUTOMATION DEMO")
        print("=" * 60)
        
        print("\n📍 Navigating to example.com...")
        await self.push_event("command", {"cmd": "goto", "url": "https://example.com"})
        
        # goto() auto-starts browser if needed
        page = await self.sandbox.goto("https://example.com", wait_until="domcontentloaded")
        print(f"   Title: {page.title}")
        print(f"   URL: {page.url}")
        
        await self.push_event("output", {"title": page.title, "url": page.url})
        
        # Take screenshot
        print("\n📸 Taking screenshot...")
        await self.push_event("command", {"cmd": "screenshot"})
        
        screenshot = await self.sandbox.screenshot()
        print(f"   Size: {len(screenshot.data)} bytes")
        
        await self.push_event("output", {"action": "screenshot", "size": len(screenshot.data)})
        
        print("\n✅ Browser demo complete")


async def main(use_local: bool = False):
    """Run all sandbox demos.
    
    Args:
        use_local: If True, use LOCAL mode (no Docker).
    """
    runtime_mode = "LOCAL (no isolation)" if use_local else "DOCKER (isolated)"
    
    print("=" * 60)
    print("🚀 AN SANDBOX COMPREHENSIVE DEMO")
    print("=" * 60)
    print("\nThis demo showcases all sandbox capabilities.")
    print("Make sure the API server is running:")
    print("  uv run python -m teaming24.server.cli")
    print("\nThen open the frontend Sandbox Monitor to watch.")
    print(f"\n📦 Runtime: {runtime_mode}")
    
    if not use_local:
        print("   Docker image: ghcr.io/agent-infra/sandbox:latest")
        print("   (Use --local flag to run without Docker)")
    
    demo = SandboxDemo(name="Comprehensive Demo", role="demo")
    
    try:
        print("\n🔧 Starting sandbox...")
        sandbox = await demo.start(use_local=use_local)
        print(f"   Workspace: {sandbox.workspace}")
        print(f"   State: {sandbox.state.value}")
        print(f"   Sandbox ID: {demo.sandbox_id}")
        print(f"   Runtime: {demo.runtime_name}")
        
        # Run all demos
        await demo.demo_shell()
        await demo.demo_code()
        await demo.demo_files()
        await demo.demo_process()
        await demo.demo_metrics()
        await demo.demo_health()
        
        # Browser demo (optional - requires playwright)
        try:
            await demo.demo_browser()
        except Exception as e:
            print(f"\n⚠️  Browser demo skipped: {e}")
            await demo.push_event("error", {"demo": "browser", "error": str(e)})
        
        print("\n" + "=" * 60)
        print("✅ ALL DEMOS COMPLETED")
        print("=" * 60)
        
        # Keep running briefly for frontend observation
        print("\n⏳ Keeping sandbox alive for 10s (press Ctrl+C to stop)...")
        for i in range(10):
            await asyncio.sleep(1)
            await demo.push_event("heartbeat", {"remaining": 10 - i})
        
    except KeyboardInterrupt:
        print("\n\n⚠️  Interrupted by user")
    finally:
        print("\n🧹 Cleaning up sandbox...")
        await demo.stop()
        print("   Sandbox stopped")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="AN Sandbox Comprehensive Demo",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Runtime Modes:
  SANDBOX (default)  Docker container isolation, recommended for production
  LOCAL              Direct execution on host, for development without Docker

Examples:
  uv run python examples/sandbox_demo.py           # Docker mode (default)
  uv run python examples/sandbox_demo.py --local   # Local mode (no Docker)
  SANDBOX_LOCAL=1 uv run python examples/sandbox_demo.py  # Via env var
        """
    )
    parser.add_argument(
        "--local", "-l",
        action="store_true",
        help="Use LOCAL mode (no Docker isolation, for development)"
    )
    args = parser.parse_args()
    
    # Check for env var override
    use_local = args.local or USE_LOCAL_MODE
    
    asyncio.run(main(use_local=use_local))
