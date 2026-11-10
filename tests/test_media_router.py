"""Teclas multimedia por código virtual y atajos del router (sin LLM)."""

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest
from mcp import Client

from core.agent import Agent
from core.mcp_client import McpTools
from core.router import Router
from core.safety import ConfirmationPolicy
from hands import server as hands_server
from hands.apps.index import AppIndex
from hands.system.media import VIRTUAL_KEYS, Media
from tests.test_agent import FakeBackend, FakeLLM, decir, llamar


class FakeSender:
    def __init__(self) -> None:
        self.pulsadas: list[int] = []

    def press(self, vk: int) -> None:
        self.pulsadas.append(vk)


# --- códigos de tecla virtual --------------------------------------------------------------------------------


def test_los_codigos_son_los_vk_de_windows() -> None:
    assert VIRTUAL_KEYS == {
        "play_pausa": 0xB3,
        "siguiente": 0xB0,
        "anterior": 0xB1,
        "parar": 0xB2,
        "subir_volumen": 0xAF,
        "bajar_volumen": 0xAE,
        "silencio": 0xAD,
    }


@pytest.mark.parametrize("tecla", list(VIRTUAL_KEYS))
def test_cada_tecla_envia_su_codigo_y_no_texto(tecla: str) -> None:
    sender = FakeSender()
    res = Media(sender).key(tecla)
    assert res["ok"] is True
    assert sender.pulsadas == [VIRTUAL_KEYS[tecla]]


def test_repeticiones_acotadas_y_tecla_desconocida() -> None:
    sender = FakeSender()
    assert Media(sender).key("subir_volumen", 5)["veces"] == 5
    assert Media(sender).key("subir_volumen", 999)["veces"] == 20
    assert Media(sender).key("subir_volumen", 0)["veces"] == 1
    assert Media(sender).key("explotar")["ok"] is False
    assert len(sender.pulsadas) == 26


async def test_media_key_es_una_herramienta_de_hands_solo_si_hay_teclado() -> None:
    sender = FakeSender()
    async with Client(hands_server.build_server(AppIndex(), media=Media(sender))) as client:
        nombres = {t.name for t in (await client.list_tools()).tools}
        out = await client.call_tool("media.key", {"tecla": "silencio"})
    assert "media.key" in nombres and json.loads(out.content[0].text)["ok"] is True
    assert sender.pulsadas == [0xAD]
    async with Client(hands_server.build_server(AppIndex())) as client:
        assert "media.key" not in {t.name for t in (await client.list_tools()).tools}


# --- router ------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def router() -> Router:
    return Router.load(Path("configuracion"))


@pytest.mark.parametrize(
    ("frase", "tecla", "veces"),
    [
        ("pausa", "play_pausa", 1),
        ("Diógenes, pausa la música", "play_pausa", 1),
        ("dale al play", "play_pausa", 1),
        ("siguiente canción por favor", "siguiente", 1),
        ("Oye Diogenes, salta", "siguiente", 1),
        ("la anterior", "anterior", 1),
        ("sube el volumen", "subir_volumen", 5),
        ("Baja el volumen, porfa", "bajar_volumen", 5),
        ("silencio", "silencio", 1),
        ("¡mutea!", "silencio", 1),
    ],
)
def test_frases_que_son_un_atajo(router: Router, frase: str, tecla: str, veces: int) -> None:
    atajo = router.match(frase)
    assert atajo is not None
    assert (atajo.tool, atajo.arguments) == ("media.key", {"tecla": tecla, "veces": veces})


@pytest.mark.parametrize(
    "frase",
    [
        "pon música de Daft Punk en Spotify",
        "abre Spotify",
        "pon la siguiente de Daft Punk en Spotify",
        "búscame en YouTube cómo hacer mochis",
        "pausa el vídeo de YouTube y ábreme Brawlhalla",
        "dame el vídeo más visto del canal Hrom",
        "",
    ],
)
def test_peticiones_que_no_son_un_atajo_siguen_al_planificador(router: Router, frase: str) -> None:
    assert router.match(frase) is None


