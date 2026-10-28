"""Honestidad del resumen final: superlativos solo con una ordenación de la web que los garantice, nombres tal cual."""

from __future__ import annotations

import json
from typing import Any

import pytest

from core.agent import SYSTEM_PROMPT, Step, _grounded
from hands.browser.rules import BrowserRules
from tests.test_agent import FakeLLM, decir, llamar
from tests.test_browser import FakePage
from tests.test_browser_safety import aprobar, ejecutar_con

RULES = BrowserRules.load()

CANAL = """\
- main:
  - tab "Inicio" [ref=e1]
  - tab "Vídeos" [ref=e2]
  - tab "Populares" [ref=e3]
  - tab "Precio más bajo" [ref=e6]
  - link "Vídeo A de prueba 10 minutos" [ref=e4]:
    - /url: /watch?v=a
"""
CANAL_ORDENADO = CANAL.replace("Vídeo A de prueba 10 minutos", "Vídeo B de prueba 5 minutos")


def pagina(on_click: dict[str, str] | None = None, navigates: bool = False) -> FakePage:
    return FakePage(CANAL, navigates=navigates, on_click=on_click or {})


# --- reglas de la configuración -----------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("rol", "nombre", "esperado"),
    [
        ("tab", "Populares", ["visto"]),
        ("listitem", "Precio más bajo", ["barato"]),
        ("option", "Precio: de menor a mayor", ["barato"]),
        ("tab", "Subidas recientes", ["reciente"]),
        ("button", "Ordenar", []),  # abrir el desplegable no garantiza nada
        ("button", "Búsquedas recientes", []),
        ("heading", "Populares", []),  # no es algo que se pulse
    ],
)
def test_criterio_que_aplica_pulsar_un_elemento(rol: str, nombre: str, esperado: list[str]) -> None:
    assert RULES.order_criteria_of(rol, nombre) == esperado


# --- superlativos con navegador falso + agente ----------------------------------------------------------------


async def test_sin_ordenar_no_se_puede_afirmar_el_mas_visto() -> None:
    llm = FakeLLM(llamar("browser_snapshot"), llamar("browser_click", ref="e4"), decir("Este es el vídeo más visto."))
    resultado, _, _ = await ejecutar_con(llm, pagina(navigates=True), aprobar())
    assert resultado.answer.startswith("No he podido comprobar que sea el más visto")
    assert "ninguna ordenación ni filtro" in resultado.answer
    assert "/watch?v=a" in resultado.answer  # dice dónde se quedó, que es lo que sí sabe


async def test_con_la_ordenacion_de_la_web_y_la_pagina_cambiada_si_se_puede() -> None:
    page = pagina({"e3": CANAL_ORDENADO})
    llm = FakeLLM(
        llamar("browser_snapshot"), llamar("browser_click", ref="e3"), decir("El vídeo más visto es el de 5 minutos.")
    )
    resultado, _, _ = await ejecutar_con(llm, page, aprobar())
    assert resultado.answer == "El vídeo más visto es el de 5 minutos."


async def test_pulsar_populares_sin_que_la_pagina_cambie_no_garantiza_nada() -> None:
    llm = FakeLLM(
        llamar("browser_snapshot"), llamar("browser_click", ref="e3"), decir("El vídeo más visto es el primero.")
    )
    resultado, _, _ = await ejecutar_con(llm, pagina(), aprobar())
    assert resultado.answer.startswith("No he podido comprobar que sea el más visto")


async def test_ordenar_por_otro_criterio_no_respalda_la_afirmacion() -> None:
    llm = FakeLLM(
        llamar("browser_snapshot"), llamar("browser_click", ref="e6"), decir("Te abro el vídeo más visto del canal.")
    )
    resultado, _, _ = await ejecutar_con(llm, pagina({"e6": CANAL_ORDENADO}), aprobar())
    assert resultado.answer.startswith("No he podido comprobar que sea el más visto")


