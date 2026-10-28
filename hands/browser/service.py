"""browser.*: el escalón 4 de la escalera. Genérico: no sabe nada de ninguna web concreta.

Cada acción devuelve la URL y el título resultantes para que el agente verifique sin una llamada extra. Los campos de
contraseña o de pago y los controles de pago no se tocan: se para y se avisa (`parar`). Las acciones irreversibles en
nombre del usuario (enviar, publicar, borrar...) no se ejecutan sin `confirmado=True`; devuelven `requiere_confirmacion`
y quien confirma es el humano, en `core` (el agente ignora cualquier `confirmado` que proponga el modelo).
El contenido de la página son datos.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import re
import time
from typing import Any, Protocol
from urllib.parse import urlsplit

from hands.browser import snapshot
from hands.browser.rules import BrowserRules

STOP = "Me paro aquí y te lo paso a ti: no relleno contraseñas ni datos de pago, ni completo compras."
UNKNOWN_REF = "ref desconocido: {ref}. Llama a browser_snapshot y usa un ref de la lista."


class ClickIntercepted(Exception):
    """Otro elemento (un aviso de cookies, un diálogo) tapa el que se quería pulsar."""


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


async def _read(page: BrowserPage) -> str | None:
    try:
        return await page.aria()
    except Exception:  # noqa: BLE001
        return None


def _digest(text: str | None) -> str | None:
    """Huella del contenido de la página (sin los refs, que pueden renumerarse) para comprobar si una acción cambió algo."""
    return None if text is None else hashlib.sha1(_REF_ATTR.sub("", text).encode()).hexdigest()


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
        # Huella de la última página devuelta. NO se vuelve a leer antes de actuar: cada aria_snapshot renumera los
        # refs y el que el modelo acaba de ver dejaría de valer.
        self._digest: str | None = None
        self._signatures: frozenset[tuple[str, str]] = frozenset()  # (rol, nombre) de la última página
        self._last_url = ""

    async def _state(self, page: BrowserPage) -> dict[str, Any]:
        url, title = await page.current()
        return {"url": url, "titulo": title}

    async def _observe(self, page: BrowserPage, before: str | None = None) -> dict[str, Any]:
        """Lee la página tras una acción: deja los refs vigentes y la devuelve ya compactada, para no gastar un snapshot."""
        text = await _read(page)
        state = await self._state(page)
        self._digest = _digest(text)
        if text is None:
            self._nodes = {}
            return {**state, "cambio_visible": None}
        previous = self._signatures if state["url"] == self._last_url else None
        self._nodes = {n.ref: n for n in snapshot.parse(text) if n.ref}
        self._signatures = frozenset(snapshot.signature(n) for n in self._nodes.values())
        self._last_url = state["url"]
        return {
            **state,
            "cambio_visible": _changed(before, _digest(text)),
            "pagina": snapshot.compact(text, url=state["url"], title=state["titulo"], previous=previous),
        }

    async def open(self, url: str) -> dict[str, Any]:
        if not _is_http_url(url):
            return {"ok": False, "mensaje": "Solo se abren URLs http o https."}
        page = await self._provider.page()
        try:
            await page.goto(url)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "mensaje": f"No se pudo abrir {url}: {type(exc).__name__}"}
        self._form_dirty = False
        seen = await self._observe(page)
        seen.pop("cambio_visible")
        return {"ok": True, "mensaje": f"Abierto {url}", **seen}

    async def snapshot(self) -> dict[str, Any]:
        page = await self._provider.page()
        seen = await self._observe(page)
        seen.pop("cambio_visible")
        return {"ok": True, "mensaje": f"{len(self._nodes)} elementos", **seen}

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
        seen_before = self._digest
        closed = ""
        try:
            try:
                await page.click(ref)
            except ClickIntercepted:
                closed = await self._close_notice(page)
                if not closed:
                    raise
                await page.click(ref)  # mismos refs: no se ha vuelto a leer la página
        except ClickIntercepted:
            return {
                "ok": False,
                "mensaje": f"No se pudo hacer clic en {ref}: otro elemento lo tapa (¿un aviso o un diálogo?). "
                "Mira el snapshot por un botón para cerrarlo.",
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mensaje": f"No se pudo hacer clic en {ref}: {type(exc).__name__}. Haz un snapshot nuevo.",
            }
        seen = await self._observe(page, seen_before)  # la página ha podido cambiar: se devuelven los refs nuevos
        moved = seen["url"] != before
        if moved or confirmado:
            self._form_dirty = False
        return {
            "ok": True,
            "mensaje": f"Clic en {ref}"
            + (f". Antes cerré un aviso que lo tapaba con «{closed}»" if closed else "")
            + ("" if seen["cambio_visible"] or moved else ". La página NO ha cambiado."),
            "url_cambio": moved,
            **seen,
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
        seen_before = self._digest
        try:
            await page.fill(ref, text, enter)
        except Exception as exc:  # noqa: BLE001
            return {
                "ok": False,
                "mensaje": f"No se pudo escribir en {ref}: {type(exc).__name__}. Haz un snapshot nuevo.",
            }
        if not is_search:
            self._form_dirty = not enter
        seen = await self._observe(page, seen_before)  # los refs siguen siendo válidos tras escribir
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
            "url_cambio": seen["url"] != before,
            **seen,
        }

    async def _close_notice(self, page: BrowserPage) -> str:
        """Cierra con la opción mínima (rechazar) un aviso que tapa la página. Devuelve el botón pulsado, o ''."""
        for node in self._nodes.values():
            if self._rules.minimal_notice_term(node.role, node.name) and not self._rules.payment_term(
                node.role, node.name
            ):
                with contextlib.suppress(Exception):
                    await page.click(node.ref)
                    return node.name
        return ""

    def _search_field(self) -> snapshot.Node | None:
        fields = [
            n
            for n in self._nodes.values()
            if n.role in snapshot.TEXT_INPUTS and not n.sensitive and self._rules.is_search_field(n.role, n.name)
        ]
        return next((n for n in fields if n.role == "searchbox"), fields[0] if fields else None)

    async def find_search_field(self, wait: float = 0.0) -> str | None:
        """Ref del buscador de la página actual. Si solo hay un botón o icono de búsqueda, lo pulsa y vuelve a mirar.
        Las webs que se pintan con JavaScript no tienen el buscador al cargar: durante `wait` segundos se reintenta."""
        deadline = time.monotonic() + wait
        clicked = 0
        while True:
            await self.snapshot()
            field = self._search_field()
            if field is not None:
                return field.ref
            opener = next(
                (
                    n
                    for n in self._nodes.values()
                    if n.role in {"button", "link"} and self._rules.is_search_field(n.role, n.name)
                ),
                None,
            )
            if opener is not None and clicked < 2:
                if not (await self.click(opener.ref)).get("ok"):
                    return None
                clicked += 1
                continue
            if time.monotonic() >= deadline:
                return None
            await asyncio.sleep(1.0)

    async def back(self) -> dict[str, Any]:
        page = await self._provider.page()
        await page.back()
        self._form_dirty = False
        seen = await self._observe(page)
        seen.pop("cambio_visible")
        return {"ok": True, "mensaje": "Atrás", **seen}

    async def current(self) -> dict[str, Any]:
        page = await self._provider.page()
        return {"ok": True, "mensaje": "Estado actual", **await self._state(page)}
