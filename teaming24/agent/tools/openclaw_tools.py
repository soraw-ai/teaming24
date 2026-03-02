"""
OpenClaw Tools for CrewAI Agents.

Wraps OpenClaw Gateway capabilities as CrewAI-compatible tools so Teaming24
workers can leverage OpenClaw's managed browser and notification features.

Tools are only registered when extensions.openclaw.enabled is True in teaming24.yaml.
Uses OpenClaw HTTP API: POST <gateway_http_url>/tools/invoke

Tool mapping (OpenClaw compat):
    openclaw_browser_snapshot  → tool "browser" (navigate + snapshot)
    openclaw_browser_action    → tool "browser" (navigate | act)
    openclaw_notify            → tool "nodes" (action=notify, title, body)
    openclaw_session_send      → tool "sessions_send" (denied by default on HTTP;
                                  requires gateway.tools.allow)

Configuration (teaming24.yaml):
    extensions:
      openclaw:
        enabled: true             # Required: enables these tools (default: false)
        gateway_url: "ws://127.0.0.1:18789"
        expose_browser_tool: true
        expose_notify_tool: true
        expose_session_tool: false   # sessions_send denied on HTTP unless allowed
        tool_timeout: 30
"""

import asyncio
import concurrent.futures
import json
import urllib.error as _urllib_err
import urllib.request as _urllib_req

from pydantic import BaseModel, Field

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# ── Optional deps ─────────────────────────────────────────────────────────────

try:
    from crewai.tools.base_tool import BaseTool

    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    BaseTool = object  # type: ignore[assignment,misc]
    logger.debug("CrewAI BaseTool unavailable for openclaw tools")

# ── Config helpers ────────────────────────────────────────────────────────────


def _oc_config() -> dict:
    """Return the openclaw extension config dict (safe fallback to {})."""
    try:
        from teaming24.config import get_config

        cfg = get_config()
        ext = cfg.extensions if isinstance(cfg.extensions, dict) else {}
        return ext.get("openclaw", {})
    except Exception as e:
        logger.debug("[OpenClawTools] Config load failed: %s", e)
        return {}


def _gateway_url() -> str:
    return _oc_config().get("gateway_url", "ws://127.0.0.1:18789")


def _http_url() -> str:
    """Derive the OpenClaw HTTP API base URL (host:port only) from the gateway WS URL."""
    from urllib.parse import urlparse, urlunparse
    gw = _gateway_url()
    parsed = urlparse(gw)
    scheme = "https" if parsed.scheme == "wss" else "http"
    # Drop path/params — gateway URL is always ws://host:port with no path.
    return urlunparse((scheme, parsed.netloc, "", "", "", ""))


def _tool_timeout() -> int:
    return int(_oc_config().get("tool_timeout", 30))


def _auth_token() -> str:
    return str(_oc_config().get("token", ""))


# ── Core HTTP helper ──────────────────────────────────────────────────────────


def _parse_tool_result(data: dict) -> str:
    """
    Parse OpenClaw /tools/invoke response.
    Format: { ok: true, result: { content: [{ type: "text", text: "..." }], ... } }
    or: { ok: true, result: "string" }
    or: { ok: false, error: { message: "..." } }
    """
    if data.get("ok") is not True:
        err = data.get("error") or {}
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        return f"ERROR: {msg}" if msg else "ERROR: unknown error"

    result = data.get("result")
    if result is None:
        return ""
    if isinstance(result, str):
        return result
    if isinstance(result, dict):
        content = result.get("content") or []
        parts = [
            b.get("text", "")
            for b in content
            if isinstance(b, dict) and b.get("type") == "text"
        ]
        return "\n".join(parts) if parts else str(result)
    return str(result)


async def _call_tool_http(tool: str, args: dict, timeout: int = 30) -> str:
    """
    Call an OpenClaw tool via its HTTP API (POST <gateway_http_url>/tools/invoke).

    Uses urllib.request (stdlib). OpenClaw returns { ok: true, result } where
    result may be a string or { content: [{ type: "text", text: "..." }], details? }.
    Errors return { ok: false, error: { message: "..." } }.
    """
    base_url = _http_url()
    token = _auth_token()

    payload = json.dumps({"tool": tool, "args": args}).encode()
    req = _urllib_req.Request(
        f"{base_url}/tools/invoke",
        data=payload,
        headers={
            "Content-Type": "application/json",
            **({"Authorization": f"Bearer {token}"} if token else {}),
        },
        method="POST",
    )
    try:
        with _urllib_req.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode())
        result = _parse_tool_result(data)
        if result.startswith("ERROR:"):
            logger.warning("[OpenClawTools] OpenClaw returned error tool=%s: %s", tool, result[:200])
        return result
    except _urllib_err.HTTPError as e:
        body = ""
        try:
            body = e.read().decode()[:200]
        except Exception as exc:
            logger.debug(
                "[OpenClawTools] Failed to read HTTPError body for tool=%s: %s",
                tool,
                exc,
                exc_info=True,
            )
        err_msg = f"HTTP {e.code} from OpenClaw — {body}"
        logger.warning("[OpenClawTools] %s tool=%s args=%s", err_msg, tool, args)
        return f"ERROR: {err_msg}"
    except _urllib_err.URLError as e:
        err_msg = f"OpenClaw not reachable at {base_url} ({e.reason})"
        logger.warning("[OpenClawTools] %s tool=%s", err_msg, tool)
        return f"ERROR: {err_msg}"
    except Exception as e:
        logger.warning("[OpenClawTools] HTTP call failed tool=%s: %s", tool, e, exc_info=True)
        return f"ERROR: {e}"


