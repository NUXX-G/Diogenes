"""Tests del escalón navegador con un navegador falso: pasan igual en Linux (no se lanza ningún Chrome)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from hands import server as hands_server
from hands.apps.index import AppIndex
from hands.browser import session, snapshot
from hands.browser.service import BrowserService

CANAL = """\
- banner:
  - link "YouTube" [ref=e2] [cursor=pointer]:
    - /url: /
  - searchbox "Buscar" [ref=e3]
- main:
  - heading "Hrom" [level=1] [ref=e5]
  - tablist:
    - tab "Inicio" [ref=e7]
    - tab "Vídeos" [selected] [ref=e8] [cursor=pointer]
  - button "Ordenar por" [ref=e9]
  - link "Vídeo A - 10 M de visualizaciones" [ref=e10] [cursor=pointer]:
    - /url: /watch?v=aaa
  - link "Vídeo A - 10 M de visualizaciones" [ref=e11] [cursor=pointer]:
    - /url: /watch?v=aaa
  - text: Texto suelto que no interesa
  - paragraph: otro texto
"""


class FakePage:
    def __init__(
        self,
        aria: str = CANAL,
        sensitive: set[str] | None = None,
        *,
        multiline: set[str] | None = None,
        navigates: bool = True,
        on_click: dict[str, str] | None = None,
    ) -> None:
        self._aria = aria
        self._sensitive = sensitive or set()
        self._multiline = multiline or set()
        self._navigates = navigates  # False: los clics no cambian la URL (solo, quizá, el contenido)
        self._on_click = on_click or {}  # ref → aria nuevo tras el clic
        self.url = "about:blank"
        self.title = ""
        self.calls: list[tuple[Any, ...]] = []

    async def goto(self, url: str) -> None:
        self.calls.append(("goto", url))
        self.url, self.title = url, "Hrom - YouTube"

    async def current(self) -> tuple[str, str]:
        return self.url, self.title

    async def aria(self) -> str:
        return self._aria

    async def click(self, ref: str) -> None:
        self.calls.append(("click", ref))
        if self._navigates:
            self.url = "https://www.youtube.com/watch?v=aaa"
        self._aria = self._on_click.get(ref, self._aria)

    async def fill(self, ref: str, text: str, enter: bool) -> None:
        self.calls.append(("fill", ref, text, enter))

    async def is_sensitive(self, ref: str) -> bool:
        return ref in self._sensitive

    async def is_multiline(self, ref: str) -> bool:
        return ref in self._multiline

    async def back(self) -> None:
        self.calls.append(("back",))


class Provider:
    def __init__(self, page: FakePage) -> None:
        self._page = page

    async def page(self) -> FakePage:
        return self._page


def servicio(page: FakePage | None = None) -> tuple[BrowserService, FakePage]:
    page = page or FakePage()
    return BrowserService(Provider(page)), page


# --- snapshot ----------------------------------------------------------------------------------------------


def test_compact_deja_lo_accionable_con_refs_y_marca_los_datos() -> None:
    out = snapshot.compact(CANAL, url="https://www.youtube.com/@Hrom", title="Hrom")
    assert out.startswith(snapshot.HEADER)
    assert '[e8] tab "Vídeos"' in out
    assert '[e10] link "Vídeo A - 10 M de visualizaciones" -> /watch?v=aaa' in out
    assert "Texto suelto" not in out and "paragraph" not in out


def test_compact_quita_duplicados_consecutivos() -> None:
    out = snapshot.compact(CANAL)
    assert out.count("Vídeo A") == 1


def test_compact_respeta_el_tope_de_elementos_y_de_caracteres() -> None:
    enorme = "\n".join(f'- link "Enlace número {i} ' + "x" * 60 + f'" [ref=e{i}]' for i in range(500))
    out = snapshot.compact(enorme)
    assert len(out) <= snapshot.MAX_CHARS + 120
    assert out.count("\n[e") <= snapshot.MAX_NODES
    assert "elementos más no mostrados" in out


def test_compact_marca_campos_sensibles() -> None:
    out = snapshot.compact('- textbox "Contraseña" [ref=e1]\n- textbox "Buscar" [ref=e2]')
    assert 'textbox "Contraseña"  (CAMPO SENSIBLE' in out
    assert 'textbox "Buscar"\n' in out + "\n"


def test_compact_pagina_vacia() -> None:
    assert "sin elementos interactivos" in snapshot.compact("- generic:\n  - text: hola")


# --- servicio ----------------------------------------------------------------------------------------------


async def test_open_solo_acepta_http() -> None:
    svc, page = servicio()
    assert (await svc.open("file:///C:/secreto.txt"))["ok"] is False
    assert (await svc.open("javascript:alert(1)"))["ok"] is False
    assert page.calls == []
    assert (await svc.open("https://www.youtube.com/@Hrom"))["ok"] is True


async def test_snapshot_clic_y_verificacion() -> None:
    svc, page = servicio()
    await svc.open("https://www.youtube.com/@Hrom")
    snap = await svc.snapshot()
    assert "[e10]" in snap["pagina"]
    clic = await svc.click("e10")
    assert clic["ok"] and clic["url_cambio"] and clic["url"].endswith("watch?v=aaa")
    assert page.calls[-1] == ("click", "e10")


async def test_ref_inventado_o_viejo_se_rechaza() -> None:
    svc, page = servicio()
    assert (await svc.click("e10"))["ok"] is False  # sin snapshot previo
    await svc.snapshot()
    assert (await svc.click("e999"))["ok"] is False
    await svc.click("e10")
    assert (await svc.click("e8"))["ok"] is False  # tras actuar hay que hacer un snapshot nuevo
    assert [c for c in page.calls if c[0] == "click"] == [("click", "e10")]


async def test_type_con_enter() -> None:
    svc, page = servicio()
    await svc.snapshot()
    res = await svc.type("e3", "mochis", True)
    assert res["ok"] is True
    assert page.calls[-1] == ("fill", "e3", "mochis", True)


async def test_no_escribe_en_campos_de_contrasena_ni_de_pago() -> None:
    aria = '- textbox "Contraseña" [ref=e1]\n- textbox "Nombre" [ref=e2]\n- textbox "Algo" [ref=e3]'
    svc, page = servicio(FakePage(aria, sensitive={"e3"}))  # e3 lo marca el DOM (type=password)
    await svc.snapshot()
    for ref in ("e1", "e3"):
        res = await svc.type(ref, "hunter2", False)
        assert res["ok"] is False and res["parar"] is True
    assert not [c for c in page.calls if c[0] == "fill"]
    assert (await svc.type("e2", "Ana", False))["ok"] is True


async def test_no_pulsa_botones_de_pago() -> None:
    svc, page = servicio(FakePage('- button "Pagar ahora" [ref=e1]\n- button "Añadir al carrito" [ref=e2]'))
    await svc.snapshot()
    res = await svc.click("e1")
    assert res["ok"] is False and res["parar"] is True
    assert not page.calls
    assert (await svc.click("e2"))["ok"] is True


async def test_back_y_current() -> None:
    svc, page = servicio()
    await svc.open("https://a.com/")
    assert (await svc.back())["url"] == "https://a.com/"
    assert page.calls[-1] == ("back",)
    assert (await svc.current())["titulo"] == "Hrom - YouTube"


# --- lanzador ----------------------------------------------------------------------------------------------


def test_el_navegador_se_lanza_con_perfil_propio_y_cdp_solo_en_loopback(tmp_path: Path) -> None:
    args = session.launch_args(Path("chrome.exe"), tmp_path / "perfil", 9222)
    assert "--remote-debugging-address=127.0.0.1" in args
    assert "--remote-debugging-port=9222" in args
    assert any(a.startswith("--user-data-dir=") and "perfil" in a for a in args)
    assert not any("remote-allow-origins" in a or "0.0.0.0" in a for a in args)


# --- herramientas MCP --------------------------------------------------------------------------------------


async def test_herramientas_browser_publicadas_y_el_texto_escrito_no_se_registra(tmp_path: Path) -> None:
    from hands.actionlog import ActionLog

    svc, _ = servicio()
    servidor = hands_server.build_server(AppIndex(), actions=ActionLog(tmp_path), browser=svc)
    async with Client(servidor) as client:
        nombres = {t.name for t in (await client.list_tools()).tools}
        await client.call_tool("browser.snapshot", {})
        res = await client.call_tool("browser.type", {"ref": "e3", "texto": "dato-privado-123", "enter": True})
        assert json.loads(res.content[0].text)["ok"] is True
    assert {
        "browser.open",
        "browser.snapshot",
        "browser.click",
        "browser.type",
        "browser.back",
        "browser.current",
    } <= nombres
    assert "dato-privado-123" not in "".join(p.read_text(encoding="utf-8") for p in tmp_path.rglob("*") if p.is_file())


@pytest.mark.parametrize("ref", ["e5", "../x", ""])
async def test_refs_raros_no_rompen(ref: str) -> None:
    svc, _ = servicio()
    await svc.snapshot()
    assert (await svc.click(ref))["ok"] in (True, False)
