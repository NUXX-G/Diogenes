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
    respuesta = 'No hay opción de "más visto" en la página. Abrí "Palit GeForce RTX 5060 Ti Dual 8GB GDDR7" sin más.'
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
