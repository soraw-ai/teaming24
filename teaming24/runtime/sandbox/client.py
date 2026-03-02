"""Teaming24 AIO Sandbox HTTP Client - Direct API Access.

This module provides the AIOSandboxClient class for directly interacting
with AIO Sandbox containers via their HTTP API. This is useful when you
need low-level access to sandbox functionality without the Sandbox wrapper.

AIO Sandbox Project:
    GitHub: https://github.com/agent-infra/sandbox
    Image:  ghcr.io/agent-infra/sandbox:latest

    AIO Sandbox is an all-in-one container environment for AI agents,
    providing shell, browser, and file system access via HTTP API.

API Services:
    ShellService    - Execute commands: client.shell.exec("ls -la")
    FileService     - File operations: client.file.read("file.txt")
    BrowserService  - Browser control: client.browser.screenshot()
    SandboxService  - Environment info: client.sandbox.get_context()

HTTP Endpoints:
    Shell:
        POST /v1/shell/exec - Execute command, get stdout/stderr

    Files:
        GET  /v1/file/read  - Read file content
        POST /v1/file/write - Write file content
        GET  /v1/file/list  - List directory

    Browser:
        GET  /v1/browser/info       - CDP URL, VNC URL, viewport
        GET  /v1/browser/screenshot - Capture PNG screenshot
        POST /v1/browser/navigate   - Navigate to URL
        POST /v1/browser/action     - Click, type, scroll

    System:
        GET  /v1/health  - Health check
        GET  /v1/sandbox - Environment context

Usage:
    from teaming24.runtime.sandbox.client import AIOSandboxClient

    # Connect to running AIO Sandbox container
    client = AIOSandboxClient("http://localhost:8080")

    # Execute shell command
    result = client.shell.exec("ls -la")
    print(result.output)

    # Take screenshot
    screenshot = client.browser.screenshot()
    with open("screenshot.png", "wb") as f:
        f.write(screenshot.data)

    # Get CDP URL for Playwright
    info = client.browser.get_info()
    print(f"CDP URL: {info.cdp_url}")

    # VNC URL for visual monitoring
    print(f"VNC: {client.vnc_url}")

Async Support:
    All service methods have async versions:

        result = await client.shell.exec_async("ls -la")
        screenshot = await client.browser.screenshot_async()

See Also:
    - teaming24.runtime.sandbox.api: API backend using this client
    - teaming24.runtime.sandbox: High-level Sandbox class
"""

from __future__ import annotations

import base64
import builtins
from dataclasses import dataclass
from typing import Any

import httpx

from teaming24.runtime.types import TEAMING24_USER_AGENT
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# ============================================================================
# Response Types
# ============================================================================

@dataclass
class ShellResult:
    """Shell command execution result."""
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
class FileContent:
    """File content result."""
    content: str
    encoding: str = "utf-8"
    size: int = 0

    @classmethod
    def from_response(cls, data: dict) -> FileContent:
        return cls(
            content=data.get("content", ""),
            encoding=data.get("encoding", "utf-8"),
            size=data.get("size", 0),
        )


@dataclass
class APIFileInfo:
    """File metadata from API."""
    path: str
    name: str
    is_dir: bool
    size: int
    modified: float

    @classmethod
    def from_response(cls, data: dict) -> APIFileInfo:
        return cls(
            path=data.get("path", ""),
            name=data.get("name", ""),
            is_dir=data.get("is_dir", False),
            size=data.get("size", 0),
            modified=data.get("modified", 0),
        )


@dataclass
class BrowserInfo:
    """Browser information including CDP URL."""
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


@dataclass
class APIScreenshotResult:
    """Screenshot result from API."""
    data: bytes
    format: str = "png"
    width: int = 0
    height: int = 0

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("utf-8")


@dataclass
class SandboxContext:
    """Sandbox environment context."""
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


# ============================================================================
# Service Clients
# ============================================================================

