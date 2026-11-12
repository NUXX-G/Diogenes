"""Bloque 2: recetas. Se crean de un turno que salió bien (y que el usuario no corrige), se reproducen sin el
planificador verificando cada paso, se degradan si fallan y nunca se saltan confirmaciones ni bloqueos."""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client

from core.agent import Agent
from core.bench.fakes import APPS, FakePC
from core.intent import IntentGate
from core.mcp_client import McpTools
from core.recipes import (
    Recipe,
    RecipeConfig,
    RecipeStep,
    RecipeStore,
    Recorded,
    Target,
    build_recipe,
    match_trigger,
    resolve_target,
    shape,
    tokenize,
)
from core.safety import ConfirmationPolicy
from tests.test_agent import POLICY, FakeBackend, FakeLLM, decir, llamar

GATE = IntentGate.load()
CFG = RecipeConfig.load()
LANZADA = json.dumps({"ok": True, "lanzada": True, "id": "x", "nombre": "Spotify"})


def receta(frase: str, *pasos: Recorded) -> Recipe:
    r = build_recipe(frase, list(pasos), GATE.verb_root, CFG)
    assert r is not None
    return r


def casa(trigger: str, frase: str) -> dict[str, str] | None:
    tokens, words = tokenize(frase, GATE.verb_root, CFG.filler)
    return match_trigger(trigger, tokens, words)


# --- disparadores ---------------------------------------------------------------------------------------------------


def test_abrir_una_app_deja_el_nombre_como_hueco() -> None:
    r = receta("abre Spotify", Recorded("apps.open", {"nombre": "Spotify"}, LANZADA))
    assert r.trigger == "<abr*> {app}" and r.steps[0].args == {"nombre": "{app}"} and r.steps[0].checks == ("lanzada",)
    assert casa(r.trigger, "ábreme el Bloc de notas") == {"app": "Bloc de notas"}  # artículo opcional
    assert casa(r.trigger, "Diogenes, abre Paint por favor") == {"app": "Paint"}  # relleno fuera
    assert casa(r.trigger, "pon música") is None  # otra raíz de verbo: no es la misma orden


def test_buscar_en_una_web_deja_sitio_y_consulta() -> None:
    ok = json.dumps({"ok": True})
    r = receta(
        "búscame en YouTube cómo hacer mochis",
        Recorded("web.site_search", {"sitio": "YouTube", "consulta": "cómo hacer mochis"}, ok),
    )
    assert r.trigger == "<busc*> en {sitio} {consulta}"
    assert casa(r.trigger, "búscame en Wikipedia Diógenes de Sinope") == {
        "sitio": "Wikipedia",
        "consulta": "Diógenes de Sinope",
    }


def test_buscar_y_lanzar_se_guarda_como_abrir_por_nombre() -> None:
    busqueda = json.dumps({"ok": True, "ganador_claro": True, "id_ganador": "steam:1"})
    r = receta(
        "abre Brawlhalla",
        Recorded("apps.search", {"nombre": "brawlhalla"}, busqueda),
        Recorded("apps.launch", {"id": "steam:1"}, json.dumps({"ok": True})),
    )
    assert [s.tool for s in r.steps] == ["apps.open"] and r.trigger == "<abr*> {app}"


def test_una_orden_sin_datos_variables_es_una_receta_fija() -> None:
    r = receta(
        "pausa el vídeo de YouTube",
        Recorded("media.control", {"accion": "pausa", "sesion": ""}, json.dumps({"ok": True, "verificado": True})),
    )
    assert r.slots == [] and r.steps[0].args == {"accion": "pausa", "sesion": ""}
    assert casa(r.trigger, "pausa el vídeo de YouTube") == {} and casa(r.trigger, "pausa el vídeo de Twitch") is None


def test_solo_lecturas_no_hacen_receta() -> None:
    assert build_recipe("qué suena", [Recorded("media.sessions", {}, "{}")], GATE.verb_root, CFG) is None


def test_selector_del_navegador_por_nombre_con_hueco_forma_y_posicion() -> None:
    pagina = (
        '[e1] searchbox "Buscar"\n[e2] link "Hrom - mejores momentos" -> /watch?v=r1\n'
        '[e3] link "Hrom" -> /@hrom\n[e4] link "Otro vídeo" -> /watch?v=r2'
    )
    from core.recipes import target_from

    canal = target_from(pagina, "e3", {"hrom": "canal"}, None)
    assert canal == Target("link", "{canal}", "/@")
    otra = pagina.replace("Hrom", "Lofi Girl").replace("/@hrom", "/@lofigirl")
    assert resolve_target(canal, otra, {"canal": "Lofi Girl"}) == "e3"  # no el vídeo que también lo nombra
    segundo = target_from(pagina, "e4", {}, 2)
    assert segundo == Target("link", "", "/watch", 2) and resolve_target(segundo, otra, {}) == "e4"
    assert shape("https://www.youtube.com/@hrom/videos") == "/@" and shape("/watch?v=x") == "/watch"


