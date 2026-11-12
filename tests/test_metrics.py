"""Bloque 0: tiempos por etapa de cada turno (modelo con tokens, herramientas, total) y el banco de medición."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from core.agent import Agent
from core.bench.__main__ import run, table
from core.bench.sim import SimulatedLLM, render
from core.llm.base import LLMResponse, LLMUsage, Message, ToolSpec
from core.llm.ollama import OllamaProvider
from core.metrics import MetricsLog, ModelCall, ToolTiming, TurnMetrics, describe
from core.session import Session
from tests.test_agent import POLICY, FakeBackend, FakeLLM, decir, llamar


async def test_ollama_devuelve_tokens_evaluados_generados_y_carga_en_frio() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "hola"},
                "prompt_eval_count": 1830,
                "eval_count": 41,
                "load_duration": 85_000_000_000,
                "prompt_eval_duration": 4_800_000_000,
                "eval_duration": 12_000_000_000,
            },
        )

    llm = OllamaProvider("http://ollama.test", "m", client=httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    respuesta = await llm.chat([Message("user", "hola")], [])
    assert respuesta.usage == LLMUsage(1830, 41, 85.0, 4.8, 12.0)


async def test_ollama_fija_num_ctx_configurable() -> None:
    visto: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": ""}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await OllamaProvider("http://o", "m", num_ctx=6144, client=client).chat([Message("user", "x")], [])
    assert visto["options"]["num_ctx"] == 6144


class UsageLLM(FakeLLM):
    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        r = await super().chat(messages, tools)
        return LLMResponse(r.content, r.tool_calls, LLMUsage(100, 10))


async def test_el_turno_registra_cada_llamada_al_modelo_y_cada_herramienta() -> None:
    llm = UsageLLM(llamar("apps_search", nombre="x"), decir("hecho"))
    resultado = await Agent(llm, FakeBackend(), policy=POLICY).run("abre x")
    m = resultado.metrics
    assert m.route == "planificador"
    assert [(c.input_tokens, c.output_tokens, c.tools_sent) for c in m.model_calls] == [(100, 10, 2), (100, 10, 2)]
    assert [(t.tool, t.ok) for t in m.tools] == [("apps.search", True)]
    assert m.seconds == resultado.seconds >= m.model_seconds + m.tool_seconds - 1e-6


async def test_un_atajo_no_llama_al_modelo_y_se_marca_como_atajo() -> None:
    backend = FakeBackend(names=("media.key",))
    resultado = await Agent(FakeLLM(), backend, policy=POLICY).run("pausa")
    assert resultado.metrics.route == "atajo"
    assert resultado.metrics.model_calls == []
    assert [t.tool for t in resultado.metrics.tools] == ["media.key"]


def test_linea_legible_y_registro_json_sin_texto_del_usuario(tmp_path: Path) -> None:
    m = TurnMetrics(
        "planificador",
        [ModelCall(5.0, 1800, 30, 13), ModelCall(9.1, 300, 12, 13, load_seconds=0.0)],
        [ToolTiming("apps.search", 0.2, True)],
        14.4,
    )
    linea = describe(m)
    assert "modelo 2× 14.1 s (2100→42 tok)" in linea and "herramientas 1× 0.2 s" in linea and "total 14.4 s" in linea
    MetricsLog(tmp_path).record(m)
    (fichero,) = tmp_path.glob("core-*.jsonl")
    fila = json.loads(fichero.read_text(encoding="utf-8"))
    assert fila["route"] == "planificador" and fila["model_seconds"] == 14.1
    assert "abre" not in fichero.read_text(encoding="utf-8")


async def test_la_sesion_ensena_y_registra_los_tiempos(tmp_path: Path) -> None:
    salidas: list[str] = []
    session = Session(salidas.append, metrics=MetricsLog(tmp_path), show_metrics=True)
    lineas = iter(["abre x", "salir"])

    async def leer() -> str:
        return next(lineas)

    agent = Agent(FakeLLM(llamar("apps_search", nombre="x"), decir("hecho")), FakeBackend(), policy=POLICY)
    await session.serve(agent, leer)
    assert any(s.strip().startswith("⏱ planificador") for s in salidas)
    assert len(list(tmp_path.glob("core-*.jsonl"))) == 1


# --- simulador y banco -----------------------------------------------------------------------------------------


async def test_el_simulador_solo_cobra_lo_que_no_estaba_en_la_cache_de_prefijo() -> None:
    llm = SimulatedLLM(lambda _m, _t: LLMResponse("ok"))
    tools = [ToolSpec("a", "x" * 700, {})]
    base = [Message("system", "s" * 3500), Message("user", "hola")]
    await llm.chat(base, tools)
    await llm.chat([*base, Message("assistant", "ok"), Message("user", "otra")], tools)
    primero, segundo = llm.calls
    assert primero.new_tokens == primero.prompt_tokens  # en frío, todo
    assert segundo.new_tokens < 20  # solo lo nuevo
    await llm.chat([base[0], Message("user", "otra")], [ToolSpec("b", "y" * 700, {})])  # otras herramientas
    assert llm.calls[-1].new_tokens > 200  # se recalcula desde las herramientas
    assert render(base, tools).index("s" * 10) < render(base, tools).index("x" * 10)  # sistema antes que herramientas


async def test_el_banco_mide_las_cinco_ordenes_sin_tocar_el_pc() -> None:
    filas = await run(passes=1)
    assert [f["orden"] for f in filas] == [
        "abre Spotify",
        "cierra la calculadora",
        "búscame en YouTube cómo hacer mochis",
        "pausa el vídeo de YouTube",
        "dame el vídeo más visto del canal Hrom",
        "dame el vídeo más visto del canal Lofi Girl",
    ]
    assert all(f["llamadas_modelo"] >= 1 and f["segundos_total"] > 0 for f in filas)
    hrom = filas[4]
    assert hrom["herramientas"][-1] == "browser.click" and "Corrección" not in hrom["respuesta"]
    assert "**Total**" in table(filas)


async def test_con_recetas_y_la_de_hrom_ensenada_la_segunda_pasada_no_llama_al_modelo() -> None:
    filas = await run(passes=2, recipes=True, teach=True)
    primera, segunda = filas[:5], filas[5:]
    assert primera[-1]["ruta"] == "receta" and primera[-1]["llamadas_modelo"] == 0  # Hrom, enseñada
    assert all(f["llamadas_modelo"] == 1 for f in primera[:4])
    assert all(f["ruta"] == "receta" and f["llamadas_modelo"] == 0 for f in segunda), segunda


async def test_la_receta_ensenada_en_el_banco_llega_al_video_mas_visto() -> None:
    filas = await run(passes=1, recipes=True, teach=True)
    hrom = filas[-1]
    assert hrom["herramientas"] == ["web.site_search", "browser.snapshot"] + ["browser.click"] * 4
    assert hrom["respuesta"].startswith("Hecho como me enseñaste") and "récord absoluto" in hrom["respuesta"]
