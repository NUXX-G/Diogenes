"""browser.*: el escalón 4 de la escalera. Genérico: no sabe nada de ninguna web concreta.

Cada acción devuelve la URL y el título resultantes para que el agente verifique sin una llamada extra. Los campos de
contraseña o de pago y los botones de pagar no se tocan: se para y se avisa. El contenido de la página son datos.
"""

from __future__ import annotations

import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from hands.browser import snapshot

PAYMENT_BUTTON = re.compile(
    r"\bpagar\b|pagar ahora|comprar ahora|realizar pedido|confirmar (el )?pedido|finalizar compra|"
    r"\bpay now\b|\bplace (your )?order\b|\bbuy now\b|complete purchase",
    re.IGNORECASE,
)
STOP = "Me paro aquí y te lo paso a ti: no relleno contraseñas ni datos de pago, ni completo compras."


class BrowserPage(Protocol):
    async def goto(self, url: str) -> None: ...
    async def current(self) -> tuple[str, str]: ...
    async def aria(self) -> str: ...
    async def click(self, ref: str) -> None: ...
    async def fill(self, ref: str, text: str, enter: bool) -> None: ...
    async def is_sensitive(self, ref: str) -> bool: ...
    async def back(self) -> None: ...


class PageProvider(Protocol):
    async def page(self) -> BrowserPage: ...


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


class BrowserService:
    def __init__(self, provider: PageProvider) -> None:
        self._provider = provider
        self._nodes: dict[str, snapshot.Node] = {}

    async def _state(self, page: BrowserPage) -> dict[str, Any]:
        url, title = await page.current()
        return {"url": url, "titulo": title}

    async def open(self, url: str) -> dict[str, Any]:
        if not _is_http_url(url):
            return {"ok": False, "mensaje": "Solo se abren URLs http o https."}
        page = await self._provider.page()
        try:
            await page.goto(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mensaje": f"No se pudo abrir {url}: {type(exc).__name__}"}
        self._nodes = {}
        return {"ok": True, "mensaje": f"Abierto {url}", **await self._state(page)}

    async def snapshot(self) -> dict[str, Any]:
        page = await self._provider.page()
        text = await page.aria()
        url, title = await page.current()
        self._nodes = {n.ref: n for n in snapshot.parse(text) if n.ref}
        return {
            "ok": True,
            "mensaje": f"{len(self._nodes)} elementos",
            "url": url,
            "titulo": title,
            "pagina": snapshot.compact(text, url=url, title=title),
        }

    async def click(self, ref: str) -> dict[str, Any]:
        node = self._nodes.get(ref)
        if node is None:
            return {
                "ok": False,
                "mensaje": f"ref desconocido: {ref}. Llama a browser_snapshot y usa un ref de la lista.",
            }
        if node.role == "button" and PAYMENT_BUTTON.search(node.name):
            return {"ok": False, "parar": True, "mensaje": STOP}
        page = await self._provider.page()
        before = (await page.current())[0]
        try:
            await page.click(ref)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mensaje": f"No se pudo hacer clic en {ref}: {type(exc).__name__}. Haz un snapshot nuevo.",
            }
        state = await self._state(page)
        self._nodes = {}  # la página ha podido cambiar: los refs viejos ya no valen
        return {"ok": True, "mensaje": f"Clic en {ref}", "url_cambio": state["url"] != before, **state}

    async def type(self, ref: str, text: str, enter: bool = False) -> dict[str, Any]:
        node = self._nodes.get(ref)
        if node is None:
            return {
                "ok": False,
                "mensaje": f"ref desconocido: {ref}. Llama a browser_snapshot y usa un ref de la lista.",
            }
        page = await self._provider.page()
        if node.sensitive or await page.is_sensitive(ref):
            return {"ok": False, "parar": True, "mensaje": STOP}
        before = (await page.current())[0]
        try:
            await page.fill(ref, text, enter)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mensaje": f"No se pudo escribir en {ref}: {type(exc).__name__}. Haz un snapshot nuevo.",
            }
        state = await self._state(page)
        self._nodes = {}
        return {"ok": True, "mensaje": f"Escrito en {ref}", "url_cambio": state["url"] != before, **state}

    async def back(self) -> dict[str, Any]:
        page = await self._provider.page()
        await page.back()
        self._nodes = {}
        return {"ok": True, "mensaje": "Atrás", **await self._state(page)}

    async def current(self) -> dict[str, Any]:
        page = await self._provider.page()
        return {"ok": True, "mensaje": "Estado actual", **await self._state(page)}
