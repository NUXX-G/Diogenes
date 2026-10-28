"""Avisos que tapan la página, nombres de tarjetas con precio y navegador como respaldo de la descarga HTTP."""

from __future__ import annotations

from hands.browser import snapshot
from hands.browser.rules import BrowserRules
from hands.browser.service import BrowserService, ClickIntercepted
from hands.web.fetch import FetchResult
from hands.web.service import WebService
from hands.web.store import TemplateStore
from tests.test_browser import FakePage, Provider
from tests.test_web import FakeWeb
from tests.test_web_browser import FakeSite, estrategia

RULES = BrowserRules.load()

TARJETA = (
    '- link "MSI GeForce RTX 5060 Ti VENTUS 2X OC PLUS 16GB GDDR7 MSI GeForce RTX 5060 Ti VENTUS 2X OC PLUS 16GB GDDR7 '
    '889,99€ 4,8 de 5 estrellas 541 opiniones Envío gratis. Entrega mañana" [ref=e1]:\n'
    "  - /url: /tarjeta-msi\n"
    '- heading "MSI GeForce RTX 5060 Ti VENTUS 2X OC PLUS 16GB GDDR7" [level=3] [ref=e2]\n'
)


# --- nombres legibles ------------------------------------------------------------------------------------


def test_la_tarjeta_de_producto_pierde_el_titulo_repetido_y_conserva_el_precio() -> None:
    out = snapshot.compact(TARJETA)
    linea = next(line for line in out.splitlines() if line.startswith("[e1]"))
    assert linea.count("MSI GeForce RTX 5060 Ti") == 1
    assert "889,99€" in linea
    assert "[e2]" not in out  # el encabezado repetía al enlace


def test_precios_en_otros_formatos() -> None:
    out = snapshot.compact('- link "Auriculares X ' + "x" * 120 + ' 49,90 € y también $59.99 en oferta" [ref=e1]')
    assert "49,90 €" in out and "$59.99" in out


def test_nombre_corto_sin_precio_no_cambia() -> None:
    assert '[e1] link "Inicio"' in snapshot.compact('- link "Inicio" [ref=e1]')


# --- avisos que tapan -------------------------------------------------------------------------------------

PAGINA_CON_AVISO = """\
- main:
  - button "Ordenar" [ref=e1]
  - heading "Cookies" [level=4] [ref=e2]
  - button "Aceptar todas" [ref=e3]
  - button "Rechazar todas" [ref=e4]
"""


class PageWithNotice(FakePage):
    """Mientras el aviso esté abierto, cualquier clic fuera de él lo intercepta (como un overlay real)."""

    def __init__(self, aria: str = PAGINA_CON_AVISO, *, closable: bool = True) -> None:
        super().__init__(aria, navigates=False)
        self.notice_open = True
        self._closable = closable

    async def click(self, ref: str) -> None:
        if ref in {"e3", "e4"}:
            self.calls.append(("click", ref))
            self.notice_open = not (ref == "e4" and self._closable) and self.notice_open
            return
        if self.notice_open:
            raise ClickIntercepted(ref)
        self.calls.append(("click", ref))


async def test_un_aviso_que_tapa_se_cierra_rechazando_y_se_reintenta() -> None:
    page = PageWithNotice()
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    res = await svc.click("e1")
    assert res["ok"] is True
    assert "Rechazar todas" in res["mensaje"]  # se le dice al modelo qué se hizo
    assert page.calls == [("click", "e4"), ("click", "e1")]  # nunca «Aceptar todas»


async def test_sin_boton_de_rechazar_no_se_acepta_nada() -> None:
    aria = '- main:\n  - button "Ordenar" [ref=e1]\n  - button "Aceptar todas" [ref=e3]'
    page = PageWithNotice(aria)
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    res = await svc.click("e1")
    assert res["ok"] is False and "tapa" in res["mensaje"]
    assert page.calls == []


async def test_si_tras_cerrar_el_aviso_sigue_tapado_falla_sin_bucle() -> None:
    page = PageWithNotice(closable=False)
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    res = await svc.click("e1")
    assert res["ok"] is False
    assert page.calls == [("click", "e4")]


async def test_un_boton_de_pago_nunca_se_pulsa_para_cerrar_avisos() -> None:
    reglas = BrowserRules.from_lists([], ["rechazar"], [], ["rechazar"])  # contradicción de configuración
    page = PageWithNotice()
    svc = BrowserService(Provider(page), reglas)
    await svc.snapshot()
    assert (await svc.click("e1"))["ok"] is False
    assert ("click", "e4") not in page.calls


# --- si la descarga HTTP falla, el navegador lo intenta -----------------------------------------------------


