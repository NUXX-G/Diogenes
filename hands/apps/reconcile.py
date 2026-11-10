r"""Une las entradas de `App Paths` con la app del menú Inicio que es la misma.

`App Paths` da el nombre del ejecutable (`notepad`), no el de la app (`Bloc de notas`). Si ese ejecutable es el de una
app del menú Inicio, no es una app desconocida: es la misma, con otro nombre. Se funde con ella (que gana, con el
nombre del ejecutable como alias) y se descarta la entrada de `App Paths`, que se lanzaría como un `.exe` suelto.

Dos formas de reconocerla, ninguna escrita a mano por app:
- un acceso del menú Inicio (`.lnk`) cuyo destino es ese mismo `.exe`;
- una app de la Store: el `.exe` vive en `WindowsApps\<Paquete>_<versión>_<arq>__<editor>\…` y el `AppID` de
  `Get-StartApps` es `<Paquete>_<editor>!<app>`.
"""

from __future__ import annotations

import logging
import ntpath
import os
import re
from collections.abc import Callable
from dataclasses import replace

from hands.apps import shortcuts
from hands.apps.models import AppEntry, normalize
from hands.apps.registry import SOURCE as REGISTRY
from hands.apps.startapps import SOURCE as STARTAPPS
from hands.apps.startmenu import SOURCE as STARTMENU

log = logging.getLogger(__name__)
_PACKAGE_DIR = re.compile(
    r"\\WindowsApps\\(?P<name>[^\\_]+)_[^\\_]+_[^\\_]+_[^\\_]*_(?P<publisher>[a-z0-9]+)(?:\\|$)", re.IGNORECASE
)


def package_key(path: str) -> str | None:
    """`<Paquete>_<editor>` de un ejecutable dentro de WindowsApps, o None."""
    found = _PACKAGE_DIR.search(path)
    return f"{found['name']}_{found['publisher']}".lower() if found else None


def _norm_path(path: str) -> str:
    return ntpath.normcase(ntpath.normpath(os.path.expandvars(path)))


def _read_all(entries: list[AppEntry], read_link: Callable[[str], shortcuts.LinkTarget | None]) -> dict[str, AppEntry]:
    """Destino (`.exe`, normalizado) → acceso del menú Inicio que lo abre. Si hay varios, gana el primero."""
    by_exe: dict[str, AppEntry] = {}
    for entry in entries:
        if entry.source != STARTMENU or not entry.target.lower().endswith(".lnk"):
            continue
        try:
            link = read_link(entry.target)
        except Exception:  # un .lnk roto no tumba el descubrimiento
            log.debug("no se pudo leer %s", entry.target, exc_info=True)
            continue
        for path in link.paths if link else ():
            if path.lower().endswith(".exe"):
                by_exe.setdefault(_norm_path(path), entry)
    return by_exe


def merge_app_paths(
    entries: list[AppEntry],
    read_link: Callable[[str], shortcuts.LinkTarget | None] = shortcuts.read,
) -> list[AppEntry]:
    """Quita de `entries` las de App Paths que son una app del menú Inicio y deja su nombre como alias de esa app."""
    appx: dict[str, list[AppEntry]] = {}
    for entry in entries:
        if entry.source == STARTAPPS and "!" in entry.target:
            appx.setdefault(entry.target.split("!", 1)[0].lower(), []).append(entry)
    by_exe = _read_all(entries, read_link)

    owner: dict[str, AppEntry] = {}  # id de la registry → app del menú Inicio que la absorbe
    for entry in entries:
        if entry.source != REGISTRY:
            continue
        package = package_key(entry.target)
        candidates = appx.get(package or "", [])
        match = candidates[0] if len(candidates) == 1 else by_exe.get(_norm_path(entry.target))
        if match is not None:
            owner[entry.id] = match

    aliases: dict[str, list[str]] = {}
    for registry_id, match in owner.items():
        absorbed = next(e for e in entries if e.id == registry_id)
        if normalize(absorbed.name) != normalize(match.name):
            aliases.setdefault(match.id, []).append(absorbed.name)
    merged: list[AppEntry] = []
    for entry in entries:
        if entry.id in owner and entry.source == REGISTRY:
            continue
        extra = aliases.get(entry.id)
        merged.append(replace(entry, aliases=(*entry.aliases, *extra)) if extra else entry)
    return merged
