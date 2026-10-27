"""Estrategias para descubrir cómo busca una web. Cada una devuelve una plantilla con `{query}` o None."""

from __future__ import annotations

from typing import Protocol

import httpx

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


class BrowserSearchStrategy:
    """Hueco para la fase 6: abrir la web con Playwright, localizar el campo de búsqueda, escribir `probe_query`,
    pulsar Enter y deducir la plantilla de la URL resultante sustituyendo la consulta por `{query}`.

    Hasta que exista el escalón del navegador no descubre nada; está registrada para que el orden de estrategias
    (aprendida → OpenSearch → navegador) y el mensaje de error ya sean los definitivos.
    """

    name = "navegador"

    async def discover(self, page: FetchResult, probe_query: str) -> str | None:
        return None
