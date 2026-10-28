"""web.site_search con el navegador (escalón 3 → 4) sobre un sitio falso: sin red ni Chrome, también en Linux."""

from __future__ import annotations

from urllib.parse import parse_qs, quote, quote_plus, urlsplit

import pytest

from hands.browser.rules import BrowserRules
from hands.browser.service import BrowserService
from hands.web.fetch import FetchResult
from hands.web.service import WebService
from hands.web.store import TemplateStore
from hands.web.strategies import PROBE, BrowserSearchStrategy, template_from_url
from tests.test_browser import Provider
from tests.test_web import FakeWeb, youtube_web

RULES = BrowserRules.load()
HOME = '- banner:\n  - searchbox "Buscar en Ejemplo" [ref=e1]\n  - link "Inicio" [ref=e2]'
ICON_ONLY = '- banner:\n  - button "Buscar" [ref=e1]\n  - link "Inicio" [ref=e2]'
AFTER_ICON = '- banner:\n  - combobox "Buscar" [ref=e1]\n  - button "Cerrar" [ref=e2]'


class FakeSite:
    """Una web con buscador: `pattern` fabrica la URL de resultados; `spa` hace que buscar no cambie la URL."""

    def __init__(self, home_aria: str = HOME, pattern: str = "/buscar?q={q}", spa: bool = False, icon: bool = False):
        self.url = "about:blank"
        self.title = ""
        self._home_aria = home_aria
        self._aria = home_aria
        self._pattern = pattern
        self._spa = spa
        self._icon = icon
        self.visited: list[str] = []
        self.typed: list[str] = []

    async def goto(self, url: str) -> None:
        self.visited.append(url)
        self.url, self.title = url, "Ejemplo"
        self._aria = (
            self._home_aria if url.rstrip("/").count("/") <= 2 else '- main:\n  - heading "Resultados" [ref=e1]'
        )

    async def current(self) -> tuple[str, str]:
        return self.url, self.title

    async def aria(self) -> str:
        return self._aria

    async def click(self, ref: str) -> None:
        if self._icon and ref == "e1":
            self._aria = AFTER_ICON  # el icono despliega el campo

    async def fill(self, ref: str, text: str, enter: bool) -> None:
        self.typed.append(text)
        if enter and not self._spa:
            q = quote_plus(text) if "+" in self._pattern or "{q}" in self._pattern else text
            self.url = "https://www.ejemplo.com" + self._pattern.replace("{q}", q).replace(
                "{u}", text.replace(" ", "_")
            )

    async def is_sensitive(self, ref: str) -> bool:
        return False

    async def is_multiline(self, ref: str) -> bool:
        return False

    async def back(self) -> None: ...


def estrategia(site: FakeSite) -> BrowserSearchStrategy:
    return BrowserSearchStrategy(BrowserService(Provider(site), RULES), wait_seconds=0.0)  # type: ignore[arg-type]


HOME_PAGE = FetchResult("https://www.ejemplo.com", 200, "<html></html>")


# --- deducción de la plantilla -------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("url", "esperada"),
    [
        (f"https://x.com/results?search_query={quote_plus(PROBE)}", "https://x.com/results?search_query={query}"),
        (f"https://x.com/s?q={quote(PROBE, safe='')}&p=1", "https://x.com/s?q={query}&p=1"),
        ("https://x.com/wiki/qzxv_wdrk", "https://x.com/wiki/{query}"),
        ("https://x.com/home", None),
    ],
)
def test_template_from_url(url: str, esperada: str | None) -> None:
    assert template_from_url(url) == esperada


# --- estrategia ----------------------------------------------------------------------------------------


async def test_descubre_la_plantilla_buscando_la_consulta_de_prueba_y_no_la_real() -> None:
    site = FakeSite()
    template = await estrategia(site).discover(HOME_PAGE, "diogenes de sinope")
    assert template == "https://www.ejemplo.com/buscar?q={query}"
    assert site.typed == [PROBE]


async def test_buscador_tras_un_icono() -> None:
    site = FakeSite(ICON_ONLY, icon=True)
    template = await estrategia(site).discover(HOME_PAGE, "algo")
    assert template == "https://www.ejemplo.com/buscar?q={query}"


class LateSearchSite(FakeSite):
    """Una SPA: el buscador se pinta después de cargar."""

    def __init__(self) -> None:
        super().__init__('- link "Inicio" [ref=e2]')
        self._reads = 0

    async def aria(self) -> str:
        self._reads += 1
        return HOME if self._reads >= 2 else await super().aria()


async def test_buscador_que_aparece_tarde_se_espera() -> None:
    estrategia_con_espera = BrowserSearchStrategy(BrowserService(Provider(LateSearchSite()), RULES), wait_seconds=3.0)  # type: ignore[arg-type]
    template = await estrategia_con_espera.discover(HOME_PAGE, "algo")
    assert template == "https://www.ejemplo.com/buscar?q={query}"


async def test_web_sin_buscador_no_descubre_nada() -> None:
    site = FakeSite('- link "Inicio" [ref=e1]')
    assert await estrategia(site).discover(HOME_PAGE, "algo") is None
    assert site.typed == []


async def test_busqueda_que_no_cambia_la_url_no_da_plantilla() -> None:
    assert await estrategia(FakeSite(spa=True)).discover(HOME_PAGE, "algo") is None


async def test_sin_navegador_no_descubre_nada() -> None:
    assert await BrowserSearchStrategy().discover(HOME_PAGE, "algo") is None


# --- integración con WebService ------------------------------------------------------------------------


