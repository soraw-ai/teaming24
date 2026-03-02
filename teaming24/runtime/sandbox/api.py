"""Teaming24 API Backend - HTTP API Execution with VNC/CDP Support.

This module provides the APIBackend class for connecting to AIO Sandbox
containers via their HTTP API. This is the preferred backend when visual
monitoring or remote sandbox access is needed.

Why Use API Backend:
    - VNC Streaming: Real-time visual monitoring of browser/desktop
    - CDP URL: Connect Playwright directly for browser automation
    - Remote Access: Connect to sandboxes running on other machines
    - No Local Docker: Works without Docker on the client side

HTTP API Endpoints (AIO Sandbox):
    POST /v1/shell/exec    - Execute shell commands
    GET  /v1/file/read     - Read file content
    POST /v1/file/write    - Write file content
    GET  /v1/file/list     - List directory contents
    GET  /v1/browser/info  - Get browser info (CDP URL)
    GET  /v1/browser/screenshot - Capture browser screenshot
    GET  /v1/health        - Health check
    GET  /vnc/index.html   - VNC web viewer

VNC Monitoring:
    The VNC URL provides a web-based viewer for watching:
    - Browser automation in real-time
    - Desktop/GUI operations
    - Visual debugging of agent actions

CDP (Chrome DevTools Protocol):
    The CDP URL allows connecting Playwright directly:

        from playwright.async_api import async_playwright

        async with async_playwright() as p:
            browser = await p.chromium.connect_over_cdp(backend.cdp_url)
            page = await browser.new_page()
            await page.goto("https://example.com")

Usage:
    from teaming24.runtime.sandbox.api import APIBackend
    from teaming24.runtime import RuntimeConfig, SandboxBackend

    config = RuntimeConfig(
        sandbox_backend=SandboxBackend.API,
        api_url="http://localhost:8080"
    )

    async with APIBackend(config) as backend:
        result = await backend.execute("ls -la")
        print(result.stdout)

        # Get VNC URL for monitoring
        print(f"VNC: {backend.vnc_url}")

        # Get browser info
        info = await backend.get_browser_info()
        print(f"CDP: {info.cdp_url}")

See Also:
    - teaming24.runtime.sandbox.docker: Docker exec backend
    - teaming24.runtime.sandbox.client: AIO Sandbox HTTP client
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import httpx

from teaming24.runtime.base import Runtime
from teaming24.runtime.types import (
    TEAMING24_USER_AGENT,
    CommandResult,
    ConnectionError,
    ProcessStatus,
    RuntimeConfig,
    RuntimeError,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# Response Types
# ============================================================================

@dataclass
class ShellResult:
    """Shell command execution result from API."""
    output: str
    exit_code: int
    error: str = ""

    @classmethod
    def from_response(cls, data: dict) -> ShellResult:
        return cls(
            output=data.get("output", ""),
            exit_code=data.get("exit_code", 0),
            error=data.get("error", ""),
        )


@dataclass
class SandboxContext:
    """Sandbox environment context from API."""
    home_dir: str
    workspace: str
    user: str

    @classmethod
    def from_response(cls, data: dict) -> SandboxContext:
        return cls(
            home_dir=data.get("home_dir", "/home/gem"),
            workspace=data.get("workspace", "/home/gem"),
            user=data.get("user", "gem"),
        )


@dataclass
class BrowserInfo:
    """Browser information from API."""
    cdp_url: str
    vnc_url: str
    viewport: dict[str, int]

    @classmethod
    def from_response(cls, data: dict) -> BrowserInfo:
        return cls(
            cdp_url=data.get("cdp_url", ""),
            vnc_url=data.get("vnc_url", ""),
            viewport=data.get("viewport", {"width": 1280, "height": 720}),
        )


# ============================================================================
# API Backend
# ============================================================================

class APIBackend(Runtime):
    """HTTP API-based sandbox backend.

    Connects to an AIO Sandbox container via its HTTP API.
    Useful for:
    - Remote sandbox deployments
    - Accessing VNC for visual browser monitoring
    - Getting CDP URL for Playwright
    - When local Docker is not available
    """

    def __init__(self, config: RuntimeConfig):
        """Initialize API backend.

        Args:
            config: RuntimeConfig with api_url setting
        """
        super().__init__(config)
        self._client: httpx.AsyncClient | None = None
        self._context: SandboxContext | None = None

    async def start(self) -> None:
        """Initialize HTTP client and verify connection."""
        self._client = httpx.AsyncClient(
            base_url=self.config.api_url,
            timeout=self.config.timeout,
            headers={"User-Agent": TEAMING24_USER_AGENT},
        )

        # Test connection
        try:
            response = await self._client.get("/v1/health")
            if response.status_code != 200:
                raise ConnectionError(f"Health check failed: {response.status_code}")

            # Get sandbox context
            response = await self._client.get("/v1/sandbox")
            if response.status_code == 200:
                data = response.json()
                self._context = SandboxContext.from_response(data.get("data", {}))

            self._started = True
            logger.info("API backend connected", extra={
                "api_url": self.config.api_url,
                "home_dir": self._context.home_dir if self._context else "unknown",
            })

        except httpx.RequestError as e:
            raise ConnectionError(f"Failed to connect to {self.config.api_url}: {e}") from e

    async def stop(self) -> None:
        """Close HTTP client."""
        if self._client:
            await self._client.aclose()
            self._client = None

        self._context = None
        self._started = False
        logger.info("API backend stopped")

    async def execute(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> CommandResult:
        """Execute command via HTTP API.

        Args:
            command: Shell command to execute
            cwd: Working directory
            timeout: Command timeout in seconds
            env: Environment variables

        Returns:
            CommandResult with exit code, stdout, stderr
        """
        if not self._client:
            raise RuntimeError("API backend not connected")

        work_dir = cwd or (self._context.home_dir if self._context else None)

        payload = {"command": command}
        if work_dir:
            payload["cwd"] = work_dir
        if timeout:
            payload["timeout"] = timeout
        if env:
            payload["env"] = env

        start = datetime.now()

        try:
            response = await self._client.post("/v1/shell/exec", json=payload)
            duration = (datetime.now() - start).total_seconds() * 1000

            if response.status_code != 200:
                return CommandResult(
                    exit_code=-1,
                    stdout="",
                    stderr=f"API error: {response.status_code}",
                    status=ProcessStatus.FAILED,
                    duration_ms=duration,
                    command=command,
                    cwd=work_dir or "",
                )

            data = response.json()
            result = ShellResult.from_response(data.get("data", {}))

            return CommandResult(
                exit_code=result.exit_code,
                stdout=result.output,
                stderr=result.error,
                status=ProcessStatus.COMPLETED if result.exit_code == 0 else ProcessStatus.FAILED,
                duration_ms=duration,
                command=command,
                cwd=work_dir or "",
            )

        except httpx.RequestError as e:
            logger.warning("API backend request failed for command=%r: %s", command, e, exc_info=True)
            duration = (datetime.now() - start).total_seconds() * 1000
            return CommandResult(
                exit_code=-1,
                stdout="",
                stderr=str(e),
                status=ProcessStatus.FAILED,
                duration_ms=duration,
                command=command,
                cwd=work_dir or "",
            )

    @property
    def workspace(self) -> str:
        """Get workspace path."""
        if self._context:
            return self._context.workspace
        return str(self.config.workspace)

    @property
    def vnc_url(self) -> str:
        """Get VNC viewer URL for visual monitoring."""
        return f"{self.config.api_url}/vnc/index.html?autoconnect=true"

    @property
    def cdp_url(self) -> str | None:
        """Get CDP URL for Playwright connection."""
        if not self._client or not self._started:
            return None

        try:
            # This needs to be sync for property access
            # In real usage, call get_browser_info() async method
            return None
        except Exception as exc:
            logger.debug("Failed to compute cdp_url property: %s", exc, exc_info=True)
            return None

    async def get_browser_info(self) -> BrowserInfo | None:
        """Get browser information including CDP URL.

        Returns:
            BrowserInfo with cdp_url and vnc_url
        """
        if not self._client:
            return None

        try:
            response = await self._client.get("/v1/browser/info")
            if response.status_code == 200:
                data = response.json()
                return BrowserInfo.from_response(data.get("data", {}))
        except Exception as e:
            logger.debug(f"Failed to get browser info: {e}")

        return None

    async def screenshot(self, full_page: bool = False) -> bytes | None:
        """Take browser screenshot.

        Args:
            full_page: Capture full page

        Returns:
            PNG image bytes
        """
        if not self._client:
            return None

        try:
            import base64

            response = await self._client.get(
                "/v1/browser/screenshot",
                params={"full_page": full_page}
            )
            if response.status_code == 200:
                data = response.json()
                image_data = data.get("data", {}).get("data", "")
                return base64.b64decode(image_data)
        except Exception as e:
            logger.debug(f"Failed to take screenshot: {e}")

        return None


__all__ = ["APIBackend", "ShellResult", "SandboxContext", "BrowserInfo"]
