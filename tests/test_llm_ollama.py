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