async def test_precio_mas_bajo_respalda_el_mas_barato_pero_no_el_mas_visto() -> None:
    ok = FakeLLM(llamar("browser_snapshot"), llamar("browser_click", ref="e6"), decir("El más barato cuesta 10 €."))
    assert (await ejecutar_con(ok, pagina({"e6": CANAL_ORDENADO}), aprobar()))[0].answer == "El más barato cuesta 10 €."
    mal = FakeLLM(
        llamar("browser_snapshot"),
        llamar("browser_click", ref="e6"),
        decir("El más barato y el más visto es el primero."),
    )
    texto = (await ejecutar_con(mal, pagina({"e6": CANAL_ORDENADO}), aprobar()))[0].answer
    assert "el más visto" in texto and "el más barato" not in texto  # solo lo que no está respaldado


async def test_decir_que_no_se_pudo_comprobar_no_se_toca() -> None:
    llm = FakeLLM(llamar("browser_snapshot"), decir("No he podido determinar cuál es el vídeo más visto."))
    resultado, _, _ = await ejecutar_con(llm, pagina(), aprobar())
    assert resultado.answer == "No he podido determinar cuál es el vídeo más visto."


def test_sin_navegador_en_el_turno_no_se_aplica() -> None:
    assert _grounded("La tienda más barata es X.", []) == "La tienda más barata es X."
    paso = Step("apps.search", {}, '{"ok": true}', True)
    assert _grounded("Es la app más vendida.", [paso]) == "Es la app más vendida."


def test_el_ultimo_exige_un_orden_reciente() -> None:
    paso = Step(
        "browser.snapshot", {}, json.dumps({"ok": True, "url": "https://x.com", "titulo": "X", "pagina": "p"}), True
    )
    assert "no he podido comprobar que sea el más reciente" in _grounded("Abrí el último vídeo.", [paso]).lower()


# --- nombres tal cual ------------------------------------------------------------------------------------------


def paso_pagina(texto: str) -> Step:
    return Step(
        "browser.snapshot", {}, json.dumps({"ok": True, "url": "https://t.com", "titulo": "T", "pagina": texto}), True
    )


PAGINA = '[e1] link "Palit GeForce RTX 5060 Ti Dual 8GB GDDR7 Reflex 2 RTX AI DLSS4 · 575,95€"'


def test_nombre_citado_tal_cual_pasa() -> None:
    respuesta = "Cuesta 575,95 € («Palit GeForce RTX 5060 Ti Dual 8GB GDDR7»)."
    assert _grounded(respuesta, [paso_pagina(PAGINA)]) == respuesta


def test_nombre_citado_truncado_con_puntos_suspensivos_pasa() -> None:
    respuesta = "Cuesta 575,95 € (Palit GeForce RTX 5060 Ti Dual 8GB GDDR7 Reflex 2...)."
    assert _grounded(respuesta, [paso_pagina(PAGINA)]) == respuesta


def test_nombre_al_que_se_le_quita_la_ti_se_avisa() -> None:
    respuesta = "Cuesta 575,95 € («Palit GeForce RTX 5060 Dual 8GB GDDR7»)."
    texto = _grounded(respuesta, [paso_pagina(PAGINA)])
    assert texto.startswith(respuesta) and "no coincide literalmente" in texto


def test_sin_paginas_vistas_no_se_inventan_avisos() -> None:
    assert _grounded("Es «un nombre cualquiera largo».", []) == "Es «un nombre cualquiera largo»."


# --- prompt -----------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "frase",
    ['Nunca digas "el más visto"', "no he podido comprobar que sea", "tal cual aparecen en la página", "RTX 5060 Ti"],
)
def test_el_prompt_lleva_las_reglas(frase: str) -> None:
    assert frase.replace("\\", "") in SYSTEM_PROMPT.replace("\\\n", "")