def test_el_atajo_se_puede_ampliar_desde_la_config(tmp_path: Path) -> None:
    (tmp_path / "atajos.json").write_text(
        json.dumps({"teclas": {"silencio": {"frases": ["chitón"]}}, "relleno": []}), encoding="utf-8"
    )
    assert Router.load(tmp_path).match("Chitón") is not None
    assert Router.load(tmp_path).match("silencio") is None


# --- el agente usa el router antes del LLM ------------------------------------------------------------------------


async def test_un_atajo_no_llama_al_llm_y_responde_rapido() -> None:
    backend = FakeBackend({"media.key": '{"ok": true}'}, names=("apps.search", "media.key"))
    llm = FakeLLM()  # sin respuestas: si se le llamara, fallaría
    pasos = []
    agent = Agent(llm, backend, policy=ConfirmationPolicy([]), on_step=pasos.append)
    inicio = time.monotonic()
    resultado = await agent.run("Diogenes, sube el volumen")
    assert time.monotonic() - inicio < 0.2
    assert backend.calls == [("media.key", {"tecla": "subir_volumen", "veces": 5})]
    assert llm.seen == [] and resultado.answer == "He pulsado subir volumen."
    assert [s.tool for s in pasos] == ["media.key"] and resultado.steps[0].ok


async def test_el_atajo_queda_en_el_historial_para_el_siguiente_turno() -> None:
    backend = FakeBackend({"media.key": '{"ok": true}'}, names=("media.key",))
    llm = FakeLLM(decir("vale"))
    agent = Agent(llm, backend, policy=ConfirmationPolicy([]))
    await agent.run("pausa")
    await agent.run("gracias por todo")
    roles = [m.role for m in llm.seen[0]]
    assert roles == ["system", "user", "assistant", "user"]


async def test_si_la_herramienta_no_esta_se_sigue_con_el_llm() -> None:
    backend = FakeBackend(names=("apps.search",))
    llm = FakeLLM(decir("No puedo."))
    resultado = await Agent(llm, backend, policy=ConfirmationPolicy([])).run("pausa")
    assert resultado.answer == "No puedo." and backend.calls == []


async def test_un_fallo_del_atajo_se_dice() -> None:
    backend = FakeBackend({"media.key": '{"ok": false, "mensaje": "sin teclado"}'}, names=("media.key",))
    resultado = await Agent(FakeLLM(), backend, policy=ConfirmationPolicy([])).run("silencio")
    assert resultado.answer.startswith("No he podido") and not resultado.steps[0].ok


async def test_una_respuesta_a_una_lista_no_se_toma_por_atajo() -> None:
    """ "siguiente" puede ser la respuesta a una pregunta del agente, no un atajo."""
    lista = json.dumps(
        {
            "ok": True,
            "ganador_claro": False,
            "opciones": [{"n": 1, "id": "a:1", "nombre": "Uno"}, {"n": 2, "id": "a:2", "nombre": "Dos"}],
        }
    )
    backend = FakeBackend({"apps.search": lista, "media.key": '{"ok": true}'}, names=("apps.search", "media.key"))
    llm = FakeLLM(llamar("apps_search", nombre="x"), decir("¿Cuál?"), decir("vale"))
    agent = Agent(llm, backend, policy=ConfirmationPolicy([]))
    await agent.run("abre uno")
    await agent.run("siguiente")
    assert "media.key" not in [name for name, _ in backend.calls]


async def test_extremo_a_extremo_con_hands_en_proceso() -> None:
    sender = FakeSender()
    async with Client(hands_server.build_server(AppIndex(), media=Media(sender))) as client:
        resultado = await Agent(FakeLLM(), McpTools(client), policy=ConfirmationPolicy([])).run("siguiente canción")
    assert sender.pulsadas == [0xB0] and resultado.answer == "He pulsado siguiente."
