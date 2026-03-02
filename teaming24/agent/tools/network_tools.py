"""
Network delegation tools for CrewAI agents.

Provides tools for discovering and delegating tasks using the Agentic Node Workforce Pool,
which is a unified view of the local Coordinator and all connected remote ANs.
"""

import ipaddress
import json
from typing import Any

import httpx
from pydantic import BaseModel, Field

from teaming24.config import get_config
from teaming24.task import get_task_manager
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

# Try to import CrewAI BaseTool
try:
    from crewai.tools.base_tool import BaseTool
    CREWAI_AVAILABLE = True
except ImportError:
    CREWAI_AVAILABLE = False
    BaseTool = object  # Fallback for type hints
    logger.debug("CrewAI BaseTool not available; using object fallback")


class DelegateInput(BaseModel):
    """Input schema for DelegateToNetwork tool."""
    task_description: str = Field(
        ..., description="Description of the task to delegate"
    )
    required_capabilities: list[str] = Field(
        default=[], description="List of required capabilities (e.g., ['python', 'sql'])"
    )
    max_cost: float = Field(
        default=None, description="Maximum cost willing to pay (in mock units)"
    )


class DelegateToNetworkTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for delegating tasks to members of the Agentic Node Workforce Pool.

    The Organizer agent uses this tool to:
    1. Search the Agentic Node Workforce Pool for matching capabilities
    2. Select the most suitable member (local Coordinator or remote AN)
    3. Send the task via HTTP (x402 payment for remote ANs)
    4. Return results from remote execution
    """

    name: str = "delegate_to_network"
    description: str = (
        "Delegate a task to a remote Agentic Node in the Agentic Node Workforce Pool. "
        "Use this tool to send work to a remote AN whose capabilities "
        "match the task requirements. Returns the execution result.\n"
        "Input MUST be a JSON dict: {\"task_description\": \"<task>\", "
        "\"required_capabilities\": [\"cap1\"], \"max_cost\": 1.0}\n"
        "Example: {\"task_description\": \"Analyze sales data\", "
        "\"required_capabilities\": [\"data_analysis\"]}"
    )
    args_schema: type[BaseModel] = DelegateInput
    handle_tool_error: bool = True

    # Internal state (set during initialization)
    _workforce_pool: Any = None
    _network_manager: Any = None   # Kept for backward compatibility
    _task_manager: Any = None
    _current_task_id: str | None = None

    def __init__(self, workforce_pool: Any = None, network_manager: Any = None,
                 task_id: str = None, **kwargs):
        """
        Initialize delegation tool.

        Args:
            workforce_pool: AgenticNodeWorkforcePool instance (preferred)
            network_manager: NetworkManager instance (legacy fallback)
            task_id: Current task ID for tracking
        """
        super().__init__(**kwargs)
        self._workforce_pool = workforce_pool
        self._network_manager = network_manager
        self._task_manager = get_task_manager()
        self._current_task_id = task_id

    def _find_matching_nodes(self, required_capabilities: list[str]) -> list[dict]:
        """Find remote pool members ranked by the routing strategy.

        Only remote entries are returned because the local Coordinator is
        already reachable via CrewAI delegation — this tool is specifically
        for sending work to *remote* ANs.
        """
        # Prefer AgenticNodeWorkforcePool if available
        if self._workforce_pool:
            # rank() returns entries scored by the RoutingStrategy
            ranked = self._workforce_pool.rank(required_capabilities)
            results = []
            for e in ranked:
                if e.entry_type == "remote" and e.node_info:
                    node = e.node_info
                    results.append({
                        "id": e.id,
                        "name": e.name,
                        "ip": node.ip,
                        "port": node.port,
                        "capabilities": e.capabilities,
                        "description": e.description or "",
                        "wallet_address": getattr(node, "wallet_address", ""),
                    })
            return results

        # Legacy fallback: use raw NetworkManager
        if not self._network_manager:
            return []

        matching = []
        required_set = set(required_capabilities)

        for node in self._network_manager.get_nodes():
            # Check node capabilities
            node_caps = set(getattr(node, 'capabilities', []) or [])

            # Also check the main capability field
            main_cap = getattr(node, 'capability', None)
            if main_cap:
                node_caps.add(main_cap)

            # Check if node has required capabilities
            if required_set.issubset(node_caps) and node.status == "online":
                matching.append({
                    "id": node.id,
                    "name": node.name,
                    "ip": node.ip,
                    "port": node.port,
                    "capabilities": list(node_caps),
                    "description": getattr(node, 'description', ''),
                    "wallet_address": getattr(node, 'wallet_address', ''),
                })

        return matching

    async def _send_task_to_node(self, node: dict, task_description: str,
                                  max_cost: float) -> dict:
        """Send task to a remote node via x402 protocol."""
        config = get_config()

        # Validate IP and port before building URL (prevent SSRF / unexpected behavior)
        try:
            ipaddress.ip_address(node['ip'])
        except (ValueError, KeyError) as exc:
            logger.warning("Invalid node IP during delegation: node=%s err=%s", node, exc, exc_info=True)
            return {"status": "error", "error": f"Invalid node IP address: {node.get('ip', '<missing>')}"}
        port = node.get('port')
        if not isinstance(port, int) or not (1 <= port <= 65535):
            return {"status": "error", "error": f"Invalid node port: {port}"}

        # Build request
        url = f"http://{node['ip']}:{node['port']}/api/agent/execute"

        # Use get_node_uid() — globally unique per machine+port.
        from teaming24.utils.ids import get_node_uid
        local_node_uid = get_node_uid()

        # Build delegation chain for loop prevention
        existing_chain: list = getattr(self, "_delegation_chain", [])
        chain = existing_chain + [local_node_uid]

        # Build x402 payment data from config
        _pay_cfg = get_config().payment
        _pay_amount = float(_pay_cfg.task_price) if hasattr(_pay_cfg, "task_price") else max_cost
        _pay_mode = _pay_cfg.mode if hasattr(_pay_cfg, "mode") else "mock"

        payload = {
            "task": task_description,
            "requester_id": local_node_uid,
            "payment": {
                "amount": _pay_amount,
                "currency": get_config().payment.token_symbol,
                "protocol": "x402",
                "mode": _pay_mode,
            },
            "delegation_chain": chain,
        }

        try:
            _net_cfg = get_config().tools.network
            async with httpx.AsyncClient(timeout=_net_cfg.http_timeout) as client:
                headers = {
                    "X-402-Payment": json.dumps(payload["payment"]),
                    "Content-Type": "application/json",
                }

                response = await client.post(url, json=payload, headers=headers)

                if response.status_code == 402:
                    # Payment required - initiate x402 payment flow
                    try:
                        payment_info = response.json()
                    except (json.JSONDecodeError, ValueError) as exc:
                        logger.warning("Failed to parse payment-required response from node=%s: %s", node, exc, exc_info=True)
                        payment_info = {}
                    logger.warning(
                        "[delegate_to_network] Payment required by remote AN",
                        extra={"node": node.get("name", ""), "payment": payment_info},
                    )
                    return {
                        "status": "payment_required",
                        "error": f"Remote AN requires payment: {payment_info.get('error', 'x402 payment required')}",
                        "payment_requirements": payment_info.get("payment"),
                    }

                if response.status_code == 409:
                    try:
                        detail = response.json().get("detail", "Loop detected")
                    except (json.JSONDecodeError, ValueError) as exc:
                        logger.warning("Failed to parse 409 loop response from node=%s: %s", node, exc, exc_info=True)
                        detail = "Loop detected"
                    logger.warning(f"[delegate_to_network] Delegation loop/depth rejected: {detail}")
                    return {
                        "status": "error",
                        "error": f"Delegation loop detected: {detail}",
                    }

                if response.status_code != 200:
                    return {
                        "status": "error",
                        "error": f"Remote node returned {response.status_code}",
                    }

                try:
                    result = response.json()
                except (json.JSONDecodeError, ValueError) as exc:
                    logger.warning("Failed to parse success JSON response from node=%s: %s", node, exc, exc_info=True)
                    return {
                        "status": "error",
                        "error": f"Invalid JSON response from {node.get('name', node['ip'])}",
                    }
                return {
                    "status": "success",
                    "result": result.get("result", ""),
                    "cost": result.get("cost", {}),
                    "node": node["name"],
                }

        except httpx.TimeoutException:
            logger.warning("Delegate request timed out for node=%s", node.get("name", node.get("id")))
            return {
                "status": "error",
                "error": f"Timeout connecting to {node['name']}",
            }
        except Exception as e:
            logger.exception("Delegate request failed for node=%s: %s", node.get("name", node.get("id")), e)
            return {
                "status": "error",
                "error": f"Failed to connect to {node['name']}: {str(e)}",
            }

    def _run(self, task_description: str, required_capabilities: list[str] = None,
             max_cost: float = None, **kwargs) -> str:
        """
        Synchronous execution (required by CrewAI).

        Returns:
            Result string or error message
        """
        import asyncio
        _net_cfg = get_config().tools.network
        if max_cost is None:
            max_cost = _net_cfg.default_max_cost
        try:
            # Check if we're already in an async context
            asyncio.get_running_loop()
            # Create a new thread to run the async code
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(
                    asyncio.run,
                    self._arun(task_description, required_capabilities, max_cost)
                )
                return future.result(timeout=_net_cfg.sync_timeout)
        except RuntimeError:
            # No running loop, safe to use asyncio.run
            logger.debug("No running loop for delegate_to_network sync path; using asyncio.run")
            return asyncio.run(self._arun(task_description, required_capabilities, max_cost))

    async def _arun(self, task_description: str, required_capabilities: list[str] = None,
                    max_cost: float = None) -> str:
        """
        Asynchronous execution.

        Returns:
            Result string or error message
        """
        required_capabilities = required_capabilities or []
        if max_cost is None:
            max_cost = get_config().tools.network.default_max_cost

        # Log delegation attempt (ANRouter-style structured logging)
        logger.info(
            f"[ANRouter] task={self._current_task_id} | "
            f"DELEGATE ATTEMPT: capabilities={required_capabilities}, "
            f"task={task_description[:60]}..."
        )

        # Check if we have an Agentic Node Workforce Pool or network manager
        if not self._workforce_pool and not self._network_manager:
            config = get_config()
            # Check AgentaNet configuration
            if not getattr(config.network, "auto_online", True):
                return ("ERROR: AgentaNet is not enabled. Cannot delegate to remote nodes. "
                       "Please enable AgentaNet in settings or complete the task locally.")
            return "ERROR: No network manager available. Cannot search for remote nodes."

        # Find matching nodes
        matching_nodes = self._find_matching_nodes(required_capabilities)

        if not matching_nodes:
            return (f"ERROR: No remote nodes found with capabilities: {required_capabilities}. "
                   f"The task cannot be delegated. Consider breaking it into smaller subtasks "
                   f"or handling it differently.")

        # Log candidate pool members (ranked)
        logger.info(
            f"[ANRouter] task={self._current_task_id} | "
            f"CANDIDATES: {len(matching_nodes)} remote node(s) matched"
        )
        for i, node in enumerate(matching_nodes, 1):
            logger.info(
                f"[ANRouter]   {i}. {node['name']} "
                f"({node['ip']}:{node['port']}): {node['capabilities']}"
            )

        # Nodes are already ranked by RoutingStrategy — pick the top one
        selected_node = matching_nodes[0]

        # Log the final selection decision
        logger.info(
            f"[ANRouter] task={self._current_task_id} | "
            f"SELECTED: {selected_node['name']} "
            f"({selected_node['ip']}:{selected_node['port']}) "
            f"for remote delegation"
        )

        # Track delegation in task manager
        if self._current_task_id and self._task_manager:
            self._task_manager.delegate_task(self._current_task_id, selected_node["id"])
            self._task_manager.add_step(
                self._current_task_id,
                agent="Organizer",
                action="delegate",
                content=f"Delegating to {selected_node['name']} ({selected_node['ip']}:{selected_node['port']})",
            )

        # Send task to remote node
        result = await self._send_task_to_node(selected_node, task_description, max_cost)

        if result["status"] == "success":
            logger.info(
                f"[ANRouter] task={self._current_task_id} | "
                f"DELEGATE SUCCESS: {selected_node['name']} completed the task"
            )
            # Update cost tracking
            if self._current_task_id and self._task_manager:
                remote_cost = result.get("cost", {})
                self._task_manager.update_cost(
                    self._current_task_id,
                    x402_payment=remote_cost.get("x402_payment", max_cost),
                )

            return f"Task completed by {result['node']}:\n\n{result['result']}"
        else:
            error_msg = result.get("error", "Unknown error")
            logger.warning(
                f"[ANRouter] task={self._current_task_id} | "
                f"DELEGATE FAILED: {selected_node['name']} — {error_msg}"
            )
            return f"ERROR: Delegation failed - {error_msg}"


def create_delegation_tool(workforce_pool: Any = None,
                           network_manager: Any = None,
                           task_id: str = None) -> DelegateToNetworkTool:
    """
    Factory function to create a DelegateToNetwork tool.

    Args:
        workforce_pool: AgenticNodeWorkforcePool instance (preferred)
        network_manager: NetworkManager instance (legacy fallback)
        task_id: Current task ID

    Returns:
        Configured DelegateToNetworkTool instance
    """
    return DelegateToNetworkTool(
        workforce_pool=workforce_pool,
        network_manager=network_manager,
        task_id=task_id,
    )


class SearchInput(BaseModel):
    """Input schema for SearchNetwork tool."""
    capabilities: list[str] = Field(
        default=[], description="Optional list of capabilities to filter by (e.g., ['python', 'ml']). Leave empty to list all."
    )


class SearchNetworkTool(BaseTool if CREWAI_AVAILABLE else object):
    """
    Tool for listing available members in the Agentic Node Workforce Pool.

    Returns a human-readable summary of the local Coordinator and all
    connected remote ANs, so the Organizer can make informed routing
    decisions.
    """

    name: str = "search_network"
    description: str = (
        "List available members of the Agentic Node Workforce Pool (local Coordinator and remote ANs). "
        "Use this to see what capabilities are available before delegating tasks.\n"
        "Input MUST be a JSON dict: {\"capabilities\": [\"cap1\", \"cap2\"]}\n"
        "Example: {\"capabilities\": []} to list all members."
    )
    args_schema: type[BaseModel] = SearchInput
    handle_tool_error: bool = True

    _workforce_pool: Any = None
    _network_manager: Any = None   # Kept for backward compatibility

    def __init__(self, workforce_pool: Any = None, network_manager: Any = None, **kwargs):
        """
        Initialize search tool.

        Args:
            workforce_pool: AgenticNodeWorkforcePool instance (preferred)
            network_manager: NetworkManager instance (legacy fallback)
        """
        super().__init__(**kwargs)
        self._workforce_pool = workforce_pool
        self._network_manager = network_manager

    def _run(self, capabilities: list[str] = None, **kwargs) -> str:
        """Search the Agentic Node Workforce Pool."""
        # Normalize: empty list → None (treat as "list all")
        if capabilities is not None and len(capabilities) == 0:
            capabilities = None

        # Prefer AgenticNodeWorkforcePool if available
        if self._workforce_pool:
            if capabilities:
                # rank() uses RoutingStrategy — results are best-first
                entries = self._workforce_pool.rank(capabilities)
                if not entries:
                    return f"No pool members found with capabilities: {capabilities}"
                lines = [f"Found {len(entries)} member(s) matching {capabilities} (ranked best-first):"]
                for e in entries:
                    tag = "LOCAL" if e.entry_type == "local" else "REMOTE"
                    caps = ", ".join(e.capabilities) if e.capabilities else "general"
                    lines.append(f"  - [{tag}] {e.name} ({e.status}): [{caps}]")
                    if e.description:
                        lines.append(f"    {e.description}")
                return "\n".join(lines)
            else:
                return self._workforce_pool.describe()

        # Legacy fallback: use raw NetworkManager
        if not self._network_manager:
            return "No network manager available"

        nodes = self._network_manager.get_nodes()

        if not nodes:
            return "No remote nodes connected"

        # Filter by capabilities if specified
        if capabilities:
            cap_set = set(capabilities)
            nodes = [n for n in nodes if cap_set.issubset(
                set(getattr(n, 'capabilities', []) or [])
            )]

        # Format results
        result = f"Found {len(nodes)} node(s):\n"
        for node in nodes:
            caps = getattr(node, 'capabilities', []) or []
            result += f"- {node.name} ({node.status}): {', '.join(caps)}\n"
            if hasattr(node, 'description') and node.description:
                result += f"  Description: {node.description}\n"

        return result


def get_organizer_tools(workforce_pool: Any = None,
                        network_manager: Any = None,
                        task_id: str = None) -> list[Any]:
    """
    Get all tools for the Organizer agent.

    Args:
        workforce_pool: AgenticNodeWorkforcePool instance (preferred)
        network_manager: NetworkManager instance (legacy fallback)
        task_id: Current task ID

    Returns:
        List of tool instances
    """
    return [
        create_delegation_tool(workforce_pool=workforce_pool,
                               network_manager=network_manager,
                               task_id=task_id),
        SearchNetworkTool(workforce_pool=workforce_pool,
                          network_manager=network_manager),
    ]
