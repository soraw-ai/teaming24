"""
Task scheduler service.

Uses APScheduler when available; falls back to a minimal asyncio-based
scheduler for basic interval/cron support.

Each job:
  1. Creates a task via create_local_crew().
  2. Runs it in an isolated session.
  3. Optionally delivers the result (webhook, channel, log).
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from typing import Any

from teaming24.utils.ids import prefixed_id, random_hex
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)

_HAS_APSCHEDULER = False
try:
    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger
    from apscheduler.triggers.interval import IntervalTrigger
    _HAS_APSCHEDULER = True
except ImportError as e:
    logger.debug("APScheduler not installed: %s", e)


@dataclass
class ScheduledJob:
    """Definition of a scheduled agent task."""
    id: str = ""
    name: str = ""
    prompt: str = ""
    cron: str = ""                # cron expression (e.g. "0 9 * * *")
    interval_seconds: int = 0    # alternative: run every N seconds
    agent_id: str = "main"
    enabled: bool = True
    last_run: float = 0.0
    last_status: str = ""
    error_count: int = 0
    max_errors: int = 5          # auto-disable after this many consecutive errors
    metadata: dict[str, Any] = field(default_factory=dict)


class TaskScheduler:
    """Manages scheduled agent task execution."""

    def __init__(self):
        self._jobs: dict[str, ScheduledJob] = {}
        self._scheduler = None
        self._running = False

        if _HAS_APSCHEDULER:
            self._scheduler = AsyncIOScheduler()
            logger.info("[Scheduler] APScheduler available")
        else:
            logger.info("[Scheduler] APScheduler not installed — using basic fallback")

    # ----- Job management -------------------------------------------------

    def add_job(
        self,
        name: str,
        prompt: str,
        cron: str = "",
        interval_seconds: int = 0,
        agent_id: str = "main",
        **metadata,
    ) -> ScheduledJob:
        """Add a new scheduled job."""
        job = ScheduledJob(
            id=prefixed_id("job_", 8, separator=""),
            name=name,
            prompt=prompt,
            cron=cron,
            interval_seconds=interval_seconds,
            agent_id=agent_id,
            metadata=metadata,
        )
        self._jobs[job.id] = job

        if self._scheduler and self._running:
            self._register_apscheduler_job(job)

        logger.info("[Scheduler] added job: %s (%s)", name, job.id)
        return job

    def remove_job(self, job_id: str) -> bool:
        job = self._jobs.pop(job_id, None)
        if job is None:
            return False
        if self._scheduler:
            try:
                self._scheduler.remove_job(job_id)
            except Exception as e:
                logger.debug("[Scheduler] remove_job: %s", e)
        logger.info("[Scheduler] removed job: %s", job_id)
        return True

    def list_jobs(self) -> list[ScheduledJob]:
        return list(self._jobs.values())

    def get_job(self, job_id: str) -> ScheduledJob | None:
        return self._jobs.get(job_id)

    # ----- Lifecycle ------------------------------------------------------

    async def start(self) -> None:
        """Start the scheduler."""
        if self._running:
            return
        self._running = True

        if self._scheduler:
            for job in self._jobs.values():
                if job.enabled:
                    self._register_apscheduler_job(job)
            self._scheduler.start()
            logger.info("[Scheduler] started with %d jobs", len(self._jobs))
        else:
            asyncio.create_task(self._fallback_loop())
            logger.info("[Scheduler] fallback loop started with %d jobs", len(self._jobs))

    async def stop(self) -> None:
        self._running = False
        if self._scheduler:
            self._scheduler.shutdown(wait=False)
        logger.info("[Scheduler] stopped")

    # ----- APScheduler integration ----------------------------------------

    def _register_apscheduler_job(self, job: ScheduledJob) -> None:
        if not self._scheduler:
            return
        trigger = None
        if job.cron:
            trigger = CronTrigger.from_crontab(job.cron)
        elif job.interval_seconds > 0:
            trigger = IntervalTrigger(seconds=job.interval_seconds)
        else:
            logger.warning("[Scheduler] job %s has no schedule, skipping", job.id)
            return

        self._scheduler.add_job(
            self._execute_job,
            trigger=trigger,
            args=[job.id],
            id=job.id,
            replace_existing=True,
        )

    # ----- Fallback loop (no APScheduler) ---------------------------------

    async def _fallback_loop(self) -> None:
        """Simple interval-based execution loop for when APScheduler is absent."""
        while self._running:
            now = time.time()
            for job in list(self._jobs.values()):
                if not job.enabled:
                    continue
                if job.interval_seconds <= 0:
                    continue
                if now - job.last_run >= job.interval_seconds:
                    asyncio.create_task(self._execute_job(job.id))
            await asyncio.sleep(10)

    # ----- Job execution --------------------------------------------------

    async def _execute_job(self, job_id: str) -> None:
        """Execute a single scheduled job."""
        job = self._jobs.get(job_id)
        if not job or not job.enabled:
            return

        logger.info("[Scheduler] executing job: %s (%s)", job.name, job.id)
        job.last_run = time.time()

        try:
            from teaming24.agent import create_local_crew
            crew = create_local_crew()
            task_id = f"sched-{job.id}-{random_hex(6)}"
            result = await crew.execute(job.prompt, task_id)

            job.last_status = "success"
            job.error_count = 0

            result_text = result.get("result", "") if isinstance(result, dict) else str(result)
            logger.info("[Scheduler] job %s completed: %s...", job.name, result_text[:200])

            # Fire hook
            try:
                from teaming24.plugins.hooks import get_hook_registry
                await get_hook_registry().fire(
                    "after_task_execute", task_id=task_id, result=result_text,
                )
            except Exception as e:
                logger.debug("[Scheduler] after_task_execute hook: %s", e)

        except Exception as exc:
            job.last_status = f"error: {exc}"
            job.error_count += 1
            logger.error("[Scheduler] job %s failed: %s", job.name, exc)

            if job.error_count >= job.max_errors:
                job.enabled = False
                logger.warning(
                    "[Scheduler] job %s auto-disabled after %d consecutive errors",
                    job.name, job.error_count,
                )


# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

_scheduler: TaskScheduler | None = None


def get_scheduler() -> TaskScheduler:
    """Return the global TaskScheduler singleton."""
    global _scheduler
    if _scheduler is None:
        _scheduler = TaskScheduler()
    return _scheduler
