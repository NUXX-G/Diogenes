"""Ejecutables registrados en `App Paths` del registro de Windows."""

from __future__ import annotations

import sys
from collections.abc import Iterable
from pathlib import PureWindowsPath

from hands.apps.models import AppEntry, es_ruido

SOURCE = "registry"
APP_PATHS_KEY = r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"


def parse(items: Iterable[tuple[str, str]]) -> list[AppEntry]:
    """`items` son pares (nombre de la subclave, valor por defecto), p. ej. ("chrome.exe", "C:\\...\\chrome.exe")."""
    entries: list[AppEntry] = []
    for subkey, value in items:
        path = value.strip().strip('"')
        stem = PureWindowsPath(subkey).stem
        if not path.lower().endswith(".exe") or es_ruido(stem):
            continue
        entries.append(AppEntry(f"{SOURCE}:{subkey.lower()}", stem, SOURCE, "file", path))
    return entries


def read_app_paths() -> list[tuple[str, str]]:
    if sys.platform != "win32":
        return []
    import winreg

    items: list[tuple[str, str]] = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        try:
            with winreg.OpenKey(hive, APP_PATHS_KEY) as root:
                for i in range(winreg.QueryInfoKey(root)[0]):
                    subkey = winreg.EnumKey(root, i)
                    try:
                        with winreg.OpenKey(root, subkey) as key:
                            items.append((subkey, str(winreg.QueryValueEx(key, "")[0])))
                    except OSError:
                        continue
        except OSError:
            continue
    return items
