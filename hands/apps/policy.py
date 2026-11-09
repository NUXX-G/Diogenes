"""Qué lanzamientos piden confirmación aunque el usuario los nombre: ejecutables y scripts.

Abrir un acceso, una carpeta, un documento, un juego de Steam o una app de la Store es lo normal. Ejecutar un `.exe`,
un `.bat`, un `.ps1`, un `.msi`… es otra cosa: Windows lo corre con los permisos del usuario. Las extensiones están en
`configuracion/confirmaciones.json` (sección `ejecutables`), con el `.example` como respaldo.

Un `.lnk` se juzga por su destino real. Solo va directo si apunta a algo que no es ejecutable o a un `.exe` normal del
menú Inicio. Un intérprete con argumentos (`powershell -File x.ps1`) cuenta como script aunque sea un `.exe`, y un
acceso cuyo destino no se puede leer pregunta.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from hands.apps import shortcuts
from hands.apps.models import AppEntry
from hands.apps.startmenu import SOURCE as START_MENU_SOURCE

CONFIG_DIR = Path("configuracion")
LINK_SUFFIX = ".lnk"
EXE_SUFFIX = ".exe"
DIRECT = "es un ejecutable o script: Windows lo ejecutaría con tus permisos"


def _is_dir(path: str) -> bool:
    return Path(path).is_dir()


@dataclass(frozen=True)
class Assessment:
    requires: bool
    target: str  # lo que se le enseña al usuario: el destino real
    reason: str = ""


@dataclass(frozen=True)
class LaunchPolicy:
    extensions: frozenset[str]  # en minúsculas y con punto: ".exe"
    interpreters: frozenset[str] = frozenset()  # nombres de ejecutable en minúsculas: "powershell.exe"

    @classmethod
    def from_list(cls, extensions: Iterable[str], interpreters: Iterable[str] = ()) -> LaunchPolicy:
        return cls(
            frozenset("." + str(e).lower().lstrip(".") for e in extensions),
            frozenset(str(i).lower() for i in interpreters),
        )

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> LaunchPolicy:
        """Cada lista sale del primer fichero que la traiga: un `confirmaciones.json` antiguo no pierde los intérpretes."""
        found: dict[str, list[str]] = {}
        for name in ("confirmaciones.json", "confirmaciones.example.json"):
            file = directory / name
            if file.is_file():
                section = json.loads(file.read_text(encoding="utf-8")).get("ejecutables", {})
                for key in ("extensiones", "interpretes"):
                    if key in section:
                        found.setdefault(key, section[key])
        if "extensiones" not in found:
            raise FileNotFoundError(f"No hay sección 'ejecutables' en confirmaciones*.json de {directory}")
        return cls.from_list(found["extensiones"], found.get("interpretes", ()))

    def _assess_link(self, entry: AppEntry, link: shortcuts.LinkTarget | None) -> Assessment:
        if link is None:
            return Assessment(True, entry.target, "no he podido leer a dónde apunta este acceso")
        names = [*link.paths, *([link.shell_name] if link.shell_name else [])]
        shown = f"{names[0]} {link.arguments}".strip() if names else entry.target
        if not names:
            # Sin archivo de destino: lo anunció Windows Installer o es un objeto del Shell del menú Inicio (Panel de control)
            if link.advertised or (entry.source == START_MENU_SOURCE and not link.arguments):
                return Assessment(False, shown)
            return Assessment(True, shown, "no he podido leer a dónde apunta este acceso")
        windows_paths = [PureWindowsPath(n) for n in names]
        if link.arguments and (not link.paths or any(p.name.lower() in self.interpreters for p in windows_paths)):
            return Assessment(True, shown, "el acceso ejecuta un intérprete con argumentos: equivale a un script")
        executable = {p.suffix.lower() for p in windows_paths} & self.extensions
        if not executable:
            return Assessment(False, shown)
        if executable != {EXE_SUFFIX}:
            return Assessment(True, shown, f"el acceso apunta a un script o instalador: {DIRECT}")
        if entry.source != START_MENU_SOURCE:
            return Assessment(True, shown, f"el acceso apunta a un ejecutable fuera del menú Inicio: {DIRECT}")
        return Assessment(False, shown)

    def assess(
        self,
        entry: AppEntry,
        is_dir: Callable[[str], bool] = _is_dir,
        read_link: Callable[[str], shortcuts.LinkTarget | None] = shortcuts.read,
    ) -> Assessment:
        if entry.launch_kind != "file":
            return Assessment(False, entry.target)
        suffix = PureWindowsPath(entry.target).suffix.lower()
        if suffix == LINK_SUFFIX and suffix not in self.extensions:
            return self._assess_link(entry, read_link(entry.target))
        requires = suffix in self.extensions and not is_dir(entry.target)
        return Assessment(requires, entry.target, DIRECT if requires else "")

    def requires_confirmation(
        self,
        entry: AppEntry,
        is_dir: Callable[[str], bool] = _is_dir,
        read_link: Callable[[str], shortcuts.LinkTarget | None] = shortcuts.read,
    ) -> bool:
        return self.assess(entry, is_dir, read_link).requires
