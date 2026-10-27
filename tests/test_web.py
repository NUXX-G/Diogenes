from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from mcp import Client

from hands.apps.index import AppIndex
from hands.server import build_server
from hands.web import opensearch
from hands.web.fetch import FetchResult
from hands.web.service import WebService
from hands.web.store import TemplateStore

FIXTURES = Path(__file__).parent / "fixtures" / "web"
YOUTUBE_RESULTS = "https://www.youtube.com/results?search_query=c%C3%B3mo+hacer+mochis&utm_source=opensearch"


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


# --- OpenSearch --------------------------------------------------------------------------------------------


def test_descriptor_enlazado_en_la_portada_de_youtube() -> None:
    url = opensearch.find_descriptor_url(fixture("youtube_home.html"), "https://www.youtube.com/")
    assert url == "https://www.youtube.com/opensearch?locale=es_ES"


def test_descriptor_relativo_se_resuelve_contra_la_pagina() -> None:
    html = '<link rel="search" type="application/opensearchdescription+xml" href="/opensearch.xml">'
    assert opensearch.find_descriptor_url(html, "https://github.com/") == "https://github.com/opensearch.xml"


def test_pagina_sin_descriptor() -> None:
    assert opensearch.find_descriptor_url('<link rel="stylesheet" href="a.css">', "https://x.com/") is None


def test_plantilla_de_youtube_descarta_parametros_opcionales() -> None:
    template = opensearch.template_from_descriptor(fixture("youtube_opensearch.xml"))
    assert template == "https://www.youtube.com/results?search_query={query}&utm_source=opensearch"


def test_plantilla_de_github() -> None:
    template = opensearch.template_from_descriptor(fixture("github_opensearch.xml"))
    assert template == "https://github.com/search?q={query}&ref=opensearch"


def test_to_template_casos() -> None:
    f = opensearch.to_template
    assert f("https://a.com/s?q={searchTerms}&p={startPage?}") == "https://a.com/s?q={query}"
    assert f("https://a.com/s?p={startPage?}&q={searchTerms}") == "https://a.com/s?q={query}"
    assert f("https://a.com/s?q={searchTerms}&enc={inputEncoding}") == "https://a.com/s?q={query}&enc=UTF-8"
    assert f("https://a.com/{searchTerms}") == "https://a.com/{query}"
    assert f("https://a.com/s?q={searchTerms}&x={algoRaro}") is None  # parámetro obligatorio que no sabemos rellenar
    assert f("https://a.com/s?q=fijo") is None
    assert f("javascript:alert({searchTerms})") is None


def test_descriptor_con_entidades_se_rechaza() -> None:
    bomba = '<!DOCTYPE x [<!ENTITY a "aaaa">]><OpenSearchDescription><Url template="https://a.com/?q={searchTerms}"/>'
    assert opensearch.template_from_descriptor(bomba + "</OpenSearchDescription>") is None
    assert opensearch.template_from_descriptor("no es xml") is None


def test_descriptor_ignora_urls_de_sugerencias() -> None:
    xml = (
        '<OpenSearchDescription xmlns="http://a9.com/-/spec/opensearch/1.1/">'
        '<Url type="application/x-suggestions+json" template="https://a.com/api?q={searchTerms}"/>'
        '<Url type="text/html" method="post" template="https://a.com/post?q={searchTerms}"/>'
        '<Url type="text/html" template="https://a.com/s?q={searchTerms}"/></OpenSearchDescription>'
    )
    assert opensearch.template_from_descriptor(xml) == "https://a.com/s?q={query}"


# --- Almacén -----------------------------------------------------------------------------------------------


def test_almacen_guarda_y_persiste_en_disco(tmp_path: Path) -> None:
    ruta = tmp_path / "datos" / "d.db"
    TemplateStore(ruta).save("youtube", "https://www.youtube.com/", "https://y/?q={query}", "opensearch")
    guardada = TemplateStore(ruta).get("youtube")
    assert guardada is not None
    assert (guardada.template, guardada.source, guardada.successes) == ("https://y/?q={query}", "opensearch", 1)


