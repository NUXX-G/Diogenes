"""Escritorio, carpetas conocidas (es/en) y ejecutables y scripts que siempre piden confirmación."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.agent import Agent
from core.intent import IntentGate
from core.mcp_client import McpTools
from core.safety import ConfirmationPolicy
from hands import server as hands_server
from hands.apps import desktop, folders
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry
from hands.apps.policy import LaunchPolicy
from tests.test_agent import FakeLLM, decir, llamar

# --- carpetas conocidas ------------------------------------------------------------------------------------------


@pytest.fixture
def perfil(tmp_path: Path) -> Path:
    for name in ("Downloads", "Documents", "Pictures", "Music", "Videos", "Desktop"):
        (tmp_path / name).mkdir()
    return tmp_path


def test_cada_carpeta_entra_con_su_nombre_en_espanol_y_en_ingles(perfil: Path) -> None:
    entradas = folders.scan(folders.resolve(perfil, registry={}))
    nombres = {e.name for e in entradas}
    assert {"Descargas", "Downloads", "Documentos", "Documents", "Imágenes", "Pictures", "Música", "Music"} <= nombres
    assert {"Vídeos", "Videos", "Escritorio", "Desktop", "Papelera", "Recycle Bin"} <= nombres
    por_nombre = {e.name: e for e in entradas}
    assert por_nombre["Descargas"].target == por_nombre["Downloads"].target == str(perfil / "Downloads")
    assert por_nombre["Papelera"].launch_kind == "uri" and por_nombre["Papelera"].target == "shell:RecycleBinFolder"
    assert len({e.id for e in entradas}) == len(entradas)


def test_el_registro_manda_sobre_la_carpeta_por_defecto(perfil: Path, tmp_path_factory: pytest.TempPathFactory) -> None:
    otra = tmp_path_factory.mktemp("OneDrive") / "Documentos"
    otra.mkdir()
    rutas = folders.resolve(perfil, registry={"documents": str(otra)})
    assert rutas["documents"] == otra and rutas["downloads"] == perfil / "Downloads"


def test_una_carpeta_que_no_existe_no_entra_pero_la_papelera_si(tmp_path: Path) -> None:
    entradas = folders.scan(folders.resolve(tmp_path, registry={}))
    assert {e.name for e in entradas} == set(folders.RECYCLE_ALIASES)


@pytest.mark.parametrize(
    ("consulta", "esperado"),
    [("descargas", "Descargas"), ("downloads", "Downloads"), ("videos", "Vídeos"), ("papelera", "Papelera")],
)
def test_se_encuentran_por_el_nombre_en_cualquier_idioma(perfil: Path, consulta: str, esperado: str) -> None:
    index = AppIndex()
    index.replace(folders.scan(folders.resolve(perfil, registry={})))
    assert index.search(consulta)[0].entry.name in {esperado, "Vídeos", "Videos"}


# --- escritorio ----------------------------------------------------------------------------------------------


def test_escritorio_del_usuario_y_publico(tmp_path: Path) -> None:
    usuario, publico = tmp_path / "usuario", tmp_path / "publico"
    for d in (usuario, publico):
        d.mkdir()
    (usuario / "Brawlhalla.lnk").write_text("x")
    (usuario / "Informe final.docx").write_text("x")
    (usuario / "Proyectos").mkdir()
    (usuario / "desktop.ini").write_text("x")
    (usuario / ".oculto").write_text("x")
    (publico / "Steam.lnk").write_text("x")
    entradas = desktop.scan([usuario, publico, tmp_path / "no-existe"])
    assert {e.name for e in entradas} == {"Brawlhalla", "Informe final", "Proyectos", "Steam"}
    assert all(e.source == "desktop" and e.launch_kind == "file" for e in entradas)
    assert next(e for e in entradas if e.name == "Steam").target == str(publico / "Steam.lnk")


def test_el_escritorio_tiene_un_tope(tmp_path: Path) -> None:
    for i in range(10):
        (tmp_path / f"archivo{i}.txt").write_text("x")
    assert len(desktop.scan([tmp_path], limit=4)) == 4


def test_raices_del_escritorio_incluyen_onedrive_y_publico_sin_repetir(perfil: Path) -> None:
    env = {"OneDrive": "D:\\OneDrive", "PUBLIC": "C:\\Users\\Public"}
    raices = desktop.roots(env, home=perfil, registry={})
    assert perfil / "Desktop" in raices
    assert Path("D:\\OneDrive") / "Desktop" in raices and Path("C:\\Users\\Public") / "Desktop" in raices
    assert len(raices) == len(set(raices))


# --- política de ejecutables y scripts -------------------------------------------------------------------------


def file_entry(target: str, kind: str = "file") -> AppEntry:
    return AppEntry("desktop:x", "Cosa", "desktop", kind, target)  # type: ignore[arg-type]


@pytest.fixture(scope="module")
def politica() -> LaunchPolicy:
    return LaunchPolicy.load(Path("configuracion"))


@pytest.mark.parametrize(
    "ext", [".exe", ".bat", ".cmd", ".ps1", ".vbs", ".msi", ".reg", ".jar", ".scr", ".hta", ".js", ".wsf", ".com"]
)
def test_ejecutables_y_scripts_piden_confirmacion(politica: LaunchPolicy, ext: str) -> None:
    assert politica.requires_confirmation(file_entry(f"C:\\Users\\u\\Desktop\\cosa{ext}"), is_dir=lambda _p: False)
    assert politica.requires_confirmation(file_entry(f"C:\\x\\COSA{ext.upper()}"), is_dir=lambda _p: False)


@pytest.mark.parametrize("target", ["C:\\x\\foto.png", "C:\\x\\informe.docx", "C:\\x\\Brawlhalla.lnk", "C:\\x\\a.url"])
def test_documentos_y_accesos_no_piden_confirmacion(politica: LaunchPolicy, target: str) -> None:
    assert not politica.requires_confirmation(file_entry(target), is_dir=lambda _p: False)


def test_una_carpeta_llamada_como_un_exe_no_es_un_exe(politica: LaunchPolicy) -> None:
    assert not politica.requires_confirmation(file_entry("C:\\x\\carpeta.exe"), is_dir=lambda _p: True)


def test_apps_de_la_store_juegos_y_protocolos_no_piden_confirmacion(politica: LaunchPolicy) -> None:
    assert not politica.requires_confirmation(file_entry("Spotify!App", "shell"))
    assert not politica.requires_confirmation(file_entry("steam://rungameid/1", "uri"))


def test_la_lista_de_extensiones_sale_de_la_config(tmp_path: Path) -> None:
    (tmp_path / "confirmaciones.json").write_text('{"ejecutables": {"extensiones": ["XYZ"]}}', encoding="utf-8")
    politica = LaunchPolicy.load(tmp_path)
    assert politica.extensions == {".xyz"}
    assert politica.requires_confirmation(file_entry("C:\\x\\a.xyz"), is_dir=lambda _p: False)
    assert not politica.requires_confirmation(file_entry("C:\\x\\a.exe"), is_dir=lambda _p: False)


# --- hands + agente: aunque el usuario lo nombre, un ejecutable pregunta ---------------------------------------


@pytest.fixture
def lugar() -> tuple[AppIndex, list[AppEntry]]:
    index = AppIndex()
    index.replace(
        [
            AppEntry("desktop:setup", "Instalador", "desktop", "file", "C:\\Users\\u\\Desktop\\Instalador.exe"),
            AppEntry("desktop:script", "Limpieza", "desktop", "file", "C:\\Users\\u\\Desktop\\Limpieza.ps1"),
            AppEntry("carpetas:desc", "Descargas", "carpetas", "file", "C:\\Users\\u\\Downloads"),
        ]
    )
    return index, []


async def lanzar(
    lugar: tuple[AppIndex, list[AppEntry]], texto: str, app_id: str, respuesta: bool | None, modelo_fuerza: bool = False
) -> tuple[Any, list[AppEntry], list[tuple[str, dict[str, Any]]]]:
    index, lanzadas = lugar
    nombre = index.get(app_id).name  # type: ignore[union-attr]
    preguntas: list[tuple[str, dict[str, Any]]] = []

    async def confirm(tool: str, args: dict[str, Any]) -> bool:
        preguntas.append((tool, args))
        return bool(respuesta)

    lanzar_args: dict[str, Any] = {"id": app_id}
    if modelo_fuerza:
        lanzar_args["confirmado"] = True
    llm = FakeLLM(llamar("apps_search", nombre=nombre), llamar("apps_launch", **lanzar_args), decir("hecho"))
    server = hands_server.build_server(index, launch=lanzadas.append)
    async with Client(server) as client:
        agent = Agent(
            llm,
            McpTools(client),
            policy=ConfirmationPolicy([]),
            intent=IntentGate.load(),
            confirm=confirm if respuesta is not None else None,
        )
        resultado = await agent.run(texto)
    return resultado, lanzadas, preguntas


async def test_un_exe_nombrado_por_el_usuario_pregunta_y_se_lanza_si_dice_si(lugar: Any) -> None:
    _, lanzadas, preguntas = await lanzar(lugar, "abre el instalador", "desktop:setup", True)
    assert [e.id for e in lanzadas] == ["desktop:setup"]
    assert len(preguntas) == 1 and "Instalador.exe" in preguntas[0][1]["objetivo"]


async def test_un_exe_nombrado_por_el_usuario_no_se_lanza_si_dice_no_o_no_hay_a_quien_preguntar(lugar: Any) -> None:
    for respuesta in (False, None):
        _, lanzadas, _ = await lanzar(lugar, "abre el instalador", "desktop:setup", respuesta)
        assert lanzadas == []


async def test_un_script_tambien_pregunta(lugar: Any) -> None:
    _, lanzadas, preguntas = await lanzar(lugar, "ejecuta limpieza", "desktop:script", False)
    assert lanzadas == [] and "Limpieza.ps1" in preguntas[0][1]["objetivo"]


async def test_el_modelo_no_puede_darse_la_confirmacion_de_un_exe(lugar: Any) -> None:
    _, lanzadas, _ = await lanzar(lugar, "abre el instalador", "desktop:setup", None, modelo_fuerza=True)
    assert lanzadas == []


async def test_una_carpeta_se_abre_sin_preguntar(lugar: Any) -> None:
    _, lanzadas, preguntas = await lanzar(lugar, "abre descargas", "carpetas:desc", None)
    assert [e.id for e in lanzadas] == ["carpetas:desc"] and preguntas == []
