from __future__ import annotations

import shutil
from pathlib import Path

from hands.apps import epic, registry, startapps, startmenu, steam, vdf

FIXTURES = Path(__file__).parent / "fixtures"


def test_vdf_anidado_y_escapes() -> None:
    data = vdf.parse((FIXTURES / "libraryfolders.vdf").read_text(encoding="utf-8"))
    carpetas = data["libraryfolders"]
    assert carpetas["0"]["path"] == "C:\\Program Files (x86)\\Steam"
    assert carpetas["1"]["apps"] == {"291550": "5678"}


def test_get_startapps_filtra_ruido_y_vacios() -> None:
    entradas = startapps.parse((FIXTURES / "get_startapps.json").read_text(encoding="utf-8"))
    nombres = [e.name for e in entradas]
    assert nombres == ["Spotify", "Calculadora", "Google Chrome", "Word", "Configuración", "Brawlhalla"]
    spotify = entradas[0]
    assert spotify.launch_kind == "shell"
    assert spotify.target == "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify"
    brawlhalla = entradas[-1]  # acceso .url de Steam: el AppID es una URI
    assert (brawlhalla.launch_kind, brawlhalla.target) == ("uri", "steam://rungameid/291550")


def test_get_startapps_un_solo_resultado_es_objeto() -> None:
    entradas = startapps.parse('\ufeff{"Name":"Spotify","AppID":"Spotify.App"}')
    assert [e.name for e in entradas] == ["Spotify"]


def test_get_startapps_salida_vacia() -> None:
    assert startapps.parse("  \n") == []


def test_startmenu_lee_lnk_recursivamente(tmp_path: Path) -> None:
    (tmp_path / "Programas" / "Steam").mkdir(parents=True)
    (tmp_path / "Programas" / "Discord.lnk").write_bytes(b"")
    (tmp_path / "Programas" / "Steam" / "Steam.lnk").write_bytes(b"")
    (tmp_path / "Programas" / "Steam" / "Desinstalar Steam.lnk").write_bytes(b"")
    (tmp_path / "Programas" / "leeme.txt").write_text("no es un acceso")
    entradas = startmenu.scan([tmp_path, tmp_path / "no-existe"])
    assert sorted(e.name for e in entradas) == ["Discord", "Steam"]
    assert all(e.launch_kind == "file" and e.target.endswith(".lnk") for e in entradas)


def test_app_paths_solo_exe_con_comillas_opcionales() -> None:
    entradas = registry.parse(
        [
            ("chrome.exe", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
            ("vlc.exe", r'"C:\Program Files\VideoLAN\VLC\vlc.exe"'),
            ("config.msc", r"C:\Windows\config.msc"),
        ]
    )
    assert [(e.name, e.target) for e in entradas] == [
        ("chrome", r"C:\Program Files\Google\Chrome\Application\chrome.exe"),
        ("vlc", r"C:\Program Files\VideoLAN\VLC\vlc.exe"),
    ]


def _steam_con_dos_bibliotecas(tmp_path: Path) -> Path:
    raiz, segunda = tmp_path / "Steam", tmp_path / "SteamLibrary"
    (raiz / "steamapps").mkdir(parents=True)
    (segunda / "steamapps").mkdir(parents=True)
    escapado = str(segunda).replace("\\", "\\\\")
    (raiz / "steamapps" / "libraryfolders.vdf").write_text(
        f'"libraryfolders"\n{{\n\t"0"\n\t{{\n\t\t"path"\t\t"{str(raiz).replace(chr(92), chr(92) * 2)}"\n\t}}\n'
        f'\t"1"\n\t{{\n\t\t"path"\t\t"{escapado}"\n\t}}\n}}\n',
        encoding="utf-8",
    )
    shutil.copy(FIXTURES / "appmanifest_228980.acf", raiz / "steamapps")
    shutil.copy(FIXTURES / "appmanifest_291550.acf", segunda / "steamapps")
    return raiz


def test_steam_recorre_todas_las_bibliotecas_y_descarta_redistribuibles(tmp_path: Path) -> None:
    entradas = steam.scan(_steam_con_dos_bibliotecas(tmp_path))
    assert [(e.name, e.launch_kind, e.target) for e in entradas] == [("Brawlhalla", "uri", "steam://rungameid/291550")]
    assert entradas[0].id == "steam:291550"


def test_steam_sin_libraryfolders_usa_la_raiz(tmp_path: Path) -> None:
    (tmp_path / "steamapps").mkdir()
    shutil.copy(FIXTURES / "appmanifest_291550.acf", tmp_path / "steamapps")
    assert [e.name for e in steam.scan(tmp_path)] == ["Brawlhalla"]


def test_epic_construye_uri_de_lanzamiento(tmp_path: Path) -> None:
    shutil.copy(FIXTURES / "epic_rocket.item", tmp_path / "A1B2.item")
    (tmp_path / "roto.item").write_text("{no es json")
    entradas = epic.scan(tmp_path)
    assert [e.name for e in entradas] == ["Rocket League"]
    assert entradas[0].launch_kind == "uri"
    assert entradas[0].target == (
        "com.epicgames.launcher://apps/"
        "9773aa1aa54f4f7b80e44bef04986cea%3A9773aa1aa54f4f7b80e44bef04986cea%3ASugar"
        "?action=launch&silent=true"
    )


def test_epic_directorio_inexistente(tmp_path: Path) -> None:
    assert epic.scan(tmp_path / "nada") == []


def test_ids_cortos_y_estables() -> None:
    entradas = startapps.parse((FIXTURES / "get_startapps.json").read_text(encoding="utf-8"))
    otra_vez = startapps.parse((FIXTURES / "get_startapps.json").read_text(encoding="utf-8"))
    assert [e.id for e in entradas] == [e.id for e in otra_vez]
    assert len({e.id for e in entradas}) == len(entradas)
    assert all(len(e.id) == len("startapps:") + 8 for e in entradas)
