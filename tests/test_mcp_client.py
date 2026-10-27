from __future__ import annotations

from mcp import Client

from core.mcp_client import McpTools
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry
from hands.server import build_server


def servidor_con_spotify():
    index = AppIndex()
    index.replace([AppEntry("startapps:Spotify!App", "Spotify", "startapps", "shell", "Spotify!App")])
    return build_server(index, launch=lambda _e: None)


async def test_lista_herramientas_con_esquema() -> None:
    async with Client(servidor_con_spotify()) as client:
        tools = {t.name: t for t in await McpTools(client).tools()}
    assert set(tools) == {"apps.search", "apps.launch"}
    assert "nombre" in tools["apps.search"].parameters["properties"]


async def test_llama_a_una_herramienta_y_devuelve_texto() -> None:
    async with Client(servidor_con_spotify()) as client:
        texto = await McpTools(client).call("apps.search", {"nombre": "spotify"})
    assert "startapps:Spotify!App" in texto


async def test_error_de_herramienta_se_marca() -> None:
    async with Client(servidor_con_spotify()) as client:
        texto = await McpTools(client).call("apps.search", {})  # falta el argumento obligatorio
    assert texto.startswith("ERROR:")
