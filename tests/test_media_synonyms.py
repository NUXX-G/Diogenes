"""media.key acepta sinónimos en el servicio (no solo en el router) y /para es el mismo botón que /parar."""

from __future__ import annotations

import asyncio

import pytest

from hands.system.media import VIRTUAL_KEYS, Media, canonical_key
from tests.test_media_router import FakeSender
from tests.test_panic import PLAZO, Montaje, SlowLLM


@pytest.mark.parametrize(
    ("dicho", "tecla"),
    [
        ("pausa", "play_pausa"),
        ("pause", "play_pausa"),
        ("play", "play_pausa"),
        ("reanuda", "play_pausa"),
        ("Reanudar", "play_pausa"),
        ("para", "play_pausa"),
        ("play/pause", "play_pausa"),
        ("play pausa", "play_pausa"),
        ("detener", "parar"),
        ("detén", "parar"),
        ("stop", "parar"),
        ("next", "siguiente"),
        ("previous", "anterior"),
        ("sube", "subir_volumen"),
        ("volume down", "bajar_volumen"),
        ("mute", "silencio"),
        ("Silenciar", "silencio"),
    ],
)
def test_los_sinonimos_pulsan_la_tecla_del_catalogo(dicho: str, tecla: str) -> None:
    sender = FakeSender()
    res = Media(sender).key(dicho)
    assert res["ok"] is True and res["tecla"] == tecla
    assert sender.pulsadas == [VIRTUAL_KEYS[tecla]]


def test_las_teclas_del_catalogo_siguen_valiendo_y_lo_desconocido_sigue_fallando() -> None:
    assert all(canonical_key(k) == k for k in VIRTUAL_KEYS)
    assert canonical_key("explotar") is None
    assert Media(FakeSender()).key("explotar")["ok"] is False


async def test_para_escrito_es_el_boton_del_panico() -> None:
    llm = SlowLLM()
    async with Montaje(llm) as m:
        m.consola.escribe("abre algo")
        await asyncio.wait_for(llm.waiting.wait(), PLAZO)
        m.consola.escribe("/para")
        await m.consola.espera_salida("tarea cancelada")
    assert llm.cancelled is True and m.liberado == 1
