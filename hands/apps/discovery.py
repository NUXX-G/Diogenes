"""Reúne todas las fuentes de aplicaciones del equipo. Fuera de Windows devuelve lo que pueda (nada)."""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from hands.apps import desktop, epic, folders, registry, startapps, startmenu, steam
from hands.apps.models import AppEntry

log = logging.getLogger(__name__)


def run_powershell(command: str, timeout: float = 45.0) -> str:
    done = subprocess.run(
        ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", command],
        capture_output=True,
        timeout=timeout,
        check=True,
    )
    return done.stdout.decode("utf-8", errors="replace")


def _start_menu_roots() -> list[Path]:
    roots = []
    for var in ("ProgramData", "APPDATA"):
        base = os.environ.get(var)
        if base:
            roots.append(Path(base) / "Microsoft" / "Windows" / "Start Menu" / "Programs")
    return roots


def _collect_steam() -> list[AppEntry]:
    root = steam.find_root()
    return steam.scan(root) if root else []


def _fuentes(powershell: Callable[[str], str]) -> dict[str, Callable[[], list[AppEntry]]]:
    return {
        startapps.SOURCE: lambda: startapps.parse(powershell(startapps.POWERSHELL_COMMAND)),
        startmenu.SOURCE: lambda: startmenu.scan(_start_menu_roots()),
        registry.SOURCE: lambda: registry.parse(registry.read_app_paths()),
        steam.SOURCE: _collect_steam,
        epic.SOURCE: lambda: epic.scan(epic.manifests_dir()),
        folders.SOURCE: lambda: folders.scan(folders.resolve()),
        desktop.SOURCE: lambda: desktop.scan(desktop.roots()),
    }


def _es_windows() -> bool:
    return sys.platform == "win32"


def discover(powershell: Callable[[str], str] = run_powershell) -> list[AppEntry]:
    """Una fuente que falla no tumba a las demás: se registra y se sigue.

    Las fuentes se recogen a la vez: `Get-StartApps` (PowerShell en frío) tarda segundos y no tiene por qué retrasar
    al resto. El orden del resultado es el de siempre.
    """
    if not _es_windows():
        return []

    def collect(item: tuple[str, Callable[[], list[AppEntry]]]) -> list[AppEntry]:
        nombre, recoger = item
        try:
            encontradas = recoger()
        except Exception:
            log.warning("fuente de apps '%s' no disponible", nombre, exc_info=True)
            return []
        log.info("fuente de apps '%s': %d entradas", nombre, len(encontradas))
        return encontradas

    fuentes = list(_fuentes(powershell).items())
    with ThreadPoolExecutor(max_workers=len(fuentes)) as pool:
        return [entry for found in pool.map(collect, fuentes) for entry in found]
