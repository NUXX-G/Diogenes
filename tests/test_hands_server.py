from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from starlette.testclient import TestClient

from hands import server as hands_server
from hands.actionlog import ActionLog
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry


@pytest.fixture
def index() -> AppIndex:
    idx = AppIndex()
    idx.replace(
        [
            AppEntry("startapps:Spotify!App", "Spotify", "startapps", "shell", "Spotify!App"),
            AppEntry("steam:291550", "Brawlhalla", "steam", "uri", "steam://rungameid/291550"),
        ]
    )
    return idx


def texto(result: Any) -> dict[str, Any]:
    return json.loads(result.content[0].text)


async def test_herramientas_publicadas(index: AppIndex) -> None:
    async with Client(hands_server.build_server(index)) as client:
        nombres = {t.name for t in (await client.list_tools()).tools}
    assert nombres == {"apps.search", "apps.launch"}


async def test_buscar_y_lanzar(index: AppIndex, tmp_path: Path) -> None:
    lanzadas: list[AppEntry] = []
    servidor = hands_server.build_server(index, launch=lanzadas.append, actions=ActionLog(tmp_path))
    async with Client(servidor) as client:
        encontrado = texto(await client.call_tool("apps.search", {"nombre": "brawlhala"}))
        assert encontrado["resultados"][0]["id"] == "steam:291550"
        lanzado = texto(await client.call_tool("apps.launch", {"id": "steam:291550"}))
    assert lanzado["ok"] is True
    assert [e.target for e in lanzadas] == ["steam://rungameid/291550"]


async def test_launch_rechaza_ids_que_no_estan_en_el_indice(index: AppIndex) -> None:
    lanzadas: list[AppEntry] = []
    async with Client(hands_server.build_server(index, launch=lanzadas.append)) as client:
        result = texto(await client.call_tool("apps.launch", {"id": "powershell.exe -c calc"}))
    assert result["ok"] is False
    assert lanzadas == []


async def test_registro_de_acciones_sin_datos_extra(index: AppIndex, tmp_path: Path) -> None:
    async with Client(hands_server.build_server(index, launch=lambda _e: None, actions=ActionLog(tmp_path))) as client:
        await client.call_tool("apps.launch", {"id": "startapps:Spotify!App"})
    (archivo,) = tmp_path.glob("hands-*.jsonl")
    linea = json.loads(archivo.read_text(encoding="utf-8").splitlines()[0])
    assert linea["tool"] == "apps.launch"
    assert linea["args"] == {"id": "startapps:Spotify!App"}
    assert linea["ok"] is True


async def test_fallo_del_lanzador_se_registra_y_se_propaga(index: AppIndex, tmp_path: Path) -> None:
    def roto(_entry: AppEntry) -> None:
        raise OSError("no se pudo")

    async with Client(hands_server.build_server(index, launch=roto, actions=ActionLog(tmp_path))) as client:
        result = await client.call_tool("apps.launch", {"id": "steam:291550"})
    assert result.is_error
    (archivo,) = tmp_path.glob("hands-*.jsonl")
    assert json.loads(archivo.read_text(encoding="utf-8"))["ok"] is False


def test_token_obligatorio_para_construir_el_middleware() -> None:
    with pytest.raises(ValueError):
        hands_server.TokenAuthMiddleware(lambda *a: None, "")  # type: ignore[arg-type,return-value]


def test_http_sin_token_o_con_token_malo_da_401(index: AppIndex) -> None:
    app = hands_server.create_app(hands_server.build_server(index), "secreto")
    with TestClient(app, base_url="http://127.0.0.1:8765") as http:
        assert http.post("/mcp", json={}).status_code == 401
        assert http.post("/mcp", json={}, headers={"Authorization": "Bearer otro"}).status_code == 401
        assert http.post("/mcp", json={}, headers={"Authorization": "secreto"}).status_code == 401
        # con el token correcto la petición llega al servidor MCP (que rechaza el cuerpo vacío, pero no con 401)
        assert http.post("/mcp", json={}, headers={"Authorization": "Bearer secreto"}).status_code != 401
