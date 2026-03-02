#!/usr/bin/env python3
"""Browser Automation Demo - Web scraping and interaction via Sandbox.

This example demonstrates browser automation capabilities using the 
container's built-in browser (visible in VNC):
- Navigate to websites
- Take screenshots (viewport, full page)
- Extract page content and elements
- Fill forms and interact with elements
- Execute JavaScript
- Real-time monitoring via GUI and VNC

Key Feature: All browser actions are performed inside the Docker container,
so you can watch the browser in real-time via VNC!

Requirements:
    # Pull Docker image (for sandbox mode)
    docker pull ghcr.io/agent-infra/sandbox:latest
    
    # Install Playwright (for CDP connection)
    uv run playwright install chromium

Usage:
    # Start API server (in terminal 1)
    uv run python -m teaming24.server.cli
    
    # Run demo (in terminal 2) - watch VNC for browser actions!
    uv run python examples/browser_automation_demo.py
    
    # Keep container running after demo (hot mode)
    uv run python examples/browser_automation_demo.py --hot
    
    # Use local mode (no Docker, no VNC)
    uv run python examples/browser_automation_demo.py --local

Monitor:
    Open http://localhost:8000 and go to "Sandbox" tab to watch:
    - VNC: Full desktop with browser visible
    - Events: Real-time action log
    - Metrics: CPU/Memory usage
"""

import argparse
import asyncio
import base64
import os
import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

import httpx

from teaming24.runtime import Sandbox, RuntimeMode

# Backward compatibility alias
RuntimeType = RuntimeMode
from teaming24.utils.logger import LogConfig, setup_logging, get_logger

# Setup logging
setup_logging(LogConfig(level="INFO", format="text", console=True))
logger = get_logger(__name__)

# API Server URL (use 127.0.0.1 to avoid IPv6/Docker conflicts)
API_BASE = "http://127.0.0.1:8000"

# Check for local mode
USE_LOCAL_MODE = os.getenv("SANDBOX_LOCAL", "").lower() in ("1", "true", "yes")


