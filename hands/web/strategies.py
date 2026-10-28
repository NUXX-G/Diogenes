"""Estrategias para descubrir cómo busca una web. Cada una devuelve una plantilla con `{query}` o None."""

from __future__ import annotations

import asyncio
import time
from typing import Protocol
from urllib.parse import quote, quote_plus

import httpx

from hands.browser.service import BrowserService
from hands.web import opensearch
from hands.web.fetch import Fetcher, FetchResult


class SearchStrategy(Protocol):
    name: str

    async def discover(self, page: FetchResult, probe_query: str) -> str | None: ...


class OpenSearchStrategy:
    """Lee el descriptor OpenSearch que la propia web anuncia en su portada."""

    name = "opensearch"

    def __init__(self, fetch: Fetcher) -> None:
        self._fetch = fetch

    async def discover(self, page: FetchResult, probe_query: str) -> str | None:
        descriptor_url = opensearch.find_descriptor_url(page.text, page.url)
        if descriptor_url is None:
            return None
        try:
            descriptor = await self._fetch(descriptor_url)
        except httpx.HTTPError:
            return None
        if descriptor.status >= 400:
            return None
        return opensearch.template_from_descriptor(descriptor.text)


# Dos palabras sin sentido: no coinciden con ningún artículo ni redirigen, y comprueban cómo se codifica el espacio.
PROBE = "qzxv wdrk"
_ENCODINGS = (lambda q: quote_plus(q), lambda q: quote(q, safe=""), lambda q: q.replace(" ", "_"), lambda q: q)


def template_from_url(url: str, probe: str = PROBE) -> str | None:
    """La URL de resultados con la consulta de prueba sustituida por `{query}`, o None si la consulta no aparece."""
    for encode in _ENCODINGS:
        encoded = encode(probe)
        if encoded in url:
            return url.replace(encoded, opensearch.PLACEHOLDER, 1)
    return None


class BrowserSearchStrategy:
    """Escalón 3 → 4: abre la web en el navegador de Diogenes, localiza el buscador en el árbol de accesibilidad, escribe
    una consulta de prueba con Enter y deduce la plantilla de la URL resultante. Los resultados reales los abre
    `WebService` (en el mismo navegador de Diogenes).

    Genérica: no sabe nada de ninguna web. Si la web busca sin cambiar la URL, no hay plantilla y no descubre nada.
    """

    name = "navegador"
    verified_by_browser = True

    def __init__(self, browser: BrowserService | None = None, wait_seconds: float = 4.0) -> None:
        self._browser = browser
        self._wait = wait_seconds

    async def discover(self, page: FetchResult, query: str) -> str | None:
        browser = self._browser
        if browser is None or not (await browser.open(page.url)).get("ok"):
            return None
        ref = await browser.find_search_field(wait=self._wait)
        if ref is None:
            return None
        start = (await browser.current())["url"]
        typed = await browser.type(ref, PROBE, True)
        if not typed.get("ok"):
            return None
        url = typed["url"]
        deadline = time.monotonic() + self._wait
        while url == start and time.monotonic() < deadline:  # búsquedas que actualizan la URL con retraso
            await asyncio.sleep(0.3)
            url = (await browser.current())["url"]
        if url == start:
            return None
        template = template_from_url(url)
        if template is None:
            return None
        return template