class ShellService:
    """Shell command execution service."""

    def __init__(self, client: AIOSandboxClient):
        self._client = client

    def exec(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Execute a shell command."""
        payload = {"command": command}
        if cwd:
            payload["cwd"] = cwd
        if timeout:
            payload["timeout"] = timeout
        if env:
            payload["env"] = env

        response = self._client._post("/v1/shell/exec", json=payload)
        return ShellResult.from_response(response.get("data", {}))

    async def exec_async(
        self,
        command: str,
        cwd: str | None = None,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
    ) -> ShellResult:
        """Execute a shell command asynchronously."""
        payload = {"command": command}
        if cwd:
            payload["cwd"] = cwd
        if timeout:
            payload["timeout"] = timeout
        if env:
            payload["env"] = env

        response = await self._client._post_async("/v1/shell/exec", json=payload)
        return ShellResult.from_response(response.get("data", {}))


class FileService:
    """File system operations service."""

    def __init__(self, client: AIOSandboxClient):
        self._client = client

    def read(self, path: str, encoding: str = "utf-8") -> FileContent:
        """Read file content."""
        response = self._client._get(
            "/v1/file/read",
            params={"file": path, "encoding": encoding}
        )
        return FileContent.from_response(response.get("data", {}))

    def write(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> bool:
        """Write file content."""
        response = self._client._post("/v1/file/write", json={
            "file": path,
            "content": content,
            "encoding": encoding,
            "create_dirs": create_dirs,
        })
        return response.get("code") == 0

    def list(self, path: str = ".") -> builtins.list[APIFileInfo]:
        """List directory contents."""
        response = self._client._get("/v1/file/list", params={"path": path})
        data = response.get("data", {})
        files = data.get("files", [])
        return [APIFileInfo.from_response(f) for f in files]

    async def read_async(self, path: str, encoding: str = "utf-8") -> FileContent:
        """Read file content asynchronously."""
        response = await self._client._get_async(
            "/v1/file/read",
            params={"file": path, "encoding": encoding}
        )
        return FileContent.from_response(response.get("data", {}))

    async def write_async(
        self,
        path: str,
        content: str,
        encoding: str = "utf-8",
        create_dirs: bool = True,
    ) -> bool:
        """Write file content asynchronously."""
        response = await self._client._post_async("/v1/file/write", json={
            "file": path,
            "content": content,
            "encoding": encoding,
            "create_dirs": create_dirs,
        })
        return response.get("code") == 0


class BrowserService:
    """Browser automation service."""

    def __init__(self, client: AIOSandboxClient):
        self._client = client

    def get_info(self) -> BrowserInfo:
        """Get browser information including CDP URL."""
        response = self._client._get("/v1/browser/info")
        return BrowserInfo.from_response(response.get("data", {}))

    def screenshot(
        self,
        full_page: bool = False,
        format: str = "png",
    ) -> APIScreenshotResult:
        """Take browser screenshot."""
        response = self._client._get(
            "/v1/browser/screenshot",
            params={"full_page": full_page, "format": format}
        )
        data = response.get("data", {})
        image_data = base64.b64decode(data.get("data", ""))

        return APIScreenshotResult(
            data=image_data,
            format=data.get("format", format),
            width=data.get("width", 0),
            height=data.get("height", 0),
        )

    def navigate(self, url: str, wait_until: str = "load") -> dict[str, Any]:
        """Navigate browser to URL."""
        response = self._client._post("/v1/browser/navigate", json={
            "url": url,
            "wait_until": wait_until,
        })
        return response.get("data", {})

    def action(self, action_type: str, **kwargs) -> dict[str, Any]:
        """Perform browser action."""
        payload = {"type": action_type, **kwargs}
        response = self._client._post("/v1/browser/action", json=payload)
        return response.get("data", {})

    async def screenshot_async(
        self,
        full_page: bool = False,
        format: str = "png",
    ) -> APIScreenshotResult:
        """Take browser screenshot asynchronously."""
        response = await self._client._get_async(
            "/v1/browser/screenshot",
            params={"full_page": full_page, "format": format}
        )
        data = response.get("data", {})
        image_data = base64.b64decode(data.get("data", ""))

        return APIScreenshotResult(
            data=image_data,
            format=data.get("format", format),
            width=data.get("width", 0),
            height=data.get("height", 0),
        )


class SandboxService:
    """Sandbox environment service."""

    def __init__(self, client: AIOSandboxClient):
        self._client = client

    def get_context(self) -> SandboxContext:
        """Get sandbox environment context."""
        response = self._client._get("/v1/sandbox")
        return SandboxContext.from_response(response.get("data", {}))

    def health(self) -> bool:
        """Check sandbox health."""
        try:
            response = self._client._get("/v1/health")
            return response.get("status") == "ok"
        except Exception as e:
            logger.debug("Client operation failed: %s", e)
            return False


# ============================================================================
# Main Client
# ============================================================================

class AIOSandboxClient:
    """Client for AIO Sandbox HTTP API."""

    def __init__(
        self,
        base_url: str = "http://localhost:8080",
        timeout: float = 30.0,
        headers: dict[str, str] | None = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._headers = {"User-Agent": TEAMING24_USER_AGENT, **(headers or {})}

        self._sync_client = httpx.Client(
            base_url=self.base_url,
            timeout=timeout,
            headers=self._headers,
        )
        self._async_client: httpx.AsyncClient | None = None

        self.shell = ShellService(self)
        self.file = FileService(self)
        self.browser = BrowserService(self)
        self.sandbox = SandboxService(self)

    def _get_async_client(self) -> httpx.AsyncClient:
        if self._async_client is None:
            self._async_client = httpx.AsyncClient(
                base_url=self.base_url,
                timeout=self.timeout,
                headers=self._headers,
            )
        return self._async_client

    def _get(self, path: str, params: dict | None = None) -> dict:
        response = self._sync_client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    def _post(self, path: str, json: dict | None = None) -> dict:
        response = self._sync_client.post(path, json=json)
        response.raise_for_status()
        return response.json()

    async def _get_async(self, path: str, params: dict | None = None) -> dict:
        client = self._get_async_client()
        response = await client.get(path, params=params)
        response.raise_for_status()
        return response.json()

    async def _post_async(self, path: str, json: dict | None = None) -> dict:
        client = self._get_async_client()
        response = await client.post(path, json=json)
        response.raise_for_status()
        return response.json()

    def close(self) -> None:
        self._sync_client.close()

    async def aclose(self) -> None:
        self._sync_client.close()
        if self._async_client:
            await self._async_client.aclose()

    def __enter__(self) -> AIOSandboxClient:
        return self

    def __exit__(self, *args) -> None:
        self.close()

    async def __aenter__(self) -> AIOSandboxClient:
        return self

    async def __aexit__(self, *args) -> None:
        await self.aclose()

    @property
    def vnc_url(self) -> str:
        return f"{self.base_url}/vnc/index.html?autoconnect=true"

    @property
    def docs_url(self) -> str:
        return f"{self.base_url}/v1/docs"


__all__ = [
    "AIOSandboxClient",
    "ShellResult",
    "FileContent",
    "APIFileInfo",
    "BrowserInfo",
    "APIScreenshotResult",
    "SandboxContext",
]
