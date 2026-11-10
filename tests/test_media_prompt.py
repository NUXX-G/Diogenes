"""El planificador conoce media_key para frases largas de control multimedia ("pausa el vídeo que tengo en incógnito")."""

from __future__ import annotations

import json

import pytest

from core.agent import SYSTEM_PROMPT, Agent
from core.safety import ConfirmationPolicy
from tests.test_agent import FakeBackend, FakeLLM, decir, llamar

POLICY = ConfirmationPolicy(frozenset())


def test_el_prompt_explica_media_key_y_sus_teclas() -> None:
    assert "media_key" in SYSTEM_PROMPT
    for tecla in ("play_pausa", "siguiente", "anterior", "subir_volumen", "bajar_volumen", "silencio"):
        assert tecla in SYSTEM_PROMPT


def test_el_prompt_dice_que_no_hay_que_abrir_ni_buscar_nada_para_pausar() -> None:
    assert "incógnito" in SYSTEM_PROMPT
    assert "NO abras ni busques" in SYSTEM_PROMPT


@pytest.mark.asyncio
async def test_frase_larga_de_pausa_llega_al_modelo_con_el_prompt_y_media_key_disponible_y_se_ejecuta() -> None:
    llm = FakeLLM(llamar("media_key", tecla="play_pausa"), decir("Pausado."))
    backend = FakeBackend({"media.key": json.dumps({"ok": True})}, names=("apps.search", "media.key"))
    result = await Agent(llm, backend, policy=POLICY).run("pausa el video que tengo en incognito")
    assert "media_key" in llm.seen[0][0].content  # el prompt de sistema llega al modelo
    assert "media_key" in {t.name for t in llm.tools_seen[0]}  # y la herramienta está a su alcance
    assert [(s.tool, s.arguments, s.ok) for s in result.steps] == [("media.key", {"tecla": "play_pausa"}, True)]


def test_el_prompt_manda_a_forzar_el_cierre_sin_preguntar_en_texto() -> None:
    from core.agent import SYSTEM_PROMPT

    assert "apps_force_close" in SYSTEM_PROMPT and "NO preguntes en texto" in SYSTEM_PROMPT


def test_el_prompt_prefiere_media_control_y_limita_lo_que_se_puede_decir_tras_media_key() -> None:
    assert "media_control" in SYSTEM_PROMPT and "media_sessions" in SYSTEM_PROMPT
    assert "he pulsado X" in SYSTEM_PROMPT and "no hay ningún vídeo" in SYSTEM_PROMPT