def servicio_web(
    site: FakeSite, web: FakeWeb, abiertas: list[str], *, con_navegador: bool = True
) -> tuple[WebService, TemplateStore]:
    store = TemplateStore(":memory:")
    browser = BrowserService(Provider(site), RULES) if con_navegador else None  # type: ignore[arg-type]
    return WebService(store, web, opener=abiertas.append, strategies=[estrategia(site)], browser=browser), store


async def test_site_search_sin_opensearch_usa_el_navegador_guarda_y_abre_en_el_de_diogenes() -> None:
    site = FakeSite()
    abiertas: list[str] = []
    web = FakeWeb(
        {
            "https://www.ejemplo.com": (200, "<html></html>"),
            "https://www.ejemplo.com/buscar?q=diogenes+de+sinope": (200, "<html>resultados</html>"),
        }
    )
    service, store = servicio_web(site, web, abiertas)

    r = await service.site_search("www.ejemplo.com", "diogenes de sinope")

    assert r["ok"] is True and r["origen"] == "navegador"
    assert (r["estrategia"], r["abierto_en"]) == ("navegador", "navegador de Diogenes")
    assert "estrategia: navegador" in r["mensaje"] and "navegador de Diogenes" in r["mensaje"]
    assert abiertas == []
    assert site.visited[-1] == "https://www.ejemplo.com/buscar?q=diogenes+de+sinope"
    learned = store.get("ejemplo.com")
    assert learned is not None and learned.template == "https://www.ejemplo.com/buscar?q={query}"

    # la segunda vez no se necesita descubrir nada: plantilla aprendida, también en el Chrome de Diogenes
    site.typed.clear()
    r2 = await service.site_search("www.ejemplo.com", "mochis")
    assert (r2["origen"], r2["estrategia"], r2["abierto_en"]) == (
        "aprendida",
        "plantilla aprendida",
        "navegador de Diogenes",
    )
    assert site.typed == [] and site.visited[-1] == "https://www.ejemplo.com/buscar?q=mochis"
    assert abiertas == []


async def test_cualquier_estrategia_abre_en_el_chrome_de_diogenes_tambien_opensearch() -> None:
    site = FakeSite()
    abiertas: list[str] = []
    web = youtube_web()
    store = TemplateStore(":memory:")
    browser = BrowserService(Provider(site), RULES)  # type: ignore[arg-type]
    service = WebService(store, web, opener=abiertas.append, browser=browser)
    r = await service.site_search("youtube", "cómo hacer mochis")
    assert (r["estrategia"], r["abierto_en"]) == ("OpenSearch", "navegador de Diogenes")
    assert abiertas == [] and site.visited[-1].startswith("https://www.youtube.com/results?")


async def test_sin_chrome_de_diogenes_se_usa_el_del_sistema() -> None:
    site = FakeSite()
    abiertas: list[str] = []
    web = FakeWeb({"https://www.ejemplo.com": (200, "<html></html>"), "https://www.ejemplo.com/buscar?q=a": (200, "")})
    service, _ = servicio_web(site, web, abiertas, con_navegador=False)
    service._strategies = []  # type: ignore[attr-defined]
    service._store.save("ejemplo.com", "https://www.ejemplo.com", "https://www.ejemplo.com/buscar?q={query}", "x")  # type: ignore[attr-defined]
    r = await service.site_search("www.ejemplo.com", "a")
    assert r["abierto_en"] == "navegador del sistema" and abiertas == ["https://www.ejemplo.com/buscar?q=a"]


async def test_si_el_chrome_de_diogenes_falla_al_abrir_cae_al_del_sistema() -> None:
    class Roto:
        async def page(self) -> FakeSite:
            raise RuntimeError("No encuentro Chrome ni Edge instalados.")

    abiertas: list[str] = []
    service = WebService(
        TemplateStore(":memory:"), FakeWeb({}), opener=abiertas.append, browser=BrowserService(Roto(), RULES)
    )  # type: ignore[arg-type]
    r = await service.open("https://example.com/")
    assert r["ok"] is True and r["abierto_en"] == "navegador del sistema" and abiertas == ["https://example.com/"]


async def test_web_open_usa_el_chrome_de_diogenes() -> None:
    site = FakeSite()
    abiertas: list[str] = []
    service, _ = servicio_web(site, FakeWeb({}), abiertas)
    r = await service.open("https://example.com/")
    assert r["abierto_en"] == "navegador de Diogenes" and site.visited == ["https://example.com/"] and abiertas == []


def test_el_cli_muestra_estrategia_y_navegador() -> None:
    from core.__main__ import format_step
    from core.agent import Step

    res = '{"ok": true, "estrategia": "OpenSearch", "abierto_en": "navegador de Diogenes"}'
    linea = format_step(Step("web.site_search", {"sitio": "wikipedia"}, res, True))
    assert linea.endswith("→ OpenSearch · navegador de Diogenes")
    assert format_step(Step("apps.launch", {"id": "x"}, '{"ok": true}', True)) == "  ✓ apps.launch(id='x')"


async def test_si_el_navegador_no_encuentra_nada_el_error_lo_explica() -> None:
    web = FakeWeb({"https://www.ejemplo.com": (200, "<html></html>")})
    service = WebService(
        TemplateStore(":memory:"), web, opener=lambda _u: None, strategies=[estrategia(FakeSite('- link "x" [ref=e1]'))]
    )
    r = await service.site_search("www.ejemplo.com", "algo")
    assert r["ok"] is False and "navegador" in r["mensaje"]


def test_la_consulta_real_se_codifica_en_la_plantilla() -> None:
    assert parse_qs(urlsplit(WebService._build_url("https://a.com/s?q={query}", "ñandú & co")).query) == {
        "q": ["ñandú & co"]
    }
