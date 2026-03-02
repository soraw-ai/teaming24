"""Teaming24 Browser Automation using Playwright."""

from __future__ import annotations

from typing import Any

from teaming24.runtime.types import (
    TEAMING24_USER_AGENT,
    BrowserError,
    BrowserType,
    ElementInfo,
    PageInfo,
    RuntimeConfig,
    ScreenshotResult,
)
from teaming24.utils.logger import get_logger

logger = get_logger(__name__)


class BrowserManager:
    """Browser automation manager using Playwright."""

    def __init__(self, config: RuntimeConfig):
        self.config = config
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._started = False

    async def start(self) -> BrowserManager:
        """Initialize browser instance."""
        if self._started:
            return self

        try:
            from playwright.async_api import async_playwright
        except ImportError as e:
            raise BrowserError(
                "Playwright not installed. Run: uv add playwright && playwright install"
            ) from e

        self._playwright = await async_playwright().start()

        browser_types = {
            BrowserType.CHROMIUM: self._playwright.chromium,
            BrowserType.FIREFOX: self._playwright.firefox,
            BrowserType.WEBKIT: self._playwright.webkit,
        }
        browser_launcher = browser_types.get(
            self.config.browser_type,
            self._playwright.chromium
        )

        launch_args = {"headless": self.config.browser_headless}
        if self.config.proxy_url:
            launch_args["proxy"] = {"server": self.config.proxy_url}

        self._browser = await browser_launcher.launch(**launch_args)
        self._context = await self._browser.new_context(
            viewport={"width": 1280, "height": 720},
            locale="en-US",
            user_agent=f"{TEAMING24_USER_AGENT} Playwright",
        )
        self._context.set_default_timeout(self.config.browser_timeout * 1000)
        self._page = await self._context.new_page()
        self._started = True

        logger.info("Browser started", extra={"type": self.config.browser_type.value})
        return self

    async def stop(self) -> None:
        """Close browser and cleanup."""
        if self._context:
            await self._context.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            await self._playwright.stop()

        self._page = None
        self._context = None
        self._browser = None
        self._playwright = None
        self._started = False

    async def goto(
        self,
        url: str,
        wait_until: str = "load",
        timeout: float | None = None,
    ) -> PageInfo:
        """Navigate to URL."""
        self._ensure_started()

        await self._page.goto(
            url,
            wait_until=wait_until,
            timeout=(timeout or self.config.browser_timeout) * 1000,
        )

        return await self.get_page_info()

    async def get_page_info(self) -> PageInfo:
        """Get current page information."""
        self._ensure_started()

        viewport = self._page.viewport_size or {"width": 1280, "height": 720}
        return PageInfo(
            url=self._page.url,
            title=await self._page.title(),
            viewport_width=viewport["width"],
            viewport_height=viewport["height"],
        )

    async def screenshot(
        self,
        selector: str | None = None,
        full_page: bool = False,
        format: str = "png",
    ) -> ScreenshotResult:
        """Take screenshot."""
        self._ensure_started()

        opts = {"type": format, "full_page": full_page}

        if selector:
            element = await self._page.query_selector(selector)
            if not element:
                raise BrowserError(f"Element not found: {selector}")
            data = await element.screenshot(**opts)
        else:
            data = await self._page.screenshot(**opts)

        return ScreenshotResult(data=data, format=format)

    async def click(
        self,
        selector: str,
        button: str = "left",
        click_count: int = 1,
        timeout: float | None = None,
    ) -> None:
        """Click on element."""
        self._ensure_started()
        await self._page.click(
            selector,
            button=button,
            click_count=click_count,
            timeout=(timeout or self.config.browser_timeout) * 1000,
        )

    async def type(
        self,
        selector: str,
        text: str,
        delay: float = 0,
        timeout: float | None = None,
    ) -> None:
        """Type text into element."""
        self._ensure_started()
        await self._page.type(
            selector,
            text,
            delay=delay,
            timeout=(timeout or self.config.browser_timeout) * 1000,
        )

    async def fill(self, selector: str, value: str, timeout: float | None = None) -> None:
        """Fill input field."""
        self._ensure_started()
        await self._page.fill(
            selector,
            value,
            timeout=(timeout or self.config.browser_timeout) * 1000,
        )

    async def select(
        self,
        selector: str,
        value: str | list[str],
        timeout: float | None = None,
    ) -> list[str]:
        """Select option(s) in dropdown."""
        self._ensure_started()
        values = [value] if isinstance(value, str) else value
        return await self._page.select_option(
            selector,
            value=values,
            timeout=(timeout or self.config.browser_timeout) * 1000,
        )

    async def scroll(self, x: int = 0, y: int = 0, selector: str | None = None) -> None:
        """Scroll page or element."""
        self._ensure_started()
        if selector:
            await self._page.eval_on_selector(selector, f"el => el.scrollBy({x}, {y})")
        else:
            await self._page.evaluate(f"window.scrollBy({x}, {y})")

    async def wait_for(
        self,
        selector: str,
        state: str = "visible",
        timeout: float | None = None,
    ) -> None:
        """Wait for element."""
        self._ensure_started()
        await self._page.wait_for_selector(
            selector,
            state=state,
            timeout=(timeout or self.config.browser_timeout) * 1000,
        )

    async def evaluate(self, expression: str) -> Any:
        """Execute JavaScript."""
        self._ensure_started()
        return await self._page.evaluate(expression)

    async def get_content(self) -> str:
        """Get page HTML."""
        self._ensure_started()
        return await self._page.content()

    async def get_text(self, selector: str) -> str:
        """Get element text."""
        self._ensure_started()
        return await self._page.text_content(selector) or ""

    async def get_attribute(self, selector: str, name: str) -> str | None:
        """Get element attribute."""
        self._ensure_started()
        return await self._page.get_attribute(selector, name)

    async def query_selector(self, selector: str) -> ElementInfo | None:
        """Query single element."""
        self._ensure_started()

        element = await self._page.query_selector(selector)
        if not element:
            return None

        tag = await element.evaluate("el => el.tagName.toLowerCase()")
        text = await element.text_content() or ""
        bbox = await element.bounding_box()
        visible = await element.is_visible()

        return ElementInfo(
            selector=selector,
            tag=tag,
            text=text.strip(),
            visible=visible,
            bbox=bbox,
        )

    async def query_all(self, selector: str) -> list[ElementInfo]:
        """Query multiple elements."""
        self._ensure_started()

        elements = await self._page.query_selector_all(selector)
        results = []

        for i, element in enumerate(elements):
            tag = await element.evaluate("el => el.tagName.toLowerCase()")
            text = await element.text_content() or ""
            bbox = await element.bounding_box()
            visible = await element.is_visible()

            results.append(ElementInfo(
                selector=f"{selector}:nth-of-type({i+1})",
                tag=tag,
                text=text.strip(),
                visible=visible,
                bbox=bbox,
            ))

        return results

    async def press(self, key: str) -> None:
        """Press keyboard key."""
        self._ensure_started()
        await self._page.keyboard.press(key)

    async def go_back(self) -> None:
        """Navigate back."""
        self._ensure_started()
        await self._page.go_back()

    async def go_forward(self) -> None:
        """Navigate forward."""
        self._ensure_started()
        await self._page.go_forward()

    async def reload(self) -> None:
        """Reload page."""
        self._ensure_started()
        await self._page.reload()

    async def pdf(self, path: str | None = None) -> bytes:
        """Generate PDF (Chromium only)."""
        self._ensure_started()
        if self.config.browser_type != BrowserType.CHROMIUM:
            raise BrowserError("PDF generation only supported in Chromium")
        return await self._page.pdf(path=path)

    async def new_page(self) -> None:
        """Create new page."""
        self._ensure_started()
        self._page = await self._context.new_page()

    async def close_page(self) -> None:
        """Close current page."""
        self._ensure_started()
        pages = self._context.pages
        if len(pages) > 1:
            await self._page.close()
            self._page = pages[-1] if pages else await self._context.new_page()

    @property
    def is_running(self) -> bool:
        return self._started

    @property
    def current_url(self) -> str:
        return self._page.url if self._page else ""

    def _ensure_started(self) -> None:
        if not self._started:
            raise BrowserError("Browser not started")


__all__ = ["BrowserManager"]
