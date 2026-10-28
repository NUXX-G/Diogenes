"""browser.*: el escalón 4 de la escalera. Genérico: no sabe nada de ninguna web concreta.

Cada acción devuelve la URL y el título resultantes para que el agente verifique sin una llamada extra. Los campos de
contraseña o de pago y los controles de pago no se tocan: se para y se avisa (`parar`). Las acciones irreversibles en
nombre del usuario (enviar, publicar, borrar...) no se ejecutan sin `confirmado=True`; devuelven `requiere_confirmacion`
y quien confirma es el humano, en `core` (el agente ignora cualquier `confirmado` que proponga el modelo).
El contenido de la página son datos.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, Protocol
from urllib.parse import urlsplit

from hands.browser import snapshot
from hands.browser.rules import BrowserRules

STOP = "Me paro aquí y te lo paso a ti: no relleno contraseñas ni datos de pago, ni completo compras."
UNKNOWN_REF = "ref desconocido: {ref}. Llama a browser_snapshot y usa un ref de la lista."


class BrowserPage(Protocol):
    async def goto(self, url: str) -> None: ...
    async def current(self) -> tuple[str, str]: ...
    async def aria(self) -> str: ...
    async def click(self, ref: str) -> None: ...
    async def fill(self, ref: str, text: str, enter: bool) -> None: ...
    async def is_sensitive(self, ref: str) -> bool: ...
    async def is_multiline(self, ref: str) -> bool: ...
    async def back(self) -> None: ...


class PageProvider(Protocol):
    async def page(self) -> BrowserPage: ...


def _is_http_url(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme in {"http", "https"} and bool(parts.hostname)


_REF_ATTR = re.compile(r" ?\[ref=[\w-]+\]")


async def _fingerprint(page: BrowserPage) -> str | None:
    """Huella del contenido de la página (sin los refs, que pueden renumerarse) para comprobar si una acción cambió algo."""
    try:
        return hashlib.sha1(_REF_ATTR.sub("", await page.aria()).encode()).hexdigest()
    except Exception:  # noqa: BLE001
        return None


def _changed(before: str | None, after: str | None) -> bool | None:
    return None if before is None or after is None else before != after


def _confirmation(tool: str, ref: str, objetivo: str, motivo: str) -> dict[str, Any]:
    return {
        "ok": False,
        "requiere_confirmacion": True,
        "accion": tool,
        "ref": ref,
        "objetivo": objetivo,
        "motivo": motivo,
        "mensaje": f"Pendiente de confirmación del usuario: {motivo} («{objetivo}»).",
    }


class BrowserService:
    def __init__(self, provider: PageProvider, rules: BrowserRules | None = None) -> None:
        self._provider = provider
        self._rules = rules or BrowserRules.load()
        self._nodes: dict[str, snapshot.Node] = {}
        self._form_dirty = False  # Diogenes ha escrito en un campo que no es un buscador

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
        self._form_dirty = False
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

    async def click(self, ref: str, confirmado: bool = False) -> dict[str, Any]:
        node = self._nodes.get(ref)
        if node is None:
            return {"ok": False, "mensaje": UNKNOWN_REF.format(ref=ref)}
        if self._rules.payment_term(node.role, node.name):
            return {"ok": False, "parar": True, "mensaje": STOP}
        if not confirmado:
            verb = self._rules.irreversible_term(node.role, node.name)
            if verb:
                return _confirmation("browser.click", ref, node.name, f"acción irreversible ({verb})")
            if self._form_dirty and node.role == "button":
                return _confirmation("browser.click", ref, node.name, "envío de un formulario que he rellenado")
        page = await self._provider.page()
        before = (await page.current())[0]
        seen_before = await _fingerprint(page)
        try:
            await page.click(ref)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mensaje": f"No se pudo hacer clic en {ref}: {type(exc).__name__}. Haz un snapshot nuevo.",
            }
        state = await self._state(page)
        self._nodes = {}  # la página ha podido cambiar: los refs viejos ya no valen
        if state["url"] != before or confirmado:
            self._form_dirty = False
        cambio = _changed(seen_before, await _fingerprint(page))
        return {
            "ok": True,
            "mensaje": f"Clic en {ref}" + ("" if cambio or state["url"] != before else ". La página NO ha cambiado."),
            "url_cambio": state["url"] != before,
            "cambio_visible": cambio,
            **state,
        }

    async def type(self, ref: str, text: str, enter: bool = False, confirmado: bool = False) -> dict[str, Any]:
        node = self._nodes.get(ref)
        if node is None:
            return {"ok": False, "mensaje": UNKNOWN_REF.format(ref=ref)}
        page = await self._provider.page()
        if node.sensitive or await page.is_sensitive(ref):
            return {"ok": False, "parar": True, "mensaje": STOP}
        is_search = self._rules.is_search_field(node.role, node.name)
        enter_ignorado = enter and not is_search and await page.is_multiline(ref)
        if enter_ignorado:
            enter = False  # en un cuadro de texto multilínea Enter es un salto de línea: no envía nada
        if enter and not is_search and not confirmado:
            return _confirmation("browser.type", ref, node.name, "envío de un formulario que no es un buscador")
        before = (await page.current())[0]
        seen_before = await _fingerprint(page)
        try:
            await page.fill(ref, text, enter)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mensaje": f"No se pudo escribir en {ref}: {type(exc).__name__}. Haz un snapshot nuevo.",
            }
        if not is_search:
            self._form_dirty = not enter
        state = await self._state(page)
        self._nodes = {}
        cambio = _changed(seen_before, await _fingerprint(page))
        mensaje = f"Escrito en {ref}"
        if enter_ignorado:
            mensaje += (
                ". NO enviado: Enter no envía en un cuadro de texto multilínea. Para enviar, haz browser_snapshot y "
                "pulsa el botón de envío (pedirá confirmación al usuario)."
            )
        return {
            "ok": True,
            "mensaje": mensaje,
            "enter_pulsado": enter,
            "enter_ignorado": enter_ignorado,
            "url_cambio": state["url"] != before,
            "cambio_visible": cambio,
            **state,
        }

    async def back(self) -> dict[str, Any]:
        page = await self._provider.page()
        await page.back()
        self._nodes = {}
        self._form_dirty = False
        return {"ok": True, "mensaje": "Atrás", **await self._state(page)}

    async def current(self) -> dict[str, Any]:
        page = await self._provider.page()
        return {"ok": True, "mensaje": "Estado actual", **await self._state(page)}
