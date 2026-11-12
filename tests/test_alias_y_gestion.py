"""Bloques 4 y 5: alias aprendidos al elegir de una lista («abre el editor» → «el segundo») y comandos para ver y
borrar recetas y alias."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from core.agent import Agent
from core.bench.fakes import FakePC
from core.mcp_client import McpTools
from core.recipes import Recipe, RecipeStep, RecipeStore
from core.safety import ConfirmationPolicy
from tests.test_agent import FakeLLM, decir, llamar


def agente(llm: FakeLLM, client: Any, store: RecipeStore, confirm: Any = None) -> Agent:
    return Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store, confirm=confirm)


async def elegir_editor(agent: Agent) -> str:
    pregunta = await agent.run("abre el editor")
    assert pregunta.answer.endswith("¿Cuál quieres?")
    segunda = pregunta.answer.splitlines()[2].split(". ", 1)[1]  # "2. Editor ..."
    resultado = await agent.run("el segundo")
    assert resultado.answer == f"He abierto {segunda}." and resultado.metrics.model_calls == []
    return segunda


async def test_elegir_de_la_lista_guarda_el_alias_y_la_proxima_vez_va_directo() -> None:
    pc, store = FakePC(), RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_open", nombre="editor"), llamar("apps_open", nombre="editor"))
    async with Client(pc.server()) as client:
        agent = agente(llm, client, store)
        elegida = await elegir_editor(agent)
        otra_vez = await agent.run("abre el editor")
    (alias,) = store.aliases()
    assert (alias.alias, alias.app_name) == ("editor", elegida)
    assert otra_vez.answer == f"He abierto {elegida}." and len(otra_vez.metrics.model_calls) == 1
    assert [s.tool for s in otra_vez.steps] == ["apps.open"] and len(pc.launched) == 2 and len(set(pc.launched)) == 1


async def test_con_la_receta_de_abrir_el_alias_no_necesita_ni_el_modelo() -> None:
    pc, store = FakePC(), RecipeStore(":memory:")
    paso = RecipeStep("apps.open", {"nombre": "{app}"}, checks=("lanzada",))
    store.save(Recipe("<abr*> {app}", "abre Spotify", [paso]))
    store.save_alias("editor", "startapps:clip", "Editor de vídeo")
    async with Client(pc.server()) as client:
        resultado = await agente(FakeLLM(), client, store).run("abre el editor")
    assert resultado.metrics.route == "receta" and resultado.metrics.model_calls == []
    assert resultado.answer == "He abierto Editor de vídeo." and pc.launched == ["startapps:clip"]


async def test_si_el_usuario_corrige_el_alias_se_degrada_y_al_segundo_fallo_se_olvida() -> None:
    store = RecipeStore(":memory:")
    store.save_alias("editor", "startapps:clip", "Editor de vídeo")
    llm = FakeLLM(*[llamar("apps_open", nombre="editor"), decir("Perdona.")] * 2)
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store)
        await agent.run("abre el editor")
        await agent.run("no, eso no")
        assert store.alias("editor").failures == 1  # type: ignore[union-attr]
        await agent.run("abre el editor")
        await agent.run("te has equivocado")
    assert store.aliases() == []


async def test_un_alias_de_una_app_que_ya_no_existe_se_borra_y_se_busca_normal() -> None:
    store = RecipeStore(":memory:")
    store.save_alias("spoti", "startapps:desinstalada", "Spotify viejo")
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"))
    pc = FakePC()
    async with Client(pc.server()) as client:
        store.save_alias("spotify", "startapps:desinstalada", "Spotify viejo")
        resultado = await agente(llm, client, store).run("abre Spotify")
    assert resultado.answer == "He abierto Spotify." and pc.launched == ["startapps:spotify"]
    assert [a.alias for a in store.aliases()] == ["spoti"]


async def test_el_alias_solo_vale_si_el_usuario_lo_dice() -> None:
    """Si el modelo pide «editor» sin que el usuario lo haya dicho, no se usa el alias: va por la puerta normal."""
    store = RecipeStore(":memory:")
    store.save_alias("editor", "startapps:clip", "Editor de vídeo")
    preguntas: list[str] = []

    async def no(tool: str, _a: dict[str, Any]) -> bool:
        preguntas.append(tool)
        return False

    pc = FakePC()
    llm = FakeLLM(llamar("apps_open", nombre="editor"), decir("No lo abro."))
    async with Client(pc.server()) as client:
        await agente(llm, client, store, no).run("abre Spotify")
    assert preguntas == ["apps.open"] and pc.launched == []


async def test_cerrar_eligiendo_de_la_lista_tambien_va_directo() -> None:
    store = RecipeStore(":memory:")
    pc = FakePC()
    from core.bench.fakes import APPS

    for app in APPS:
        if app.name.startswith("Editor"):
            pc.windows.open(app)
    llm = FakeLLM(llamar("apps_close", id="editor"))
    async with Client(pc.server()) as client:
        agent = agente(llm, client, store)
        pregunta = await agent.run("cierra el editor")
        assert pregunta.answer.endswith("¿Cuál quieres?")
        resultado = await agent.run("el primero")
    assert resultado.answer.startswith("He cerrado") and len(pc.windows.windows) == 1
    assert store.aliases()[0].alias == "editor"


# --- gestión --------------------------------------------------------------------------------------------------------


def test_recetas_listar_y_borrar() -> None:
    store = RecipeStore(":memory:")
    paso = RecipeStep("apps.open", {"nombre": "{app}"}, checks=("lanzada",))
    rid = store.save(Recipe("<abr*> {app}", "abre Spotify", [paso]))
    agent = Agent(FakeLLM(), _NoTools(), recipes=store)
    listado = agent.command("/recetas")
    assert listado == f"{rid}. «<abr*> {{app}}» (auto) ✓1 ✗0 · ej.: abre Spotify"
    assert agent.command("/receta borrar 99") == "No hay ninguna receta n.º 99."
    assert agent.command(f"/receta borrar {rid}") == f"Receta n.º {rid} borrada."
    assert agent.command("/recetas") == "No hay recetas aprendidas."
    assert agent.command("/receta") == "Uso: /recetas · /receta borrar <n.º> · /alias · /alias borrar <nombre>"


def test_una_receta_desactivada_se_marca_en_la_lista() -> None:
    store = RecipeStore(":memory:")
    rid = store.save(Recipe("<cierr*> {app}", "cierra paint", [RecipeStep("apps.close", {"id": "{app}"})]))
    for _ in range(3):
        store.failure(rid, 3)
    assert "DESACTIVADA" in (Agent(FakeLLM(), _NoTools(), recipes=store).command("/recetas") or "")


def test_alias_listar_y_borrar() -> None:
    store = RecipeStore(":memory:")
    agent = Agent(FakeLLM(), _NoTools(), recipes=store)
    assert agent.command("/alias") == "No hay alias aprendidos."
    store.save_alias("el editor", "startapps:clip", "Editor de vídeo")
    assert agent.command("/alias") == "«editor» → Editor de vídeo (✓1 ✗0)"
    assert agent.command("/alias borrar editor") == "Alias «editor» olvidado."
    assert agent.command("/alias borrar editor") == "No hay ningún alias «editor»."


def test_lo_que_no_es_un_comando_sigue_al_agente() -> None:
    agent = Agent(FakeLLM(), _NoTools(), recipes=RecipeStore(":memory:"))
    assert agent.command("abre Spotify") is None and agent.command("/desconocido") is None


class _NoTools:
    async def tools(self) -> list[Any]:
        return []

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        return json.dumps({"ok": False})
