"""Cerrar apps: cierre suave con confirmación, forzar solo después y con otra confirmación, y procesos protegidos."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp import Client

from core.agent import Agent
from core.intent import IntentGate
from core.mcp_client import McpTools
from core.safety import ConfirmationPolicy
from hands import server as hands_server
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry
from hands.system.closer import AppCloser, CloseConfig, matches
from hands.system.windows import Win
from tests.test_agent import FakeBackend, FakeLLM, decir, llamar

NOTEPAD = AppEntry(
    "startapps:bloc", "Bloc de notas", "startapps", "shell", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App"
)
CONFIG = CloseConfig(confirm_soft_close=True, wait_seconds=0.0, protected=frozenset({"explorer.exe", "ollama.exe"}))


class FakeWindows:
    """Ventanas simuladas. `ignora_cierre`: la app no cierra al pedírselo (p. ej. pregunta si guardar)."""

    def __init__(self, *windows: Win, ignora_cierre: bool = False) -> None:
        self.windows = list(windows)
        self.ignora_cierre = ignora_cierre
        self.cerradas: list[int] = []
        self.terminados: list[int] = []

    def list_windows(self) -> list[Win]:
        return list(self.windows)

    def post_close(self, hwnd: int) -> None:
        self.cerradas.append(hwnd)
        if not self.ignora_cierre:
            self.windows = [w for w in self.windows if w.hwnd != hwnd]

    def terminate(self, pid: int) -> bool:
        self.terminados.append(pid)
        self.windows = [w for w in self.windows if w.pid != pid]
        return True


def bloc(hwnd: int = 100, pid: int = 4000) -> Win:
    return Win(hwnd, pid, "Sin título - Bloc de notas", "C:\\Windows\\System32\\notepad.exe")


def closer(backend: FakeWindows, config: CloseConfig = CONFIG, pids: set[int] | None = None) -> AppCloser:
    return AppCloser(backend, config, protected_pids=lambda: pids or set(), sleep=lambda _s: None)


# --- cierre suave ----------------------------------------------------------------------------------------------


def test_sin_confirmar_no_toca_nada_y_pide_confirmacion_con_lo_que_se_cerraria() -> None:
    windows = FakeWindows(bloc())
    res = closer(windows).close(NOTEPAD, confirmed=False)
    assert res["requiere_confirmacion"] is True
    assert "notepad.exe" in res["objetivo"] and "Bloc de notas" in res["objetivo"]
    assert windows.cerradas == [] and windows.terminados == []


def test_confirmado_pide_cerrar_con_wm_close_y_no_termina_el_proceso() -> None:
    windows = FakeWindows(bloc())
    res = closer(windows).close(NOTEPAD, confirmed=True)
    assert res == {"ok": True, "cerrada": True, "mensaje": "He cerrado Bloc de notas."}
    assert windows.cerradas == [100] and windows.terminados == []


def test_si_la_app_pregunta_por_guardar_sigue_abierta_y_se_dice() -> None:
    windows = FakeWindows(bloc(), ignora_cierre=True)
    res = closer(windows).close(NOTEPAD, confirmed=True)
    assert res["ok"] is True and res["sigue_abierta"] is True and res["cerrada"] is False
    assert "Forzar" in res["mensaje"]
    assert windows.terminados == []


def test_cierra_todas_las_ventanas_de_la_app() -> None:
    windows = FakeWindows(bloc(100), bloc(101))
    closer(windows).close(NOTEPAD, confirmed=True)
    assert sorted(windows.cerradas) == [100, 101]


def test_sin_confirmacion_configurada_cierra_directamente() -> None:
    sin = CloseConfig(confirm_soft_close=False, wait_seconds=0.0, protected=frozenset())
    windows = FakeWindows(bloc())
    assert closer(windows, sin).close(NOTEPAD, confirmed=False)["cerrada"] is True


def test_nombrada_por_el_usuario_y_una_sola_app_abierta_cierra_directo() -> None:
    windows = FakeWindows(bloc())
    assert closer(windows).close(NOTEPAD, confirmed=False, named=True)["cerrada"] is True
    assert windows.cerradas == [100]


def test_dos_ventanas_de_la_misma_app_no_son_ambiguas() -> None:
    windows = FakeWindows(bloc(100), bloc(101))
    assert closer(windows).close(NOTEPAD, confirmed=False, named=True)["cerrada"] is True
    assert sorted(windows.cerradas) == [100, 101]


def test_varias_apps_que_coinciden_piden_confirmacion_aunque_la_haya_nombrado() -> None:
    otra = Win(200, 5000, "Bloc de notas - Word", "C:\\Office\\winword.exe")
    windows = FakeWindows(bloc(), otra)
    res = closer(windows).close(NOTEPAD, confirmed=False, named=True)
    assert res["requiere_confirmacion"] is True and "varias" in res["motivo"]
    assert "notepad.exe" in res["objetivo"] and "winword.exe" in res["objetivo"]
    assert windows.cerradas == []


def test_app_de_app_paths_pide_confirmacion_aunque_la_haya_nombrado() -> None:
    entry = AppEntry("registry:notepad.exe", "notepad", "registry", "file", "C:\\Windows\\notepad.exe")
    windows = FakeWindows(bloc())
    res = closer(windows).close(entry, confirmed=False, named=True)
    assert res["requiere_confirmacion"] is True and "App Paths" in res["motivo"]
    assert windows.cerradas == []


def test_app_que_no_esta_abierta() -> None:
    res = closer(FakeWindows(Win(1, 1, "Otra cosa", "C:\\x\\otra.exe"))).close(NOTEPAD, confirmed=True)
    assert res["ok"] is False and "no está abierta" in res["mensaje"]


# --- forzar ----------------------------------------------------------------------------------------------------


def test_forzar_sin_cierre_suave_previo_lo_intenta_antes_y_solo_fuerza_si_sigue_abierta() -> None:
    """El usuario ya confirmó forzar: primero se pide cerrar como con la X (deja guardar) y solo si sigue, se fuerza."""
    windows = FakeWindows(bloc(), ignora_cierre=True)
    res = closer(windows).force_close(NOTEPAD)
    assert windows.cerradas == [100] and windows.terminados == [4000]
    assert res["ok"] is True and res["forzada"] is True


def test_forzar_algo_que_se_cierra_con_la_x_no_lo_mata() -> None:
    windows = FakeWindows(bloc())
    res = closer(windows).force_close(NOTEPAD)
    assert res["cerrada"] is True and "forzada" not in res and windows.terminados == []


def test_se_fuerza_despues_de_intentar_el_cierre_suave() -> None:
    windows = FakeWindows(bloc(), ignora_cierre=True)
    c = closer(windows)
    c.close(NOTEPAD, confirmed=True)
    res = c.force_close(NOTEPAD)
    assert res["ok"] is True and res["forzada"] is True
    assert windows.terminados == [4000]


def test_forzar_gasta_el_permiso_del_cierre_suave() -> None:
    windows = FakeWindows(bloc(), ignora_cierre=True)
    c = closer(windows)
    c.close(NOTEPAD, confirmed=True)
    c.force_close(NOTEPAD)
    windows.windows = [bloc()]  # la app vuelve a abrirse: antes de forzar de nuevo, otra vez el cierre suave
    windows.cerradas.clear()
    c.force_close(NOTEPAD)
    assert windows.cerradas == [100]


# --- protegidos ------------------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "exe", ["C:\\Windows\\explorer.exe", "C:\\Users\\u\\AppData\\Local\\Programs\\Ollama\\ollama.exe"]
)
def test_los_procesos_protegidos_de_la_config_nunca_se_cierran(exe: str) -> None:
    entry = AppEntry("startapps:x", "Explorador de archivos", "startapps", "shell", "x")
    windows = FakeWindows(Win(1, 99, "Explorador de archivos", exe))
    c = closer(windows)
    for res in (c.close(entry, confirmed=True), c.force_close(entry)):
        assert res["ok"] is False and res["parar"] is True
    assert windows.cerradas == [] and windows.terminados == []


def test_el_proceso_de_hands_y_su_padre_estan_protegidos() -> None:
    entry = AppEntry("startapps:py", "Mi herramienta", "startapps", "shell", "x")
    for pid in (os.getpid(), os.getppid()):
        windows = FakeWindows(Win(1, pid, "Mi herramienta", "C:\\raro\\mi_herramienta.exe"))
        assert closer(windows).close(entry, confirmed=True)["parar"] is True
        assert windows.cerradas == []


def test_el_navegador_de_diogenes_esta_protegido_mientras_este_abierto() -> None:
    entry = AppEntry("startapps:chrome", "Google Chrome", "startapps", "shell", "chrome")
    chrome = Win(7, 5555, "Mochis - YouTube - Google Chrome", "C:\\Program Files\\Google\\Chrome\\chrome.exe")
    windows = FakeWindows(chrome)
    assert closer(windows, pids={5555}).close(entry, confirmed=True)["parar"] is True
    assert windows.cerradas == []
    assert closer(windows, pids=set()).close(entry, confirmed=True)["cerrada"] is True  # Chrome del usuario: sí


def test_si_alguna_ventana_es_protegida_no_se_cierra_ninguna() -> None:
    entry = AppEntry("startapps:x", "Proyecto", "startapps", "shell", "x")
    windows = FakeWindows(
        Win(1, 10, "Proyecto - bloc", "C:\\a\\bloc.exe"), Win(2, 11, "Proyecto - explorer", "C:\\Windows\\explorer.exe")
    )
    assert closer(windows).close(entry, confirmed=True)["ok"] is False
    assert windows.cerradas == []


# --- emparejar ventanas ---------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("nombre", "titulo", "exe", "esperado"),
    [
        ("Bloc de notas", "Sin título - Bloc de notas", "notepad.exe", True),
        ("Spotify", "Daft Punk - Get Lucky", "Spotify.exe", True),
        ("Notepad", "algo", "notepad.exe", True),
        ("Calculadora", "Sin título - Bloc de notas", "notepad.exe", False),
        ("Edge", "Inicio", "msedge.exe", False),  # nombre corto: solo cuenta si el ejecutable coincide
    ],
)
def test_emparejar_ventana_con_app(nombre: str, titulo: str, exe: str, esperado: bool) -> None:
    entry = AppEntry("startapps:x", nombre, "startapps", "shell", "x")
    assert matches(entry, Win(1, 1, titulo, f"C:\\x\\{exe}")) is esperado


def test_config_real_trae_la_seccion_cierre_con_los_procesos_vitales() -> None:
    config = CloseConfig.load(Path("configuracion"))
    assert {"explorer.exe", "dwm.exe", "python.exe", "ollama.exe"} <= config.protected
    assert config.confirm_soft_close is True


# --- de extremo a extremo: agente + hands en proceso ---------------------------------------------------------


async def pasos(windows: FakeWindows, llm: FakeLLM, texto: str, respuestas: list[bool]) -> tuple[Any, list[str]]:
    index = AppIndex()
    index.replace([NOTEPAD])
    preguntas: list[str] = []

    async def confirm(tool: str, _args: dict[str, Any]) -> bool:
        preguntas.append(tool)
        return respuestas.pop(0)

    server = hands_server.build_server(index, closer=closer(windows))
    async with Client(server) as client:
        agent = Agent(
            llm,
            McpTools(client),
            policy=ConfirmationPolicy(["apps.force_close"]),
            intent=IntentGate.load(),
            confirm=confirm,
        )
        return await agent.run(texto), preguntas


async def test_agente_cierra_sin_preguntar_lo_que_el_usuario_nombro() -> None:
    windows = FakeWindows(bloc())
    llm = FakeLLM(
        llamar("apps_search", nombre="bloc de notas"),
        llamar("apps_close", id="startapps:bloc"),
        decir("Cerrado."),
    )
    resultado, preguntas = await pasos(windows, llm, "cierra el bloc de notas", [])
    assert preguntas == []
    assert json.loads(resultado.steps[-1].result)["cerrada"] is True
    assert windows.cerradas == [100]


async def test_agente_si_el_modelo_propone_cerrar_pregunta_una_vez_y_el_no_se_respeta() -> None:
    windows = FakeWindows(bloc())
    llm = FakeLLM(
        llamar("apps_search", nombre="bloc de notas"),
        llamar("apps_close", id="startapps:bloc"),
        decir("Vale, no lo cierro."),
    )
    resultado, preguntas = await pasos(windows, llm, "búscame el bloc de notas", [False])
    assert preguntas == ["apps.close"]
    assert resultado.steps[-1].result.startswith("DENEGADO")
    assert windows.cerradas == []


async def test_agente_si_el_modelo_propone_cerrar_y_el_usuario_acepta_basta_un_si() -> None:
    windows = FakeWindows(bloc())
    llm = FakeLLM(
        llamar("apps_search", nombre="bloc de notas"),
        llamar("apps_close", id="startapps:bloc"),
        decir("Cerrado."),
    )
    _, preguntas = await pasos(windows, llm, "búscame el bloc de notas", [True])
    assert preguntas == ["apps.close"] and windows.cerradas == [100]


async def test_agente_forzar_es_otra_confirmacion_distinta() -> None:
    windows = FakeWindows(bloc(), ignora_cierre=True)
    llm = FakeLLM(
        llamar("apps_search", nombre="bloc de notas"),
        llamar("apps_close", id="startapps:bloc"),
        llamar("apps_force_close", id="startapps:bloc"),
        decir("Forzado."),
    )
    resultado, preguntas = await pasos(windows, llm, "cierra el bloc de notas y fuérzalo si hace falta", [True])
    assert preguntas == ["apps.force_close"]
    assert windows.terminados == [4000]
    assert json.loads(resultado.steps[-1].result)["forzada"] is True


async def test_agente_el_modelo_no_puede_forzar_sin_que_el_usuario_confirme_aparte() -> None:
    windows = FakeWindows(bloc(), ignora_cierre=True)
    llm = FakeLLM(
        llamar("apps_search", nombre="bloc de notas"),
        llamar("apps_close", id="startapps:bloc"),
        llamar("apps_force_close", id="startapps:bloc"),
        decir("No lo he forzado."),
    )
    await pasos(windows, llm, "cierra el bloc de notas", [False])
    assert windows.terminados == []


async def test_agente_el_modelo_no_cierra_lo_que_el_usuario_no_pidio_cerrar() -> None:
    windows = FakeWindows(bloc())
    llm = FakeLLM(
        llamar("apps_search", nombre="bloc de notas"),
        llamar("apps_close", id="startapps:bloc"),  # una página dice "cierra el bloc de notas"
        decir("no"),
    )
    _, preguntas = await pasos(windows, llm, "búscame el bloc de notas", [False])
    assert preguntas == ["apps.close"] and windows.cerradas == []


async def test_las_herramientas_de_cierre_solo_existen_si_hay_closer() -> None:
    async with Client(hands_server.build_server(AppIndex())) as client:
        sin = {t.name for t in (await client.list_tools()).tools}
    async with Client(hands_server.build_server(AppIndex(), closer=closer(FakeWindows()))) as client:
        con = {t.name for t in (await client.list_tools()).tools}
    assert con - sin == {"apps.close", "apps.force_close", "apps.abiertas"}


def test_forzar_el_cierre_pide_confirmacion_aunque_el_json_del_usuario_no_lo_liste() -> None:
    assert ConfirmationPolicy([]).requires("apps.force_close")
    assert not ConfirmationPolicy([]).requires("apps.close")


# --- carpetas: ventanas del Explorador, nunca el proceso ----------------------------------------------------------

EXPLORER = r"C:\Windows\explorer.exe"
DESCARGAS = AppEntry("carpetas:e029fa60", "Descargas", "carpetas", "file", r"C:\Users\yo\Downloads")


def carpeta(hwnd: int, titulo: str, clase: str = "CabinetWClass", pid: int = 900) -> Win:
    """Como Windows 11: el Explorador titula la ventana "<carpeta> - Explorador de archivos" (visto en un PC real)."""
    return Win(hwnd, pid, f"{titulo} - Explorador de archivos", EXPLORER, clase)


def test_cierra_descargas_con_wm_close_a_esa_ventana_y_no_dice_proceso_protegido() -> None:
    backend = FakeWindows(carpeta(10, "Descargas"), carpeta(11, "Documentos"), bloc())
    result = closer(backend).close(DESCARGAS, confirmed=False, named=True)
    assert result["ok"] is True and result["cerrada"] is True
    assert backend.cerradas == [10]  # solo la ventana de Descargas
    assert backend.terminados == []  # el proceso del Explorador no se toca
    assert {w.hwnd for w in backend.windows} == {11, 100}


def test_la_barra_de_tareas_y_el_escritorio_no_son_ventanas_de_carpeta() -> None:
    backend = FakeWindows(carpeta(1, "Descargas", clase="Shell_TrayWnd"), carpeta(2, "Descargas", clase="Progman"))
    result = closer(backend).close(DESCARGAS, confirmed=False, named=True)
    assert result["ok"] is False and "no está abierta" in result["mensaje"]
    assert backend.cerradas == []


def test_el_nombre_ingles_de_una_carpeta_encuentra_la_ventana_en_espanol() -> None:
    downloads = AppEntry("carpetas:aaaa", "Downloads", "carpetas", "file", r"C:\Users\yo\Downloads")
    backend = FakeWindows(carpeta(10, "Descargas"))
    assert closer(backend).close(downloads, confirmed=False, named=True)["cerrada"] is True


def test_una_carpeta_propuesta_por_el_modelo_pide_confirmacion_como_cualquier_app() -> None:
    backend = FakeWindows(carpeta(10, "Descargas"))
    result = closer(backend).close(DESCARGAS, confirmed=False, named=False)
    assert result["requiere_confirmacion"] is True and backend.cerradas == []


def test_forzar_el_cierre_de_una_carpeta_se_niega_y_no_termina_el_explorador() -> None:
    backend = FakeWindows(carpeta(10, "Descargas", pid=900))
    closer(backend).close(DESCARGAS, confirmed=True)
    result = closer(backend).force_close(DESCARGAS)
    assert result["ok"] is False and "Explorador" in result["mensaje"]
    assert backend.terminados == []


def test_una_carpeta_del_escritorio_detectada_por_ser_directorio_tambien_cierra_solo_la_ventana() -> None:
    mia = AppEntry("desktop:bbbb", "Carpeta Diogenes", "desktop", "file", r"C:\Users\yo\Desktop\Carpeta Diogenes")
    backend = FakeWindows(carpeta(20, "Carpeta Diogenes"))
    result = AppCloser(backend, CONFIG, sleep=lambda _s: None, is_dir=lambda _p: True).close(mia, False, named=True)
    assert result["cerrada"] is True and backend.cerradas == [20]


def test_el_proceso_del_explorador_sigue_protegido_para_otras_apps() -> None:
    """Una app que no es carpeta y casa con una ventana del Explorador sigue sin poder cerrarse."""
    otra = AppEntry("startapps:zzz", "Descargas Rapidas", "startapps", "shell", "x!y")
    backend = FakeWindows(Win(5, 900, "Descargas Rapidas", EXPLORER))
    assert closer(backend).close(otra, confirmed=True)["parar"] is True


def test_tambien_vale_el_titulo_sin_el_sufijo_del_explorador() -> None:
    backend = FakeWindows(Win(10, 900, "Descargas", EXPLORER, "CabinetWClass"))
    assert closer(backend).close(DESCARGAS, confirmed=False, named=True)["cerrada"] is True


def test_una_carpeta_con_guion_en_el_nombre_se_reconoce() -> None:
    mia = AppEntry("desktop:cccc", "Mis - Cosas", "desktop", "file", r"C:\Users\yo\Desktop\Mis - Cosas")
    backend = FakeWindows(carpeta(30, "Mis - Cosas"))
    result = AppCloser(backend, CONFIG, sleep=lambda _s: None, is_dir=lambda _p: True).close(mia, False, named=True)
    assert result["cerrada"] is True


# --- ids inventados y cierre sin abrir nada ---------------------------------------------------------------------


@pytest.fixture
def servidor_con_bloc() -> tuple[Any, FakeWindows]:
    index = AppIndex()
    index.replace(
        [
            NOTEPAD,
            AppEntry("startapps:paint", "Paint", "startapps", "shell", "Microsoft.Paint_8wekyb3d8bbwe!App"),
            AppEntry("startapps:calc", "Calculadora", "startapps", "shell", "Microsoft.WindowsCalculator!App"),
        ]
    )
    windows = FakeWindows(bloc())
    return hands_server.build_server(index, closer=closer(windows), launch=lambda _e: None), windows


async def test_un_id_inventado_se_resuelve_por_nombre_entre_las_apps_abiertas(servidor_con_bloc: Any) -> None:
    server, windows = servidor_con_bloc
    async with Client(server) as client:
        res = json.loads(
            (await client.call_tool("apps.close", {"id": "bloc de notas", "nombrado": True})).content[0].text
        )
    assert res["cerrada"] is True and windows.cerradas == [100]


async def test_cerrar_una_app_que_no_esta_abierta_lo_dice_y_no_propone_abrirla(servidor_con_bloc: Any) -> None:
    server, windows = servidor_con_bloc
    async with Client(server) as client:
        res = json.loads(
            (await client.call_tool("apps.close", {"id": "calculadora", "nombrado": True})).content[0].text
        )
    assert res["ok"] is False and "no está abierta" in res["mensaje"] and "no la abro" in res["mensaje"]
    assert windows.cerradas == []


async def test_un_id_con_forma_de_id_pero_inexistente_tampoco_cierra_nada(servidor_con_bloc: Any) -> None:
    server, windows = servidor_con_bloc
    async with Client(server) as client:
        res = json.loads((await client.call_tool("apps.close", {"id": "startapps:inventado"})).content[0].text)
    assert res["ok"] is False and windows.cerradas == []


async def test_varias_abiertas_que_encajan_devuelve_la_lista_de_candidatas() -> None:
    index = AppIndex()
    index.replace(
        [
            AppEntry("startapps:a", "Bloc de notas", "startapps", "shell", "A!App"),
            AppEntry("startapps:b", "Bloc de dibujo", "startapps", "shell", "B!App"),
        ]
    )
    windows = FakeWindows(
        Win(1, 10, "Sin título - Bloc de notas", r"C:\x\a.exe"), Win(2, 11, "Lienzo - Bloc de dibujo", r"C:\x\b.exe")
    )
    server = hands_server.build_server(index, closer=closer(windows), launch=lambda _e: None)
    async with Client(server) as client:
        res = json.loads((await client.call_tool("apps.close", {"id": "bloc"})).content[0].text)
    assert res["ganador_claro"] is False and {o["id"] for o in res["opciones"]} == {"startapps:a", "startapps:b"}
    assert windows.cerradas == []


async def test_force_close_tambien_resuelve_por_nombre(servidor_con_bloc: Any) -> None:
    server, windows = servidor_con_bloc
    async with Client(server) as client:
        res = json.loads((await client.call_tool("apps.force_close", {"id": "calculadora"})).content[0].text)
    assert res["ok"] is False and "no está abierta" in res["mensaje"] and windows.terminados == []


# --- qué está abierto y cierres con un id que no es el pedido -------------------------------------------------------


async def test_apps_abiertas_dice_si_algo_esta_abierto_y_lista_las_ventanas(servidor_con_bloc: Any) -> None:
    server, _ = servidor_con_bloc
    async with Client(server) as client:
        bloc = json.loads((await client.call_tool("apps.abiertas", {"nombre": "bloc de notas"})).content[0].text)
        paint = json.loads((await client.call_tool("apps.abiertas", {"nombre": "paint"})).content[0].text)
        todas = json.loads((await client.call_tool("apps.abiertas", {})).content[0].text)
    assert bloc["abierta"] is True and paint["abierta"] is False
    assert todas["abiertas"] == [{"titulo": "Sin título - Bloc de notas", "proceso": "notepad.exe"}]


def test_preguntar_si_esta_abierto_no_es_pedir_abrir() -> None:
    gate = IntentGate.load()
    gate.begin_turn("busca si hay un bloc de notas abierto")
    assert gate.asking_state() and gate.check("apps.launch", {"id": "bloc de notas"}).blocked
    assert gate.state_target("busca si hay un bloc de notas abierto") == "bloc de notas"
    gate.begin_turn("abre el bloc de notas si no está abierto")
    assert not gate.asking_state() and gate.check("apps.open", {"nombre": "bloc de notas"}).allowed


async def test_la_pregunta_de_estado_contestada_sin_mirar_fuerza_apps_abiertas() -> None:
    backend = FakeBackend(
        {"apps.abiertas": json.dumps({"ok": True, "abierta": True, "mensaje": "Sí: Bloc de notas está abierta."})},
        names=("apps.search", "apps.launch", "apps.abiertas"),
    )
    llm = FakeLLM(decir("No lo sé."))
    resultado = await Agent(llm, backend, policy=ConfirmationPolicy(["apps.force_close"])).run(
        "¿está abierto el bloc de notas?"
    )
    assert backend.calls == [("apps.abiertas", {"nombre": "bloc de notas"})]
    assert resultado.answer == "Sí: Bloc de notas está abierta."


async def test_cerrar_con_un_id_de_otra_app_del_historial_usa_el_nombre_del_mensaje(servidor_con_bloc: Any) -> None:
    """Caso real: «cierra a la fuerza el bloc de notas» llegó con el id de Registry Editor y contestó con otro nombre."""
    server, windows = servidor_con_bloc
    gate = IntentGate.load()
    gate.observe(
        "apps.search", {}, json.dumps({"ok": True, "resultados": [{"id": "startapps:paint", "nombre": "Paint"}]})
    )
    llm = FakeLLM(llamar("apps_close", id="startapps:paint"))
    async with Client(server) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy(["apps.force_close"]), intent=gate)
        resultado = await agent.run("cierra el bloc de notas")
    assert resultado.steps[0].arguments == {"nombre": "bloc de notas"}
    assert resultado.answer == "He cerrado Bloc de notas." and windows.windows == []


async def test_si_el_modelo_mira_si_esta_abierta_en_vez_de_cerrar_el_turno_sigue(servidor_con_bloc: Any) -> None:
    """Caso real (banco, 2026-11-12): «cierra la calculadora» → apps_abiertas → «Sí: está abierta», sin cerrarla."""
    server, windows = servidor_con_bloc
    llm = FakeLLM(llamar("apps_abiertas", nombre="bloc de notas"), llamar("apps_close", id="bloc de notas"))
    async with Client(server) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy(["apps.force_close"]))
        resultado = await agent.run("cierra el bloc de notas")
    assert [s.tool for s in resultado.steps] == ["apps.abiertas", "apps.close"]
    assert resultado.answer == "He cerrado Bloc de notas." and windows.windows == []


# --- ronda 3: cierres por nombre, ⚠ con la app real, contradicciones y coincidencias falsas ----------------------

EXPLORADOR = AppEntry(
    "startapps:explorer", "Explorador de archivos", "startapps", "shell", "Microsoft.Windows.Explorer"
)


def servidor_bloc_y_explorador() -> tuple[Any, FakeWindows]:
    index = AppIndex()
    index.replace([NOTEPAD, EXPLORADOR, AppEntry("startapps:paint", "Paint", "startapps", "shell", "Paint!App")])
    windows = FakeWindows(bloc(), Win(300, 900, "Descargas - Explorador de archivos", r"C:\Windows\explorer.exe"))
    return hands_server.build_server(index, closer=closer(windows), launch=lambda _e: None), windows


async def test_apps_abiertas_no_confunde_el_bloc_con_el_explorador_y_lo_dice_bien() -> None:
    server, _ = servidor_bloc_y_explorador()
    async with Client(server) as client:
        res = json.loads((await client.call_tool("apps.abiertas", {"nombre": "bloc de notas"})).content[0].text)
    assert res["apps"] == [{"nombre": "Bloc de notas", "ventanas": ["Sin título"]}]
    assert res["mensaje"] == "Sí, está abierto: Bloc de notas (1 ventana: Sin título)."


class Pregunta:
    def __init__(self) -> None:
        self.vistas: list[tuple[str, dict[str, Any]]] = []

    async def __call__(self, tool: str, args: dict[str, Any]) -> bool:
        self.vistas.append((tool, args))
        return True


async def test_forzar_con_un_id_inventado_cierra_la_app_que_se_pidio_y_la_aviso_dice_su_nombre() -> None:
    """Caso real: apps.abiertas encontró el Bloc de notas, el modelo forzó id='app_bloc_de_nota' y dijo «no está
    abierto»; la ⚠ enseñaba el id inventado."""
    server, windows = servidor_bloc_y_explorador()
    preguntas = Pregunta()
    llm = FakeLLM(llamar("apps_abiertas", nombre="Bloc de notas"), llamar("apps_force_close", id="app_bloc_de_nota"))
    async with Client(server) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy(["apps.force_close"]), confirm=preguntas)
        resultado = await agent.run("cierra a la fuerza el bloc de notas")
    assert preguntas.vistas == [("apps.force_close", {"app": "Bloc de notas", "ventanas": "Sin título"})]
    assert resultado.answer.startswith("He cerrado Bloc de notas")
    assert [w.title for w in windows.windows] == ["Descargas - Explorador de archivos"]  # el Explorador, intacto


async def test_cierralo_con_un_id_inventado_cierra_la_app_de_la_que_se_hablaba() -> None:
    server, windows = servidor_bloc_y_explorador()
    llm = FakeLLM(
        llamar("apps_abiertas", nombre="bloc de notas"),
        llamar("apps_close", id="app_bloc"),
    )
    async with Client(server) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy(["apps.force_close"]))
        await agent.run("busca si hay un bloc de notas abierto")
        resultado = await agent.run("ciérralo")
    assert resultado.steps[0].arguments["nombre"] == "Bloc de notas"
    assert resultado.answer == "He cerrado Bloc de notas." and len(windows.windows) == 1


async def test_forzar_algo_que_no_esta_abierto_no_pregunta() -> None:
    server, _ = servidor_bloc_y_explorador()
    preguntas = Pregunta()
    llm = FakeLLM(llamar("apps_force_close", nombre="paint"), decir("Paint no está abierto."))
    async with Client(server) as client:
        agent = Agent(llm, McpTools(client), policy=ConfirmationPolicy(["apps.force_close"]), confirm=preguntas)
        await agent.run("cierra a la fuerza paint")
    assert preguntas.vistas == []


async def test_no_puede_decir_que_no_esta_abierto_si_apps_abiertas_lo_encontro() -> None:
    abierta = json.dumps(
        {
            "ok": True,
            "abierta": True,
            "apps": [{"nombre": "Bloc de notas", "ventanas": ["Sin título"]}],
            "mensaje": "Sí",
        }
    )
    fallo = json.dumps({"ok": False, "mensaje": "«x» no está abierta"})
    backend = FakeBackend({"apps.abiertas": abierta, "apps.close": fallo}, names=("apps.abiertas", "apps.close"))
    llm = FakeLLM(
        llamar("apps_abiertas", nombre="bloc de notas"), llamar("apps_close", nombre="x"), decir("No está abierto.")
    )
    resultado = await Agent(llm, backend, policy=ConfirmationPolicy([])).run("cierra el bloc de notas")
    assert resultado.answer == "Bloc de notas sí está abierto (lo he comprobado), pero no he conseguido cerrarlo."


def test_la_pregunta_de_confirmacion_dice_la_app_y_sus_ventanas_nunca_el_id() -> None:
    from core.session import confirmation_text

    texto = confirmation_text("apps.force_close", {"app": "Bloc de notas", "ventanas": "Sin título"})
    assert texto == "¿Confirmas forzar el cierre: Bloc de notas — ventanas: Sin título?"
    assert "startapps" not in confirmation_text("apps.close", {"id": "startapps:bloc", "nombre": "Bloc de notas"})


async def test_el_modelo_no_ve_el_id_de_los_cierres(servidor_con_bloc: Any) -> None:
    server, _ = servidor_con_bloc
    llm = FakeLLM(decir("vale"))
    async with Client(server) as client:
        await Agent(llm, McpTools(client), policy=ConfirmationPolicy([])).run("hola")
    cierres = {t.name: t.parameters["properties"] for t in llm.tools_seen[0] if t.name.startswith("apps_")}
    assert "id" not in cierres["apps_close"] and "nombre" in cierres["apps_force_close"]
