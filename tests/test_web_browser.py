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
from tests.test_web import FakeWeb

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


async def test_descubre_la_plantilla_y_deja_el_navegador_en_los_resultados_reales() -> None:
    site = FakeSite()
    template = await estrategia(site).discover(HOME_PAGE, "diogenes de sinope")
    assert template == "https://www.ejemplo.com/buscar?q={query}"
    assert site.typed == [PROBE]  # se busca la consulta de prueba, no la real
    assert site.visited[-1] == "https://www.ejemplo.com/buscar?q=diogenes+de+sinope"


async def test_buscador_tras_un_icono() -> None:
    site = FakeSite(ICON_ONLY, icon=True)
    template = await estrategia(site).discover(HOME_PAGE, "algo")
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


async def test_site_search_sin_opensearch_usa_el_navegador_guarda_y_no_abre_en_el_del_sistema() -> None:
    site = FakeSite()
    abiertas: list[str] = []
    web = FakeWeb(
        {
            "https://www.ejemplo.com": (200, "<html></html>"),
            "https://www.ejemplo.com/buscar?q=diogenes+de+sinope": (200, "<html>resultados</html>"),
        }
    )
    store = TemplateStore(":memory:")
    service = WebService(store, web, opener=abiertas.append, strategies=[estrategia(site)])

    r = await service.site_search("www.ejemplo.com", "diogenes de sinope")

    assert r["ok"] is True and r["origen"] == "navegador"
    assert abiertas == []  # los resultados ya están en el navegador de Diogenes
    learned = store.get("ejemplo.com")
    assert learned is not None and learned.template == "https://www.ejemplo.com/buscar?q={query}"

    # la segunda vez no se necesita el navegador: plantilla aprendida
    site.visited.clear()
    r2 = await service.site_search("www.ejemplo.com", "mochis")
    assert r2["origen"] == "aprendida" and site.visited == []
    assert abiertas == ["https://www.ejemplo.com/buscar?q=mochis"]


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