# --- almacén --------------------------------------------------------------------------------------------------------


def test_almacen_guarda_busca_degrada_y_borra() -> None:
    store = RecipeStore(":memory:")
    rid = store.save(receta("abre Spotify", Recorded("apps.open", {"nombre": "Spotify"}, LANZADA)))
    tokens, words = tokenize("abre Paint", GATE.verb_root, CFG.filler)
    found = store.find(tokens, words)
    assert found is not None and found[0].id == rid and found[1] == {"app": "Paint"}
    assert store.failure(rid, 3) is False and store.failure(rid, 3) is False
    assert store.failure(rid, 3) is True  # tres seguidos: desactivada
    assert store.find(tokens, words) is None
    assert store.delete(rid) and store.all() == []


def test_la_receta_mas_concreta_gana() -> None:
    store = RecipeStore(":memory:")
    store.save(receta("abre Spotify", Recorded("apps.open", {"nombre": "Spotify"}, LANZADA)))
    fija = receta("abre la música de estudio", Recorded("uri.open", {"uri": "spotify:playlist:1"}, '{"ok": true}'))
    store.save(fija)
    tokens, words = tokenize("abre la música de estudio", GATE.verb_root, CFG.filler)
    assert store.find(tokens, words)[0].trigger == fija.trigger  # type: ignore[index]


def test_correcciones() -> None:
    assert CFG.is_correction("no, eso no") and CFG.is_correction("Te has equivocado")
    assert not CFG.is_correction("abre notas") and not CFG.is_correction("nota nueva")


# --- en el agente ---------------------------------------------------------------------------------------------------


def agente(llm: FakeLLM, client: Any, store: RecipeStore, confirm: Any = None) -> Agent:
    return Agent(llm, McpTools(client), policy=ConfirmationPolicy.load(), recipes=store, confirm=confirm)


async def test_la_segunda_orden_del_mismo_tipo_no_llama_al_modelo() -> None:
    pc, store = FakePC(), RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"))
    async with Client(pc.server()) as client:
        agent = agente(llm, client, store)
        primera = await agent.run("abre Spotify")
        assert store.all() == []  # aún candidata: espera a ver si el usuario corrige
        segunda = await agent.run("ábreme Brawlhalla")
    assert primera.metrics.route == "planificador" and len(primera.metrics.model_calls) == 1
    assert segunda.metrics.route == "receta" and segunda.metrics.model_calls == []
    assert segunda.answer == "He abierto Brawlhalla." and pc.launched == ["startapps:spotify", "steam:291550"]
    assert store.all()[0].successes == 2


async def test_si_el_usuario_corrige_no_se_aprende() -> None:
    store = RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"), decir("Vale."))
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store)
        await agent.run("abre Spotify")
        await agent.run("no, eso no")
    assert store.all() == []


async def test_si_se_corrige_una_receta_reproducida_suma_un_fallo() -> None:
    store = RecipeStore(":memory:")
    store.save(receta("abre Spotify", Recorded("apps.open", {"nombre": "Spotify"}, LANZADA)))
    llm = FakeLLM(decir("Perdona."))
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store)
        await agent.run("abre Paint")
        await agent.run("te has equivocado")
    assert (store.all()[0].failures, store.all()[0].streak) == (1, 1)


async def test_si_un_paso_falla_para_degrada_y_sigue_el_planificador() -> None:
    store = RecipeStore(":memory:")
    cerrar = receta(
        "cierra la calculadora",
        Recorded("apps.close", {"id": "calculadora"}, json.dumps({"ok": True, "cerrada": True})),
    )
    store.save(cerrar)
    llm = FakeLLM(decir("No estaba abierta."))
    pc = FakePC()  # sin ventanas: cerrar no puede salir bien
    async with Client(pc.server()) as client:
        resultado = await agente(llm, client, store).run("cierra la calculadora")
    assert [s.tool for s in resultado.steps] == ["apps.close"]
    assert len(resultado.metrics.model_calls) == 1 and resultado.metrics.route == "planificador"
    assert store.all()[0].failures == 1
    # el modelo vio lo que ya se hizo
    assert any(m.role == "tool" and "no está abierta" in m.content for m in llm.seen[0])