def _run_async(coro) -> str:
    """
    Execute an async coroutine from a synchronous CrewAI _run() context.

    CrewAI calls tools synchronously from worker threads that may or may not
    have a running event loop. This helper handles both cases safely:

    - Running loop present (e.g. async test, nested executor): spawn a
      dedicated thread with its own event loop to avoid "loop already running".
    - No running loop: call asyncio.run() directly.
    """
    timeout = _tool_timeout() + 5

    # Determine whether we are inside a running event loop.
    in_loop = True
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("No running loop in openclaw sync bridge; using direct asyncio.run path")
        in_loop = False

    if in_loop:
        # We cannot call asyncio.run() here — it would raise RuntimeError.
        # Offload to a fresh thread that has no loop of its own.
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(asyncio.run, coro)
            try:
                return future.result(timeout=timeout)
            except concurrent.futures.TimeoutError:
                logger.warning("[OpenClawTools] Tool call timed out after %ds", timeout)
                return f"ERROR: OpenClaw tool call timed out after {timeout}s"
            except Exception as e:
                logger.warning("[OpenClawTools] Tool call failed in thread: %s", e)
                return f"ERROR: {e}"
    else:
        # No running loop in this thread — safe to call asyncio.run() directly.
        try:
            return asyncio.run(coro)
        except Exception as e:
            logger.warning("[OpenClawTools] Tool call failed: %s", e)
            return f"ERROR: {e}"


def _call_tool(tool: str, args: dict) -> str:
    """Synchronous wrapper: call OpenClaw HTTP tool API from CrewAI _run()."""
    return _run_async(_call_tool_http(tool, args, timeout=_tool_timeout()))


# ── Tool: Browser Snapshot ────────────────────────────────────────────────────


class BrowserSnapshotInput(BaseModel):
    url: str = Field(..., description="The URL to visit and capture")


class OpenClawBrowserSnapshotTool(BaseTool if CREWAI_AVAILABLE else object):  # type: ignore[misc]
    """
    Browse a URL using OpenClaw's managed Chromium browser and return the
    page's visible text content.

    Useful for reading web pages, extracting information from sites, and
    verifying that URLs return expected content.
    """

    name: str = "openclaw_browser_snapshot"
    description: str = (
        "Visit a URL with OpenClaw's browser and capture its visible text content.\n"
        "Input: {\"url\": \"<full URL including https://\"}\n"
        "Returns: Page title + extracted text, or an error message."
    )
    args_schema: type[BaseModel] = BrowserSnapshotInput
    handle_tool_error: bool = True

    def _run(self, url: str) -> str:
        # OpenClaw: single "browser" tool; snapshot requires navigate first
        nav = _call_tool("browser", {"action": "navigate", "targetUrl": url})
        if nav.startswith("ERROR:"):
            return nav
        return _call_tool("browser", {"action": "snapshot"})


# ── Tool: Browser Action ──────────────────────────────────────────────────────


class BrowserActionInput(BaseModel):
    action: str = Field(
        ...,
        description="Action to perform: 'navigate' | 'click' | 'type' | 'scroll'",
    )
    url: str | None = Field(None, description="URL to navigate to (for 'navigate')")
    selector: str | None = Field(
        None, description="CSS selector or text description of the element (for 'click'/'type')"
    )
    text: str | None = Field(None, description="Text to type (for 'type')")
    direction: str | None = Field(None, description="'up' or 'down' (for 'scroll')")


