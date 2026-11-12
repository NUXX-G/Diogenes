"""Carpetas conocidas de Windows (Descargas, Documentos, Imágenes, Música, Vídeos, Escritorio) y la Papelera.

Cada carpeta entra en el índice con su nombre en español y en inglés, para que "abre Descargas" y "open Downloads"
encuentren lo mismo. Las rutas reales (con OneDrive, otro disco, etc.) se leen de `User Shell Folders` del registro.
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping
from pathlib import Path

from hands.apps.models import AppEntry, normalize, short_id

SOURCE = "carpetas"
USER_SHELL_FOLDERS_KEY = r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders"
RECYCLE_BIN_TARGET = "shell:RecycleBinFolder"

# clave → nombres con los que se pide (español primero, luego inglés)
ALIASES: dict[str, tuple[str, ...]] = {
    "downloads": ("Descargas", "Downloads"),
    "documents": ("Documentos", "Mis documentos", "Documents"),
    "pictures": ("Imágenes", "Fotos", "Pictures"),
    "music": ("Música", "Music"),
    "videos": ("Vídeos", "Videos"),
    "desktop": ("Escritorio", "Desktop"),
}
RECYCLE_ALIASES = ("Papelera", "Papelera de reciclaje", "Recycle Bin")


def sibling_names(name: str) -> tuple[str, ...]:
    """Todos los nombres con que se conoce una carpeta conocida (Downloads → Descargas, Downloads): el Explorador
    titula su ventana con el nombre del idioma de Windows, que no tiene por qué ser el que se pidió."""
    wanted = normalize(name)
    for group in (*ALIASES.values(), RECYCLE_ALIASES):
        if wanted in {normalize(n) for n in group}:
            return group
    return ()


# nombre del valor en `User Shell Folders` (Downloads solo tiene GUID)
_REGISTRY_VALUES = {
    "downloads": "{374DE290-123F-4565-9164-39C4925E467B}",
    "documents": "Personal",
    "pictures": "My Pictures",
    "music": "My Music",
    "videos": "My Video",
    "desktop": "Desktop",
}
_HOME_DEFAULTS = {
    "downloads": "Downloads",
    "documents": "Documents",
    "pictures": "Pictures",
    "music": "Music",
    "videos": "Videos",
    "desktop": "Desktop",
}


def read_user_shell_folders() -> dict[str, str]:
    """Rutas (ya sin variables de entorno) de las carpetas conocidas, según el registro. Vacío fuera de Windows."""
    if sys.platform != "win32":
        return {}
    import winreg

    found: dict[str, str] = {}
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, USER_SHELL_FOLDERS_KEY) as key:
            for name, value_name in _REGISTRY_VALUES.items():
                try:
                    found[name] = os.path.expandvars(str(winreg.QueryValueEx(key, value_name)[0]))
                except OSError:
                    continue
    except OSError:
        pass
    return found


def resolve(home: Path | None = None, registry: Mapping[str, str] | None = None) -> dict[str, Path]:
    """El registro manda; si falta una carpeta, la de siempre bajo el perfil del usuario."""
    base = home or Path.home()
    from_registry = read_user_shell_folders() if registry is None else registry
    return {name: Path(from_registry.get(name) or base / default) for name, default in _HOME_DEFAULTS.items()}


def scan(paths: Mapping[str, Path | str], recycle_bin: bool = True) -> list[AppEntry]:
    entries: list[AppEntry] = []
    for key, aliases in ALIASES.items():
        path = paths.get(key)
        if path is None or not Path(path).is_dir():
            continue
        entries += [AppEntry(short_id(SOURCE, f"{key}:{alias}"), alias, SOURCE, "file", str(path)) for alias in aliases]
    if recycle_bin:
        entries += [
            AppEntry(short_id(SOURCE, f"papelera:{alias}"), alias, SOURCE, "uri", RECYCLE_BIN_TARGET)
            for alias in RECYCLE_ALIASES
        ]
    return entries
