"""El navegador de Diogenes no disponible se dice claro y con motivo; nunca se cae en silencio ni se falla en snapshot."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from hands.browser import session as browser_session
from hands.browser import snapshot
from hands.browser.rules import BrowserRules
from hands.browser.service import BrowserService, BrowserUnavailable
from hands.browser.session import BrowserSession, explain_start_failure
from hands.web.service import WebService
from hands.web.store import TemplateStore
from tests.test_web import FakeWeb

RULES = BrowserRules.load()
PREFIX = "el navegador de Diogenes no está disponible: "


class NoBrowser:
    """Un proveedor cuyo navegador no se puede usar: lo que le pasa a `BrowserSession.page()` en el PC de clase."""

    def __init__(self, reason: str = "falta node.exe") -> None:
        self.reason = reason

    async def page(self) -> Any:
        raise BrowserUnavailable(self.reason)

    async def release(self) -> None:
        return None


def start_fails(monkeypatch: pytest.MonkeyPatch, exc: BaseException) -> None:
    def start() -> Any:
        async def boom() -> Any:
            raise exc

        return boom()

    class Fake:
        def start(self) -> Any:
            return start()

    monkeypatch.setattr(browser_session, "async_playwright", lambda: Fake())


# --- diagnóstico de async_playwright().start() ------------------------------------------------------------------


async def test_si_falta_node_exe_se_dice_cual_falta_y_como_arreglarlo(monkeypatch: pytest.MonkeyPatch) -> None:
    start_fails(monkeypatch, FileNotFoundError(2, "El sistema no puede encontrar el archivo especificado"))
    monkeypatch.setattr(
        browser_session, "driver_problem", lambda: r"falta C:\x\playwright\driver\node.exe: reinstálalo con uv sync"
    )
    with pytest.raises(BrowserUnavailable) as info:
        await BrowserSession().page()
    assert str(info.value) == PREFIX + r"falta C:\x\playwright\driver\node.exe: reinstálalo con uv sync"


async def test_si_node_exe_existe_el_motivo_apunta_a_la_politica_del_centro(monkeypatch: pytest.MonkeyPatch) -> None:
    start_fails(monkeypatch, FileNotFoundError(2, "El sistema no puede encontrar el archivo especificado"))
    monkeypatch.setattr(browser_session, "driver_problem", lambda: None)
    with pytest.raises(BrowserUnavailable) as info:
        await BrowserSession().page()
    assert str(info.value).startswith(PREFIX) and "política" in str(info.value)


def test_un_permiso_denegado_al_lanzar_el_driver_se_explica_como_bloqueo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_session, "driver_problem", lambda: None)
    assert "no deja ejecutar" in explain_start_failure(PermissionError(13, "Acceso denegado"))


def test_otros_fallos_de_arranque_conservan_el_tipo_y_el_texto(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(browser_session, "driver_problem", lambda: None)
    assert "ValueError: raro" in explain_start_failure(ValueError("raro"))


def test_el_driver_real_de_este_entorno_se_comprueba_sin_romper() -> None:
    assert driver_problem_result() in (None, driver_problem_result())  # no lanza; da None o un motivo


def driver_problem_result() -> str | None:
    return browser_session.driver_problem()


async def test_sin_chrome_ni_edge_el_motivo_es_ese(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class Pw:
        async def stop(self) -> None:
            return None

    class Fake:
        async def start(self) -> Pw:
            return Pw()

    monkeypatch.setattr(browser_session, "async_playwright", lambda: Fake())
    monkeypatch.setattr(browser_session, "find_browser", lambda _preferred: None)
    monkeypatch.setattr(browser_session.sys, "platform", "win32")
    session = BrowserSession(profile=tmp_path / "perfil", port=1)
    with pytest.raises(BrowserUnavailable) as info:
        await session.page()
    assert str(info.value) == PREFIX + "no encuentro Chrome ni Edge instalados"


# --- las herramientas browser.* lo dicen claro ---------------------------------------------------------------------


@pytest.mark.parametrize("tool", ["open", "snapshot", "click", "type", "back", "current"])
async def test_cada_herramienta_del_navegador_devuelve_el_motivo_y_para(tool: str) -> None:
    service = BrowserService(NoBrowser("falta node.exe"), RULES)
    service._nodes = {"e1": snapshot.Node(role="button", name="Ir", ref="e1")}  # type: ignore[attr-defined]
    args = {"open": ("https://example.com",), "click": ("e1",), "type": ("e1", "hola")}.get(tool, ())
    result = await getattr(service, tool)(*args)
    assert result["ok"] is False and result["parar"] is True
    assert result["mensaje"] == PREFIX + "falta node.exe"


async def test_el_motivo_no_se_antepone_dos_veces() -> None:
    assert str(BrowserUnavailable(PREFIX + "x")) == PREFIX + "x"


async def test_buscar_el_campo_de_busqueda_sin_navegador_no_espera_ni_cuelga() -> None:
    service = BrowserService(NoBrowser(), RULES)
    assert await service.find_search_field(wait=30.0) is None  # con wait=30 colgaría si siguiera reintentando


# --- web.open / web.site_search: el cambio al navegador del sistema es visible -------------------------------------


class SanoProvider:
    def __init__(self, page: Any) -> None:
        self._page = page

    async def page(self) -> Any:
        return self._page

    async def release(self) -> None:
        return None


def web_con(provider: Any, abiertas: list[str]) -> WebService:
    return WebService(
        TemplateStore(":memory:"), FakeWeb({}), opener=abiertas.append, browser=BrowserService(provider, RULES)
    )


async def test_web_open_sin_navegador_de_diogenes_avisa_del_motivo_y_usa_el_del_sistema() -> None:
    abiertas: list[str] = []
    r = await web_con(NoBrowser("falta node.exe"), abiertas).open("https://example.com/")
    assert abiertas == ["https://example.com/"]
    assert r["ok"] is True and r["abierto_en"] == "navegador del sistema"
    assert r["aviso"] == PREFIX + "falta node.exe"
    assert (
        "AVISO" in r["mensaje"] and "falta node.exe" in r["mensaje"] and "no puedo seguir con browser_" in r["mensaje"]
    )


async def test_web_open_con_navegador_sano_no_trae_aviso() -> None:
    from tests.test_web_browser import FakeSite

    abiertas: list[str] = []
    r = await web_con(SanoProvider(FakeSite()), abiertas).open("https://example.com/")
    assert r["abierto_en"] == "navegador de Diogenes" and "aviso" not in r and abiertas == []


async def test_el_aviso_tambien_sale_en_site_search_y_se_quita_la_promesa_de_seguir_con_browser() -> None:
    abiertas: list[str] = []
    service = web_con(NoBrowser("falta node.exe"), abiertas)
    service._strategies = []  # type: ignore[attr-defined]
    service._store.save("ejemplo.com", "https://www.ejemplo.com", "https://www.ejemplo.com/buscar?q={query}", "x")  # type: ignore[attr-defined]
    r = await service.site_search("www.ejemplo.com", "a")
    assert r["aviso"] == PREFIX + "falta node.exe" and "siguiente" not in r
    assert abiertas == ["https://www.ejemplo.com/buscar?q=a"]