class BrowserDemo:
    """Browser automation demo with API integration for real-time monitoring.
    
    Uses the container's built-in browser via CDP, so all actions are
    visible in VNC!
    """
    
    def __init__(self, name: str = "Browser Demo"):
        self.name = name
        self.sandbox = None
        self.sandbox_id = None
        self.client = httpx.AsyncClient(timeout=30.0)
        self.runtime_name = "docker"
        
        # CDP browser (connects to container's browser)
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._cdp_url = None
        
    async def register(self) -> bool:
        """Register sandbox with API for monitoring (including VNC URL)."""
        try:
            # Use frontend-provided ID if available
            demo_id = os.getenv("TEAMING24_DEMO_ID")
            
            # Get VNC and API URLs from sandbox
            vnc_url = self.sandbox.vnc_url if self.sandbox else None
            api_url = self.sandbox.api_url if self.sandbox else None
            
            # Get container info for cleanup
            container_name = getattr(self.sandbox, 'container_name', None) if self.sandbox else None
            container_id = getattr(self.sandbox, 'container_id', None) if self.sandbox else None
            
            resp = await self.client.post(
                f"{API_BASE}/api/sandbox/register",
                json={
                    "id": demo_id,  # Use frontend-provided ID if available
                    "name": self.name,
                    "role": "browser-automation",
                    "runtime": self.runtime_name,
                    "workspace": str(self.sandbox.workspace) if self.sandbox else "",
                    # Container info for cleanup
                    "container_name": container_name,
                    "container_id": container_id,
                    # Task info
                    "task_id": "browser-demo-001",
                    "task_name": "Web Automation Demo",
                    # VNC/CDP URLs for live view in GUI
                    "vnc_url": vnc_url,
                    "api_url": api_url,
                }
            )
            if resp.status_code == 200:
                data = resp.json()
                self.sandbox_id = data.get("id")
                logger.info(f"Registered with API: {self.sandbox_id}")
                if vnc_url:
                    logger.info(f"VNC Live View: {vnc_url}")
                return True
        except Exception as e:
            logger.warning(f"API registration failed: {e}")
        return False
    
    async def push_event(self, event_type: str, data: dict):
        """Push event to API for real-time display."""
        if not self.sandbox_id:
            return
            
        try:
            await self.client.post(
                f"{API_BASE}/api/sandbox/{self.sandbox_id}/event",
                json={"type": event_type, "data": data}
            )
            # Also send heartbeat
            await self.client.post(f"{API_BASE}/api/sandbox/{self.sandbox_id}/heartbeat")
        except Exception:
            pass
    
    async def push_screenshot(self, screenshot_data: bytes, width: int = 0, height: int = 0):
        """Push browser screenshot to API for real-time display in frontend."""
        if not self.sandbox_id:
            return
            
        try:
            # Encode screenshot as base64
            b64_data = base64.b64encode(screenshot_data).decode("utf-8")
            
            await self.client.post(
                f"{API_BASE}/api/sandbox/{self.sandbox_id}/screenshot",
                json={
                    "data": b64_data,
                    "width": width,
                    "height": height,
                }
            )
        except Exception as e:
            logger.debug(f"Failed to push screenshot: {e}")
    
    async def start(self, use_local: bool = False):
        """Initialize sandbox with browser support.
        
        For Docker mode, connects to container's browser via CDP so
        all actions are visible in VNC.
        """
        print("\n" + "=" * 60)
        print("🌐 BROWSER AUTOMATION DEMO")
        print("=" * 60)
        
        # Determine runtime mode
        if use_local:
            # Local mode - direct execution without Docker
            self.sandbox = Sandbox(runtime=RuntimeType.LOCAL)
            await self.sandbox.start()
            self.runtime_name = "local"
            print("✅ Sandbox started (Local mode)")
            print("   Note: VNC not available in local mode")
        else:
            # Docker mode - requires Docker to be running
            try:
                self.sandbox = Sandbox(runtime=RuntimeType.SANDBOX)
                await self.sandbox.start()
                self.runtime_name = "docker"
                print("✅ Sandbox started (Docker mode)")
                
                # Get CDP URL to connect to container's browser
                await self._connect_to_container_browser()
                
            except Exception as e:
                print("\n" + "=" * 60)
                print("❌ ERROR: Docker is not available")
                print("=" * 60)
                print(f"\nError: {e}")
                print("\nTo fix this:")
                print("  1. Start Docker Desktop, OR")
                print("  2. Use --local flag: uv run python examples/browser_automation_demo.py --local")
                print("\nNote: Local mode has NO isolation. Only use for development.")
                print("=" * 60 + "\n")
                raise RuntimeError(f"Docker not available: {e}") from e
        
        # Register with API
        await self.register()
        await self.push_event("sandbox_started", {"runtime": self.runtime_name})
        
        return self.sandbox
    
    async def _connect_to_container_browser(self):
        """Connect to the container's browser via CDP.
        
        This makes browser actions visible in VNC!
        """
        if not self.sandbox or not self.sandbox.api_url:
            print("   ⚠ Cannot connect to container browser (no API URL)")
            return
        
        api_url = self.sandbox.api_url
        print(f"   Container API: {api_url}")
        
        # Get CDP URL from container's browser info endpoint
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(f"{api_url}/v1/browser/info")
                if resp.status_code == 200:
                    data = resp.json()
                    self._cdp_url = data.get("data", {}).get("cdp_url")
                    if self._cdp_url:
                        print(f"   CDP URL: {self._cdp_url}")
                    else:
                        print("   ⚠ No CDP URL in response, trying fallback...")
                        # Try fallback CDP endpoint
                        resp2 = await client.get(f"{api_url}/json/version")
                        if resp2.status_code == 200:
                            data2 = resp2.json()
                            self._cdp_url = data2.get("webSocketDebuggerUrl")
                            if self._cdp_url:
                                print(f"   CDP URL (fallback): {self._cdp_url}")
                else:
                    print(f"   ⚠ Browser info failed: {resp.status_code}")
        except Exception as e:
            print(f"   ⚠ Failed to get CDP URL: {e}")
            return
        
        if not self._cdp_url:
            print("   ⚠ Could not get CDP URL, using local browser")
            return
        
        # Connect Playwright to container's browser
        try:
            from playwright.async_api import async_playwright
            
            self._playwright = await async_playwright().start()
            self._browser = await self._playwright.chromium.connect_over_cdp(self._cdp_url)
            
            # Create context and page
            contexts = self._browser.contexts
            if contexts:
                self._context = contexts[0]
                pages = self._context.pages
                if pages:
                    self._page = pages[0]
                else:
                    self._page = await self._context.new_page()
            else:
                self._context = await self._browser.new_context()
                self._page = await self._context.new_page()
            
            print("✅ Connected to container's browser (visible in VNC!)")
            print(f"   VNC URL: {self.sandbox.vnc_url}")
            
        except Exception as e:
            print(f"   ⚠ CDP connection failed: {e}")
            print("   Falling back to local browser")
            self._cdp_url = None
    
    async def stop(self, remove_container: bool = True):
        """Clean up sandbox and mark as completed.
        
        Args:
            remove_container: If True, fully cleanup including container removal.
                              If False (hot mode), keep container running.
        """
        # Close CDP browser connection (but not the browser itself if hot mode)
        if self._browser:
            try:
                # Just disconnect, don't close browser in container
                await self._browser.close()
            except Exception:
                pass
            self._browser = None
            self._context = None
            self._page = None
        
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception:
                pass
            self._playwright = None
        
        if self.sandbox:
            await self.sandbox.stop(remove_container=remove_container)
            
        # Mark task as completed (container may still be running in hot mode)
        if self.sandbox_id:
            try:
                # Always mark as completed when demo finishes
                # Hot mode: container stays running but task is done
                resp = await self.client.patch(
                    f"{API_BASE}/api/sandbox/{self.sandbox_id}/state",
                    params={"state": "completed", "completed": "true"}
                )
                if resp.status_code == 200:
                    logger.info(f"Sandbox marked as completed: {self.sandbox_id}")
                else:
                    logger.warning(f"Failed to mark sandbox as completed: {resp.status_code}")
            except Exception as e:
                logger.warning(f"Error marking sandbox as completed: {e}")
                
        await self.client.aclose()
        if remove_container:
            print("\n✅ Sandbox stopped and removed")
        else:
            print("\n✅ Sandbox disconnected (container still running)")
    
    # ========================================================================
    # Browser Helpers (use CDP when available)
    # ========================================================================
    
    @property
    def has_cdp_browser(self) -> bool:
        """Check if CDP browser is connected."""
        return self._page is not None
    
    async def goto(self, url: str) -> dict:
        """Navigate to URL using container's browser (if CDP connected).
        
        Returns dict with title and url.
        """
        if self.has_cdp_browser:
            await self._page.goto(url, wait_until="domcontentloaded")
            return {"title": await self._page.title(), "url": self._page.url}
        else:
            # Fallback to sandbox's local browser
            return await self.sandbox.goto(url)
    
    async def screenshot(self) -> bytes:
        """Take screenshot using container's browser."""
        if self.has_cdp_browser:
            return await self._page.screenshot(type="png")
        else:
            result = await self.sandbox.screenshot()
            return result.data
    
    async def fill(self, selector: str, text: str):
        """Fill input field."""
        if self.has_cdp_browser:
            await self._page.fill(selector, text)
        else:
            await self.sandbox._browser.fill(selector, text)
    
    async def click(self, selector: str):
        """Click element."""
        if self.has_cdp_browser:
            await self._page.click(selector)
        else:
            await self.sandbox._browser.click(selector)
    
    async def type_text(self, selector: str, text: str):
        """Type text into element."""
        if self.has_cdp_browser:
            await self._page.locator(selector).type(text)
        else:
            await self.sandbox._browser.fill(selector, text)
    
    async def get_content(self) -> str:
        """Get page HTML content."""
        if self.has_cdp_browser:
            return await self._page.content()
        else:
            return await self.sandbox.get_page_content()
    
    async def evaluate(self, expression: str):
        """Execute JavaScript and return result."""
        if self.has_cdp_browser:
            return await self._page.evaluate(expression)
        else:
            return await self.sandbox._browser.evaluate(expression)
    
    async def page_title(self) -> str:
        """Get page title."""
        if self.has_cdp_browser:
            return await self._page.title()
        else:
            return (await self.sandbox.goto(self._page.url if self._page else "")).title
    
    # ========================================================================
    # Demo Scenarios
    # ========================================================================
    
    async def demo_basic_navigation(self):
        """Demo 1: Basic page navigation and screenshots."""
        print("\n" + "-" * 60)
        print("📍 Demo 1: Basic Navigation")
        print("-" * 60)
        if self.has_cdp_browser:
            print("   [Using container browser - visible in VNC!]")
        
        await self.push_event("demo_started", {"name": "Basic Navigation"})
        
        # Navigate to example.com
        print("   Navigating to example.com...")
        await self.push_event("browser_action", {"action": "goto", "url": "https://example.com"})
        
        page = await self.goto("https://example.com")
        title = page["title"] if isinstance(page, dict) else page.title
        url = page["url"] if isinstance(page, dict) else page.url
        print(f"   ✓ Loaded: {title}")
        await self.push_event("page_loaded", {"title": title, "url": url})
        
        # Take screenshot
        print("   Taking screenshot...")
        screenshot_data = await self.screenshot()
        print(f"   ✓ Screenshot: {len(screenshot_data)} bytes")
        
        # Push to API for real-time display
        await self.push_screenshot(screenshot_data, 1280, 720)
        await self.push_event("screenshot_taken", {"size": len(screenshot_data)})
        
        # Save screenshot locally
        await self.sandbox.write_bytes("example_screenshot.png", screenshot_data)
        print(f"   ✓ Saved to: example_screenshot.png")
        
        await self.push_event("demo_completed", {"name": "Basic Navigation"})
        print("   ✓ Demo 1 complete")
    
    async def demo_content_extraction(self):
        """Demo 2: Extract content from web pages."""
        print("\n" + "-" * 60)
        print("📝 Demo 2: Content Extraction")
        print("-" * 60)
        if self.has_cdp_browser:
            print("   [Using container browser - visible in VNC!]")
        
        await self.push_event("demo_started", {"name": "Content Extraction"})
        
        # Navigate to a content-rich page
        print("   Navigating to httpbin.org...")
        await self.push_event("browser_action", {"action": "goto", "url": "https://httpbin.org"})
        
        page = await self.goto("https://httpbin.org")
        title = page["title"] if isinstance(page, dict) else page.title
        print(f"   ✓ Loaded: {title}")
        
        # Get page content
        print("   Extracting page content...")
        content = await self.get_content()
        print(f"   ✓ HTML content: {len(content)} chars")
        await self.push_event("content_extracted", {"type": "html", "size": len(content)})
        
        # Execute JavaScript to get info
        print("   Running JavaScript...")
        user_agent = await self.evaluate("navigator.userAgent")
        print(f"   ✓ User Agent: {user_agent[:50]}...")
        await self.push_event("js_executed", {"expression": "navigator.userAgent"})
        
        # Get viewport size
        viewport = await self.evaluate("({width: window.innerWidth, height: window.innerHeight})")
        print(f"   ✓ Viewport: {viewport}")
        await self.push_event("js_executed", {"expression": "viewport size", "result": viewport})
        
        await self.push_event("demo_completed", {"name": "Content Extraction"})
        print("   ✓ Demo 2 complete")
    
    async def demo_search_interaction(self):
        """Demo 3: Interact with a search page."""
        print("\n" + "-" * 60)
        print("🔍 Demo 3: Search Interaction")
        print("-" * 60)
        if self.has_cdp_browser:
            print("   [Using container browser - visible in VNC!]")
        
        await self.push_event("demo_started", {"name": "Search Interaction"})
        
        # Navigate to DuckDuckGo
        print("   Navigating to DuckDuckGo...")
        await self.push_event("browser_action", {"action": "goto", "url": "https://duckduckgo.com"})
        
        page = await self.goto("https://duckduckgo.com")
        title = page["title"] if isinstance(page, dict) else page.title
        print(f"   ✓ Loaded: {title}")
        
        # Take before screenshot
        print("   Taking 'before' screenshot...")
        screenshot_data = await self.screenshot()
        await self.push_screenshot(screenshot_data, 1280, 720)
        await self.push_event("screenshot_taken", {"stage": "before_search"})
        
        # Type search query
        search_query = "Teaming24 AI agents"
        print(f"   Typing search: '{search_query}'...")
        await self.push_event("browser_action", {
            "action": "type",
            "selector": "input[name=q]",
            "text": search_query
        })
        
        try:
            # Try to find the search input and type
            await self.fill("input[name=q]", search_query)
            print("   ✓ Text entered")
            
            # Take after screenshot
            print("   Taking 'after' screenshot...")
            screenshot_data = await self.screenshot()
            await self.push_screenshot(screenshot_data, 1280, 720)
            await self.sandbox.write_bytes("search_typed.png", screenshot_data)
            await self.push_event("screenshot_taken", {"stage": "after_typing"})
            print("   ✓ Screenshot saved: search_typed.png")
            
        except Exception as e:
            print(f"   ⚠ Search input interaction: {e}")
            await self.push_event("error", {"message": str(e), "recoverable": True})
        
        await self.push_event("demo_completed", {"name": "Search Interaction"})
        print("   ✓ Demo 3 complete")
    
    async def demo_form_filling(self):
        """Demo 4: Fill out a form on httpbin."""
        print("\n" + "-" * 60)
        print("📋 Demo 4: Form Interaction")
        print("-" * 60)
        if self.has_cdp_browser:
            print("   [Using container browser - visible in VNC!]")
        
        await self.push_event("demo_started", {"name": "Form Interaction"})
        
        # Navigate to httpbin forms page
        print("   Navigating to httpbin forms...")
        await self.push_event("browser_action", {"action": "goto", "url": "https://httpbin.org/forms/post"})
        
        page = await self.goto("https://httpbin.org/forms/post")
        title = page["title"] if isinstance(page, dict) else page.title
        print(f"   ✓ Loaded: {title or 'Form Page'}")
        
        # Fill form fields
        form_data = {
            "custname": "Test User",
            "custtel": "555-1234",
            "custemail": "test@example.com",
            "comments": "This is a test from Teaming24 sandbox browser automation!"
        }
        
        print("   Filling form fields...")
        for field, value in form_data.items():
            try:
                await self.fill(f"input[name={field}], textarea[name={field}]", value)
                print(f"   ✓ Filled {field}")
                await self.push_event("form_field_filled", {"field": field, "value": value[:20]})
                await asyncio.sleep(0.3)  # Brief pause to see typing in VNC
            except Exception as e:
                print(f"   ⚠ Could not fill {field}: {e}")
        
        # Take screenshot of filled form
        print("   Taking screenshot of filled form...")
        screenshot_data = await self.screenshot()
        await self.push_screenshot(screenshot_data, 1280, 720)
        await self.sandbox.write_bytes("form_filled.png", screenshot_data)
        await self.push_event("screenshot_taken", {"stage": "form_filled"})
        print("   ✓ Screenshot saved: form_filled.png")
        
        await self.push_event("demo_completed", {"name": "Form Interaction"})
        print("   ✓ Demo 4 complete")
    
    async def demo_multi_page(self):
        """Demo 5: Navigate through multiple pages."""
        print("\n" + "-" * 60)
        print("📚 Demo 5: Multi-Page Navigation")
        print("-" * 60)
        if self.has_cdp_browser:
            print("   [Using container browser - visible in VNC!]")
        
        await self.push_event("demo_started", {"name": "Multi-Page Navigation"})
        
        urls = [
            ("https://example.com", "Example Domain"),
            ("https://httpbin.org/html", "HTML Test Page"),
            ("https://httpbin.org/links/5", "Link Navigation"),
        ]
        
        for i, (url, description) in enumerate(urls):
            print(f"   Visiting: {description}...")
            await self.push_event("browser_action", {"action": "goto", "url": url})
            
            page = await self.goto(url)
            title = page["title"] if isinstance(page, dict) else page.title
            print(f"   ✓ {title or description}")
            
            # Quick screenshot
            screenshot_data = await self.screenshot()
            await self.push_screenshot(screenshot_data, 1280, 720)
            filename = f"page_{i + 1}.png"
            await self.sandbox.write_bytes(filename, screenshot_data)
            await self.push_event("page_visited", {
                "url": url,
                "title": title,
                "screenshot": filename
            })
            
            await asyncio.sleep(0.5)
        
        await self.push_event("demo_completed", {"name": "Multi-Page Navigation"})
        print("   ✓ Demo 5 complete")
    
    async def demo_javascript_execution(self):
        """Demo 6: Execute JavaScript in browser context."""
        print("\n" + "-" * 60)
        print("⚡ Demo 6: JavaScript Execution")
        print("-" * 60)
        if self.has_cdp_browser:
            print("   [Using container browser - visible in VNC!]")
        
        await self.push_event("demo_started", {"name": "JavaScript Execution"})
        
        # Navigate to a page
        print("   Navigating to example.com...")
        await self.goto("https://example.com")
        
        # Execute various JavaScript
        js_examples = [
            ("document.title", "Get page title"),
            ("document.querySelectorAll('a').length", "Count links"),
            ("window.location.href", "Get current URL"),
            ("navigator.language", "Get browser language"),
            ("new Date().toISOString()", "Get current time"),
            ("({screen: {width: screen.width, height: screen.height}})", "Get screen size"),
        ]
        
        print("   Executing JavaScript expressions...")
        for expr, description in js_examples:
            try:
                result = await self.evaluate(expr)
                print(f"   ✓ {description}: {result}")
                await self.push_event("js_executed", {
                    "description": description,
                    "expression": expr[:30],
                    "result": str(result)[:50]
                })
            except Exception as e:
                print(f"   ⚠ {description}: {e}")
        
        # Modify page content - visible in VNC!
        print("   Modifying page content via JS...")
        await self.evaluate("""
            document.body.innerHTML += '<div id="injected" style="background: #4CAF50; color: white; padding: 20px; margin: 20px; border-radius: 8px;">' +
            '<h2>🤖 Injected by Teaming24 Sandbox</h2>' +
            '<p>This content was added via JavaScript execution.</p>' +
            '</div>';
        """)
        await self.push_event("js_executed", {"description": "Content injection", "modified_dom": True})
        
        # Screenshot the modified page
        screenshot_data = await self.screenshot()
        await self.push_screenshot(screenshot_data, 1280, 720)
        await self.sandbox.write_bytes("js_modified.png", screenshot_data)
        await self.push_event("screenshot_taken", {"stage": "after_js_injection"})
        print("   ✓ Screenshot saved: js_modified.png")
        
        await self.push_event("demo_completed", {"name": "JavaScript Execution"})
        print("   ✓ Demo 6 complete")


