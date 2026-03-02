"""
Scheduler (cron) API endpoints.

This module provides CRUD for scheduled jobs and lifecycle control of the
scheduler. Jobs can use cron expressions or interval_seconds. The router
uses prefix ``/api/scheduler``.

Endpoints
---------
- GET /api/scheduler/jobs — List all scheduled jobs
- POST /api/scheduler/jobs — Add job (name, prompt, cron, interval_seconds, agent_id)
- DELETE /api/scheduler/jobs/{job_id} — Remove job
- GET /api/scheduler/status — Scheduler running state
- POST /api/scheduler/start — Start scheduler
- POST /api/scheduler/stop — Stop scheduler

Dependencies
------------
Uses ``teaming24.scheduler.get_scheduler()`` for all operations.
No deps.py or state.py usage.

Extending
---------
Add new endpoints with ``@router.get(...)`` or ``@router.post(...)``.
Paths are relative to the router prefix, so ``/jobs`` becomes
``/api/scheduler/jobs``.
"""
from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel

router = APIRouter(prefix="/api/scheduler", tags=["scheduler"])


class ScheduleJobRequest(BaseModel):
    name: str
    prompt: str
    cron: str = ""
    interval_seconds: int = 0
    agent_id: str = "main"


@router.get("/jobs")
async def list_scheduled_jobs():
    from teaming24.scheduler import get_scheduler
    jobs = get_scheduler().list_jobs()
    return JSONResponse(content=[{
        "id": j.id, "name": j.name, "prompt": j.prompt,
        "cron": j.cron, "interval_seconds": j.interval_seconds,
        "enabled": j.enabled, "last_status": j.last_status,
        "error_count": j.error_count,
    } for j in jobs])


@router.post("/jobs")
async def add_scheduled_job(req: ScheduleJobRequest):
    from teaming24.scheduler import get_scheduler
    job = get_scheduler().add_job(
        name=req.name, prompt=req.prompt,
        cron=req.cron, interval_seconds=req.interval_seconds,
        agent_id=req.agent_id,
    )
    return JSONResponse(content={"id": job.id, "name": job.name, "status": "added"})


@router.delete("/jobs/{job_id}")
async def remove_scheduled_job(job_id: str):
    from teaming24.scheduler import get_scheduler
    ok = get_scheduler().remove_job(job_id)
    if not ok:
        return JSONResponse(status_code=404, content={"error": "job not found"})
    return JSONResponse(content={"status": "removed"})


@router.get("/status")
async def scheduler_status():
    from teaming24.scheduler import get_scheduler
    return JSONResponse(content={"running": get_scheduler()._running})


@router.post("/start")
async def start_scheduler():
    from teaming24.scheduler import get_scheduler
    await get_scheduler().start()
    return JSONResponse(content={"status": "started"})


@router.post("/stop")
async def stop_scheduler():
    from teaming24.scheduler import get_scheduler
    await get_scheduler().stop()
    return JSONResponse(content={"status": "stopped"})
