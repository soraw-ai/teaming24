"""
AgentaNet Central Service Client.

Handles communication with the central service for:
- Marketplace registration
- Heartbeat (keep-alive)
- Node discovery
"""

import asyncio
import time
from typing import Any

import httpx

from teaming24.config import get_config
from teaming24.data.database import get_database
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class CentralClient:
    """Client for AgentaNet Central Service."""

    def __init__(self):
        self._heartbeat_task: asyncio.Task | None = None
        self._is_registered = False
        self._last_heartbeat: float | None = None
        self._last_registered_node: dict[str, Any] | None = None

    @staticmethod
    def _cfg():
        """Always fetch the latest runtime config (supports reload)."""
        return get_config()

    @staticmethod
    def _setting_override(key: str) -> str:
        """Read runtime override from settings DB (if available)."""
        try:
            value = get_database().get_setting(key)
        except Exception as exc:
            logger.warning(
                "Failed to read central setting override key=%s: %s",
                key,
                exc,
                exc_info=True,
            )
            return ""
        return str(value or "").strip()

    @staticmethod
    def _normalize_base_url(raw_url: str) -> str:
        """Normalize central base URL (accepts legacy API-suffixed URLs)."""
        url = str(raw_url or "").strip().rstrip("/")
        for suffix in ("/api/marketplace", "/api"):
            if url.endswith(suffix):
                return url[: -len(suffix)]
        return url

    @property
    def base_url(self) -> str:
        """Get central service URL from settings override or config."""
        override = self._setting_override("agentanetCentralUrl")
        if override:
            return self._normalize_base_url(override)
        return self._normalize_base_url(self._cfg().agentanet_central.url or "")

    @property
    def token(self) -> str:
        """Get API token from settings override or config."""
        override = self._setting_override("agentanetToken")
        if override:
            return override
        return str(self._cfg().agentanet_central.token or "").strip()

    @property
    def heartbeat_interval(self) -> int:
        """Get heartbeat interval from config."""
        return self._cfg().agentanet_central.heartbeat_interval

    @property
    def is_configured(self) -> bool:
        """Check if central service is enabled and configured with URL+token."""
        if not bool(getattr(self._cfg().agentanet_central, "enabled", True)):
            return False
        return bool(self.base_url and self.token)

    @property
    def has_base_url(self) -> bool:
        """Check if central URL is configured (for public search; token optional)."""
        if not bool(getattr(self._cfg().agentanet_central, "enabled", True)):
            return False
        return bool(self.base_url)

    def _get_headers(self) -> dict[str, str]:
        """Get authorization headers."""
        token = str(self.token or "").strip()
        if not token:
            raise ValueError("AgentaNet token not configured")
        return {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }

    async def register(
        self,
        name: str,
        description: str = "",
        capability: str = "",
        capabilities: list[Any] | None = None,
        an_id: str | None = None,
        wallet_address: str | None = None,
        price: str = "",
        ip: str | None = None,
        port: int | None = None,
        region: str = "",
    ) -> dict[str, Any]:
        """
        Register node on central marketplace.

        Returns node info on success, raises exception on failure.
        """
        if not self.is_configured:
            raise ValueError("AgentaNet token not configured")

        url = f"{self.base_url}/api/marketplace/register"
        # Central requires an_id wallet prefix to match wallet_address; ensure consistency
        _an_id = (an_id or "").strip() or None
        _wallet = (wallet_address or "").strip() or None
        if _an_id and "-" in _an_id and _an_id.lower().startswith("0x"):
            _prefix = _an_id.split("-", 1)[0].strip().lower()
            if _prefix and len(_prefix) >= 4:
                if _wallet and _wallet.lower() != _prefix:
                    _wallet = _prefix  # Use an_id wallet to avoid central 422
                elif not _wallet:
                    _wallet = _prefix
        an_id = _an_id
        wallet_address = _wallet

        # Central NodeRegisterRequest: port in [1,65535] or None; capability max 64; etc.
        safe_port = None
        if port is not None and 1 <= port <= 65535:
            safe_port = int(port)
        safe_capability = (capability or "")[:64].strip() or None
        safe_caps = []
        for c in (capabilities or []):
            if isinstance(c, dict):
                n = str(c.get("name") or "").strip()
                if n:
                    safe_caps.append({"name": n[:128], "description": str(c.get("description") or "")[:512]})
            elif isinstance(c, str) and c.strip():
                safe_caps.append({"name": c.strip()[:128], "description": ""})
        payload = {
            "name": (name or "").strip()[:128] or "Agentic Node",
            "description": (description or "")[:1024] or None,
            "capability": safe_capability,
            "capabilities": safe_caps if safe_caps else None,
            "an_id": (an_id or "").strip()[:128] or None,
            "wallet_address": (wallet_address or "").strip()[:128] or None,
            "price": (price or "")[:64] or None,
            "ip": (ip or "")[:64] or None,
            "port": safe_port,
            "region": (region or "")[:64] or None,
        }
        # Remove None values to avoid sending null for optional fields that central might reject
        payload = {k: v for k, v in payload.items() if v is not None}

        _central = self._cfg().agentanet_central
        async with httpx.AsyncClient(timeout=_central.register_timeout) as client:
            resp = await client.post(url, json=payload, headers=self._get_headers())
            resp.raise_for_status()
            result = resp.json()
            self._is_registered = True
            self._last_registered_node = result if isinstance(result, dict) else None
            logger.info(f"Registered on central marketplace: {result.get('id')}")
            return result

    async def heartbeat(self) -> bool:
        """Send heartbeat to central service."""
        if not self.is_configured or not self._is_registered:
            return False

        url = f"{self.base_url}/api/marketplace/heartbeat"
        _central = self._cfg().agentanet_central

        try:
            async with httpx.AsyncClient(timeout=_central.heartbeat_http_timeout) as client:
                resp = await client.post(url, headers=self._get_headers())
                resp.raise_for_status()
                self._last_heartbeat = time.time()
                logger.debug("Heartbeat sent to central service")
                return True
        except Exception as e:
            logger.warning(f"Heartbeat failed: {e}")
            return False

    async def unlist(self, raise_on_error: bool = False) -> bool:
        """Remove node from marketplace listing."""
        if not self.is_configured:
            return False

        url = f"{self.base_url}/api/marketplace/unlist"
        me_url = f"{self.base_url}/api/marketplace/me"
        _central = self._cfg().agentanet_central

        try:
            async with httpx.AsyncClient(timeout=_central.heartbeat_http_timeout) as client:
                resp = await client.post(url, headers=self._get_headers())
                resp.raise_for_status()
                # Verify central state reflects unlisted status.
                me_resp = await client.get(me_url, headers=self._get_headers())
                me_resp.raise_for_status()
                me_payload = me_resp.json() if me_resp.content else {}
                if bool(me_payload.get("listed", False)):
                    raise RuntimeError("Central still reports node as listed after unlist")
                self._is_registered = False
                self._last_registered_node = None
                logger.info("Unlisted from central marketplace")
                return True
        except Exception as e:
            logger.warning(f"Failed to unlist: {e}")
            if raise_on_error:
                raise
            return False

    async def search_nodes(
        self,
        search: str | None = None,
        capability: str | None = None,
        region: str | None = None,
        status: str | None = None,
        raise_on_error: bool = False,
    ) -> list[dict[str, Any]]:
        """Search marketplace nodes.

        Central /api/marketplace/nodes is public; token optional (auth shows full IP).
        Handles both legacy flat list responses and the new paginated
        ``{"items": [...], "total": N, ...}`` format.
        """
        if not self.has_base_url:
            if raise_on_error:
                raise ValueError("AgentaNet Central URL not configured")
            return []

        url = f"{self.base_url}/api/marketplace/nodes"
        params = {}
        if search:
            params["search"] = search
        if capability:
            params["capability"] = capability
        if region:
            params["region"] = region
        if status:
            params["status"] = status

        _central = self._cfg().agentanet_central
        page_size = max(1, int(getattr(_central, "search_page_size", 100) or 100))
        max_pages = max(1, int(getattr(_central, "search_max_pages", 20) or 20))
        try:
            async with httpx.AsyncClient(timeout=_central.search_timeout) as client:
                all_items: list[dict[str, Any]] = []
                page = 1
                while page <= max_pages:
                    page_params = dict(params)
                    page_params["page"] = page
                    page_params["page_size"] = page_size

                    headers = self._get_headers() if self.token else {"Content-Type": "application/json"}
                    resp = await client.get(url, params=page_params, headers=headers)
                    resp.raise_for_status()
                    data = resp.json()

                    # Support paginated response format
                    if isinstance(data, dict) and "items" in data:
                        items = data.get("items") or []
                        if isinstance(items, list):
                            all_items.extend([item for item in items if isinstance(item, dict)])
                        total = int(data.get("total") or len(all_items))
                        if len(all_items) >= total or not items:
                            break
                        page += 1
                        continue

                    # Legacy flat list format: single response is final.
                    if isinstance(data, list):
                        return [item for item in data if isinstance(item, dict)]
                    return []

                return all_items
        except Exception as e:
            logger.warning(f"Failed to search nodes: {e}")
            if raise_on_error:
                raise
            return []

    async def get_node(self, node_id: str) -> dict[str, Any] | None:
        """Get specific node by ID.

        Sends auth headers so Central returns full IP:port instead of masked values.
        """
        if not self.is_configured:
            return None

        url = f"{self.base_url}/api/marketplace/nodes/{node_id}"
        _central = self._cfg().agentanet_central

        try:
            async with httpx.AsyncClient(timeout=_central.get_node_timeout) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 404:
                    return None
                resp.raise_for_status()
                return resp.json()
        except Exception as e:
            logger.warning(f"Failed to get node {node_id}: {e}")
            return None

    async def get_my_node(self, raise_on_error: bool = False) -> dict[str, Any] | None:
        """Get current token owner's node state from central service."""
        if not self.is_configured:
            return None

        url = f"{self.base_url}/api/marketplace/me"
        _central = self._cfg().agentanet_central
        try:
            async with httpx.AsyncClient(timeout=_central.get_node_timeout) as client:
                resp = await client.get(url, headers=self._get_headers())
                if resp.status_code == 404:
                    self._is_registered = False
                    return None
                resp.raise_for_status()
                data = resp.json()
                if isinstance(data, dict):
                    node = data.get("node")
                    listed = bool(data.get("listed", False))
                    self._is_registered = listed and isinstance(node, dict)
                    if isinstance(node, dict):
                        self._last_registered_node = node
                        return node
                return None
        except Exception as e:
            logger.warning(f"Failed to get current node from central: {e}")
            if raise_on_error:
                raise
            return None

    async def start_heartbeat_loop(self):
        """Start background heartbeat loop."""
        if self._heartbeat_task:
            return

        async def _loop():
            while True:
                await asyncio.sleep(self.heartbeat_interval)
                if self._is_registered:
                    await self.heartbeat()

        self._heartbeat_task = asyncio.create_task(_loop())
        logger.info(f"Heartbeat loop started (interval: {self.heartbeat_interval}s)")

    async def stop_heartbeat_loop(self):
        """Stop background heartbeat loop."""
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            try:
                await self._heartbeat_task
            except asyncio.CancelledError:
                logger.debug("Heartbeat task cancelled")
                pass
            self._heartbeat_task = None
            logger.info("Heartbeat loop stopped")

    async def shutdown(self):
        """Cleanup on shutdown."""
        await self.stop_heartbeat_loop()
        if self._is_registered:
            await self.unlist()


# Global client instance
_central_client: CentralClient | None = None


def get_central_client() -> CentralClient:
    """Get or create central client instance."""
    global _central_client
    if _central_client is None:
        _central_client = CentralClient()
    return _central_client
