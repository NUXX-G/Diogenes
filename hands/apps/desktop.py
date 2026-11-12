"""Elementos del escritorio: los del usuario y los públicos (accesos, archivos y carpetas)."""

from __future__ import annotations

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from hands.apps import folders
from hands.apps.models import AppEntry, es_ruido, short_id

SOURCE = "desktop"
IGNORED = frozenset({"desktop.ini", "thumbs.db"})
MAX_ITEMS = 500  # un escritorio con miles de archivos no debe inflar el índice


def roots(
    env: Mapping[str, str] | None = None, home: Path | None = None, registry: Mapping[str, str] | None = None
) -> list[Path]:
    """Escritorio del usuario (según el registro), el de OneDrive si existe y el público."""
    variables = os.environ if env is None else env
    candidates = [folders.resolve(home, registry)["desktop"]]
    for var in ("OneDrive", "OneDriveConsumer", "OneDriveCommercial"):
        if variables.get(var):
            candidates.append(Path(variables[var]) / "Desktop")
    if variables.get("PUBLIC"):
        candidates.append(Path(variables["PUBLIC"]) / "Desktop")
    unique: list[Path] = []
    for path in candidates:
        if path not in unique:
            unique.append(path)
    return unique


def scan(directories: Iterable[Path], limit: int = MAX_ITEMS) -> list[AppEntry]:
    entries: list[AppEntry] = []
    for directory in directories:
        if not directory.is_dir():
            continue
        for item in sorted(directory.iterdir(), key=lambda p: p.name.lower()):
            if item.name.lower() in IGNORED or item.name.startswith("."):
                continue
            name = item.name if item.is_dir() else item.stem
            if es_ruido(name):
                continue
            entries.append(AppEntry(short_id(SOURCE, str(item)), name, SOURCE, "file", str(item)))
            if len(entries) >= limit:
                return entries
    return entries
