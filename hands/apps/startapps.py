"""Apps del menú Inicio según `Get-StartApps` (incluye las de la Store)."""

from __future__ import annotations

import json
import re

from hands.apps.models import AppEntry, es_ruido, short_id

SOURCE = "startapps"
_ES_URI = re.compile(r"^[a-z][a-z0-9+.-]*://", re.IGNORECASE)
POWERSHELL_COMMAND = (
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
    "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress"
)


def parse(output: str) -> list[AppEntry]:
    """Interpreta el JSON de `Get-StartApps`. Con un solo resultado PowerShell emite un objeto, no una lista."""
    output = output.strip().lstrip("﻿")
    if not output:
        return []
    data = json.loads(output)
    if isinstance(data, dict):
        data = [data]
    entries: list[AppEntry] = []
    for item in data:
        name, app_id = (item.get("Name") or "").strip(), (item.get("AppID") or "").strip()
        if not app_id or es_ruido(name):
            continue
        # Los accesos .url (p. ej. juegos de Steam) aparecen con una URI como AppID: se abre como protocolo.
        kind = "uri" if _ES_URI.match(app_id) else "shell"
        entries.append(AppEntry(short_id(SOURCE, app_id), name, SOURCE, kind, app_id))
    return entries
