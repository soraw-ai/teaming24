"""
Cron/scheduled task system for Teaming24.

Runs agent tasks on a schedule using APScheduler (or a built-in
fallback).  Each job creates a task through the normal Organizer
flow so all routing, payment, and session logic applies.

Usage:
    from teaming24.scheduler import TaskScheduler, get_scheduler
    sched = get_scheduler()
    sched.add_job("daily-report", cron="0 9 * * *", prompt="Generate daily report")
    sched.start()
"""

from teaming24.scheduler.service import ScheduledJob, TaskScheduler, get_scheduler

__all__ = [
    "ScheduledJob",
    "TaskScheduler",
    "get_scheduler",
]
