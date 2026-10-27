"""web.open y web.site_search: abrir una URL y buscar dentro de cualquier web, descubriendo cómo busca.

Orden de estrategias (la más barata primero): 1) plantilla ya aprendida, 2) descriptor OpenSearch de la web,
3) automatización con navegador (hueco para la fase 6). La plantilla que funciona se guarda en SQLite.
"""

from __future__ import annotations

import re
import webbrowser
from collections.abc import Callable, Sequence
from typing import Any
from urllib.parse import quote_plus, urlsplit

import httpx

from hands.apps.models import normalize
from hands.web.fetch import Fetcher, FetchResult
from hands.web.opensearch import PLACEHOLDER
from hands.web.store import TemplateStore
from hands.web.strategies import BrowserSearchStrategy, OpenSearchStrategy, SearchStrategy

Opener = Callable[[str], None]

# Para un nombre sin dominio ("youtube") se prueban dominios habituales; no hay ninguna web escrita a mano.
TLDS = ("com", "es", "org", "net", "tv", "io")


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


def _same_site(url_a: str, url_b: str) -> bool:
    """Mismo sitio = mismos dos últimos rótulos del host (suficiente para descartar dominios ajenos)."""
    a, b = urlsplit(url_a).hostname or "", urlsplit(url_b).hostname or ""
    return bool(a) and a.split(".")[-2:] == b.split(".")[-2:]


def open_in_browser(url: str) -> None:
    webbrowser.open(url)


def _candidates(site: str) -> list[str]:
    """URLs a probar para llegar a la portada de `site`: una URL o dominio dado, o `www.<nombre>.<tld>`."""
    site = site.strip()
    if _is_http_url(site):
        return [site]
    if re.fullmatch(r"[\w-]+(\.[\w-]+)+(/.*)?", site):
        return [f"https://{site}"]
    slug = normalize(site).replace(" ", "")
    return [f"https://www.{slug}.{tld}" for tld in TLDS] if slug else []


def _site_key(site: str) -> str:
    site = site.strip().lower()
    if _is_http_url(site) or "." in site:
        host = urlsplit(site if "://" in site else f"//{site}").hostname or site
        return host.removeprefix("www.")
    return normalize(site)


class WebService:
    def __init__(
        self,
        store: TemplateStore,
        fetch: Fetcher,
        opener: Opener = open_in_browser,
        strategies: Sequence[SearchStrategy] | None = None,
    ) -> None:
        self._store = store
        self._fetch = fetch
        self._open = opener
        self._strategies = (
            list(strategies)
            if strategies is not None
            else [
                OpenSearchStrategy(fetch),
                BrowserSearchStrategy(),
            ]
        )

    async def open(self, url: str) -> dict[str, Any]:
        if not _is_http_url(url):
            return {"ok": False, "mensaje": "Solo se abren URLs http o https."}
        self._open(url)
        return {"ok": True, "mensaje": f"Abierto {url}", "url": url}

    async def site_search(self, site: str, query: str) -> dict[str, Any]:
        key = _site_key(site)
        if not key or not query.strip():
            return {"ok": False, "mensaje": "Faltan el sitio o la consulta."}

        learned = self._store.get(key)
        if learned is not None:
            url = self._build_url(learned.template, query)
            self._open(url)
            self._store.record_success(key)
            return {
                "ok": True,
                "mensaje": f"Búsqueda en {site} (plantilla aprendida)",
                "url": url,
                "origen": "aprendida",
            }

        page = await self._home(site)
        if page is None:
            return {"ok": False, "mensaje": f"No he podido abrir la web '{site}'. ¿Puedes darme el dominio exacto?"}

        intentadas = []
        for strategy in self._strategies:
            intentadas.append(strategy.name)
            template = await strategy.discover(page, query)
            if template is None or not _same_site(template, page.url):
                continue
            url = self._build_url(template, query)
            if not await self._works(url):
                continue
            self._store.save(key, page.url, template, strategy.name)
            self._open(url)
            return {"ok": True, "mensaje": f"Búsqueda en {site} ({strategy.name})", "url": url, "origen": strategy.name}

        return {
            "ok": False,
            "mensaje": f"No he descubierto cómo se busca en {site} (probado: {', '.join(intentadas)}). "
            "La automatización con navegador aún no está disponible.",
        }

    @staticmethod
    def _build_url(template: str, query: str) -> str:
        return template.replace(PLACEHOLDER, quote_plus(query))

    async def _home(self, site: str) -> FetchResult | None:
        for url in _candidates(site):
            try:
                page = await self._fetch(url)
            except httpx.HTTPError:
                continue
            if page.status < 400:
                return page
        return None

    async def _works(self, url: str) -> bool:
        """Verificación barata: la URL de resultados responde. La comprobación de título/URL llega con el navegador."""
        try:
            return (await self._fetch(url)).status < 400
        except httpx.HTTPError:
            return False
