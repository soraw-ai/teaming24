"""
API route modules — plug-and-play domain-specific endpoint groups.

Each file in this package defines a ``fastapi.APIRouter`` for a single
domain (health, config, database, wallet, scheduler, gateway, etc.).
Route modules are self-contained and registered in ``server.py`` via
``app.include_router()``.

Adding a new route module::

    1. Create ``teaming24/api/routes/my_domain.py``
    2. Define: ``router = APIRouter()``
    3. Add endpoints: ``@router.get("/api/my_domain/...")``
    4. In ``server.py``, add::

           from teaming24.api.routes.my_domain import router as _my_router
           app.include_router(_my_router)

Naming conventions
-----------------
- Use lowercase filenames with underscores (e.g., ``my_domain.py``).
- Import routers with a leading underscore alias (e.g., ``_my_router``)
  to avoid polluting the server namespace.
- Use ``tags=["domain"]`` on the router for OpenAPI grouping.

Available modules
-----------------
- **health.py** — GET /api, /api/health, /api/config, /api/config/reload, /api/docs
- **config.py** — Agent tools, channels, framework, memory (GET/POST)
- **db.py** — CRUD for /api/db/settings, /api/db/tasks, /api/db/chat, etc.
- **wallet.py** — GET /api/wallet/balance, /api/wallet/config, /api/payment/*
- **scheduler.py** — GET/POST/DELETE /api/scheduler/jobs, start, stop
- **gateway.py** — GET /api/gateway/status, POST execute, restart
"""
