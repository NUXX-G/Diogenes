"""Bloque 3: modo enseñar. /aprender <frase> graba lo que el usuario guía paso a paso, /fin lo guarda como receta con
huecos y /cancelar lo descarta. Hito: enseñado con un canal, funciona con otro sin el planificador."""

from __future__ import annotations

from typing import Any

from mcp import Client

from core.agent import Agent
from core.bench.fakes import FakePC
from core.mcp_client import McpTools
from core.recipes import RecipeStore
from core.safety import ConfirmationPolicy
from core.session import Session
from tests.test_agent import FakeLLM, decir, llamar

FRASE = "dame el vídeo más visto del canal Hrom"


def guion_hrom() -> list[Any]:
    """Lo que hace el modelo guiado: refs del sitio falso (resultados → canal → Vídeos → Populares → primero)."""
    return [
        # «abre el canal Hrom en youtube»
        llamar("web_site_search", sitio="YouTube", consulta="Hrom"),
        llamar("browser_snapshot"),
        llamar("browser_click", ref="e3"),  # link "Hrom" -> /@hrom
        decir("Estoy en el canal."),
        # «pestaña vídeos»
        llamar("browser_click", ref="e3"),  # tab "Vídeos"
        decir("Estoy en Vídeos."),
        # «ordena por populares»
        llamar("browser_click", ref="e6"),  # button "Populares"
        decir("Ordenado."),
        # «abre el primero»
        llamar("browser_click", ref="e7"),  # el primero de la lista ya ordenada
        decir("Abierto."),
    ]


async def ensenar(agent: Agent) -> str:
    assert agent.command(f"/aprender {FRASE}").startswith("Grabando")  # type: ignore[union-attr]
    for paso in ("abre el canal Hrom en youtube", "pestaña vídeos", "ordena por populares", "abre el primero"):
        await agent.run(paso)
    return agent.command("/fin")  # type: ignore[return-value]


async def test_ensenado_con_un_canal_funciona_con_otro_sin_el_planificador() -> None:
    pc, store = FakePC(), RecipeStore(":memory:")
    llm = FakeLLM(*guion_hrom())
    async with Client(pc.server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store)
        fin = await ensenar(agent)
        assert fin.startswith("Aprendida la receta n.º 1: «<dame> el video mas visto del canal {canal}»")
        llamadas_al_ensenar = len(llm.seen)
        resultado = await agent.run("dame el vídeo más visto del canal Lofi Girl")

    assert resultado.metrics.route == "receta" and len(llm.seen) == llamadas_al_ensenar  # sin el modelo
    assert pc.site.url.startswith("https://www.youtube.com/watch")
    assert "lofigirl: récord absoluto" in pc.site.title  # el más visto de ESE canal (98000 visualizaciones)
    assert [s.tool for s in resultado.steps] == [
        "web.site_search",
        "browser.snapshot",
        "browser.click",
        "browser.click",
        "browser.click",
        "browser.click",
    ]
    assert resultado.answer.startswith("Hecho como me enseñaste") and "lofigirl: récord absoluto" in resultado.answer
    receta = store.all()[0]
    assert receta.origin == "enseñada" and receta.steps[0].args == {"sitio": "YouTube", "consulta": "{canal}"}
    assert [s.target.describe() for s in receta.steps[1:]] == [  # type: ignore[union-attr]
        'link "{canal}"',
        'tab "videos"',
        'button "populares"',
        "link n.º 1 (/watch)",
    ]


async def test_mientras_se_ensena_no_se_reproducen_ni_se_aprenden_otras_recetas() -> None:
    store = RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"), llamar("apps_open", nombre="Paint"))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store)
        agent.command("/aprender abre mis apps de música")
        await agent.run("abre Spotify")
        await agent.run("abre Paint")  # sin /aprender, la de «abre Spotify» ya se habría guardado y reproducido
        assert store.all() == [] and len(llm.seen) == 2
        fin = agent.command("/fin")
    assert fin is not None and "apps.open(nombre='Spotify')" in fin and "apps.open(nombre='Paint')" in fin
    assert store.all()[0].trigger == "<abr*> mis apps de musica"


async def test_cancelar_descarta_y_fin_sin_pasos_no_guarda() -> None:
    store = RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store)
        agent.command("/aprender abre lo mío")
        await agent.run("abre Spotify")
        assert agent.command("/cancelar") == "Descartado: no he guardado nada."
        assert (
            agent.command(
                "/fin",
            )
            == "No estaba aprendiendo nada. Empieza con /aprender <frase>."
        )
        agent.command("/aprender algo")
        assert agent.command("/fin") == "No he guardado nada: no hubo ningún paso que se pueda repetir."
    assert store.all() == []


async def test_los_comandos_pasan_por_la_sesion_sin_llamar_al_modelo() -> None:
    salidas: list[str] = []
    lineas = iter(["/aprender pon lo mío", "/cancelar", "/recetas", "salir"])

    async def leer() -> str:
        return next(lineas)

    llm = FakeLLM()
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=RecipeStore(":memory:"))
        await Session(salidas.append).serve(agent, leer)
    assert salidas[0].startswith("Grabando «pon lo mío»")
    assert salidas[1:] == ["Descartado: no he guardado nada.", "No hay recetas aprendidas."]
    assert llm.seen == []


async def test_fin_avisa_de_los_pasos_guiados_que_no_salieron_bien() -> None:
    store = RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"), llamar("browser_click", ref="e99"), decir("Hecho."))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store)
        agent.command("/aprender pon lo mío")
        await agent.run("abre Spotify")
        await agent.run("pulsa el botón raro")
        fin = agent.command("/fin")
    assert fin is not None and fin.endswith("Aviso: «pulsa el botón raro» no salió bien y no se ha grabado.")
