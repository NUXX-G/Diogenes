"""Caché del índice, carga en segundo plano y descubrimiento en paralelo."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from hands import server as hands_server
from hands.apps import discovery
from hands.apps.cache import IndexCache
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry

APPS = [
    AppEntry("startapps:s", "Spotify", "startapps", "shell", "Spotify!App"),
    AppEntry("steam:1", "Brawlhalla", "steam", "uri", "steam://rungameid/291550"),
]


class Reloj:
    def __init__(self, ahora: float = 1000.0) -> None:
        self.ahora = ahora

    def __call__(self) -> float:
        return self.ahora


# --- caché ---------------------------------------------------------------------------------------------------


def test_guarda_y_recupera_dentro_del_tiempo(tmp_path: Path) -> None:
    reloj = Reloj()
    cache = IndexCache(tmp_path / "data" / "indice.json", ttl=600, now=reloj)
    assert cache.load() is None
    cache.save(APPS)
    reloj.ahora += 599
    assert cache.load() == APPS


def test_caduca_pasado_el_tiempo(tmp_path: Path) -> None:
    reloj = Reloj()
    cache = IndexCache(tmp_path / "i.json", ttl=600, now=reloj)
    cache.save(APPS)
    reloj.ahora += 601
    assert cache.load() is None


@pytest.mark.parametrize(
    "contenido",
    ["{no es json", "[]", '{"version": 99, "guardado": 1000, "entradas": []}', '{"version": 1, "guardado": 1000}', ""],
)
def test_una_cache_rota_o_de_otra_version_se_ignora(tmp_path: Path, contenido: str) -> None:
    archivo = tmp_path / "i.json"
    archivo.write_text(contenido, encoding="utf-8")
    assert IndexCache(archivo, now=Reloj()).load() is None


def test_guardar_no_deja_ficheros_temporales_y_no_falla_si_no_se_puede(tmp_path: Path) -> None:
    cache = IndexCache(tmp_path / "i.json", now=Reloj())
    cache.save(APPS)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["i.json"]
    bloqueado = tmp_path / "fichero"
    bloqueado.write_text("x")
    IndexCache(bloqueado / "sub" / "i.json", now=Reloj()).save(APPS)  # el padre es un fichero: no lanza


# --- primera carga ----------------------------------------------------------------------------------------------


async def test_con_cache_vigente_no_se_descubre_nada(tmp_path: Path) -> None:
    cache = IndexCache(tmp_path / "i.json", now=Reloj())
    cache.save(APPS)
    index = AppIndex()

    def descubrir() -> list[AppEntry]:
        pytest.fail("no debe tocar PowerShell con la caché vigente")

    await hands_server.load_index(index, cache, descubrir)
    assert index.ready and len(index) == 2


async def test_sin_cache_se_descubre_y_se_guarda_para_el_proximo_arranque(tmp_path: Path) -> None:
    cache = IndexCache(tmp_path / "i.json", now=Reloj())
    index = AppIndex()
    await hands_server.load_index(index, cache, lambda: list(APPS))
    assert index.ready and len(index) == 2
    assert cache.load() == APPS


async def test_si_el_descubrimiento_revienta_el_indice_queda_listo_y_vacio(tmp_path: Path) -> None:
    def descubrir() -> list[AppEntry]:
        raise OSError("PowerShell")

    index = AppIndex()
    await hands_server.load_index(index, IndexCache(tmp_path / "i.json", now=Reloj()), descubrir)
    assert index.ready and len(index) == 0


async def test_el_refresco_periodico_actualiza_indice_y_cache(tmp_path: Path) -> None:
    cache = IndexCache(tmp_path / "i.json", now=Reloj())
    index = AppIndex()
    import asyncio

    tarea = asyncio.create_task(hands_server._refresh_forever(index, cache, every=0.01, discover=lambda: list(APPS)))
    await asyncio.wait_for(asyncio.to_thread(index._ready.wait), timeout=2)
    tarea.cancel()
    assert len(index) == 2 and cache.load() == APPS


# --- las herramientas esperan a que el índice esté listo ---------------------------------------------------------


async def test_mientras_se_indexa_las_herramientas_lo_dicen_en_vez_de_devolver_cero_resultados() -> None:
    index = AppIndex()  # nunca cargado
    async with Client(hands_server.build_server(index, ready_timeout=0.05)) as client:
        res = json.loads((await client.call_tool("apps.search", {"nombre": "spotify"})).content[0].text)
        lanzar = json.loads((await client.call_tool("apps.launch", {"id": "startapps:s"})).content[0].text)
    assert res["ok"] is False and "indexando" in res["mensaje"] and "resultados" not in res
    assert lanzar["ok"] is False and "indexando" in lanzar["mensaje"]


async def test_si_el_indice_termina_mientras_esperan_responden_normal() -> None:
    index = AppIndex()
    threading.Timer(0.1, index.replace, args=[APPS]).start()
    async with Client(hands_server.build_server(index, ready_timeout=5)) as client:
        res = json.loads((await client.call_tool("apps.search", {"nombre": "spotify"})).content[0].text)
    assert res["ok"] is True and res["resultados"][0]["id"] == "startapps:s"


# --- descubrimiento en paralelo ----------------------------------------------------------------------------------


def test_las_fuentes_se_recogen_a_la_vez_y_el_orden_se_mantiene(monkeypatch: pytest.MonkeyPatch) -> None:
    barrera = threading.Barrier(3, timeout=5)  # solo se abre si las tres fuentes corren a la vez

    def fuente(nombre: str) -> Any:
        def recoger() -> list[AppEntry]:
            barrera.wait()
            return [AppEntry(f"{nombre}:1", nombre, nombre, "file", nombre)]

        return recoger

    monkeypatch.setattr(discovery, "_es_windows", lambda: True)
    monkeypatch.setattr(discovery, "_fuentes", lambda _ps: {n: fuente(n) for n in ("a", "b", "c")})
    assert [e.name for e in discovery.discover()] == ["a", "b", "c"]
