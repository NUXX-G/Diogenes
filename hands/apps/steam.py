"""Juegos de Steam: `libraryfolders.vdf` + `appmanifest_*.acf`, se lanzan con `steam://rungameid/<id>`."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from hands.apps import vdf
from hands.apps.models import AppEntry

SOURCE = "steam"
# Componentes que Steam instala como "apps" pero que no son nada que se abra.
_NO_JUEGOS = ("Steamworks Common Redistributables", "Steam Linux Runtime", "Proton")


def library_paths(steam_root: Path) -> list[Path]:
    paths = [steam_root]
    file = steam_root / "steamapps" / "libraryfolders.vdf"
    if file.is_file():
        folders = vdf.parse(file.read_text(encoding="utf-8", errors="replace")).get("libraryfolders", {})
        for entry in folders.values():
            if isinstance(entry, dict) and entry.get("path"):
                path = Path(entry["path"])
                if path not in paths:
                    paths.append(path)
    return paths


def scan(steam_root: Path) -> list[AppEntry]:
    entries: list[AppEntry] = []
    seen: set[str] = set()
    for library in library_paths(steam_root):
        for acf in sorted((library / "steamapps").glob("appmanifest_*.acf")):
            state = vdf.parse(acf.read_text(encoding="utf-8", errors="replace")).get("AppState", {})
            app_id, name = str(state.get("appid", "")).strip(), str(state.get("name", "")).strip()
            if not app_id or not name or app_id in seen or name.startswith(_NO_JUEGOS):
                continue
            seen.add(app_id)
            entries.append(AppEntry(f"{SOURCE}:{app_id}", name, SOURCE, "uri", f"steam://rungameid/{app_id}"))
    return entries


def find_root() -> Path | None:
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam") as key:
            path = Path(str(winreg.QueryValueEx(key, "SteamPath")[0]))
            if path.is_dir():
                return path
    except OSError:
        pass
    fallback = Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "Steam"
    return fallback if fallback.is_dir() else None
