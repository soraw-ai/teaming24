"""
FastAPI application factory.

This module creates the FastAPI app, attaches middleware, mounts
sub-routers, and wires the lifespan handler.  It is the single
entry point for constructing the application instance.

Importing ``app`` from here (or calling ``create_app()``) gives you
the fully-assembled server.
"""
from __future__ import annotations

import asyncio
import importlib
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles

import teaming24.api.state as _st
from teaming24.api.deps import (
    GUI_DIST_DIR,
    config,
    get_database,
    get_output_manager,
    logger,
)
from teaming24.api.routes.config import router as config_router
from teaming24.api.routes.db import router as db_router
from teaming24.api.routes.gateway import router as gateway_router

# ---------------------------------------------------------------------------
# Route sub-modules  (each is an APIRouter)
# ---------------------------------------------------------------------------
from teaming24.api.routes.health import router as health_router
from teaming24.api.routes.scheduler import router as scheduler_router
from teaming24.api.routes.wallet import load_wallet_from_env
from teaming24.api.routes.wallet import router as wallet_router
from teaming24.utils.logger import NoisyAccessFilter
from teaming24.utils.logger import get_logger as _get_logger

# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

@asynccontextmanager
async def _app_lifespan(application: FastAPI):
    """Startup / shutdown lifecycle handler."""
    import threading

    # Suppress noisy polling endpoints from uvicorn access logs.
    # Must run here (after uvicorn initialises its own logging on startup).
    _get_logger("uvicorn.access").addFilter(NoisyAccessFilter())

    # Store the main event loop for worker threads
    threading._teaming24_main_loop = asyncio.get_running_loop()

    # Cleanup old task outputs
    try:
        removed = get_output_manager().cleanup_old_outputs()
        if removed > 0:
            logger.info(f"Startup: cleaned up {removed} old task outputs")
    except Exception as e:
        logger.warning(f"Output cleanup at startup failed: {e}")

    # Import heavy modules lazily to avoid circular deps at import time
    try:
        from teaming24.api.routes.network import (
            _refresh_local_node_advertisement,
            get_local_crew_singleton,
            get_network_manager,
        )
    except ModuleNotFoundError:
        logger.debug("api.routes.network not available in lifespan startup; falling back to api.server")
        from teaming24.api.server import (
            _refresh_local_node_advertisement,
            get_local_crew_singleton,
            get_network_manager,
        )

    manager = get_network_manager()

    # Initialize LocalCrew early for accurate capability advertisement
    try:
        crew = get_local_crew_singleton()
        if crew:
            _refresh_local_node_advertisement()
            logger.info("Local crew initialized at startup — AN advertisement up to date")
    except Exception as e:
        logger.warning(f"Could not initialize LocalCrew at startup: {e}")

    # Auto-start LAN discovery
    if config.network.auto_online:
        try:
            await manager.start()
            manager.set_discoverable(True)
            logger.info("Auto-online: LAN discovery started, node is discoverable")
        except Exception as e:
            logger.warning(f"Auto-online: failed to start LAN discovery: {e}")

    # Background tasks
    try:
        from teaming24.api.routes.network import _peer_health_loop
    except ModuleNotFoundError:
        logger.debug("api.routes.network._peer_health_loop not available; falling back to api.server")
        from teaming24.api.server import _peer_health_loop
    application.state.peer_health_task = asyncio.create_task(_peer_health_loop())

    try:
        from teaming24.api.routes.sandbox import sync_openhands_status
    except ModuleNotFoundError:
        logger.debug("api.routes.sandbox.sync_openhands_status not available; falling back to api.server")
        from teaming24.api.server import sync_openhands_status
    async def _openhands_sync_loop():
        while True:
            try:
                await sync_openhands_status()
            except Exception as e:
                logger.debug(f"OpenHands sync: {e}")
            await asyncio.sleep(5)

    application.state.openhands_sync_task = asyncio.create_task(_openhands_sync_loop())

    _frontend_built = GUI_DIST_DIR.exists() and (GUI_DIST_DIR / "index.html").exists()
    frontend_status = "serving from dist" if _frontend_built else "not built (use npm run dev)"
    logger.info("Teaming24 API started", extra={"docs": config.api.docs_enabled, "frontend": frontend_status})

    _st.subscription_manager.set_shutdown_event(_st.shutdown_event)

    # Initialize Skill Registry
    try:
        from teaming24.agent.skills import Skill as SkillModel
        from teaming24.agent.skills import get_skill_registry
        skill_registry = get_skill_registry()
        skill_registry.load()
        db = get_database()
        db_skills_raw = db.get_skills()
        db_skill_objs = [SkillModel.from_dict(s) for s in db_skills_raw]
        skill_registry.merge_db_skills(db_skill_objs)
        logger.info(f"Skill registry: {len(skill_registry)} skills loaded")
    except Exception as e:
        logger.debug(f"Skill registry init: {e}")

    # Initialize Gateway
    from teaming24.communication.websocket import get_ws_hub
    from teaming24.gateway import get_gateway
    gateway = get_gateway()
    try:
        from teaming24.api.routes.agent import get_task_manager_instance
    except ModuleNotFoundError:
        logger.debug("api.routes.agent.get_task_manager_instance not available; using api.deps")
        from teaming24.api.deps import get_task_manager_instance
    gateway.set_task_manager(get_task_manager_instance())
    gateway.set_subscription_manager(_st.subscription_manager)
    gateway.set_ws_hub(get_ws_hub())
    try:
        await gateway.start()
        logger.info("Gateway started — channels connected to agent framework")
    except Exception as e:
        logger.warning(f"Gateway startup failed: {e}")

    yield

    # === SHUTDOWN ===
    logger.info("Shutting down — signalling SSE connections to close...")

    try:
        await gateway.stop()
    except Exception as e:
        logger.debug("Gateway stop: %s", e)

    _st.shutdown_event.set()
    _st.subscription_manager.close_all()

    for q in list(_st.sandbox_list_subscribers):
        try:
            q.put_nowait(None)
        except Exception as e:
            logger.debug("Sandbox subscriber cleanup: %s", e)
    _st.sandbox_list_subscribers.clear()

    await asyncio.sleep(0.3)

    for task_name in ["peer_health_task", "openhands_sync_task"]:
        task = getattr(application.state, task_name, None)
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                logger.debug("Lifespan task cancelled during shutdown: %s", task_name)
                pass

    # Notify peers of shutdown
    import httpx

    try:
        peers = list(manager.wan_nodes.values()) + list(manager.inbound_peers.values())
        if peers:
            async with httpx.AsyncClient(timeout=config.api.health_check_http_timeout) as client:
                await asyncio.gather(
                    *[client.post(f"http://{p.ip}:{p.port}/api/network/peer-disconnect",
                                  json={"nodeId": manager.local_node.id, "reason": "shutdown"})
                      for p in peers],
                    return_exceptions=True,
                )
    except Exception as e:
        logger.debug("Peer disconnect notify: %s", e)

    if _st.network_manager:
        await _st.network_manager.stop()

    # Cleanup sandboxes
    try:
        from teaming24.api.routes.sandbox import _cleanup_sandbox_resources
    except ModuleNotFoundError:
        logger.debug("api.routes.sandbox._cleanup_sandbox_resources not available; falling back to api.server")
        from teaming24.api.server import _cleanup_sandbox_resources
    active = [sid for sid, info in _st.sandboxes.items() if not info.get("resources_released")]
    if active:
        logger.info(f"Cleaning up {len(active)} sandbox(es) on shutdown")
        await asyncio.gather(*[_cleanup_sandbox_resources(sid) for sid in active], return_exceptions=True)

    try:
        from teaming24.runtime.openhands import OPENHANDS_AVAILABLE, cleanup_all_openhands
        if OPENHANDS_AVAILABLE:
            await cleanup_all_openhands()
    except (ImportError, Exception) as e:
        logger.debug("OpenHands shutdown cleanup: %s", e)

    logger.info("Teaming24 API stopped")


