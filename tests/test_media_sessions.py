"""media.sessions / media.control (System Media Transport Controls) y la honestidad tras pulsar una tecla multimedia."""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest
from mcp import Client

from core.agent import Step, _grounded
from core.router import ANSWERS
from hands import server as hands_server
from hands.apps.index import AppIndex
from hands.system.media import Media
from hands.system.sessions import (
    PAUSED,
    PLAYING,
    STOPPED,
    MediaSession,
    Sessions,
    SessionsUnavailable,
    canonical_action,
)
from tests.test_media_router import FakeSender

CHROME = MediaSession("Chrome", "Chrome", "Mochis fáciles", "Canal Cocina", PLAYING)
SPOTIFY = MediaSession("Spotify.exe", "Spotify.exe", "Get Lucky", "Daft Punk", PAUSED)


class FakeBackend:
    """Sesiones simuladas. `efecto=False`: la orden se acepta pero el estado no cambia (la app la ignora)."""

    def __init__(self, *sessions: MediaSession, efecto: bool = True, acepta: bool = True) -> None:
        self.found = list(sessions)
        self.efecto = efecto
        self.acepta = acepta
        self.ordenes: list[tuple[str, str]] = []

    async def sessions(self) -> list[MediaSession]:
        return list(self.found)

    async def act(self, session_id: str, action: str) -> bool:
        self.ordenes.append((session_id, action))
        if self.efecto:
            for i, s in enumerate(self.found):
                if s.id != session_id:
                    continue
                if action == "pause":
                    self.found[i] = replace(s, status=PAUSED)
                elif action == "play":
                    self.found[i] = replace(s, status=PLAYING)
                elif action == "toggle":
                    self.found[i] = replace(s, status=PAUSED if s.status == PLAYING else PLAYING)
                elif action == "stop":
                    self.found[i] = replace(s, status=STOPPED)
                elif action == "next":
                    self.found[i] = replace(s, title="Otra pista")
        return self.acepta


class NoApi:
    async def sessions(self) -> list[MediaSession]:
        raise SessionsUnavailable("no puedo cargar la API de sesiones multimedia (ImportError: x)")

    async def act(self, session_id: str, action: str) -> bool:
        raise SessionsUnavailable("x")


async def no_sleep(_s: float) -> None:
    return None


def sessions(backend: Any, sender: FakeSender | None = None) -> Sessions:
    press = Media(sender).key if sender is not None else None
    return Sessions(backend, press_key=press, sleep=no_sleep, verify_seconds=0.5)


# --- listar ------------------------------------------------------------------------------------------------------


async def test_lista_app_titulo_artista_y_estado() -> None:
    res = await sessions(FakeBackend(CHROME, SPOTIFY)).listing()
    assert res["ok"] is True and res["disponible"] is True
    assert res["sesiones"][0] == {
        "id": "Chrome",
        "app": "Chrome",
        "titulo": "Mochis fáciles",
        "artista": "Canal Cocina",
        "estado": "reproduciendo",
    }
    assert res["sesiones"][1]["estado"] == "en pausa"


async def test_sin_sesiones_lo_dice() -> None:
    res = await sessions(FakeBackend()).listing()
    assert res["ok"] is True and res["sesiones"] == [] and "ninguna sesión" in res["mensaje"]


async def test_sin_api_dice_el_motivo_y_manda_a_media_key() -> None:
    res = await sessions(NoApi()).listing()
    assert res["ok"] is False and res["disponible"] is False
    assert "ImportError" in res["mensaje"] and "media_key" in res["mensaje"]


# --- controlar y verificar -----------------------------------------------------------------------------------------


async def test_pausar_la_que_suena_y_verificar_el_estado_despues() -> None:
    backend = FakeBackend(CHROME, SPOTIFY)
    res = await sessions(backend).control("", "pausa")
    assert backend.ordenes == [("Chrome", "pause")]
    assert res["ok"] is True and res["verificado"] is True
    assert res["estado_antes"] == "reproduciendo" and res["estado_despues"] == "en pausa"
    assert "comprobado" in res["mensaje"]


async def test_si_la_orden_se_acepta_pero_no_cambia_nada_no_se_dice_que_se_hizo() -> None:
    res = await sessions(FakeBackend(CHROME, efecto=False)).control("chrome", "pausa")
    assert res["ok"] is False and res["verificado"] is False
    assert "no cambió" in res["mensaje"] and "sigue reproduciendo" in res["mensaje"]


async def test_si_windows_rechaza_la_orden_lo_dice() -> None:
    res = await sessions(FakeBackend(CHROME, efecto=False, acepta=False)).control("chrome", "pausa")
    assert res["ok"] is False and "rechazó" in res["mensaje"]


async def test_siguiente_se_verifica_por_el_cambio_de_pista() -> None:
    res = await sessions(FakeBackend(CHROME)).control("chrome", "siguiente")
    assert res["verificado"] is True and res["titulo"] == "Otra pista"
    sin = await sessions(FakeBackend(CHROME, efecto=False)).control("chrome", "siguiente")
    assert sin["ok"] is False and sin["verificado"] is False


async def test_elige_la_sesion_por_app_o_por_titulo() -> None:
    backend = FakeBackend(CHROME, SPOTIFY)
    await sessions(backend).control("spotify", "play")
    await sessions(backend).control("get lucky", "play")
    assert backend.ordenes == [("Spotify.exe", "play"), ("Spotify.exe", "play")]


async def test_varias_sesiones_posibles_sin_elegir_devuelve_la_lista_y_no_toca_nada() -> None:
    otra = replace(CHROME, id="Chrome#2", title="Otro vídeo")
    backend = FakeBackend(CHROME, otra)
    res = await sessions(backend).control("chrome", "pausa")
    assert res["ok"] is False and {s["id"] for s in res["sesiones"]} == {"Chrome", "Chrome#2"}
    assert backend.ordenes == []


