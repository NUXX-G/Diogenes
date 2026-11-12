"""Bloque 1: menos llamadas al modelo. apps.open busca y abre en un paso, la respuesta de una acción simple verificada
la escribe el código, y el historial se recorta a saltos para no romper la caché de prefijo de Ollama en cada turno."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mcp import Client

from core.agent import Agent
from core.bench.fakes import APPS, FakePC
from core.intent import IntentGate
from core.llm.base import ToolSpec
from core.mcp_client import McpTools
from core.safety import ConfirmationPolicy
from core.toolsets import RELEVANT, ToolSelector
from hands.apps.index import AppIndex, cubre
from hands.apps.models import AppEntry
from hands.server import build_server
from tests.test_agent import POLICY, FakeBackend, FakeLLM, decir, llamar, usuario_de

SPOTIFY = AppEntry("startapps:spotify", "Spotify", "startapps", "shell", "Spotify!App")


async def abrir(nombre: str, index: AppIndex | None = None, **kw: Any) -> tuple[dict[str, Any], list[str]]:
    lanzadas: list[str] = []
    if index is None:
        index = AppIndex()
        index.replace([SPOTIFY])
    async with Client(build_server(index, launch=lambda e: lanzadas.append(e.id))) as client:
        res = await client.call_tool("apps.open", {"nombre": nombre, **kw})
    return json.loads(res.content[0].text), lanzadas  # type: ignore[union-attr]


# --- apps.open en hands --------------------------------------------------------------------------------------------


async def test_apps_open_con_ganador_claro_lanza_en_un_paso() -> None:
    data, lanzadas = await abrir("spotfy")
    assert data["lanzada"] is True and data["nombre"] == "Spotify" and lanzadas == ["startapps:spotify"]


async def test_apps_open_sin_resultados_no_abre_nada() -> None:
    data, lanzadas = await abrir("brawlhalla")
    assert data["lanzada"] is False and data["resultados"] == [] and lanzadas == []


async def test_apps_open_con_empate_devuelve_opciones_y_no_abre() -> None:
    index = AppIndex()
    index.replace([AppEntry(f"startapps:{n}", f"Discord {n}", "startapps", "shell", n) for n in ("Canary", "PTB")])
    data, lanzadas = await abrir("discord", index)
    assert data["ganador_claro"] is False and [o["n"] for o in data["opciones"]] == [1, 2] and lanzadas == []


async def test_apps_open_no_abre_si_la_orden_trae_mas_palabras_que_la_app() -> None:
    data, lanzadas = await abrir("spotify y pon daft punk")
    assert data["lanzada"] is False and lanzadas == []


async def test_apps_open_de_un_ejecutable_pide_confirmacion() -> None:
    index = AppIndex()
    index.replace([AppEntry("desktop:abc", "instalar.bat", "desktop", "file", "C:\\Users\\x\\Desktop\\instalar.bat")])
    data, lanzadas = await abrir("instalar.bat", index)
    assert data["requiere_confirmacion"] is True and lanzadas == []
    data, lanzadas = await abrir("instalar.bat", index, confirmado=True)
    assert data["lanzada"] is True and lanzadas == ["desktop:abc"]


def test_cobertura_de_la_consulta() -> None:
    bloc = AppEntry("startapps:bloc", "Bloc de notas", "startapps", "shell", "x", ("notepad",))
    assert cubre("el bloc de notas", bloc) and cubre("notepad", bloc) and cubre("bloc notas", bloc)
    assert not cubre("bloc de notas y escribe hola", bloc)
    assert cubre("vscode", AppEntry("a:b", "Visual Studio Code", "startapps", "shell", "x"))  # una palabra: índice


# --- puerta de intención -------------------------------------------------------------------------------------------


def test_puerta_apps_open_por_nombre() -> None:
    gate = IntentGate.load()
    gate.begin_turn("abre spotify")
    assert gate.check("apps.open", {"nombre": "Spotify"}).allowed is True
    assert gate.check("apps.open", {"nombre": "calculadora"}).allowed is False  # no lo ha pedido
    gate.begin_turn("cierra spotify")
    assert gate.check("apps.open", {"nombre": "Spotify"}).blocked is True


def test_un_config_antiguo_sin_apps_open_la_trata_como_apps_launch() -> None:
    from core.intent import IntentConfig

    texto = Path("configuracion/confirmaciones.example.json").read_text(encoding="utf-8")
    data = json.loads(texto)["intencion"]
    del data["herramientas"]["apps.open"]
    data["bloqueadas_al_cerrar"] = ["apps.launch"]
    config = IntentConfig.from_dict(data)
    assert config.tools["apps.open"] == config.tools["apps.launch"] and "apps.open" in config.blocked_when_closing


def test_orden_encadenada() -> None:
    gate = IntentGate.load()
    assert gate.is_compound("abre Spotify y pon Daft Punk")
    assert gate.is_compound("busca mochis en youtube, luego abre el primero")
    assert not gate.is_compound("abre el bloc de notas")
    assert not gate.is_compound("busca blanco y negro en youtube")  # la «y» no encadena otro verbo


# --- respuesta final sin modelo --------------------------------------------------------------------------------------


async def agente_pc(llm: FakeLLM, pc: FakePC | None = None) -> tuple[Agent, Any, FakePC]:
    pc = pc or FakePC()
    client = Client(pc.server())
    return Agent(llm, McpTools(client), policy=ConfirmationPolicy.load()), client, pc


async def test_abre_spotify_es_una_sola_llamada_al_modelo() -> None:
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"))
    pc = FakePC()
    async with Client(pc.server()) as client:
        resultado = await Agent(llm, McpTools(client), policy=ConfirmationPolicy.load()).run("abre Spotify")
    assert resultado.answer == "He abierto Spotify."
    assert len(resultado.metrics.model_calls) == 1 and resultado.metrics.answer_by_template
    assert pc.launched == ["startapps:spotify"]


async def test_buscar_en_una_web_es_una_sola_llamada() -> None:
    llm = FakeLLM(llamar("web_site_search", sitio="YouTube", consulta="cómo hacer mochis"))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        resultado = await agent.run("búscame en YouTube cómo hacer mochis")
    assert resultado.answer == "He buscado «cómo hacer mochis» en YouTube."
    assert len(resultado.metrics.model_calls) == 1


async def test_si_la_busqueda_no_cubre_la_orden_sigue_el_modelo() -> None:
    llm = FakeLLM(
        llamar("web_site_search", sitio="YouTube", consulta="Hrom"), decir("Me he quedado en los resultados.")
    )
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        resultado = await agent.run("dame el vídeo más visto del canal Hrom")
    assert len(resultado.metrics.model_calls) == 2 and not resultado.metrics.answer_by_template


async def test_orden_encadenada_vuelve_al_modelo_tras_la_primera_accion() -> None:
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"), decir("He abierto Spotify, pero no sé poner música aún."))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        resultado = await agent.run("abre Spotify y pon Daft Punk")
    assert resultado.answer.startswith("He abierto Spotify, pero")


async def test_pausar_contesta_con_el_mensaje_verificado_de_la_herramienta() -> None:
    llm = FakeLLM(llamar("media_control", accion="pausa", sesion=""))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        resultado = await agent.run("pausa el vídeo de YouTube")
    assert resultado.answer.startswith("Hecho y comprobado")
    assert len(resultado.metrics.model_calls) == 1


async def test_cerrar_lo_que_el_usuario_nombro_es_una_llamada() -> None:
    pc = FakePC()
    pc.windows.open(next(a for a in APPS if a.id == "startapps:calc"))
    llm = FakeLLM(llamar("apps_close", id="calculadora"))
    async with Client(pc.server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        resultado = await agent.run("cierra la calculadora")
    assert resultado.answer == "He cerrado Calculadora." and pc.windows.windows == []


async def test_varias_opciones_se_preguntan_sin_modelo() -> None:
    llm = FakeLLM(llamar("apps_open", nombre="editor"))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        resultado = await agent.run("abre el editor")
    assert resultado.answer.endswith("¿Cuál quieres?") and "1. " in resultado.answer
    assert len(resultado.metrics.model_calls) == 1


async def test_el_modelo_no_puede_abrir_por_su_cuenta_lo_que_no_se_pidio() -> None:
    preguntas: list[str] = []

    async def no(tool: str, _args: dict[str, Any]) -> bool:
        preguntas.append(tool)
        return False

    pc = FakePC()
    llm = FakeLLM(llamar("apps_open", nombre="Calculadora"), decir("No la he abierto."))
    async with Client(pc.server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), confirm=no)
        await agent.run("abre Spotify")
    assert preguntas == ["apps.open"] and pc.launched == []


# --- historial recortado a saltos ------------------------------------------------------------------------------------


async def test_el_recorte_del_historial_es_a_saltos_y_no_en_cada_turno() -> None:
    llm = FakeLLM(*[decir(f"r{i}") for i in range(12)])
    agente = Agent(llm, FakeBackend(), policy=POLICY, max_turns=9)
    primeros: list[str] = []
    for i in range(12):
        await agente.run(f"p{i}")
        primeros.append(usuario_de(list(agente.history))[0])
    # el principio de la conversación (lo que Ollama tiene en caché) cambia una sola vez en 12 turnos
    assert len(set(primeros)) == 2
    assert all(len(usuario_de(vista)) <= 9 for vista in llm.seen)


# --- herramientas relevantes (opcional) ------------------------------------------------------------------------------


def _specs(*names: str) -> tuple[list[ToolSpec], dict[str, str]]:
    return [ToolSpec(n.replace(".", "_"), "", {}) for n in names], {n.replace(".", "_"): n for n in names}


def test_por_defecto_se_ensenan_todas_las_herramientas() -> None:
    specs, to_mcp = _specs("apps.open", "media.control", "browser.click")
    assert ToolSelector.load().select(specs, to_mcp, "abre Spotify") == specs


def test_relevantes_quita_grupos_sin_pistas_y_los_deja_al_final() -> None:
    specs, to_mcp = _specs("browser.click", "apps.open", "media.control", "web.open")
    selector = ToolSelector.load(RELEVANT)
    assert [s.name for s in selector.select(specs, to_mcp, "abre Spotify")] == ["apps_open", "web_open"]
    pausa = selector.select(specs, to_mcp, "pausa la música")
    assert [s.name for s in pausa] == ["apps_open", "web_open", "media_control"]
    canal = selector.select(specs, to_mcp, "dame el vídeo más visto del canal Hrom")
    assert [s.name for s in canal][-2:] == ["media_control", "browser_click"]
    assert [s.name for s in selector.select(specs, to_mcp, "ve a ejemplo.com")][-1] == "browser_click"
    assert selector.select(specs, to_mcp, "el segundo", everything=True) == specs
    assert [s.name for s in selector.select(specs, to_mcp, "pestaña vídeos", in_web=True)][-1] == "browser_click"


async def test_el_agente_ensena_solo_las_relevantes_si_se_pide() -> None:
    backend = FakeBackend(names=("apps.open", "media.control", "browser.click"))
    llm = FakeLLM(decir("vale"))
    await Agent(llm, backend, policy=POLICY, toolset=ToolSelector.load(RELEVANT)).run("hola")
    assert [t.name for t in llm.tools_seen[0]] == ["apps_open"]


def test_el_modelo_ve_una_pagina_recortada_y_lo_nuevo_primero() -> None:
    from core.agent import MODEL_PAGE_CHARS, _present

    lineas = [f'[e{i}] link "{"vídeo larguísimo " * 8}{i}" -> /watch?v={"x" * 60}' for i in range(1, 80)]
    pagina = "[CONTENIDO DE LA PÁGINA]\nurl: u\ntítulo: t\nNUEVO tras tu acción:\n" + "\n".join(lineas)
    visto = _present(json.dumps({"ok": True, "mensaje": "Clic", "pagina": pagina}))
    assert len(visto) < MODEL_PAGE_CHARS + 200 and "NUEVO tras tu acción" in visto
    assert "[e1] link" in visto and "elementos más no mostrados" in visto and "x" * 60 not in visto
