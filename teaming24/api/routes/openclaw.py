"""
OpenClaw integration API endpoints.

This module exposes a clean REST + SSE interface that the teaming24 OpenClaw
TypeScript plugin uses to invoke Teaming24 capabilities from OpenClaw sessions.

Endpoints
---------
- POST /api/openclaw/execute        — Submit a task; stream SSE progress + final result
- GET  /api/openclaw/wallet         — Wallet balance / transactions / summary
- GET  /api/openclaw/network        — Network peers / node status
- POST /api/openclaw/delegate       — Delegate task to a specific network peer

SSE stream format (execute)
---------------------------
All events are standard Server-Sent Events::

    event: started
    data: {"task_id": "t-..."}

    event: step
    data: {"agent": "Worker", "action": "...", "content": "...", "step_number": 1}

    event: progress
    data: {"task_id": "t-...", "progress": {...}}

    event: completed
    data: {"task_id": "t-...", "result": "...", "cost": {...}}

    event: failed
    data: {"task_id": "t-...", "error": "..."}

Authentication
--------------
Pass ``X-OpenClaw-Token`` header (same value as ``extensions.openclaw.token``
in teaming24.yaml).  When the token is empty/unset, the endpoint is only
accessible from loopback addresses (127.0.0.1, ::1, localhost).
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import teaming24.api.state as _st
from teaming24.api.deps import config, logger

router = APIRouter(prefix="/api/openclaw", tags=["openclaw"])

# Maximum prompt length sent in the "started" SSE event.
_PROMPT_PREVIEW_LEN = 120

# Terminal task states (stop listening once reached).
_TERMINAL_STATES = frozenset({"completed", "failed", "cancelled", "error"})

# OpenClaw streaming defaults centralized in system.api.
_OC_EVENT_QUEUE_SIZE = max(1, int(config.api.openclaw_event_queue_size))
_OC_STREAM_POLL_TIMEOUT = max(0.1, float(config.api.openclaw_stream_poll_timeout))
_OC_EXECUTION_TIMEOUT = max(1.0, float(config.api.openclaw_execution_timeout))
_OC_DELEGATE_TIMEOUT = max(1.0, float(config.api.openclaw_delegate_timeout))
_OC_DELEGATE_CONNECT_TIMEOUT = max(
    0.1, float(config.api.openclaw_delegate_connect_timeout)
)


# ---------------------------------------------------------------------------
# Auth helper
# ---------------------------------------------------------------------------

def _check_auth(request: Request) -> None:
    """
    Enforce auth for OpenClaw API endpoints.

    When token is blank: allow only loopback (127.0.0.1, ::1, localhost).
    When token is set: require X-OpenClaw-Token header to match.
    """
    try:
        ext = config.extensions if isinstance(config.extensions, dict) else {}
        oc = ext.get("openclaw", {})
        token = (oc.get("token") or "").strip()
    except Exception as e:
        logger.warning("[OpenClawRoute] Auth config load failed: %s", e)
        token = ""

    if not token:
        host = ""
        if request and request.client:
            host = getattr(request.client, "host", "") or ""
        try:
            if ipaddress.ip_address(host).is_loopback:
                return
        except ValueError as exc:
            logger.debug("[OpenClawRoute] Non-IP request host in auth check: %s (%s)", host, exc)
            if host == "localhost":
                return
        logger.warning(
            "[OpenClawRoute] Auth rejected: non-loopback host=%s (token blank)", host
        )
        raise HTTPException(status_code=403, detail="Local access only when token is not configured")

    if request is None:
        logger.warning("[OpenClawRoute] Auth rejected: no request context (internal misconfiguration)")
        raise HTTPException(status_code=401, detail="Request context required")
    provided = request.headers.get("X-OpenClaw-Token", "").strip()
    if provided != token:
        logger.warning("[OpenClawRoute] Auth rejected: invalid or missing X-OpenClaw-Token")
        raise HTTPException(status_code=401, detail="Invalid or missing X-OpenClaw-Token")


# ---------------------------------------------------------------------------
# SSE helper
# ---------------------------------------------------------------------------

def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


# ---------------------------------------------------------------------------
# /execute  —  SSE streaming task execution
# ---------------------------------------------------------------------------

class OpenClawExecuteRequest(BaseModel):
    prompt: str
    session_key: str | None = None   # originating OpenClaw session (for logging)
    user_id: str | None = None


@router.post("/execute")
async def openclaw_execute(body: OpenClawExecuteRequest, request: Request):
    """
    Submit a task to Teaming24 and stream progress events via SSE.

    The TypeScript plugin calls this endpoint and streams the response to
    forward step-by-step progress to the OpenClaw session while waiting.
    """
    _check_auth(request)

    if not body.prompt.strip():
        logger.warning("[OpenClawRoute] Execute: prompt empty")
        raise HTTPException(status_code=400, detail="prompt must not be empty")

    # --- Resolve Gateway and TaskManager ---
    try:
        from teaming24.gateway import get_gateway
        gateway = get_gateway()
    except Exception as e:
        logger.error("[OpenClawRoute] Gateway unavailable: %s", e, exc_info=True)
        raise HTTPException(status_code=503, detail=f"Gateway unavailable: {e}") from e

    from teaming24.api.deps import get_task_manager_instance
    task_manager = get_task_manager_instance()

    # --- Create task before starting, so we have a stable task_id ---
    task = task_manager.create_task(
        prompt=body.prompt,
        user_id=body.user_id or "openclaw",
        metadata={"source": "openclaw", "session_key": body.session_key},
    )
    task_id = task.id

    # --- Queue for thread-safe event delivery from any thread ---
    event_queue: asyncio.Queue = asyncio.Queue(maxsize=_OC_EVENT_QUEUE_SIZE)
    main_loop = asyncio.get_running_loop()

    def _listener(t, event_type: str) -> None:
        if t.id != task_id:
            return
        # Guard: only call into the loop if it is still running.
        if not (main_loop and main_loop.is_running()):
            return
        def _put():
            try:
                event_queue.put_nowait({"type": event_type, "task": t})
            except asyncio.QueueFull:
                logger.debug(
                    "[OpenClawRoute] SSE queue full, dropping %s event for task %s",
                    event_type, task_id,
                )
        main_loop.call_soon_threadsafe(_put)

    task_manager.add_listener(_listener)

    # Close the race window: if the task already reached a terminal state
    # between create_task() and add_listener(), manually enqueue the final event.
    _recheck = task_manager.get_task(task_id)
    if _recheck and getattr(_recheck, "status", None):
        if _recheck.status.value in _TERMINAL_STATES:
            try:
                event_queue.put_nowait({"type": _recheck.status.value, "task": _recheck})
            except asyncio.QueueFull:
                logger.debug("[OpenClawRoute] Initial SSE queue full for task %s", task_id)
                pass

    # --- Run task execution in background ---
    async def _run():
        try:
            await gateway.execute(
                body.prompt,
                channel="openclaw",
                peer_id=body.session_key or "openclaw",
                task_id=task_id,
                metadata={"source": "openclaw"},
                skip_payment=True,      # payment gate is for AN-to-AN, not local callers
            )
        except Exception as exc:
            logger.error("[OpenClawRoute] Task %s error: %s", task_id, exc, exc_info=True)

    asyncio.create_task(_run(), name=f"oc-exec-{task_id}")

    # --- SSE generator ---
    async def _stream():
        try:
            yield _sse("started", {
                "task_id": task_id,
                "prompt": body.prompt[:_PROMPT_PREVIEW_LEN],
            })

            deadline = time.monotonic() + _OC_EXECUTION_TIMEOUT

            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    logger.warning(
                        "[OpenClawRoute] Execute task_id=%s timed out (cap=%ss)",
                        task_id,
                        _OC_EXECUTION_TIMEOUT,
                    )
                    yield _sse("failed", {"task_id": task_id, "error": "execution timeout"})
                    break

                try:
                    item = await asyncio.wait_for(
                        event_queue.get(), timeout=min(remaining, _OC_STREAM_POLL_TIMEOUT)
                    )
                except TimeoutError:
                    logger.debug("[OpenClawRoute] SSE stream keepalive timeout task_id=%s", task_id)
                    # No events for N seconds — send keepalive comment to prevent proxy drops.
                    yield ": keepalive\n\n"
                    continue

                etype = item["type"]
                t = item["task"]

                if etype == "step":
                    steps = getattr(t, "steps", None) or []
                    if steps:
                        s = steps[-1]
                        sd = s if isinstance(s, dict) else vars(s)
                        yield _sse("step", {
                            "task_id": task_id,
                            "agent": sd.get("agent") or sd.get("agent_id") or "Agent",
                            "action": sd.get("action") or sd.get("type") or "processing",
                            "content": (sd.get("content") or sd.get("output") or "")[:300],
                            "thought": (sd.get("thought") or "")[:200],
                            "step_number": sd.get("step_number") or len(steps),
                        })
                elif etype == "progress":
                    prog = getattr(t, "progress", None)
                    if prog is not None:
                        pd = prog.to_dict() if hasattr(prog, "to_dict") else (prog if isinstance(prog, dict) else vars(prog))
                        yield _sse("progress", {"task_id": task_id, "progress": pd})

                elif etype == "completed":
                    result_text = getattr(t, "result", "") or ""
                    cost_obj = getattr(t, "cost", None)
                    cost = cost_obj.to_dict() if hasattr(cost_obj, "to_dict") else (cost_obj or {})
                    yield _sse("completed", {
                        "task_id": task_id,
                        "result": result_text,
                        "cost": cost,
                    })
                    break

                elif etype in _TERMINAL_STATES:
                    # "failed", "cancelled", "error" — all treated as failure
                    error_text = getattr(t, "error", "") or etype
                    yield _sse("failed", {"task_id": task_id, "error": error_text})
                    break

                # Ignore unknown event types (e.g. "created", "started" from TaskManager)

        except asyncio.CancelledError:
            logger.debug(
                "[OpenClawRoute] Execute SSE cancelled task_id=%s (client disconnect)", task_id
            )
        finally:
            try:
                task_manager.remove_listener(_listener)
            except Exception as exc:
                logger.warning(
                    "[OpenClawRoute] Failed to remove task listener task_id=%s: %s",
                    task_id,
                    exc,
                    exc_info=True,
                )

    return StreamingResponse(
        _stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )


# ---------------------------------------------------------------------------
# /wallet  —  balance, transactions, summary
# ---------------------------------------------------------------------------

@router.get("/wallet")
async def openclaw_wallet(action: str = "balance", limit: int = 10, request: Request = None):
    """Query wallet state.

    ``action`` = ``balance`` | ``transactions`` | ``summary``
    """
    _check_auth(request)

    if action == "balance":
        return {
            "balance": round(_st.mock_balance, 6),
            "currency": config.payment.token_symbol,
            "address": _st.wallet_config.get("address", ""),
            "network": _st.wallet_config.get("network", "mock"),
            "is_configured": _st.wallet_config.get("is_configured", False),
        }

    if action == "transactions":
        # Most recent first; respect limit.
        limit = max(1, min(limit, 500))
        txs = list(reversed(_st.wallet_ledger[-limit:]))
        return {"transactions": txs, "total": len(_st.wallet_ledger)}

    if action == "summary":
        ledger = _st.wallet_ledger
        income = sum(t.get("amount", 0) for t in ledger if t.get("type") == "income")
        expense = sum(t.get("amount", 0) for t in ledger if t.get("type") == "expense")
        return {
            "balance": round(_st.mock_balance, 6),
            "total_income": round(income, 6),
            "total_expense": round(expense, 6),
            "net_profit": round(income - expense, 6),
            "transaction_count": len(ledger),
            "currency": config.payment.token_symbol,
        }

    logger.warning("[OpenClawRoute] Wallet unknown action: %s", action)
    raise HTTPException(
        status_code=400,
        detail=f"Unknown action: {action!r}. Use balance|transactions|summary",
    )


# ---------------------------------------------------------------------------
# /network  —  peers, status
# ---------------------------------------------------------------------------

def _get_network_manager_or_503():
    """Return initialized network manager from shared API state."""
    manager = _st.network_manager
    if manager is None:
        raise HTTPException(status_code=503, detail="Network manager unavailable")
    return manager


@router.get("/network")
async def openclaw_network(action: str = "peers", request: Request = None):
    """Query network state.

    action=peers: list all reachable peers (LAN + WAN + inbound).
    action=status: local node status and discovery state.
    """
    _check_auth(request)

    try:
        manager = _get_network_manager_or_503()
    except HTTPException as e:
        logger.error("[OpenClawRoute] Network manager unavailable: %s", e.detail)
        return {"error": str(e.detail), "peers": []}

    if action == "peers":
        peers = []
        try:
            for node in manager.all_reachable_nodes.values():
                nd = node.model_dump() if hasattr(node, "model_dump") else vars(node)
                peers.append({
                    "id": nd.get("id", ""),
                    "name": nd.get("name", ""),
                    # NodeInfo stores address as "ip", not "host" — fall back for compat
                    "host": nd.get("ip", nd.get("host", "")),
                    "port": nd.get("port", 0),
                    "capability": nd.get("capability", ""),
                    "capabilities": nd.get("capabilities", []),
                    "region": nd.get("region", ""),
                    "last_seen": nd.get("last_seen", 0),
                })
        except Exception as e:
            logger.warning("[OpenClawRoute] Peer list error: %s", e, exc_info=True)
        return {"peers": peers, "count": len(peers)}

    if action == "status":
        try:
            ln = manager.local_node
            return {
                "status": "online" if manager.is_running else "offline",
                "node_id": ln.id,
                "node_name": ln.name,
                "peer_count": len(manager.known_nodes),
                "is_discovering": manager.discovery.running,
                "capabilities": getattr(ln, "capabilities", []),
            }
        except Exception as e:
            logger.warning("[OpenClawRoute] Network status error: %s", e, exc_info=True)
            return {"status": "unavailable", "error": str(e)}

    logger.warning("[OpenClawRoute] Network unknown action: %s", action)
    raise HTTPException(
        status_code=400,
        detail=f"Unknown action: {action!r}. Use peers|status",
    )


# ---------------------------------------------------------------------------
# /delegate  —  forward task to a specific network peer
# ---------------------------------------------------------------------------

class DelegateRequest(BaseModel):
    prompt: str
    node_id: str
    user_id: str | None = None


@router.post("/delegate")
async def openclaw_delegate(body: DelegateRequest, request: Request):
    """
    Delegate a task to a specific Teaming24 network peer by node_id.

    Searches all reachable peers (LAN + WAN + inbound). Posts to the peer's
    POST /api/agent/execute and waits for completion (sync, configurable timeout).
    """
    _check_auth(request)

    if not body.prompt.strip():
        raise HTTPException(status_code=400, detail="prompt must not be empty")
    if not body.node_id.strip():
        raise HTTPException(status_code=400, detail="node_id must not be empty")

    try:
        manager = _get_network_manager_or_503()
    except HTTPException as e:
        logger.error("[OpenClawRoute] Delegate: network manager unavailable: %s", e.detail)
        raise

    # Find target peer (search all reachable: LAN + WAN + inbound)
    target = None
    try:
        for node in manager.all_reachable_nodes.values():
            nd = node.model_dump() if hasattr(node, "model_dump") else vars(node)
            if nd.get("id") == body.node_id or nd.get("name") == body.node_id:
                target = nd
                break
    except Exception as e:
        logger.error(
            "[OpenClawRoute] Delegate: peer lookup failed node_id=%s: %s",
            body.node_id, e, exc_info=True,
        )
        raise HTTPException(status_code=500, detail=f"Peer lookup failed: {e}") from e

    if not target:
        raise HTTPException(status_code=404, detail=f"Peer not found: {body.node_id}")

    # NodeInfo stores address as "ip", not "host" — fall back for compat
    peer_host = target.get("ip", target.get("host", ""))
    try:
        peer_port = int(target.get("port", 8000))
    except (TypeError, ValueError):
        logger.error(
            "[OpenClawRoute] Delegate: peer node_id=%s has non-numeric port=%r",
            body.node_id, target.get("port"),
        )
        raise HTTPException(status_code=500, detail="Peer has invalid port (non-numeric)") from None

    if not peer_host:
        logger.error(
            "[OpenClawRoute] Delegate: peer node_id=%s has no ip/host: %s",
            body.node_id, target,
        )
        raise HTTPException(status_code=500, detail="Peer has no reachable host (ip/host missing)")
    if peer_port <= 0 or peer_port > 65535:
        logger.error(
            "[OpenClawRoute] Delegate: peer node_id=%s has invalid port=%s",
            body.node_id, peer_port,
        )
        raise HTTPException(status_code=500, detail=f"Peer has invalid port: {peer_port}")

    peer_url = f"http://{peer_host}:{peer_port}/api/agent/execute"
    logger.info("[OpenClawRoute] Delegating to %s: %s", peer_url, body.prompt[:80])

    try:
        import aiohttp
        timeout = aiohttp.ClientTimeout(
            total=_OC_DELEGATE_TIMEOUT,
            connect=_OC_DELEGATE_CONNECT_TIMEOUT,
        )
        async with aiohttp.ClientSession(timeout=timeout) as http:
            resp = await http.post(peer_url, json={
                "task": body.prompt,
                "requester_id": body.user_id or "openclaw",
            })
            try:
                data = await resp.json()
            except Exception as json_err:
                try:
                    raw = await resp.text()
                except Exception as raw_exc:
                    logger.warning(
                        "[OpenClawRoute] Delegate failed to read peer error body from %s: %s",
                        peer_url,
                        raw_exc,
                        exc_info=True,
                    )
                    raw = ""
                raise HTTPException(
                    status_code=502,
                    detail=f"Peer returned non-JSON ({resp.status}, {type(json_err).__name__}): {raw[:200]}",
                ) from json_err
            if not resp.ok:
                detail = (
                    data.get("detail") or data.get("error")
                    if isinstance(data, dict) else f"Peer returned {resp.status}"
                )
                logger.warning(
                    "[OpenClawRoute] Delegate: peer %s returned %s: %s",
                    peer_url, resp.status, detail,
                )
                raise HTTPException(status_code=502, detail=str(detail))

        return {
            "task_id": data.get("task_id", "") if isinstance(data, dict) else "",
            "result": data.get("result", "") if isinstance(data, dict) else str(data),
            "status": data.get("status", "") if isinstance(data, dict) else "",
            "node_id": body.node_id,
            "peer_url": peer_url,
        }
    except ImportError:
        logger.error("[OpenClawRoute] Delegate: aiohttp not installed")
        raise HTTPException(
            status_code=500, detail="aiohttp not installed: pip install aiohttp>=3"
        ) from None
    except HTTPException:
        raise
    except Exception as e:
        logger.error(
            "[OpenClawRoute] Delegate failed peer_url=%s: %s", peer_url, e, exc_info=True
        )
        raise HTTPException(status_code=502, detail=f"Delegation failed: {e}") from e