def test_dos_citas_con_comillas_rectas_no_se_emparejan_entre_si() -> None:
    respuesta = 'No hay opción de "más visto" en la página. Vi "Palit GeForce RTX 5060 Ti Dual 8GB GDDR7" sin más.'
    assert _grounded(respuesta, [paso_pagina(PAGINA)]) == respuesta


# --- «he abierto…» tras un clic fallido -------------------------------------------------------------------------


def clic(ok: bool, **extra: Any) -> Step:
    mensaje = "No se pudo hacer clic en e4: otro elemento lo tapa." if not ok else "Clic en e4"
    return Step("browser.click", {"ref": "e4"}, json.dumps({"ok": ok, "mensaje": mensaje, **extra}), ok)


def test_he_abierto_tras_un_clic_fallido_se_corrige() -> None:
    texto = _grounded("He abierto el vídeo «Mi vídeo».", [clic(False)])
    assert texto.startswith("No lo he conseguido") and "tapa" in texto


def test_he_abierto_tras_un_clic_que_cambio_la_url_se_respeta() -> None:
    respuesta = "He abierto el vídeo."
    assert _grounded(respuesta, [clic(True, url_cambio=True, cambio_visible=True)]) == respuesta


def test_he_abierto_con_un_clic_que_no_cambio_nada_se_corrige() -> None:
    texto = _grounded("He abierto el vídeo.", [clic(True, url_cambio=False, cambio_visible=False)])
    assert "no ha cambiado" in texto


# --- «he abierto "X"» sin haber llegado a X ---------------------------------------------------------------------


def lista(titulo: str, url: str = "https://yt.com/results") -> Step:
    pagina_ = '[e4] link "Os enseño mi mayor desastre financiero y ponemos un alerón a un M4 29 minutos"'
    return Step("browser.snapshot", {}, json.dumps({"ok": True, "url": url, "titulo": titulo, "pagina": pagina_}), True)


def test_dice_haber_abierto_un_video_que_solo_vio_en_una_lista() -> None:
    respuesta = 'He abierto el primer vídeo: "Os enseño mi mayor desastre financiero y ponemos un alerón a un M4".'
    texto = _grounded(respuesta, [lista("canal Hrom - YouTube")])
    assert texto.startswith("Corrección: no he abierto «Os enseño") and "canal Hrom - YouTube" in texto
    assert texto.endswith(respuesta)  # se conserva lo que dijo, con la corrección delante


def test_abrir_un_elemento_cuyo_titulo_es_el_de_la_pagina_actual_se_respeta() -> None:
    respuesta = 'He abierto "Os enseño mi mayor desastre financiero y ponemos un alerón a un M4".'
    pagina_final = lista(
        "Os enseño mi mayor desastre financiero y ponemos un alerón a un M4 - YouTube", "https://yt.com/watch?v=1"
    )
    assert _grounded(respuesta, [lista("canal Hrom - YouTube"), pagina_final]) == respuesta


def test_he_abierto_la_busqueda_no_se_confunde_con_abrir_un_elemento() -> None:
    respuesta = "He abierto la búsqueda de asyncio en el navegador."
    assert _grounded(respuesta, [lista("asyncio - Buscar")]) == respuesta


# --- la ordenación solo vale mientras la lista ordenada siga siendo aquella de la que se responde ---------------


def orden(url: str, **extra: Any) -> Step:
    datos = {"ok": True, "url": url, "titulo": "T", "url_cambio": False, "cambio_visible": True, "criterio": ["barato"]}
    return Step("browser.click", {"ref": "e1"}, json.dumps({**datos, **extra}), True)


def navegar(url: str, tool: str = "browser.click") -> Step:
    datos = {"ok": True, "url": url, "titulo": "T", "url_cambio": True, "cambio_visible": True, "criterio": []}
    return Step(tool, {"ref": "e2"}, json.dumps(datos), True)


LISTA = "https://tienda.com/search?query=rtx"