class OpenClawBrowserActionTool(BaseTool if CREWAI_AVAILABLE else object):  # type: ignore[misc]
    """
    Interact with the currently loaded page in OpenClaw's browser.

    Supports navigation, clicking elements, typing text, and scrolling.
    Use after openclaw_browser_snapshot to interact with a loaded page.
    """

    name: str = "openclaw_browser_action"
    description: str = (
        "Interact with OpenClaw's browser: navigate, click, type, or scroll.\n"
        "Input examples:\n"
        "  {\"action\": \"navigate\", \"url\": \"https://example.com\"}\n"
        "  {\"action\": \"click\", \"selector\": \"#submit-btn\"}\n"
        "  {\"action\": \"type\", \"selector\": \"input[name=q]\", \"text\": \"search query\"}\n"
        "  {\"action\": \"scroll\", \"direction\": \"down\"}\n"
        "Returns: Result description or error."
    )
    args_schema: type[BaseModel] = BrowserActionInput
    handle_tool_error: bool = True

    def _run(
        self,
        action: str,
        url: str | None = None,
        selector: str | None = None,
        text: str | None = None,
        direction: str | None = None,
    ) -> str:
        # OpenClaw: single "browser" tool; navigate uses targetUrl, act uses request
        if action == "navigate":
            if not url:
                return "ERROR: url required for navigate"
            return _call_tool("browser", {"action": "navigate", "targetUrl": url})
        if action in ("click", "type", "press", "hover"):
            req: dict = {"kind": action}
            if selector:
                req["element"] = selector
            if text:
                req["text"] = text
            return _call_tool("browser", {"action": "act", "request": req})
        if action == "scroll":
            # direction=None or "up" → scroll up (-300px); "down" → scroll down (300px)
            step = 300 if direction == "down" else -300
            fn = f"() => window.scrollBy(0, {step})"
            return _call_tool("browser", {"action": "act", "request": {"kind": "evaluate", "fn": fn}})
        return f"ERROR: action '{action}' not supported (use navigate|click|type|scroll)"


# ── Tool: Notify (OpenClaw nodes) ─────────────────────────────────────────────


class NotifyInput(BaseModel):
    title: str = Field(..., description="Short notification title (max ~60 chars)")
    message: str = Field(..., description="Notification body text")


class OpenClawNotifyTool(BaseTool if CREWAI_AVAILABLE else object):  # type: ignore[misc]
    """
    Send a system notification to the user via OpenClaw.

    Useful for alerting the user when a key step completes, an important
    finding is made, or manual input is required.
    """

    name: str = "openclaw_notify"
    description: str = (
        "Send a desktop/mobile notification to the user via OpenClaw.\n"
        "Use sparingly — only for genuinely important events.\n"
        "Input: {\"title\": \"<short title>\", \"message\": \"<body text>\"}\n"
        "Returns: 'sent' or error message."
    )
    args_schema: type[BaseModel] = NotifyInput
    handle_tool_error: bool = True

    def _run(self, title: str, message: str) -> str:
        # OpenClaw: tool "nodes", action "notify", param "body" (not message)
        return _call_tool("nodes", {"action": "notify", "title": title, "body": message})


# ── Availability check ────────────────────────────────────────────────────────


async def check_openclaw_available() -> bool:
    """
    Return True if the OpenClaw HTTP API is reachable.

    Sends a no-op notify to OpenClaw nodes tool; non-ERROR response means reachable.
    """
    try:
        loop = asyncio.get_running_loop()
        result = await loop.run_in_executor(
            None, lambda: _call_tool("nodes", {"action": "notify", "title": "_ping", "body": ""}),
        )
        return not result.startswith("ERROR:")
    except Exception as e:
        logger.debug("[OpenClawTools] Availability check failed: %s", e)
        return False


# ── Tool: Session Send (Agent-to-Agent via OpenClaw) ─────────────────────────


class SessionSendInput(BaseModel):
    session_id: str = Field(..., description="Target OpenClaw session ID")
    message: str = Field(..., description="Message to send to the session")


class OpenClawSessionSendTool(BaseTool if CREWAI_AVAILABLE else object):  # type: ignore[misc]
    """
    Send a message to another OpenClaw session (agent-to-agent coordination).

    Useful for coordinating with other agents running in separate OpenClaw
    sessions, forwarding results, or requesting information.
    """

    name: str = "openclaw_session_send"
    description: str = (
        "Send a message to another OpenClaw session for agent-to-agent coordination.\n"
        "Input: {\"session_id\": \"<target session>\", \"message\": \"<content>\"}\n"
        "Returns: Confirmation or error."
    )
    args_schema: type[BaseModel] = SessionSendInput
    handle_tool_error: bool = True

    def _run(self, session_id: str, message: str) -> str:
        # OpenClaw's sessions_send tool uses sessionKey, not sessionId
        return _call_tool("sessions_send", {"sessionKey": session_id, "message": message})


# ── Factory ───────────────────────────────────────────────────────────────────


def create_openclaw_tools() -> list:
    """
    Create all configured OpenClaw tools for use by CrewAI agents.

    Tools call OpenClaw's HTTP API (POST <gateway_url>/tools/invoke) — no
    WebSocket session required.  Returns an empty list if CrewAI is unavailable.
    """
    if not CREWAI_AVAILABLE:
        logger.warning("[OpenClawTools] CrewAI not available — returning empty tool list")
        return []

    cfg = _oc_config()
    tools: list = []

    if cfg.get("expose_browser_tool", True):
        tools.append(OpenClawBrowserSnapshotTool())
        tools.append(OpenClawBrowserActionTool())

    if cfg.get("expose_notify_tool", True):
        tools.append(OpenClawNotifyTool())

    if cfg.get("expose_session_tool", False):  # Denied on HTTP unless allowed
        tools.append(OpenClawSessionSendTool())

    logger.info(
        "[OpenClawTools] %d tool(s) ready: %s",
        len(tools), [t.name for t in tools],
    )
    return tools
