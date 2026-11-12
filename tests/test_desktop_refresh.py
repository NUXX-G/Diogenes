"""Un archivo recién creado en el escritorio sale al buscarlo, sin esperar al refresco del índice ni reiniciar hands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from hands import server as hands_server
from hands.apps import desktop
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry


@pytest.fixture(autouse=True)
def _restaura_el_limite() -> Any:
    antes = hands_server.DESKTOP_REFRESH_SECONDS
    yield
    hands_server.DESKTOP_REFRESH_SECONDS = antes


SPOTIFY = AppEntry("startapps:spotify", "Spotify", "startapps", "shell", "Spotify!App")


def archivo(nombre: str) -> AppEntry:
    return AppEntry(f"desktop:{nombre}", Path(nombre).stem, "desktop", "file", rf"C:\Users\x\Desktop\{nombre}")


class Escritorio:
    """Lo que hay ahora mismo en el escritorio; crece entre una búsqueda y la siguiente."""

    def __init__(self, *nombres: str) -> None:
        self.nombres = list(nombres)
        self.lecturas = 0
        self.falla = False

    def __call__(self) -> list[AppEntry]:
        self.lecturas += 1
        if self.falla:
            raise OSError("escritorio ilegible")
        return [archivo(n) for n in self.nombres]


async def buscar(server: Any, nombre: str) -> dict[str, Any]:
    async with Client(server) as client:
        return json.loads((await client.call_tool("apps.search", {"nombre": nombre})).content[0].text)


def servidor(escritorio: Escritorio, tiempo: float = 0.0) -> tuple[Any, AppIndex]:
    index = AppIndex()
    index.replace([SPOTIFY, *escritorio()])
    escritorio.lecturas = 0
    hands_server.DESKTOP_REFRESH_SECONDS = tiempo
    return hands_server.build_server(index, desktop_scan=escritorio), index


async def test_un_archivo_nuevo_del_escritorio_sale_sin_reiniciar() -> None:
    escritorio = Escritorio("viejo.txt")
    server, _ = servidor(escritorio)
    escritorio.nombres.append("informe final.docx")  # se acaba de crear
    res = await buscar(server, "informe final")
    assert res["resultados"][0]["id"] == "desktop:informe final.docx"


async def test_si_la_lectura_limitada_no_lo_vio_una_busqueda_sin_resultados_relee_igualmente() -> None:
    escritorio = Escritorio("viejo.txt")
    server, _ = servidor(escritorio, tiempo=3600)  # la lectura normal está limitada: acaba de hacerse
    await buscar(server, "viejo")  # gasta la lectura permitida
    escritorio.nombres.append("presupuesto.xlsx")
    res = await buscar(server, "presupuesto")
    assert res["resultados"] and res["resultados"][0]["id"] == "desktop:presupuesto.xlsx"


async def test_la_lectura_normal_esta_limitada_en_frecuencia() -> None:
    escritorio = Escritorio("viejo.txt")
    server, _ = servidor(escritorio, tiempo=3600)
    await buscar(server, "viejo")
    await buscar(server, "viejo")
    await buscar(server, "spotify")
    assert escritorio.lecturas == 1


async def test_si_el_escritorio_no_se_puede_leer_la_busqueda_sigue_funcionando() -> None:
    escritorio = Escritorio("viejo.txt")
    server, _ = servidor(escritorio)
    escritorio.falla = True
    res = await buscar(server, "spotify")
    assert res["resultados"][0]["id"] == "startapps:spotify"


async def test_un_archivo_borrado_del_escritorio_deja_de_salir() -> None:
    escritorio = Escritorio("viejo.txt", "borrar.txt")
    server, _ = servidor(escritorio)
    escritorio.nombres.remove("borrar.txt")
    res = await buscar(server, "borrar")
    assert all(r["id"] != "desktop:borrar.txt" for r in res["resultados"])


def test_refresh_source_solo_cambia_esa_fuente_y_dice_si_cambio_algo() -> None:
    index = AppIndex()
    index.replace([SPOTIFY, archivo("a.txt")])
    assert index.refresh_source("desktop", [archivo("a.txt")]) is False
    assert index.refresh_source("desktop", [archivo("a.txt"), archivo("b.txt")]) is True
    assert index.get("desktop:b.txt") is not None and index.get("startapps:spotify") is not None
    assert index.refresh_source("desktop", []) is True
    assert index.get("desktop:a.txt") is None and index.get("startapps:spotify") is not None


def test_el_origen_coincide_con_el_de_la_fuente_del_escritorio() -> None:
    assert desktop.SOURCE == "desktop"