async def test_descarga_http_bloqueada_pero_el_navegador_descubre_el_buscador() -> None:
    site = FakeSite()
    abiertas: list[str] = []
    web = FakeWeb({})  # toda descarga HTTP falla, también la comprobación de resultados
    service = WebService(
        TemplateStore(":memory:"),
        web,
        opener=abiertas.append,
        strategies=[estrategia(site)],
        browser=BrowserService(Provider(site), RULES),  # type: ignore[arg-type]
    )
    r = await service.site_search("www.ejemplo.com", "rtx 5060")
    assert r["ok"] is True and r["estrategia"] == "navegador" and r["abierto_en"] == "navegador de Diogenes"
    assert site.visited[-1] == "https://www.ejemplo.com/buscar?q=rtx+5060"
    assert abiertas == []


async def test_nombre_sin_dominio_no_se_adivina_con_el_navegador_si_falla_http() -> None:
    site = FakeSite()
    service = WebService(TemplateStore(":memory:"), FakeWeb({}), strategies=[estrategia(site)])
    r = await service.site_search("ejemplo", "x")  # varios dominios posibles: sin HTTP no se sabe cuál
    assert r["ok"] is False and site.visited == []


async def test_otras_estrategias_siguen_verificando_por_http() -> None:
    class Dudosa:
        name = "dudosa"

        async def discover(self, page: FetchResult, query: str) -> str | None:
            return "https://www.ejemplo.com/s?q={query}"

    web = FakeWeb({"https://www.ejemplo.com": (200, "<html></html>")})  # la URL de resultados no responde
    service = WebService(TemplateStore(":memory:"), web, opener=lambda _u: None, strategies=[Dudosa()])
    assert (await service.site_search("www.ejemplo.com", "x"))["ok"] is False


# --- lo nuevo tras una acción va primero (menús y avisos se pintan al final del árbol) ------------------------


def pagina_larga(extra: str = "") -> str:
    productos = "\n".join(f'- link "Producto número {i} {i},99€" [ref=e{i + 10}]' for i in range(200))
    return f'- main:\n  - button "Ordenar" [ref=e1]\n{productos}\n{extra}'


MENU = '- menu:\n  - menuitem "Precio: de menor a mayor" [ref=e900]\n  - menuitem "Relevancia" [ref=e901]'


async def test_las_opciones_de_un_menu_desplegado_salen_primero_aunque_esten_al_final_del_arbol() -> None:
    page = FakePage(pagina_larga(), navigates=False, on_click={"e1": pagina_larga(MENU)})
    svc = BrowserService(Provider(page), RULES)
    abierta = await svc.snapshot()
    assert "Precio: de menor a mayor" not in abierta["pagina"]  # no cabe entre los 70 primeros
    res = await svc.click("e1")
    texto = res["pagina"]
    assert snapshot.FRESH_HEADER in texto
    assert texto.index("Precio: de menor a mayor") < texto.index("Producto número 0")
    assert (await svc.click("e900"))["ok"] is True  # y su ref es utilizable sin otro snapshot


async def test_si_no_aparece_nada_nuevo_no_hay_seccion_nuevo() -> None:
    page = FakePage(pagina_larga(), navigates=False)
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    assert snapshot.FRESH_HEADER not in (await svc.click("e1"))["pagina"]


async def test_al_cambiar_de_url_no_se_marca_nada_como_nuevo() -> None:
    page = FakePage(pagina_larga(), navigates=True, on_click={"e1": pagina_larga(MENU)})
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    assert snapshot.FRESH_HEADER not in (await svc.click("e1"))["pagina"]


# --- elementos que la web hace clicables sin ser controles ARIA (opciones de un desplegable) ----------------

DESPLEGABLE = """\
- main:
  - button "Ordenar" [active] [ref=e1] [cursor=pointer]
  - list:
    - listitem [ref=e2] [cursor=pointer]: Relevancia
    - listitem [ref=e3] [cursor=pointer]: Precio más bajo
    - listitem [ref=e4]: Texto que no se puede pulsar
    - listitem [ref=e5] [cursor=pointer]: Comprar ahora
"""


def test_las_opciones_clicables_de_un_desplegable_se_listan() -> None:
    out = snapshot.compact(DESPLEGABLE)
    assert '[e3] listitem "Precio más bajo"' in out
    assert "[e4]" not in out  # sin cursor de puntero no es clicable


async def test_una_opcion_clicable_se_puede_pulsar() -> None:
    page = FakePage(DESPLEGABLE, navigates=False)
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    assert (await svc.click("e3"))["ok"] is True
    assert ("click", "e3") in page.calls


async def test_las_reglas_de_pago_tambien_valen_para_elementos_clicables_que_no_son_botones() -> None:
    page = FakePage(DESPLEGABLE, navigates=False)
    svc = BrowserService(Provider(page), RULES)
    await svc.snapshot()
    res = await svc.click("e5")
    assert res["ok"] is False and res["parar"] is True
    assert ("click", "e5") not in page.calls
