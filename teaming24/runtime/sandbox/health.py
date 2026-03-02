"""Health Check and Lifecycle Management."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from teaming24.runtime.types import (
    HealthStatus,
    ReadyTimeout,
    RuntimeConfig,
    RuntimeError,
    SandboxState,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


# Valid state transitions
_TRANSITIONS: dict[SandboxState, set[SandboxState]] = {
    SandboxState.INIT: {SandboxState.RUNNING, SandboxState.ERROR},
    SandboxState.RUNNING: {SandboxState.PAUSED, SandboxState.STOPPING, SandboxState.ERROR},
    SandboxState.PAUSED: {SandboxState.RUNNING, SandboxState.STOPPING, SandboxState.ERROR},
    SandboxState.STOPPING: {SandboxState.STOPPED, SandboxState.ERROR},
    SandboxState.STOPPED: set(),
    SandboxState.ERROR: {SandboxState.STOPPED},
}


class HealthError(RuntimeError):
    """Health check failed."""
    pass


HealthCheck = Callable[[], Awaitable[bool]]


class HealthManager:
    """Health check and lifecycle management."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._state = SandboxState.INIT
        self._created = datetime.now()
        self._expires: datetime | None = None
        self._custom_check: HealthCheck | None = None

    @property
    def state(self) -> SandboxState:
        return self._state

    @state.setter
    def state(self, value: SandboxState) -> None:
        old = self._state
        self._state = value
        logger.debug("State changed", extra={"from": old.value, "to": value.value})

    def transition(self, target: SandboxState) -> SandboxState:
        """Transition to new state with validation."""
        allowed = _TRANSITIONS.get(self._state, set())
        if target not in allowed:
            raise RuntimeError(f"Invalid transition: {self._state.value} -> {target.value}")

        old = self._state
        self._state = target
        logger.info("State transition", extra={"from": old.value, "to": target.value})
        return target

    @property
    def expires_at(self) -> datetime | None:
        return self._expires

    def set_ttl(self, duration: timedelta) -> datetime:
        """Set time-to-live."""
        self._expires = datetime.now() + duration
        return self._expires

    def renew(self, duration: timedelta) -> datetime:
        """Extend expiration time."""
        self._expires = datetime.now() + duration
        return self._expires

    def is_expired(self) -> bool:
        if self._expires is None:
            return False
        return datetime.now() > self._expires

    async def ping(self) -> bool:
        """Basic health check."""
        return self._state == SandboxState.RUNNING

    async def check(self, custom: HealthCheck | None = None) -> HealthStatus:
        """Run health check."""
        start = datetime.now()
        check_fn = custom or self._custom_check or self.ping

        try:
            ok = await check_fn()
            latency = (datetime.now() - start).total_seconds() * 1000

            return HealthStatus(
                ok=ok,
                state=self._state,
                message="OK" if ok else "Health check failed",
                ts=datetime.now(),
                latency_ms=round(latency, 2),
            )
        except Exception as e:
            logger.warning(f"Health check failed: {e}")
            latency = (datetime.now() - start).total_seconds() * 1000
            return HealthStatus(
                ok=False,
                state=self._state,
                message=f"Error: {e}",
                ts=datetime.now(),
                latency_ms=round(latency, 2),
            )

    async def wait_ready(
        self,
        timeout: float = 30.0,
        interval: float = 0.5,
        check: HealthCheck | None = None,
    ) -> HealthStatus:
        """Wait for sandbox to become ready."""
        deadline = datetime.now() + timedelta(seconds=timeout)
        attempts = 0
        last: HealthStatus | None = None

        while datetime.now() < deadline:
            attempts += 1
            status = await self.check(check)
            last = status

            if status.ok:
                logger.info("Sandbox ready", extra={"attempts": attempts})
                return status

            await asyncio.sleep(interval)

        msg = last.message if last else "Unknown"
        raise ReadyTimeout(f"Not ready after {timeout}s ({attempts} attempts): {msg}")

    def set_custom_check(self, check: HealthCheck) -> None:
        """Set custom health check function."""
        self._custom_check = check


@dataclass
class LifecycleConfig:
    """Lifecycle configuration options."""
    ready_timeout: float = 30.0
    ready_interval: float = 0.5
    shutdown_timeout: float = 10.0
    default_ttl: timedelta | None = None


__all__ = ["HealthManager", "HealthError", "LifecycleConfig"]
