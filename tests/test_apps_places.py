"""Escritorio, carpetas conocidas (es/en) y ejecutables y scripts que siempre piden confirmación."""

from __future__ import annotations

import struct
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.agent import Agent
from core.intent import IntentGate
from core.mcp_client import McpTools
from core.safety import ConfirmationPolicy
from hands import server as hands_server
from hands.apps import desktop, folders, shortcuts
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry
from hands.apps.policy import LaunchPolicy
from hands.apps.shortcuts import LinkTarget
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


@pytest.mark.parametrize("target", ["C:\\x\\foto.png", "C:\\x\\informe.docx", "C:\\x\\a.url"])
def test_documentos_y_accesos_web_no_piden_confirmacion(politica: LaunchPolicy, target: str) -> None:
    assert not politica.requires_confirmation(file_entry(target), is_dir=lambda _p: False)


# --- accesos .lnk: manda el destino ------------------------------------------------------------------------------


def lnk_entry(source: str = "startmenu") -> AppEntry:
    return AppEntry(f"{source}:x", "Cosa", source, "file", "C:\\x\\Cosa.lnk")


def destino(*paths: str, arguments: str = "", shell_name: str = "", advertised: bool = False) -> Any:
    link = LinkTarget(tuple(paths), shell_name, arguments, advertised)
    return lambda _path: link


@pytest.mark.parametrize("ext", [".bat", ".cmd", ".ps1", ".vbs", ".js", ".hta", ".reg", ".msi"])
def test_lnk_a_un_script_pide_confirmacion_y_ensena_el_destino(politica: LaunchPolicy, ext: str) -> None:
    review = politica.assess(lnk_entry(), read_link=destino(f"C:\\Temp\\malo{ext}"))
    assert review.requires and review.target == f"C:\\Temp\\malo{ext}"


def test_lnk_a_un_exe_del_menu_inicio_va_directo(politica: LaunchPolicy) -> None:
    assert not politica.assess(lnk_entry(), read_link=destino("C:\\Program Files\\App\\app.exe")).requires


def test_lnk_a_un_exe_con_argumentos_normales_va_directo(politica: LaunchPolicy) -> None:
    leer = destino("C:\\Program Files\\Chrome\\chrome.exe", arguments="--profile-directory=Default")
    assert not politica.assess(lnk_entry(), read_link=leer).requires


def test_lnk_a_un_exe_desde_el_escritorio_pregunta(politica: LaunchPolicy) -> None:
    review = politica.assess(lnk_entry("desktop"), read_link=destino("C:\\Users\\u\\Downloads\\raro.exe"))
    assert review.requires and "menú Inicio" in review.reason


def test_lnk_a_documento_o_carpeta_no_pregunta_desde_ningun_sitio(politica: LaunchPolicy) -> None:
    for destino_real in ("C:\\x\\informe.docx", "C:\\Users\\u\\Proyectos"):
        assert not politica.assess(lnk_entry("desktop"), read_link=destino(destino_real)).requires


@pytest.mark.parametrize(
    ("exe", "args"),
    [
        ("powershell.exe", "-File x.ps1"),
        ("cmd.exe", "/c malo.bat"),
        ("mshta.exe", "http://x"),
        ("WScript.exe", "x.vbs"),
    ],
)
def test_lnk_a_un_interprete_con_argumentos_es_un_script(politica: LaunchPolicy, exe: str, args: str) -> None:
    review = politica.assess(lnk_entry(), read_link=destino(f"C:\\Windows\\System32\\{exe}", arguments=args))
    assert review.requires and args in review.target


def test_lnk_a_un_interprete_sin_argumentos_del_menu_inicio_va_directo(politica: LaunchPolicy) -> None:
    assert not politica.assess(lnk_entry(), read_link=destino("C:\\Windows\\System32\\cmd.exe")).requires


def test_si_cualquier_destino_del_lnk_es_un_script_pregunta(politica: LaunchPolicy) -> None:
    leer = destino("C:\\Program Files\\App\\app.exe", shell_name="MALO.BAT")
    assert politica.assess(lnk_entry(), read_link=leer).requires