def test_almacen_contadores_y_olvido() -> None:
    store = TemplateStore(":memory:")
    assert store.get("x") is None
    store.save("x", "https://x.com", "https://x.com/?q={query}", "opensearch")
    store.record_success("x")
    store.record_failure("x")
    g = store.get("x")
    assert g is not None and (g.successes, g.failures) == (2, 1)
    store.save("x", "https://x.com", "https://x.com/s?q={query}", "opensearch")  # reaprender reinicia los fallos
    g = store.get("x")
    assert g is not None and (g.template, g.failures) == ("https://x.com/s?q={query}", 0)
    store.forget("x")
    assert store.get("x") is None


# --- WebService --------------------------------------------------------------------------------------------


class FakeWeb:
    """Simula la red: un dict de URL → (estado, texto). Cuenta las descargas."""

    def __init__(self, pages: dict[str, tuple[int, str]]) -> None:
        self.pages = pages
        self.fetched: list[str] = []

    async def __call__(self, url: str) -> FetchResult:
        self.fetched.append(url)
        if url not in self.pages:
            raise httpx.ConnectError("sin red")
        status, text = self.pages[url]
        return FetchResult(url, status, text)


def youtube_web() -> FakeWeb:
    return FakeWeb(
        {
            "https://www.youtube.com": (200, fixture("youtube_home.html")),
            "https://www.youtube.com/opensearch?locale=es_ES": (200, fixture("youtube_opensearch.xml")),
            YOUTUBE_RESULTS: (200, "<html>resultados</html>"),
        }
    )


def servicio(web: FakeWeb, abiertas: list[str]) -> tuple[WebService, TemplateStore]:
    store = TemplateStore(":memory:")
    return WebService(store, web, opener=abiertas.append), store


async def test_busqueda_en_youtube_descubre_abre_y_aprende() -> None:
    web, abiertas = youtube_web(), []
    service, store = servicio(web, abiertas)
    r = await service.site_search("YouTube", "cómo hacer mochis")
    assert r["ok"] is True and r["origen"] == "opensearch"
    assert abiertas == [YOUTUBE_RESULTS]
    aprendida = store.get("youtube")
    assert aprendida is not None and aprendida.template.endswith("search_query={query}&utm_source=opensearch")


async def test_segunda_busqueda_usa_la_plantilla_aprendida_sin_tocar_la_red() -> None:
    web, abiertas = youtube_web(), []
    service, _store = servicio(web, abiertas)
    await service.site_search("youtube", "cómo hacer mochis")
    descargas_primera = len(web.fetched)
    assert descargas_primera >= 3  # portada, descriptor y comprobación de resultados

    r = await service.site_search("YouTube", "daft punk")
    assert r["origen"] == "aprendida"
    assert len(web.fetched) == descargas_primera  # nada de descubrir otra vez
    assert abiertas[-1] == "https://www.youtube.com/results?search_query=daft+punk&utm_source=opensearch"


async def test_la_plantilla_sobrevive_a_un_reinicio(tmp_path: Path) -> None:
    web, abiertas = youtube_web(), []
    await WebService(TemplateStore(tmp_path / "d.db"), web, opener=abiertas.append).site_search(
        "youtube", "cómo hacer mochis"
    )
    sin_red = FakeWeb({})
    r = await WebService(TemplateStore(tmp_path / "d.db"), sin_red, opener=abiertas.append).site_search(
        "youtube", "otra"
    )
    assert r["origen"] == "aprendida" and sin_red.fetched == []


async def test_nombre_sin_dominio_prueba_tlds_hasta_dar_con_la_web() -> None:
    web = FakeWeb(
        {
            "https://www.rutas.es": (
                200,
                '<link rel="search" type="application/opensearchdescription+xml" href="/o.xml">',
            ),
            "https://www.rutas.es/o.xml": (
                200,
                '<Url type="text/html" template="https://www.rutas.es/b?q={searchTerms}"/>',
            ),
            "https://www.rutas.es/b?q=senda": (200, "ok"),
        }
    )
    abiertas: list[str] = []
    service, _ = servicio(web, abiertas)
    r = await service.site_search("rutas", "senda")
    assert r["ok"] and abiertas == ["https://www.rutas.es/b?q=senda"]
    assert web.fetched[:2] == ["https://www.rutas.com", "https://www.rutas.es"]


