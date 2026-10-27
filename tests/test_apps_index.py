from __future__ import annotations

import logging
import sys

import pytest

from hands.apps import discovery, launcher
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry, normalize


def app(name: str, source: str = "startapps", kind: str = "shell", target: str = "") -> AppEntry:
    return AppEntry(f"{source}:{name}", name, source, kind, target or name)  # type: ignore[arg-type]


@pytest.fixture
def index() -> AppIndex:
    idx = AppIndex()
    idx.replace(
        [
            app("Spotify"),
            app("Calculadora"),
            app("Google Chrome"),
            app("Brawlhalla", "steam", "uri", "steam://rungameid/291550"),
            app("Rocket League", "epic", "uri", "com.epicgames.launcher://apps/x"),
            app("chrome", "registry", "file", r"C:\chrome.exe"),
        ]
    )
    return idx


def test_normalize_quita_acentos_y_signos() -> None:
    assert normalize("  Configuración — Windows! ") == "configuracion windows"


def test_busqueda_exacta_y_tolerante_a_erratas(index: AppIndex) -> None:
    assert index.search("spotify")[0].entry.name == "Spotify"
    assert index.search("spotfy")[0].entry.name == "Spotify"
    assert index.search("brawlhala")[0].entry.id == "steam:Brawlhalla"


def test_busqueda_ignora_acentos(index: AppIndex) -> None:
    assert index.search("CALCULADÓRA")[0].entry.name == "Calculadora"


def test_sin_coincidencias_devuelve_vacio(index: AppIndex) -> None:
    idx = AppIndex()
    idx.replace([app("py", "registry", "file", "py.exe")])
    assert idx.search("spotify") == []  # un nombre corto no cuenta como acierto
    assert index.search("zzzzqqq") == []
    assert index.search("   ") == []


def test_limite_de_resultados(index: AppIndex) -> None:
    assert len(index.search("e", limit=2, score_minimo=0)) <= 2


def test_misma_app_en_dos_fuentes_gana_la_de_mayor_prioridad() -> None:
    idx = AppIndex()
    idx.replace([app("Chrome", "registry", "file", r"C:\chrome.exe"), app("Chrome", "startapps", "shell", "Chrome")])
    assert len(idx) == 1
    assert idx.search("chrome")[0].entry.source == "startapps"


def test_get_solo_devuelve_lo_indexado(index: AppIndex) -> None:
    assert index.get("steam:Brawlhalla") is not None
    assert index.get("cmd.exe") is None


def test_replace_sustituye_el_contenido(index: AppIndex) -> None:
    index.replace([app("Discord")])
    assert index.search("spotify") == []
    assert index.search("discord")[0].entry.name == "Discord"


def test_plan_de_lanzamiento_por_tipo() -> None:
    shell = launcher.plan(app("Spotify", target="SpotifyAB.SpotifyMusic!Spotify"))
    assert shell == launcher.Plan("run", ("explorer.exe", "shell:AppsFolder\\SpotifyAB.SpotifyMusic!Spotify"))
    uri = launcher.plan(app("Brawlhalla", "steam", "uri", "steam://rungameid/291550"))
    assert uri == launcher.Plan("open", ("steam://rungameid/291550",))


@pytest.mark.skipif(sys.platform == "win32", reason="en Windows sí lanza")
def test_lanzar_fuera_de_windows_falla_claro() -> None:
    with pytest.raises(RuntimeError, match="Windows"):
        launcher.launch(app("Spotify"))


def test_discover_une_fuentes_y_una_caida_no_tumba_a_las_demas(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    monkeypatch.setattr(discovery, "_es_windows", lambda: True)
    monkeypatch.setattr(discovery, "_start_menu_roots", list)
    monkeypatch.setattr(discovery.registry, "read_app_paths", lambda: [("a.exe", r"C:\a.exe")])
    monkeypatch.setattr(discovery.steam, "find_root", lambda: None)
    monkeypatch.setattr(discovery.epic, "manifests_dir", lambda: discovery.Path("no-existe"))

    def powershell(_command: str) -> str:
        raise OSError("PowerShell no disponible")

    with caplog.at_level(logging.WARNING):
        entradas = discovery.discover(powershell)
    assert [e.source for e in entradas] == ["registry"]
    assert "startapps" in caplog.text


def test_discover_fuera_de_windows_no_hace_nada(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(discovery, "_es_windows", lambda: False)
    assert discovery.discover(lambda _c: pytest.fail("no debe llamar a PowerShell")) == []
