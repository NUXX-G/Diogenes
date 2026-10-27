from __future__ import annotations

import json

import pytest

from core.choices import Option, parse_options, resolve

TRES = (Option(1, "a:1", "Discord Canary"), Option(2, "a:2", "Discord PTB"), Option(3, "a:3", "Discord Dev"))
DOS = (Option(1, "s:1", "Spotify"), Option(2, "r:1", "spotify_cli"))


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("el primero", 1),
        ("La primera", 1),
        ("la segunda", 2),
        ("2", 2),
        ("¡el tercero!", 3),
        ("la última", 3),
        ("el 1", 1),
    ],
)
def test_ordinales(texto: str, esperado: int) -> None:
    choice = resolve(texto, TRES)
    assert choice is not None and choice.n == esperado


def test_nombre_exacto_y_por_palabra_unica() -> None:
    assert resolve("spotify", DOS) == DOS[0]  # igual al nombre, no al prefijo
    assert resolve("cli", DOS) == DOS[1]
    assert resolve("PTB", TRES) == TRES[1]


def test_nombre_ambiguo_no_se_resuelve() -> None:
    assert resolve("discord", TRES) is None


def test_si_solo_vale_con_una_opcion() -> None:
    una = (Option(1, "a:1", "Brawlhalla"),)
    assert resolve("sí", una) == una[0]
    assert resolve("vale", una) == una[0]
    assert resolve("sí", TRES) is None


def test_lo_que_no_es_una_eleccion_no_se_resuelve() -> None:
    assert resolve("la app", DOS) is None  # lo decide el modelo con el historial
    assert resolve("el cuarto", TRES) is None
    assert resolve("el primero y luego el segundo", TRES) is None
    assert resolve("mejor ábreme el primero de los de la lista de ayer", TRES) is None
    assert resolve("", TRES) is None
    assert resolve("el primero", ()) is None


def test_parse_options_solo_con_empate() -> None:
    empate = {
        "ganador_claro": False,
        "opciones": [{"n": 1, "id": "a:1", "nombre": "A"}, {"n": 2, "id": "a:2", "nombre": "B"}],
    }
    assert parse_options(json.dumps(empate)) == (Option(1, "a:1", "A"), Option(2, "a:2", "B"))
    assert parse_options(json.dumps({**empate, "ganador_claro": True})) == ()
    assert parse_options(json.dumps({"ganador_claro": False, "resultados": []})) == ()
    assert parse_options("no es json") == ()
