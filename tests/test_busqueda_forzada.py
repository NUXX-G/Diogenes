"""Si el usuario pide una acción y el modelo contesta sin haber llamado a nada, el código busca antes de responder.

Caso real (PC de clase): «abre prueba nueva» (un archivo del escritorio) → «No tengo información sobre una app o sitio
llamado prueba nueva. No la puedo abrir», con 0 acciones.
"""

from __future__ import annotations

import json

import pytest

from core.agent import SYSTEM_PROMPT, Agent, _without_tool_names
from core.intent import IntentGate
from tests.test_agent import POLICY, FakeBackend, FakeLLM, decir, llamar

SIN_INFORMACION = "No tengo información sobre una app o sitio llamado prueba nueva. No la puedo abrir."
ENCONTRADA = json.dumps(
    {
        "ok": True,
        "resultados": [{"id": "desktop:717e7cd8", "nombre": "prueba nueva", "origen": "desktop"}],
        "ganador_claro": True,
        "id_ganador": "desktop:717e7cd8",
    }
)


async def test_orden_contestada_sin_herramientas_fuerza_la_busqueda_y_sigue() -> None:
    backend = FakeBackend({"apps.search": ENCONTRADA})
    llm = FakeLLM(
        decir(SIN_INFORMACION), llamar("apps_launch", id="desktop:717e7cd8"), decir("He abierto prueba nueva.")
    )
    resultado = await Agent(llm, backend, policy=POLICY).run("abre prueba nueva")

    assert backend.calls == [
        ("apps.search", {"nombre": "prueba nueva"}),
        ("apps.launch", {"id": "desktop:717e7cd8"}),
    ]
    assert [s.tool for s in resultado.steps] == ["apps.search", "apps.launch"]
    assert resultado.answer == "He abierto prueba nueva."
    # el modelo recibe el resultado de la búsqueda antes de volver a responder
    ultimo = llm.seen[1][-1]
    assert (ultimo.role, ultimo.tool_name, ultimo.content) == ("tool", "apps_search", ENCONTRADA)
    # y su respuesta sin comprobar no queda en la conversación
    assert all(SIN_INFORMACION not in m.content for m in llm.seen[1])


async def test_la_busqueda_se_fuerza_una_sola_vez_por_turno() -> None:
    backend = FakeBackend({"apps.search": json.dumps({"ok": True, "resultados": []})})
    llm = FakeLLM(decir(SIN_INFORMACION), decir("No la he encontrado en el equipo."))
    resultado = await Agent(llm, backend, policy=POLICY).run("abre prueba nueva")
    assert backend.calls == [("apps.search", {"nombre": "prueba nueva"})]
    assert resultado.answer == "No la he encontrado en el equipo."  # ahora sí está comprobado


async def test_sin_verbo_de_accion_no_se_fuerza_nada() -> None:
    backend = FakeBackend()
    llm = FakeLLM(decir("Soy Diogenes, y vivo en una tinaja."))
    resultado = await Agent(llm, backend, policy=POLICY).run("¿quién eres?")
    assert backend.calls == [] and resultado.steps == []


async def test_si_el_modelo_ya_llamo_a_una_herramienta_no_se_fuerza() -> None:
    backend = FakeBackend({"apps.search": ENCONTRADA})
    llm = FakeLLM(llamar("apps_search", nombre="prueba"), decir("Hay una, pero no la abro."))
    await Agent(llm, backend, policy=POLICY).run("abre prueba nueva")
    assert backend.calls == [("apps.search", {"nombre": "prueba"})]


async def test_sin_herramienta_de_busqueda_no_se_fuerza() -> None:
    backend = FakeBackend(names=("media.key",))
    llm = FakeLLM(decir("No puedo."))
    resultado = await Agent(llm, backend, policy=POLICY).run("abre prueba nueva")
    assert backend.calls == [] and resultado.answer == "No puedo."


async def test_cerrar_tambien_busca_el_objetivo() -> None:
    backend = FakeBackend({"apps.search": ENCONTRADA}, names=("apps.search", "apps.launch", "apps.close"))
    llm = FakeLLM(decir("No sé qué es eso."), decir("No está abierta."))
    await Agent(llm, backend, policy=POLICY).run("cierra la calculadora")
    assert backend.calls == [("apps.search", {"nombre": "calculadora"})]


@pytest.mark.parametrize(
    ("mensaje", "objetivo"),
    [
        ("abre prueba nueva", "prueba nueva"),
        ("ábreme la calculadora", "calculadora"),
        ("Diogenes, abre prueba nueva, por favor", "prueba nueva"),
        ("cierra la calculadora", "calculadora"),
        ("pon Daft Punk en Spotify", "daft punk en spotify"),
        ("búscame cómo hacer mochis", "como hacer mochis"),
        ("ciérralo", None),
        ("abre eso", None),
        ("¿qué hora es?", None),
        ("gracias", None),
    ],
)
def test_objetivo_extraido_del_mensaje(mensaje: str, objetivo: str | None) -> None:
    assert IntentGate.load().action_target(mensaje) == objetivo


# --- nombres internos de herramientas ---------------------------------------------------------------------------


async def test_la_respuesta_final_no_muestra_nombres_internos() -> None:
    backend = FakeBackend(names=("apps.search", "apps.close", "media.key"))
    llm = FakeLLM(decir("He usado apps_close y `media_key()`; luego apps.search."))
    resultado = await Agent(llm, backend, policy=POLICY).run("hola")
    for interno in ("apps_close", "media_key", "apps.search", "`", "()"):
        assert interno not in resultado.answer
    assert resultado.answer == "He usado cerrar la app y las teclas multimedia; luego la búsqueda de apps."


def test_nombres_desconocidos_caen_en_una_etiqueta_generica() -> None:
    nombres = {"browser_snapshot", "browser.snapshot", "clipboard_read"}
    texto = _without_tool_names("Llamé a browser_snapshot y a clipboard_read.", nombres)
    assert texto == "Llamé al navegador y a una herramienta."


def test_palabras_parecidas_no_se_tocan() -> None:
    assert _without_tool_names("Mis apps_closet favoritas", {"apps_close"}) == "Mis apps_closet favoritas"


def test_el_prompt_prohibe_nombres_internos_y_abrir_al_cerrar() -> None:
    assert "nunca nombres tus herramientas internas" in SYSTEM_PROMPT
    assert "NUNCA llames a apps_launch" in SYSTEM_PROMPT
    assert "búscalo primero" in SYSTEM_PROMPT