async def test_tres_fallos_seguidos_la_desactivan() -> None:
    store = RecipeStore(":memory:")
    store.save(receta("cierra paint", Recorded("apps.close", {"id": "paint"}, json.dumps({"cerrada": True}))))
    llm = FakeLLM(*[decir("No.") for _ in range(5)])  # la 4.ª, ya sin receta, fuerza además una búsqueda
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store)
        for _ in range(4):
            await agent.run("cierra paint")
    r = store.all()[0]
    assert (r.failures, r.enabled) == (3, False)  # la cuarta ya no la intenta


async def test_si_no_hay_una_app_con_ese_nombre_no_es_un_fallo_de_la_receta() -> None:
    store = RecipeStore(":memory:")
    store.save(receta("abre Spotify", Recorded("apps.open", {"nombre": "Spotify"}, LANZADA)))
    llm = FakeLLM(llamar("web_open", url="https://www.youtube.com"))
    async with Client(FakePC().server()) as client:
        resultado = await agente(llm, client, store).run("abre youtube")
    assert store.all()[0].failures == 0
    assert [s.tool for s in resultado.steps] == ["web.open"]  # el intento de la receta no queda


async def test_un_turno_que_pidio_confirmacion_no_se_aprende() -> None:
    store = RecipeStore(":memory:")

    async def si(_t: str, _a: dict[str, Any]) -> bool:
        return True

    # el usuario pidió Spotify y el modelo abre la calculadora: hubo que preguntar
    llm = FakeLLM(llamar("apps_open", nombre="Calculadora"), decir("Abierta."), decir("De nada."))
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store, si)
        await agent.run("abre Spotify")
        await agent.run("gracias")
    assert store.all() == []


async def test_si_el_modelo_dijo_algo_que_hubo_que_corregir_no_se_aprende() -> None:
    store = RecipeStore(":memory:")
    llm = FakeLLM(llamar("apps_search", nombre="calculadora"), decir("He cerrado la calculadora."), decir("ok"))
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store)
        await agent.run("cierra la calculadora")
        await agent.run("vale")
    assert store.all() == []


# --- seguridad al reproducir ----------------------------------------------------------------------------------------


async def test_reproducir_no_se_salta_las_confirmaciones_irreversibles() -> None:
    store = RecipeStore(":memory:")
    store.save(Recipe("<cierr*> a la fuerza {app}", "x", [RecipeStep("apps.force_close", {"id": "{app}"})]))
    preguntas: list[str] = []

    async def no(tool: str, _a: dict[str, Any]) -> bool:
        preguntas.append(tool)
        return False

    pc = FakePC()
    pc.windows.open(APPS[1])
    async with Client(pc.server()) as client:
        resultado = await agente(FakeLLM(), client, store, no).run("cierra a la fuerza la calculadora")
    assert preguntas == ["apps.force_close"] and resultado.answer.startswith("No lo he hecho")
    assert pc.windows.windows  # sigue abierta


async def test_un_paso_que_hubo_que_confirmar_al_aprender_se_vuelve_a_preguntar() -> None:
    store = RecipeStore(":memory:")
    paso = RecipeStep("web.open", {"url": "https://tienda-rara.example/oferta"}, asked=True)
    store.save(Recipe("<dame> ofertas", "dame ofertas", [paso]))
    preguntas: list[dict[str, Any]] = []

    async def no(_t: str, args: dict[str, Any]) -> bool:
        preguntas.append(args)
        return False

    backend = FakeBackend(names=("web.open",))
    await Agent(FakeLLM(), backend, policy=POLICY, recipes=store, confirm=no).run("dame ofertas")
    assert backend.calls == [] and preguntas[0]["destino"] == "https://tienda-rara.example/oferta"


async def test_los_destinos_fijos_de_una_receta_cuentan_como_pedidos_solo_si_encaja_el_disparador() -> None:
    store = RecipeStore(":memory:")
    paso = RecipeStep("web.site_search", {"sitio": "youtube", "consulta": "{consulta}"})
    store.save(Recipe("<pon> {consulta} de fondo", "pon lluvia de fondo", [paso]))
    backend = FakeBackend(names=("web.site_search",))
    preguntas: list[str] = []

    async def no(tool: str, _a: dict[str, Any]) -> bool:
        preguntas.append(tool)
        return False

    agent = Agent(FakeLLM(), backend, policy=POLICY, recipes=store, confirm=no)
    await agent.run("pon lluvia de fondo")
    assert backend.calls == [("web.site_search", {"sitio": "youtube", "consulta": "lluvia"})] and preguntas == []


