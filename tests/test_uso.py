"""Aprendizaje por uso (todo local): guardar la receta al salir, contador de uso con decaimiento que ordena y desempata
las búsquedas de apps, ascenso de recetas fiables a atajo y /frecuentes."""

from __future__ import annotations

import json
from typing import Any

from mcp import Client

from core.agent import Agent
from core.bench.fakes import FakePC
from core.mcp_client import McpTools
from core.recipes import Recipe, RecipeStep, RecipeStore
from core.safety import ConfirmationPolicy
from core.session import Session
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry
from hands.apps.usage import UsageReader, decayed
from hands.server import build_server
from tests.test_agent import FakeLLM, llamar

DIA = 86400.0


# --- guardar al salir -----------------------------------------------------------------------------------------------


async def sesion(lineas: list[str], store: RecipeStore, llm: FakeLLM) -> list[str]:
    salidas: list[str] = []
    entrada = iter(lineas)

    async def leer() -> str:
        linea = next(entrada, None)
        if linea is None:
            raise EOFError
        return linea

    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store)
        await Session(salidas.append).serve(agent, leer)
    return salidas


async def test_al_salir_se_guarda_la_receta_del_ultimo_turno() -> None:
    store = RecipeStore(":memory:")
    await sesion(["abre Spotify", "salir"], store, FakeLLM(llamar("apps_open", nombre="Spotify")))
    assert [r.trigger for r in store.all()] == ["<abr*> {app}"]


async def test_al_cerrar_el_cli_sin_salir_tambien() -> None:
    store = RecipeStore(":memory:")
    await sesion(["abre Spotify"], store, FakeLLM(llamar("apps_open", nombre="Spotify")))  # fin de la entrada
    assert len(store.all()) == 1


async def test_nuevo_no_es_una_correccion_y_guarda_la_candidata() -> None:
    store = RecipeStore(":memory:")
    await sesion(["abre Spotify", "/nuevo", "salir"], store, FakeLLM(llamar("apps_open", nombre="Spotify")))
    assert len(store.all()) == 1


# --- uso con decaimiento --------------------------------------------------------------------------------------------


def test_el_uso_decae_a_la_mitad_cada_vida_media() -> None:
    assert decayed(4.0, 0.0, 14 * DIA, 14) == 2.0
    store = RecipeStore(":memory:")
    store.used("app", "a", "Spotify", 14, now=0)
    store.used("app", "a", "Spotify", 14, now=0)
    store.used("app", "b", "Paint", 14, now=0)
    assert store.frequent(14, now=14 * DIA) == [("app", "Spotify", 1.0), ("app", "Paint", 0.5)]


def test_el_lector_de_hands_aplica_el_decaimiento(tmp_path: Any) -> None:
    db = tmp_path / "d.db"
    store = RecipeStore(db)
    store.used("app", "startapps:clip", "Editor de vídeo", 14, now=0)
    reloj = [7 * DIA]
    lector = UsageReader(db, 14, ttl=0, clock=lambda: reloj[0])
    assert round(lector.apps()["startapps:clip"], 3) == round(0.5**0.5, 3)


EDITORES = [
    AppEntry("startapps:clip", "Editor de vídeo", "startapps", "shell", "Clipchamp"),
    AppEntry("startapps:regedit", "Editor del Registro", "startapps", "shell", "Regedit"),
]


class Uso:
    def __init__(self, scores: dict[str, float]) -> None:
        self.scores = scores

    def apps(self) -> dict[str, float]:
        return self.scores


async def abrir(nombre: str, scores: dict[str, float]) -> tuple[dict[str, Any], list[str]]:
    index = AppIndex()
    index.replace(EDITORES)
    lanzadas: list[str] = []
    server = build_server(index, launch=lambda e: lanzadas.append(e.id), usage=Uso(scores))  # type: ignore[arg-type]
    async with Client(server) as client:
        res = await client.call_tool("apps.open", {"nombre": nombre})
    return json.loads(res.content[0].text), lanzadas  # type: ignore[union-attr]


