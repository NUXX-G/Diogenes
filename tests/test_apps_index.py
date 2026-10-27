from __future__ import annotations

import logging
import sys

import pytest

from hands.apps import discovery, launcher
from hands.apps.index import AppIndex, hay_ganador_claro
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


# --- Ranking y desambiguación -------------------------------------------------------------------------------


def indice(*apps: AppEntry) -> AppIndex:
    idx = AppIndex()
    idx.replace(apps)
    return idx


def test_exacta_gana_a_prefijo_aunque_el_prefijo_tenga_interfaz() -> None:
    idx = indice(app("Spotify"), app("spotify_cli", "registry", "file", "spotify_cli.exe"))
    r = idx.search("spotify")
    assert [x.entry.name for x in r][:2] == ["Spotify", "spotify_cli"]
    assert (r[0].tier, r[1].tier) == (0, 1)
    assert hay_ganador_claro(r)


def test_prefijo_gana_a_difusa() -> None:
    idx = indice(app("Brawlhalla Launcher", "steam", "uri", "x"), app("Brawlhala Toolz"), app("Brawlhalla Wiki"))
    r = idx.search("brawlhalla")
    assert r[0].tier == 1 and all(x.tier >= r[0].tier for x in r)


def test_a_igualdad_gana_la_app_con_interfaz_sobre_app_paths() -> None:
    idx = indice(app("Foo Tool", "registry", "file", "foo.exe"), app("Foo Pro"))
    r = idx.search("foo")
    assert [x.entry.name for x in r] == ["Foo Pro", "Foo Tool"]
    assert hay_ganador_claro(r)  # la interfaz desempata: no hace falta preguntar


def test_empate_real_no_es_ganador_claro() -> None:
    idx = indice(app("Discord Canary"), app("Discord PTB"))
    r = idx.search("discord")
    assert r[0].tier == r[1].tier == 1 and r[0].con_interfaz and r[1].con_interfaz
    assert not hay_ganador_claro(r)


def test_ganador_claro_sin_resultados_o_con_uno() -> None:
    assert not hay_ganador_claro([])
    assert hay_ganador_claro(indice(app("Unica")).search("unica"))


def test_exacta_ignora_mayusculas_y_acentos() -> None:
    r = indice(app("Configuración"), app("Configuración avanzada")).search("CONFIGURACION")
    assert r[0].entry.name == "Configuración" and r[0].tier == 0