@pytest.mark.parametrize("frase", ["abre Spotify", "búscame en YouTube cómo hacer mochis"])
async def test_sin_almacen_no_se_aprende_nada(frase: str) -> None:
    llm = FakeLLM(llamar("apps_open", nombre="Spotify"), decir("ok"), decir("ok"))
    async with Client(FakePC().server()) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy.load())
        await agent.run(frase)
        assert agent.command("/recetas") == "No hay base de recetas."


async def test_si_la_receta_no_aplica_el_planificador_empieza_limpio_y_sin_eleccion_pendiente() -> None:
    store = RecipeStore(":memory:")
    store.save(receta("abre Spotify", Recorded("apps.open", {"nombre": "Spotify"}, LANZADA)))
    llm = FakeLLM(llamar("apps_search", nombre="cosas raras"), decir("No existe."), decir("¿El primero de qué?"))
    async with Client(FakePC().server()) as client:
        agent = agente(llm, client, store)
        await agent.run("abre el editor de cosas raras")  # «editor» tiene varias opciones: la receta no aplica
        assert all(m.role != "tool" for m in llm.seen[0])  # el modelo no ve el intento de la receta
        siguiente = await agent.run("el primero")  # no hay lista pendiente: va al modelo como texto
    assert siguiente.metrics.route == "planificador"


async def test_los_turnos_con_pasos_del_navegador_no_se_aprenden_solos() -> None:
    store = RecipeStore(":memory:")
    pc = FakePC()
    llm = FakeLLM(
        llamar("web_site_search", sitio="YouTube", consulta="Hrom"),
        llamar("browser_snapshot"),
        llamar("browser_click", ref="e3"),
        decir("Estoy en el canal de Hrom."),
        decir("De nada."),
    )
    async with Client(pc.server()) as client:
        agent = agente(llm, client, store)
        await agent.run("abre el canal Hrom en youtube")
        await agent.run("gracias")
    assert store.all() == []


# --- palabras sobrantes y duplicados ------------------------------------------------------------------------------

BUSCAR = '{"ok": true}'


def test_las_palabras_que_ningun_paso_usa_quedan_opcionales() -> None:
    r = receta(
        "busca gatos en youtube en modo incognito",
        Recorded("web.site_search", {"sitio": "youtube", "consulta": "gatos"}, BUSCAR),
    )
    assert r.trigger == "<busc*> {consulta} en {sitio} [en modo incognito]"
    assert casa(r.trigger, "busca perros en youtube") == {"consulta": "perros", "sitio": "youtube"}
    assert casa(r.trigger, "busca perros en youtube en modo incognito") == {"consulta": "perros", "sitio": "youtube"}


def test_una_receta_fija_sin_nada_que_la_ancle_no_se_vuelve_toda_opcional() -> None:
    r = receta("pon la música de estudio", Recorded("uri.open", {"uri": "spotify:playlist:1"}, BUSCAR))
    assert "[" not in r.trigger and casa(r.trigger, "pon") is None


def test_no_se_crean_duplicados_con_palabras_de_mas() -> None:
    store = RecipeStore(":memory:")
    paso = Recorded("web.site_search", {"sitio": "youtube", "consulta": "perros"}, BUSCAR)
    corta = store.save(receta("busca perros en youtube", paso))
    gatos = Recorded("web.site_search", {"sitio": "youtube", "consulta": "gatos"}, BUSCAR)
    larga = build_recipe("busca gatos en youtube ya mismo", [gatos], GATE.verb_root, CFG)
    assert larga is not None and store.save(larga) == corta and len(store.all()) == 1


def test_al_abrir_la_base_se_fusionan_los_duplicados_que_ya_hubiera(tmp_path: Any) -> None:
    db = tmp_path / "d.db"
    store = RecipeStore(db)
    paso = [RecipeStep("web.site_search", {"sitio": "{sitio}", "consulta": "{consulta}"})]
    # como en el PC de clase: la 2 se creó antes de que hubiera palabras opcionales
    store._db.execute(
        "INSERT INTO recipes (trigger, example, steps, successes) VALUES (?, ?, ?, 1), (?, ?, ?, 2)",
        (
            "<busc*> {consulta} en {sitio} en modo incognito",
            "busca gatos en youtube en modo incognito",
            json.dumps([s.as_dict() for s in paso]),
            "<busc*> {consulta} en {sitio}",
            "busca perros en youtube",
            json.dumps([s.as_dict() for s in paso]),
        ),
    )
    store._db.commit()
    store.close()
    (unica,) = RecipeStore(db).all()
    assert unica.trigger == "<busc*> {consulta} en {sitio} [en modo incognito]" and unica.successes == 3
