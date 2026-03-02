"""System Metrics Collection."""

from __future__ import annotations

import asyncio
import os
import time
from collections.abc import AsyncIterator
from datetime import datetime

from teaming24.runtime.types import RuntimeConfig, SysMetrics
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class MetricsCollector:
    """System metrics collector."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._start_time = time.time()

    async def snapshot(self) -> SysMetrics:
        """Get current system metrics."""
        try:
            return await self._snapshot_psutil()
        except ImportError:
            logger.debug("psutil unavailable; using fallback metrics collector")
            return await self._snapshot_fallback()

    async def _snapshot_psutil(self) -> SysMetrics:
        """Get metrics using psutil."""
        import psutil

        cpu_pct = psutil.cpu_percent(interval=0.1)
        cpu_cores = psutil.cpu_count() or 1

        mem = psutil.virtual_memory()
        mem_total_mb = mem.total // (1024 * 1024)
        mem_used_mb = mem.used // (1024 * 1024)
        mem_pct = mem.percent

        workspace = str(self.config.workspace)
        disk = psutil.disk_usage(workspace)
        disk_total_mb = disk.total // (1024 * 1024)
        disk_used_mb = disk.used // (1024 * 1024)
        disk_pct = disk.percent

        return SysMetrics(
            ts=datetime.now(),
            uptime_sec=time.time() - self._start_time,
            cpu_pct=round(cpu_pct, 1),
            cpu_cores=cpu_cores,
            mem_total_mb=mem_total_mb,
            mem_used_mb=mem_used_mb,
            mem_pct=round(mem_pct, 1),
            disk_total_mb=disk_total_mb,
            disk_used_mb=disk_used_mb,
            disk_pct=round(disk_pct, 1),
        )

    async def _snapshot_fallback(self) -> SysMetrics:
        """Get metrics from /proc filesystem."""
        cpu_pct = 0.0
        cpu_cores = os.cpu_count() or 1
        mem_total_mb = 0
        mem_used_mb = 0
        mem_pct = 0.0
        disk_total_mb = 0
        disk_used_mb = 0
        disk_pct = 0.0

        try:
            with open('/proc/meminfo') as f:
                meminfo = {}
                for line in f:
                    parts = line.split(':')
                    if len(parts) == 2:
                        key = parts[0].strip()
                        val = int(parts[1].strip().split()[0])
                        meminfo[key] = val

                total_kb = meminfo.get('MemTotal', 0)
                avail_kb = meminfo.get('MemAvailable', meminfo.get('MemFree', 0))
                used_kb = total_kb - avail_kb

                mem_total_mb = total_kb // 1024
                mem_used_mb = used_kb // 1024
                mem_pct = round(100.0 * used_kb / total_kb, 1) if total_kb else 0.0
        except Exception as e:
            logger.debug(f"Failed to read /proc/meminfo: {e}")

        try:
            stat = os.statvfs(str(self.config.workspace))
            disk_total_mb = (stat.f_blocks * stat.f_frsize) // (1024 * 1024)
            disk_free_mb = (stat.f_bavail * stat.f_frsize) // (1024 * 1024)
            disk_used_mb = disk_total_mb - disk_free_mb
            disk_pct = round(100.0 * disk_used_mb / disk_total_mb, 1) if disk_total_mb else 0.0
        except Exception as e:
            logger.debug(f"Failed to get disk stats: {e}")

        return SysMetrics(
            ts=datetime.now(),
            uptime_sec=time.time() - self._start_time,
            cpu_pct=cpu_pct,
            cpu_cores=cpu_cores,
            mem_total_mb=mem_total_mb,
            mem_used_mb=mem_used_mb,
            mem_pct=mem_pct,
            disk_total_mb=disk_total_mb,
            disk_used_mb=disk_used_mb,
            disk_pct=disk_pct,
        )

    async def stream(
        self,
        interval: float = 1.0,
        count: int | None = None,
    ) -> AsyncIterator[SysMetrics]:
        """Stream metrics at regular intervals."""
        n = 0
        while count is None or n < count:
            yield await self.snapshot()
            n += 1
            await asyncio.sleep(interval)


__all__ = ["MetricsCollector"]