async def test_sin_nombre_y_varias_sonando_pregunta_en_vez_de_adivinar() -> None:
    otra = replace(SPOTIFY, status=PLAYING)
    backend = FakeBackend(CHROME, otra)
    res = await sessions(backend).control("", "pausa")
    assert res["ok"] is False and len(res["sesiones"]) == 2 and backend.ordenes == []


async def test_sesion_que_no_existe_o_ninguna_sesion() -> None:
    assert (await sessions(FakeBackend(CHROME)).control("netflix", "pausa"))["ok"] is False
    vacio = await sessions(FakeBackend()).control("", "pausa")
    assert vacio["ok"] is False and "ninguna sesión" in vacio["mensaje"]


async def test_accion_desconocida() -> None:
    res = await sessions(FakeBackend(CHROME)).control("", "explotar")
    assert res["ok"] is False and "desconocida" in res["mensaje"]


@pytest.mark.parametrize(
    ("dicho", "accion"),
    [("Pausa", "pause"), ("reanuda", "play"), ("play/pause", "toggle"), ("detén", "stop"), ("next", "next")],
)
def test_sinonimos_de_accion(dicho: str, accion: str) -> None:
    assert canonical_action(dicho) == accion


# --- sin la API: cae a media.key con respuesta honesta ---------------------------------------------------------------


async def test_sin_api_media_control_pulsa_la_tecla_y_solo_dice_eso() -> None:
    sender = FakeSender()
    res = await sessions(NoApi(), sender).control("", "pausa")
    assert sender.pulsadas == [0xB3]  # VK_MEDIA_PLAY_PAUSE
    assert res["ok"] is True and res["verificado"] is False and res["disponible"] is False
    assert res["mensaje"].startswith("He pulsado play_pausa") and "no puedo comprobar" in res["mensaje"]
    assert "alternar" in res["mensaje"]


async def test_sin_api_ni_teclas_dice_que_no_puede() -> None:
    res = await sessions(NoApi()).control("", "pausa")
    assert res["ok"] is False and "No puedo" in res["mensaje"]


# --- como herramientas MCP -----------------------------------------------------------------------------------------


async def test_las_herramientas_estan_publicadas_y_funcionan_por_mcp() -> None:
    backend = FakeBackend(CHROME)
    server = hands_server.build_server(AppIndex(), sessions=sessions(backend))
    async with Client(server) as client:
        nombres = {t.name for t in (await client.list_tools()).tools}
        listado = json.loads((await client.call_tool("media.sessions", {})).content[0].text)
        control = json.loads((await client.call_tool("media.control", {"accion": "pausa"})).content[0].text)
    assert {"media.sessions", "media.control"} <= nombres
    assert listado["sesiones"][0]["app"] == "Chrome" and control["verificado"] is True


# --- honestidad tras pulsar una tecla ------------------------------------------------------------------------------


def test_media_key_solo_dice_que_pulso_la_tecla() -> None:
    res = Media(FakeSender()).key("pausa")
    assert res["verificado"] is False and res["mensaje"].startswith("He pulsado play_pausa")
    assert "nunca" in res["aviso"]


def test_las_respuestas_del_router_dicen_lo_que_se_hizo_no_el_efecto() -> None:
    assert all(texto.startswith("He pulsado") for texto in ANSWERS.values())


def paso_media_key() -> Step:
    resultado = json.dumps(Media(FakeSender()).key("play_pausa"))
    return Step("media.key", {"tecla": "play_pausa"}, resultado, True)


@pytest.mark.parametrize(
    "respuesta",
    [
        "El vídeo está en pausa.",
        "Ya está sonando.",
        "Se ha pausado la música.",
        "Pausado.",
        "No hay ningún vídeo reproduciéndose.",
        "No suena nada.",
    ],
)
def test_tras_media_key_no_se_puede_afirmar_el_estado(respuesta: str) -> None:
    texto = _grounded(respuesta, [paso_media_key()])
    assert texto.startswith("He pulsado play_pausa") and "no puedo comprobar" in texto
    assert "en pausa" not in texto.split(";")[0]


def test_tras_media_key_decir_que_se_pulso_la_tecla_se_acepta() -> None:
    assert _grounded("He pulsado play_pausa.", [paso_media_key()]) == "He pulsado play_pausa."


def test_con_media_control_verificado_el_estado_si_se_puede_decir() -> None:
    verificado = Step(
        "media.control",
        {"accion": "pausa"},
        json.dumps({"ok": True, "verificado": True, "estado_despues": "en pausa"}),
        True,
    )
    assert _grounded("Ahora está en pausa.", [verificado]) == "Ahora está en pausa."


def test_el_media_key_de_respaldo_de_media_control_tambien_es_solo_pulsar() -> None:
    respaldo = Step(
        "media.control",
        {"accion": "pausa"},
        json.dumps(
            {"ok": True, "verificado": False, "mensaje": "He pulsado play_pausa; no puedo comprobar si cambió."}
        ),
        True,
    )
    assert _grounded("Está en pausa.", [respaldo]).startswith("He pulsado play_pausa")


def test_media_sessions_sin_media_key_tampoco_se_toca_lo_que_afirma_con_datos() -> None:
    listado = Step("media.sessions", {}, json.dumps({"ok": True, "disponible": True, "sesiones": []}), True)
    assert _grounded("No hay ningún vídeo sonando.", [listado]) == "No hay ningún vídeo sonando."


def test_una_ausencia_sobre_lo_que_suena_sin_media_sessions_ni_busqueda_se_corrige() -> None:
    assert _grounded("No hay ningún vídeo sonando.", []).startswith("No lo he comprobado")
