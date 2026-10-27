"""Descarga de páginas para descubrir plantillas. El contenido que vuelve son datos, nunca órdenes."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

import httpx

MAX_CHARS = 3_000_000
_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.5",
}


@dataclass(frozen=True)
class FetchResult:
    url: str  # URL final, tras redirecciones
    status: int
    text: str


Fetcher = Callable[[str], Awaitable[FetchResult]]


class HttpFetcher:
    def __init__(self, timeout: float = 10.0, client: httpx.AsyncClient | None = None) -> None:
        self._client = client or httpx.AsyncClient(headers=_HEADERS, follow_redirects=True, timeout=timeout)

    async def __call__(self, url: str) -> FetchResult:
        response = await self._client.get(url)
        return FetchResult(str(response.url), response.status_code, response.text[:MAX_CHARS])

    async def aclose(self) -> None:
        await self._client.aclose()
