"""Lanza o se conecta a un Chrome/Edge con perfil dedicado de Diogenes (nunca el perfil personal) por CDP.

La depuración remota va solo en 127.0.0.1. El perfil vive en `data/browser-profile` (ignorado por Git).
Código específico de Windows: los tests usan un navegador falso (ver tests/test_browser.py).
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

DEFAULT_PORT = 9222
DEFAULT_PROFILE = Path("data/browser-profile")
_CANDIDATES = {
    "chrome": [r"%ProgramFiles%\Google\Chrome\Application\chrome.exe", r"%ProgramFiles(x86)%\Google\Chrome\Application\chrome.exe", r"%LocalAppData%\Google\Chrome\Application\chrome.exe"],
    "edge": [r"%ProgramFiles(x86)%\Microsoft\Edge\Application\msedge.exe", r"%ProgramFiles%\Microsoft\Edge\Application\msedge.exe"],
}  # fmt: skip


def find_browser(preferred: str = "chrome") -> Path | None:
    order = [preferred, *(k for k in _CANDIDATES if k != preferred)]
    for kind in order:
        for template in _CANDIDATES.get(kind, []):
            path = Path(os.path.expandvars(template))
            if path.is_file():
                return path
    return None


def launch_args(executable: Path, profile: Path, port: int) -> list[str]:
    """Perfil propio + depuración remota solo en loopback. Sin `--remote-allow-origins=*`."""
    return [
        str(executable),
        f"--remote-debugging-port={port}",
        "--remote-debugging-address=127.0.0.1",
        f"--user-data-dir={profile.resolve()}",
        "--no-first-run",
        "--no-default-browser-check",
    ]


class PlaywrightPage:
    """Adaptador de una pestaña de Playwright a `BrowserPage`. Sigue a la pestaña más reciente (clics que abren otra)."""

    def __init__(self, context: BrowserContext, page: Page) -> None:
        self._context = context
        self._page = page
        context.on("page", self._adopt)

    def _adopt(self, page: Page) -> None:
        self._page = page

    async def _live(self) -> Page:
        if self._page.is_closed():
            pages = self._context.pages
            self._page = pages[-1] if pages else await self._context.new_page()
        return self._page

    async def goto(self, url: str) -> None:
        page = await self._live()
        await page.goto(url, wait_until="domcontentloaded", timeout=20_000)

    async def current(self) -> tuple[str, str]:
        page = await self._live()
        return page.url, await page.title()

    async def aria(self) -> str:
        page = await self._live()
        await page.wait_for_load_state("domcontentloaded")
        return await page.locator("body").aria_snapshot(mode="ai", timeout=8_000)

    async def click(self, ref: str) -> None:
        page = await self._live()
        await page.locator(f"aria-ref={ref}").click(timeout=5_000)
        await self._settle()

    async def fill(self, ref: str, text: str, enter: bool) -> None:
        page = await self._live()
        target = page.locator(f"aria-ref={ref}")
        await target.fill(text, timeout=5_000)
        if enter:
            await target.press("Enter")
        await self._settle()

    async def is_sensitive(self, ref: str) -> bool:
        page = await self._live()
        script = (
            "e => e.type === 'password' || /^cc-|one-time-code/.test(e.autocomplete || '')"
            " || /card|cvv|cvc|iban/i.test((e.name || '') + (e.id || ''))"
        )
        try:
            return bool(await page.locator(f"aria-ref={ref}").evaluate(script, timeout=3_000))
        except Exception:  # noqa: BLE001
            return True  # ante la duda, no se escribe

    async def is_multiline(self, ref: str) -> bool:
        page = await self._live()
        script = "e => e.tagName === 'TEXTAREA' || e.isContentEditable"
        try:
            return bool(await page.locator(f"aria-ref={ref}").evaluate(script, timeout=3_000))
        except Exception:  # noqa: BLE001
            return False

    async def back(self) -> None:
        page = await self._live()
        await page.go_back(wait_until="domcontentloaded", timeout=10_000)

    async def _settle(self) -> None:
        page = await self._live()
        with contextlib.suppress(Exception):
            await page.wait_for_load_state("domcontentloaded", timeout=5_000)
        await asyncio.sleep(0.4)


class BrowserSession:
    def __init__(self, preferred: str = "chrome", profile: Path = DEFAULT_PROFILE, port: int = DEFAULT_PORT) -> None:
        self._preferred = preferred
        self._profile = profile
        self._port = port
        self._pw: Playwright | None = None
        self._browser: Browser | None = None
        self._page: PlaywrightPage | None = None
        self._proc: subprocess.Popen[bytes] | None = None
        self._lock = asyncio.Lock()

    @property
    def _endpoint(self) -> str:
        return f"http://127.0.0.1:{self._port}"

    async def page(self) -> PlaywrightPage:
        async with self._lock:
            if self._page is not None and self._browser is not None and self._browser.is_connected():
                return self._page
            self._pw = self._pw or await async_playwright().start()
            if not await self._alive():
                self._start()
                await self._wait_alive()
            self._browser = await self._pw.chromium.connect_over_cdp(self._endpoint)
            context = self._browser.contexts[0] if self._browser.contexts else await self._browser.new_context()
            page = context.pages[0] if context.pages else await context.new_page()
            self._page = PlaywrightPage(context, page)
            return self._page

    async def _alive(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=1.0) as client:
                return (await client.get(f"{self._endpoint}/json/version")).status_code == 200
        except httpx.HTTPError:
            return False

    def _start(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Lanzar el navegador solo está soportado en Windows; conecta uno ya abierto por CDP.")
        executable = find_browser(self._preferred)
        if executable is None:
            raise RuntimeError("No encuentro Chrome ni Edge instalados.")
        self._profile.mkdir(parents=True, exist_ok=True)
        self._proc = subprocess.Popen(launch_args(executable, self._profile, self._port))

    async def _wait_alive(self, timeout: float = 15.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if await self._alive():
                return
            await asyncio.sleep(0.3)
        raise RuntimeError("El navegador no abrió el puerto de depuración a tiempo.")

    async def aclose(self) -> None:
        if self._pw is not None:
            await self._pw.stop()  # desconecta; el navegador sigue abierto para el usuario