async def test_dominio_explicito_no_prueba_otros() -> None:
    web, abiertas = youtube_web(), []
    service, store = servicio(web, abiertas)
    await service.site_search("https://www.youtube.com", "cómo hacer mochis")
    assert web.fetched[0] == "https://www.youtube.com"
    assert store.get("youtube.com") is not None


async def test_web_sin_opensearch_no_guarda_nada_y_explica() -> None:
    web = FakeWeb({"https://www.sinbuscador.com": (200, "<html></html>")})
    service, store = servicio(web, [])
    r = await service.site_search("sinbuscador", "algo")
    assert r["ok"] is False
    assert "opensearch" in r["mensaje"] and "navegador" in r["mensaje"]
    assert store.get("sinbuscador") is None


async def test_plantilla_que_no_funciona_no_se_aprende() -> None:
    pages = youtube_web().pages | {YOUTUBE_RESULTS: (404, "")}
    service, store = servicio(FakeWeb(pages), abiertas := [])
    r = await service.site_search("youtube", "cómo hacer mochis")
    assert r["ok"] is False and abiertas == [] and store.get("youtube") is None


async def test_plantilla_hacia_otro_dominio_se_descarta() -> None:
    xml = '<Url type="text/html" template="https://malo.example/s?q={searchTerms}"/>'
    web = FakeWeb(
        {
            "https://www.tienda.com": (
                200,
                '<link rel="search" type="application/opensearchdescription+xml" href="/o">',
            ),
            "https://www.tienda.com/o": (200, xml),
            "https://malo.example/s?q=x": (200, "ok"),
        }
    )
    service, store = servicio(web, abiertas := [])
    r = await service.site_search("tienda", "x")
    assert r["ok"] is False and abiertas == [] and store.get("tienda") is None


async def test_web_inalcanzable() -> None:
    service, _ = servicio(FakeWeb({}), [])
    r = await service.site_search("nadaquever", "x")
    assert r["ok"] is False and "dominio" in r["mensaje"]


@pytest.mark.parametrize(
    "url", ["file:///C:/Windows/System32/cmd.exe", "javascript:alert(1)", "ms-settings:", "youtube.com", ""]
)
async def test_web_open_solo_http_y_https(url: str) -> None:
    abiertas: list[str] = []
    service, _ = servicio(FakeWeb({}), abiertas)
    r = await service.open(url)
    assert r["ok"] is False and abiertas == []


async def test_web_open_abre_http() -> None:
    abiertas: list[str] = []
    service, _ = servicio(FakeWeb({}), abiertas)
    assert (await service.open("https://example.com/a?b=1"))["ok"] is True
    assert abiertas == ["https://example.com/a?b=1"]


async def test_consulta_vacia() -> None:
    service, _ = servicio(youtube_web(), [])
    assert (await service.site_search("youtube", "  "))["ok"] is False


# --- Herramientas MCP --------------------------------------------------------------------------------------


async def test_herramientas_web_publicadas_y_utilizables_por_mcp() -> None:
    abiertas: list[str] = []
    service, _ = servicio(youtube_web(), abiertas)
    async with Client(build_server(AppIndex(), web=service)) as client:
        nombres = {t.name for t in (await client.list_tools()).tools}
        r: Any = await client.call_tool("web.site_search", {"sitio": "youtube", "consulta": "cómo hacer mochis"})
    assert {"web.open", "web.site_search"} <= nombres
    assert json.loads(r.content[0].text)["url"] == YOUTUBE_RESULTS
    assert abiertas == [YOUTUBE_RESULTS]


async def test_sin_servicio_web_no_se_publican_herramientas_web() -> None:
    async with Client(build_server(AppIndex())) as client:
        nombres = {t.name for t in (await client.list_tools()).tools}
    assert nombres == {"apps.search", "apps.launch"}
