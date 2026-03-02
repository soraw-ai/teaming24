"""Streaming Monitor for Real-time Operations."""

from __future__ import annotations

import asyncio
import base64
import time
from collections.abc import AsyncGenerator, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class StreamEventType(Enum):
    """Types of streaming events."""
    SCREENSHOT = "screenshot"
    SHELL_OUTPUT = "shell_output"
    SHELL_ERROR = "shell_error"
    BROWSER_ACTION = "browser_action"
    FILE_CHANGE = "file_change"
    METRICS = "metrics"
    ERROR = "error"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"


@dataclass
class StreamEvent:
    """A streaming event from the sandbox."""
    type: StreamEventType
    timestamp: float = field(default_factory=time.time)
    data: Any = None
    sandbox_id: str | None = None

    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "timestamp": self.timestamp,
            "data": self.data,
            "sandbox_id": self.sandbox_id,
        }


@dataclass
class Screenshot:
    """Browser screenshot data."""
    data: bytes
    format: str = "png"
    width: int = 0
    height: int = 0
    timestamp: float = field(default_factory=time.time)

    @property
    def base64(self) -> str:
        return base64.b64encode(self.data).decode("utf-8")

    def to_dict(self) -> dict:
        return {
            "data": self.base64,
            "format": self.format,
            "width": self.width,
            "height": self.height,
            "timestamp": self.timestamp,
        }


class ScreenshotStreamer:
    """Stream browser screenshots at regular intervals."""

    def __init__(
        self,
        sandbox: Any,
        fps: float = 1.0,
        max_errors: int = 5,
        on_error: Callable[[Exception], None] | None = None,
    ):
        self.sandbox = sandbox
        self.fps = fps
        self.interval = 1.0 / fps
        self.max_errors = max_errors
        self.on_error = on_error

        self._running = False
        self._error_count = 0
        self._last_screenshot: Screenshot | None = None

    async def stream(self) -> AsyncGenerator[Screenshot, None]:
        """Stream screenshots as an async generator."""
        self._running = True
        self._error_count = 0

        while self._running and self.sandbox.is_running:
            start_time = time.time()

            try:
                if not hasattr(self.sandbox, '_browser') or not self.sandbox._browser:
                    await asyncio.sleep(self.interval)
                    continue

                if not self.sandbox._browser.is_running:
                    await asyncio.sleep(self.interval)
                    continue

                result = await self.sandbox.screenshot()

                screenshot = Screenshot(
                    data=result.data,
                    format=result.format,
                    width=getattr(result, 'width', 0),
                    height=getattr(result, 'height', 0),
                )

                self._last_screenshot = screenshot
                self._error_count = 0

                yield screenshot

            except Exception as e:
                self._error_count += 1
                logger.warning("Screenshot stream iteration failed: %s", e, exc_info=True)

                if self.on_error:
                    self.on_error(e)

                if self._error_count >= self.max_errors:
                    break

            elapsed = time.time() - start_time
            sleep_time = max(0, self.interval - elapsed)
            if sleep_time > 0:
                await asyncio.sleep(sleep_time)

        self._running = False

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def last_screenshot(self) -> Screenshot | None:
        return self._last_screenshot


class EventStreamer:
    """Stream all sandbox events in real-time."""

    def __init__(
        self,
        sandbox: Any,
        include_screenshots: bool = True,
        screenshot_fps: float = 1.0,
        include_metrics: bool = True,
        metrics_interval: float = 5.0,
    ):
        self.sandbox = sandbox
        self.include_screenshots = include_screenshots
        self.screenshot_fps = screenshot_fps
        self.include_metrics = include_metrics
        self.metrics_interval = metrics_interval

        self._running = False
        self._listeners: list[Callable[[StreamEvent], None]] = []

    def add_listener(self, callback: Callable[[StreamEvent], None]) -> None:
        self._listeners.append(callback)

    def remove_listener(self, callback: Callable[[StreamEvent], None]) -> None:
        if callback in self._listeners:
            self._listeners.remove(callback)

    def _emit(self, event: StreamEvent) -> None:
        for listener in self._listeners:
            try:
                listener(event)
            except Exception as e:
                logger.warning(f"Event listener error: {e}")

    async def stream(self) -> AsyncGenerator[StreamEvent, None]:
        """Stream all sandbox events."""
        self._running = True

        yield StreamEvent(
            type=StreamEventType.CONNECTED,
            sandbox_id=getattr(self.sandbox, 'id', None),
        )

        tasks = []
        event_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()

        if self.include_screenshots:
            tasks.append(asyncio.create_task(self._screenshot_task(event_queue)))

        if self.include_metrics:
            tasks.append(asyncio.create_task(self._metrics_task(event_queue)))

        try:
            while self._running and self.sandbox.is_running:
                try:
                    event = await asyncio.wait_for(event_queue.get(), timeout=1.0)
                    self._emit(event)
                    yield event
                except TimeoutError:
                    logger.debug("Sandbox event stream poll timeout")
                    continue
        finally:
            for task in tasks:
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    logger.debug("Sandbox stream background task cancelled")
                    pass

            yield StreamEvent(
                type=StreamEventType.DISCONNECTED,
                sandbox_id=getattr(self.sandbox, 'id', None),
            )

        self._running = False

    async def _screenshot_task(self, queue: asyncio.Queue[StreamEvent]) -> None:
        interval = 1.0 / self.screenshot_fps
        error_count = 0
        max_errors = 10

        while self._running:
            try:
                if (hasattr(self.sandbox, '_browser') and
                    self.sandbox._browser and
                    self.sandbox._browser.is_running):

                    result = await self.sandbox.screenshot()
                    screenshot = Screenshot(data=result.data, format=result.format)

                    await queue.put(StreamEvent(
                        type=StreamEventType.SCREENSHOT,
                        data=screenshot.to_dict(),
                        sandbox_id=getattr(self.sandbox, 'id', None),
                    ))
                    error_count = 0

            except asyncio.CancelledError:
                logger.debug("Screenshot task cancelled")
                break
            except Exception as e:
                error_count += 1
                logger.warning("Screenshot task failed: %s", e, exc_info=True)
                if error_count >= max_errors:
                    await queue.put(StreamEvent(
                        type=StreamEventType.ERROR,
                        data={"message": f"Screenshot stream stopped: {e}"},
                    ))
                    break

            await asyncio.sleep(interval)

    async def _metrics_task(self, queue: asyncio.Queue[StreamEvent]) -> None:
        while self._running:
            try:
                if hasattr(self.sandbox, 'get_metrics'):
                    metrics = await self.sandbox.get_metrics()
                    await queue.put(StreamEvent(
                        type=StreamEventType.METRICS,
                        data={
                            "cpu_pct": metrics.cpu_pct,
                            "mem_pct": metrics.mem_pct,
                            "mem_used_mb": metrics.mem_used_mb,
                            "disk_pct": metrics.disk_pct,
                        },
                        sandbox_id=getattr(self.sandbox, 'id', None),
                    ))
            except asyncio.CancelledError:
                logger.debug("Metrics stream task cancelled")
                break
            except Exception as e:
                logger.debug(f"Error collecting metrics in stream: {e}")

            await asyncio.sleep(self.metrics_interval)

    def stop(self) -> None:
        self._running = False

    @property
    def is_running(self) -> bool:
        return self._running


__all__ = [
    "StreamEventType",
    "StreamEvent",
    "Screenshot",
    "ScreenshotStreamer",
    "EventStreamer",
]