# ---------------------------------------------------------------------------
# Build the app
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    """Create and return the fully-configured FastAPI application."""
    application = FastAPI(
        title="Teaming24 API",
        description="Backend API for Teaming24 multi-agent collaboration platform",
        version="0.1.0",
        docs_url="/docs" if config.api.docs_enabled else None,
        redoc_url="/redoc" if config.api.docs_enabled else None,
    )

    application.add_middleware(
        CORSMiddleware,
        allow_origins=config.cors.allow_origins,
        allow_credentials=config.cors.allow_credentials,
        allow_methods=config.cors.allow_methods,
        allow_headers=config.cors.allow_headers,
    )

    # Mount WebSocket
    from teaming24.communication.websocket import mount_websocket
    mount_websocket(application)

    # Include route sub-modules
    application.include_router(health_router)
    application.include_router(scheduler_router)
    application.include_router(gateway_router)
    application.include_router(config_router)
    application.include_router(db_router)
    application.include_router(wallet_router)

    # Optional/heavy routers
    for module_path in (
        "teaming24.api.routes.sandbox",
        "teaming24.api.routes.network",
        "teaming24.api.routes.agent",
        "teaming24.api.routes.chat",
        "teaming24.api.routes.skills",
    ):
        try:
            module = importlib.import_module(module_path)
            router = getattr(module, "router", None)
            if router is not None:
                application.include_router(router)
        except ModuleNotFoundError as exc:
            if exc.name == module_path:
                logger.debug("Optional router not installed: %s", module_path)
            else:
                logger.warning("Failed importing optional router %s: %s", module_path, exc)
        except Exception as exc:
            logger.warning("Skipping optional router %s: %s", module_path, exc)

    # Wallet env init
    load_wallet_from_env()

    # Frontend static files
    _frontend_built = GUI_DIST_DIR.exists() and (GUI_DIST_DIR / "index.html").exists()
    if _frontend_built:
        application.mount("/assets", StaticFiles(directory=GUI_DIST_DIR / "assets"), name="assets")

    @application.get("/")
    async def serve_root():
        if _frontend_built:
            return FileResponse(GUI_DIST_DIR / "index.html")
        return HTMLResponse(content=_FALLBACK_HTML)

    if _frontend_built:
        @application.get("/{path:path}")
        async def serve_spa(path: str):
            from fastapi import HTTPException
            if path.startswith("api") or path in ("docs", "redoc", "openapi.json"):
                raise HTTPException(status_code=404)
            file_path = GUI_DIST_DIR / path
            if file_path.exists() and file_path.is_file():
                return FileResponse(file_path)
            return FileResponse(GUI_DIST_DIR / "index.html")

    application.router.lifespan_context = _app_lifespan
    return application


_FALLBACK_HTML = """<!DOCTYPE html>
<html>
<head><title>Teaming24</title><meta charset="utf-8"></head>
<body style="font-family: system-ui; padding: 40px; max-width: 600px; margin: 0 auto; background: #1a1a2e; color: #eee;">
    <h1>Teaming24 API Server</h1>
    <p style="color: #0f0;">API is running!</p>
    <h3>Frontend Setup</h3>
    <pre style="background: #16213e; padding: 15px; border-radius: 5px;">cd teaming24/gui && npm install && npm run build</pre>
    <h3>API Endpoints</h3>
    <ul>
        <li><a href="/docs" style="color: #4fc3f7;">/docs</a></li>
        <li><a href="/api/health" style="color: #4fc3f7;">/api/health</a></li>
    </ul>
</body>
</html>"""


# Default ASGI app export for `uvicorn teaming24.api.app:app`
app = create_app()