async def main(use_local: bool = False, keep_alive: bool = False):
    """Run the browser automation demo.
    
    Args:
        use_local: Use local mode (no Docker)
        keep_alive: Keep sandbox running after demo (hot mode)
    """
    
    print("\n" + "=" * 60)
    print("🌐 TEAMING24 BROWSER AUTOMATION DEMO")
    print("=" * 60)
    print(f"Runtime Mode: {'LOCAL' if use_local else 'SANDBOX (Docker)'}")
    print(f"Hot Mode: {'Yes (container stays running)' if keep_alive else 'No (auto cleanup)'}")
    print(f"API Server: {API_BASE}")
    print("Monitor at: http://localhost:8000 -> Sandbox tab")
    print("=" * 60)
    
    if not use_local:
        print("\n📝 Note: VNC shows the Docker container's display.")
        print("   Browser automation runs via Playwright (host-side).")
        print("   To see browser in VNC, use container's CDP browser.")
        print("=" * 60)
    
    demo = BrowserDemo(name="Web Automation Demo")
    
    try:
        # Start sandbox
        sandbox = await demo.start(use_local=use_local)
        
        # Run key demos (3 demos that showcase main features)
        await demo.demo_basic_navigation()
        await asyncio.sleep(0.5)
        
        await demo.demo_form_filling()
        await asyncio.sleep(0.5)
        
        await demo.demo_javascript_execution()
        
        # Summary
        print("\n" + "=" * 60)
        print("✅ ALL DEMOS COMPLETED")
        print("=" * 60)
        
        if keep_alive:
            # Hot mode - container keeps running
            print("\n🔥 HOT MODE - Container will keep running")
            print("   VNC URL:", demo.sandbox.vnc_url or "N/A")
            print("   Delete from GUI to cleanup.")
            await demo.push_event("hot_mode", {"message": "Container staying alive"})
        else:
            # Normal mode - wait briefly then cleanup
            print("\n⏳ Keeping sandbox alive for 10s...")
            print("   Use --hot to keep container running.")
            await demo.push_event("waiting", {"message": "Demo complete"})
            
            for i in range(10, 0, -2):
                print(f"   {i}s remaining...")
                await asyncio.sleep(2)
                await demo.push_event("heartbeat", {"remaining": i})
        
    except KeyboardInterrupt:
        print("\n⚡ Interrupted by user")
    except Exception as e:
        logger.error(f"Demo error: {e}")
        await demo.push_event("error", {"message": str(e), "fatal": True})
        raise
    finally:
        # Stop sandbox - with hot mode, container keeps running
        await demo.stop(remove_container=not keep_alive)
        
        if keep_alive:
            print("\n🔥 Container stays running. Delete from GUI to cleanup.")
            if demo.sandbox:
                print(f"   VNC: {demo.sandbox.vnc_url or 'N/A'}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="Browser automation demo with real-time monitoring",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Run with Docker sandbox (default)
  uv run python examples/browser_automation_demo.py
  
  # Run with local mode (no Docker)
  uv run python examples/browser_automation_demo.py --local
  
  # Keep container running after demo (hot mode)
  uv run python examples/browser_automation_demo.py --hot
  
  # Set via environment variable
  SANDBOX_LOCAL=1 uv run python examples/browser_automation_demo.py
        """
    )
    parser.add_argument(
        "--local", "-l",
        action="store_true",
        help="Use LOCAL mode instead of Docker sandbox"
    )
    parser.add_argument(
        "--hot", "--keep-alive",
        action="store_true",
        dest="keep_alive",
        help="Keep sandbox container running after demo completes (hot mode)"
    )
    args = parser.parse_args()
    
    use_local = args.local or USE_LOCAL_MODE
    asyncio.run(main(use_local=use_local, keep_alive=args.keep_alive))
