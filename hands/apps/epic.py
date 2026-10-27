"""Juegos de Epic: manifiestos `.item` (JSON) en `ProgramData\\Epic\\EpicGamesLauncher\\Data\\Manifests`."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import quote

from hands.apps.models import AppEntry

SOURCE = "epic"


def manifests_dir() -> Path:
    program_data = os.environ.get("ProgramData", r"C:\ProgramData")
    return Path(program_data) / "Epic" / "EpicGamesLauncher" / "Data" / "Manifests"


def scan(directory: Path) -> list[AppEntry]:
    entries: list[AppEntry] = []
    if not directory.is_dir():
        return entries
    for item in sorted(directory.glob("*.item")):
        try:
            data = json.loads(item.read_text(encoding="utf-8-sig"))
        except (OSError, json.JSONDecodeError):
            continue
        name, app_name = str(data.get("DisplayName", "")).strip(), str(data.get("AppName", "")).strip()
        if not name or not app_name:
            continue
        ref = ":".join((data.get("CatalogNamespace", ""), data.get("CatalogItemId", ""), app_name))
        uri = f"com.epicgames.launcher://apps/{quote(ref, safe='')}?action=launch&silent=true"
        entries.append(AppEntry(f"{SOURCE}:{app_name}", name, SOURCE, "uri", uri))
    return entries
