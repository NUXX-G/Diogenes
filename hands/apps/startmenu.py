"""Accesos `.lnk` de las carpetas del menú Inicio."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from hands.apps.models import AppEntry, es_ruido

SOURCE = "startmenu"


def scan(roots: Iterable[Path]) -> list[AppEntry]:
    entries: list[AppEntry] = []
    for root in roots:
        if not root.is_dir():
            continue
        for lnk in sorted(root.rglob("*.lnk")):
            if es_ruido(lnk.stem):
                continue
            entries.append(AppEntry(f"{SOURCE}:{lnk}", lnk.stem, SOURCE, "file", str(lnk)))
    return entries