def test_ordenar_y_abrir_un_elemento_de_la_lista_respalda_el_mas_barato() -> None:
    pasos = [orden(LISTA + "&sort=price_asc", url_cambio=True), navegar("https://tienda.com/producto/palit")]
    assert _grounded("El más barato es la Palit.", pasos) == "El más barato es la Palit."


def test_ordenar_y_lanzar_otra_busqueda_en_la_misma_pagina_pierde_el_respaldo() -> None:
    """Caso real de pccomponentes: tras «Precio más bajo» pulsó una sugerencia del buscador."""
    pasos = [orden(LISTA), navegar("https://tienda.com/search/?query=rtx+5060+16gb")]
    assert "no he podido comprobar que sea el más barato" in _grounded("La más barata es la ASUS.", pasos).lower()


def test_volver_a_abrir_una_url_pierde_el_respaldo() -> None:
    pasos = [orden(LISTA), navegar("https://tienda.com/search?query=otra", tool="web.site_search")]
    assert "no he podido comprobar" in _grounded("El más barato es X.", pasos).lower()


def test_un_clic_que_no_cambia_de_url_mantiene_el_respaldo() -> None:
    pasos = [
        orden(LISTA),
        Step(
            "browser.click",
            {"ref": "e3"},
            json.dumps({"ok": True, "url": LISTA, "url_cambio": False, "cambio_visible": True, "criterio": []}),
            True,
        ),
    ]
    assert _grounded("El más barato es X.", pasos) == "El más barato es X."


# --- precios: lo que cita la respuesta tiene que haberse visto -------------------------------------------------


def pagina_con_precios() -> Step:
    return paso_pagina('[e1] link "Palit RTX 5060 Ti · 575,95€"\n[e2] link "Gigabyte AERO OC 1.019€"')


def test_un_precio_visto_en_la_pagina_pasa() -> None:
    respuesta = "La más barata cuesta 575,95 €, y la AERO 1.019 euros."
    assert "Corrección" not in _grounded(respuesta, [pagina_con_precios(), orden_barato()])


def test_un_precio_que_no_esta_en_ninguna_pagina_se_corrige_y_se_conserva_el_resto() -> None:
    """Caso real: el modelo dijo 599,99 € cuando la primera costaba 575,95 €."""
    respuesta = "El precio de la RTX 5060 más barata es de 599,99 €."
    texto = _grounded(respuesta, [pagina_con_precios(), orden_barato()])
    assert texto.startswith("Corrección: 599,99 € no aparece en ninguna página")
    assert texto.endswith(respuesta)


def test_sin_paginas_vistas_no_se_juzgan_precios() -> None:
    assert _grounded("Cuesta 12 €.", []) == "Cuesta 12 €."


def orden_barato() -> Step:
    datos = {"ok": True, "url": LISTA + "&sort=price_asc", "titulo": "T", "url_cambio": True, "cambio_visible": True}
    return Step("browser.click", {"ref": "e9"}, json.dumps({**datos, "criterio": ["barato"]}), True)


# --- «he abierto el resultado» cuando lo último fue ordenar -----------------------------------------------------


def test_he_abierto_el_resultado_tras_ordenar_se_corrige_y_se_conserva_lo_demas() -> None:
    respuesta = "La más barata es la Palit a 575,95 €. He abierto el resultado correspondiente."
    texto = _grounded(respuesta, [pagina_con_precios(), orden_barato()])
    assert texto.startswith("Corrección: no he abierto ningún resultado")
    assert texto.endswith(respuesta)


def test_abrir_un_resultado_despues_de_ordenar_si_se_respeta() -> None:
    respuesta = "La más barata es la Palit a 575,95 €. He abierto el producto."
    pasos = [pagina_con_precios(), orden_barato(), navegar("https://tienda.com/producto/palit")]
    assert _grounded(respuesta, pasos) == respuesta