async def test_sin_uso_editor_es_un_empate() -> None:
    data, lanzadas = await abrir("editor", {})
    assert data["ganador_claro"] is False and lanzadas == []


async def test_la_app_mucho_mas_usada_desempata() -> None:
    data, lanzadas = await abrir("editor", {"startapps:regedit": 4.0, "startapps:clip": 0.5})
    assert data["lanzada"] is True and lanzadas == ["startapps:regedit"]


async def test_un_uso_suelto_no_decide() -> None:
    data, _ = await abrir("editor", {"startapps:regedit": 1.0})
    assert data["ganador_claro"] is False


async def test_el_uso_no_se_salta_la_cobertura() -> None:
    data, lanzadas = await abrir("editor y escribe hola", {"startapps:regedit": 9.0})
    assert data["lanzada"] is False and lanzadas == []


async def test_abrir_una_app_suma_uso_y_sale_en_frecuentes() -> None:
    store = RecipeStore(":memory:")
    async with Client(FakePC().server()) as client:
        agent = Agent(FakeLLM(llamar("apps_open", nombre="Spotify")), McpTools(client), recipes=store)
        await agent.run("abre Spotify")
        assert agent.command("/frecuentes") == "app Spotify · 1.0"


# --- ascenso a atajo ------------------------------------------------------------------------------------------------


async def test_cinco_aciertos_seguidos_la_ascienden_y_un_fallo_la_devuelve() -> None:
    store = RecipeStore(":memory:")
    paso = RecipeStep("apps.open", {"nombre": "{app}"}, checks=("lanzada",))
    rid = store.save(Recipe("<abr*> {app}", "abre Spotify", [paso]))  # guardar ya cuenta como un acierto
    notas: list[str] = []
    async with Client(FakePC().server()) as client:
        agent = Agent(FakeLLM(), McpTools(client), recipes=store, on_note=notas.append)
        rutas = [(await agent.run(f"abre {app}")).metrics.route for app in ("Paint", "Spotify", "Paint", "Spotify")]
        assert rutas == ["receta", "receta", "receta", "receta"] and store.get(rid).promoted  # type: ignore[union-attr]
        assert any("ascendida a atajo" in n for n in notas)
        assert (await agent.run("abre Paint")).metrics.route == "atajo"
        assert "⚡ atajo" in (agent.command("/recetas") or "")
    store.failure(rid, 3)
    assert not store.get(rid).promoted  # type: ignore[union-attr]


def test_con_algun_fallo_no_asciende() -> None:
    store = RecipeStore(":memory:")
    rid = store.save(Recipe("<abr*> {app}", "abre Spotify", [RecipeStep("apps.open", {"nombre": "{app}"})]))
    store.failure(rid, 3)
    for _ in range(6):
        store.success(rid, 5)
    assert not store.get(rid).promoted  # type: ignore[union-attr]


def test_una_base_antigua_gana_las_columnas_nuevas(tmp_path: Any) -> None:
    import sqlite3

    db = tmp_path / "vieja.db"
    with sqlite3.connect(db) as con:
        con.execute(
            "CREATE TABLE recipes (id INTEGER PRIMARY KEY AUTOINCREMENT, trigger TEXT NOT NULL UNIQUE, example TEXT "
            "NOT NULL, steps TEXT NOT NULL, origin TEXT NOT NULL DEFAULT 'auto', successes INTEGER NOT NULL DEFAULT 0, "
            "failures INTEGER NOT NULL DEFAULT 0, streak INTEGER NOT NULL DEFAULT 0, enabled INTEGER NOT NULL DEFAULT 1, "
            "created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP, last_used TEXT NOT NULL DEFAULT '')"
        )
        con.execute("INSERT INTO recipes (trigger, example, steps) VALUES ('<abr*> {app}', 'abre x', '[]')")
    (r,) = RecipeStore(db).all()
    assert (r.wins, r.promoted) == (0, False)