def test_lnk_ilegible_o_sin_destino_pregunta(politica: LaunchPolicy) -> None:
    assert politica.assess(lnk_entry(), read_link=lambda _p: None).requires
    assert politica.assess(lnk_entry("desktop"), read_link=destino()).requires
    assert politica.assess(lnk_entry(), read_link=destino(arguments="/c algo")).requires


def test_lnk_a_un_objeto_del_shell_en_el_menu_inicio_va_directo(politica: LaunchPolicy) -> None:
    assert not politica.assess(lnk_entry(), read_link=destino()).requires  # Explorador de archivos, Panel de control


def test_lnk_anunciado_de_windows_installer_va_directo(politica: LaunchPolicy) -> None:
    assert not politica.assess(lnk_entry(), read_link=destino(advertised=True)).requires


def _string(text: str) -> bytes:
    return struct.pack("<H", len(text)) + text.encode("utf-16-le")


def construir_lnk(path: str, arguments: str = "", relative: str = "") -> bytes:
    """Un .lnk mínimo: cabecera, LinkInfo con ruta local y cadenas en Unicode."""
    base = path.encode("cp1252") + b"\0"
    info = struct.pack("<IIIIIII", 28 + len(base) + 1, 28, 1, 28, 28, 0, 28 + len(base)) + base + b"\0"
    flags = 0x02 | 0x80 | (0x08 if relative else 0) | (0x20 if arguments else 0)
    header = struct.pack("<I", 0x4C) + shortcuts.LINK_CLSID + struct.pack("<II", flags, 0) + b"\0" * (0x4C - 28)
    strings = (_string(relative) if relative else b"") + (_string(arguments) if arguments else b"")
    return header + info + strings + struct.pack("<I", 0)


def test_parser_lee_ruta_y_argumentos_de_un_lnk() -> None:
    link = shortcuts.parse(construir_lnk("C:\\Temp\\malo.bat", arguments="/q", relative="..\\Temp\\malo.bat"))
    assert link is not None
    assert link.paths == ("C:\\Temp\\malo.bat", "..\\Temp\\malo.bat") and link.arguments == "/q"


@pytest.mark.parametrize("datos", [b"", b"x", b"no es un acceso directo" * 10, construir_lnk("C:\\a.exe")[:90]])
def test_parser_devuelve_none_con_basura(datos: bytes) -> None:
    assert shortcuts.parse(datos) is None


def test_read_lee_del_disco_y_un_fichero_que_no_existe_es_none(tmp_path: Path) -> None:
    acceso = tmp_path / "x.lnk"
    acceso.write_bytes(construir_lnk("C:\\Temp\\malo.ps1"))
    leido = shortcuts.read(str(acceso))
    assert leido is not None and leido.paths == ("C:\\Temp\\malo.ps1",)
    assert shortcuts.read(str(tmp_path / "no.lnk")) is None


def test_los_interpretes_salen_del_example_si_el_json_real_no_los_trae(tmp_path: Path) -> None:
    (tmp_path / "confirmaciones.json").write_text('{"ejecutables": {"extensiones": [".exe"]}}', encoding="utf-8")
    ejemplo = Path("configuracion/confirmaciones.example.json").read_text(encoding="utf-8")
    (tmp_path / "confirmaciones.example.json").write_text(ejemplo, encoding="utf-8")
    politica_mixta = LaunchPolicy.load(tmp_path)
    assert politica_mixta.extensions == {".exe"} and "powershell.exe" in politica_mixta.interpreters


async def test_hands_lanza_tras_el_si_y_ensena_el_destino_real_del_lnk(tmp_path: Path) -> None:
    acceso = tmp_path / "Limpiar.lnk"
    acceso.write_bytes(construir_lnk("C:\\Temp\\limpiar.bat"))
    index = AppIndex()
    index.replace([AppEntry("desktop:limpiar", "Limpiar", "desktop", "file", str(acceso))])
    lanzadas: list[AppEntry] = []
    async with Client(hands_server.build_server(index, launch=lanzadas.append)) as client:
        res = (await client.call_tool("apps.launch", {"id": "desktop:limpiar"})).structured_content
        assert res is not None and res["requiere_confirmacion"] is True and "limpiar.bat" in res["objetivo"]
        assert lanzadas == []
        await client.call_tool("apps.launch", {"id": "desktop:limpiar", "confirmado": True})
    assert [e.id for e in lanzadas] == ["desktop:limpiar"]


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
