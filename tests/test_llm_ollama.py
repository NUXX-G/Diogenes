from __future__ import annotations

import json
from typing import Any

import httpx
import pytest

from core.llm.base import LLMError, Message, ToolCall, ToolSpec
from core.llm.ollama import OllamaProvider

TOOL = ToolSpec("apps_search", "Busca apps", {"type": "object", "properties": {"nombre": {"type": "string"}}})


def provider(handler: Any) -> OllamaProvider:
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return OllamaProvider("http://ollama.test/", "modelo-x", client=client)


async def test_envia_modelo_herramientas_y_thinking_desactivado() -> None:
    visto: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto["url"] = str(request.url)
        visto["body"] = json.loads(request.content)
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hola"}})

    respuesta = await provider(handler).chat([Message("user", "hola")], [TOOL])
    assert respuesta.content == "hola" and respuesta.tool_calls == ()
    assert visto["url"] == "http://ollama.test/api/chat"
    body = visto["body"]
    assert (body["model"], body["stream"], body["think"]) == ("modelo-x", False, False)
    assert body["tools"][0]["function"]["name"] == "apps_search"
    assert body["options"]["num_predict"] == 512  # generación acotada


async def test_interpreta_llamadas_a_herramientas_con_argumentos_dict_o_texto() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        calls = [
            {"function": {"name": "apps_search", "arguments": {"nombre": "spotify"}}},
            {"function": {"name": "apps_search", "arguments": '{"nombre": "steam"}'}},
            {"function": {"name": "apps_search", "arguments": "no es json"}},
        ]
        return httpx.Response(200, json={"message": {"content": "", "tool_calls": calls}})

    respuesta = await provider(handler).chat([Message("user", "x")], [TOOL])
    assert respuesta.tool_calls == (
        ToolCall("apps_search", {"nombre": "spotify"}),
        ToolCall("apps_search", {"nombre": "steam"}),
        ToolCall("apps_search", {}),
    )


async def test_historial_con_llamadas_y_resultados_viaja_en_formato_ollama() -> None:
    visto: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        visto.update(json.loads(request.content))
        return httpx.Response(200, json={"message": {"content": "ok"}})

    historial = [
        Message("assistant", "", (ToolCall("apps_search", {"nombre": "x"}),)),
        Message("tool", "{}", tool_name="apps_search"),
    ]
    await provider(handler).chat(historial, [])
    assert visto["messages"][0]["tool_calls"] == [{"function": {"name": "apps_search", "arguments": {"nombre": "x"}}}]
    assert visto["messages"][1] == {"role": "tool", "content": "{}", "tool_name": "apps_search"}
    assert "tools" not in visto


async def test_modelo_no_descargado_explica_como_arreglarlo() -> None:
    with pytest.raises(LLMError, match="ollama pull modelo-x"):
        await provider(lambda _r: httpx.Response(404, json={"error": "model not found"})).chat([], [])


async def test_ollama_apagado_da_error_legible() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("rechazado")

    with pytest.raises(LLMError, match="¿Está arrancado"):
        await provider(handler).chat([], [])


async def test_mantiene_el_modelo_cargado_30_minutos_por_defecto_y_es_configurable() -> None:
    cuerpos: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        cuerpos.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "hola"}})

    await provider(handler).chat([Message("user", "hola")], [])
    cliente = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    await OllamaProvider("http://ollama.test/", "m", client=cliente, keep_alive="2h").chat([Message("user", "x")], [])
    assert [c["keep_alive"] for c in cuerpos] == ["30m", "2h"]  # evita el arranque en frío de ~85 s en la CPU del aula


def _capturar(cuerpos: list[dict[str, Any]]) -> httpx.AsyncClient:
    def handler(request: httpx.Request) -> httpx.Response:
        cuerpos.append(json.loads(request.content))
        return httpx.Response(200, json={"message": {"role": "assistant", "content": "x"}})

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


async def test_usa_6_hilos_por_defecto_y_0_deja_que_decida_ollama() -> None:
    cuerpos: list[dict[str, Any]] = []
    await OllamaProvider("http://o", "m", client=_capturar(cuerpos)).chat([Message("user", "x")], [])
    await OllamaProvider("http://o", "m", num_thread=0, client=_capturar(cuerpos)).chat([Message("user", "x")], [])
    assert cuerpos[0]["options"]["num_thread"] == 6  # medido en el aula: ~1,6× más rápido que los hilos de Ollama
    assert "num_thread" not in cuerpos[1]["options"]


def test_from_env_lee_las_variables_y_las_vacias_valen_por_defecto() -> None:
    llm = OllamaProvider.from_env(
        {
            "DIOGENES_OLLAMA_BASE_URL": "",
            "DIOGENES_OLLAMA_MODEL": "otro:1b",
            "DIOGENES_OLLAMA_NUM_THREAD": "4",
            "DIOGENES_OLLAMA_NUM_CTX": "2048",
            "DIOGENES_OLLAMA_KEEP_ALIVE": "1h",
        }
    )
    assert (llm.base_url, llm.model) == ("http://127.0.0.1:11434", "otro:1b")
    assert llm._options["num_thread"] == 4 and llm._options["num_ctx"] == 2048
    assert llm._keep_alive == "1h"


def test_from_env_explica_un_numero_mal_escrito() -> None:
    with pytest.raises(LLMError, match="DIOGENES_OLLAMA_NUM_THREAD debe ser un número entero"):
        OllamaProvider.from_env({"DIOGENES_OLLAMA_NUM_THREAD": "seis"})


async def test_precarga_genera_un_solo_token_con_las_mismas_opciones_de_carga() -> None:
    cuerpos: list[dict[str, Any]] = []
    llm = OllamaProvider("http://o", "m", num_ctx=4096, client=_capturar(cuerpos))
    await llm.warm([Message("system", "s"), Message("user", "hola")], [TOOL])
    await llm.chat([Message("system", "s"), Message("user", "abre x")], [TOOL])
    precarga, orden = cuerpos
    assert precarga["options"]["num_predict"] == 1 and orden["options"]["num_predict"] == 512
    # cambiar num_ctx o num_thread recargaría el modelo: la precarga usa los mismos
    for clave in ("num_ctx", "num_thread"):
        assert precarga["options"][clave] == orden["options"][clave]
    assert precarga["tools"] == orden["tools"] and precarga["messages"][0] == orden["messages"][0]


async def test_explica_que_variable_subir_si_el_prompt_no_cabe() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        error = "request (4109 tokens) exceeds the available context size (4096 tokens), try increasing it"
        return httpx.Response(400, json={"error": error})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(LLMError, match="sube DIOGENES_OLLAMA_NUM_CTX"):
        await OllamaProvider("http://o", "m", num_ctx=4096, client=client).chat([Message("user", "x")], [])
