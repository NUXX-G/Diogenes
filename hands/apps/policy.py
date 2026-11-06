"""Qué lanzamientos piden confirmación aunque el usuario los nombre: ejecutables y scripts.

Abrir un acceso, una carpeta, un documento, un juego de Steam o una app de la Store es lo normal. Ejecutar un `.exe`,
un `.bat`, un `.ps1`, un `.msi`… es otra cosa: Windows lo corre con los permisos del usuario. Las extensiones están en
`configuracion/confirmaciones.json` (sección `ejecutables`), con el `.example` como respaldo.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath

from hands.apps.models import AppEntry

CONFIG_DIR = Path("configuracion")


def _is_dir(path: str) -> bool:
    return Path(path).is_dir()


@dataclass(frozen=True)
class LaunchPolicy:
    extensions: frozenset[str]  # en minúsculas y con punto: ".exe"

    @classmethod
    def from_list(cls, extensions: list[str]) -> LaunchPolicy:
        return cls(frozenset("." + str(e).lower().lstrip(".") for e in extensions))

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> LaunchPolicy:
        for name in ("confirmaciones.json", "confirmaciones.example.json"):
            file = directory / name
            if file.is_file():
                data = json.loads(file.read_text(encoding="utf-8"))
                if "ejecutables" in data:
                    return cls.from_list(data["ejecutables"]["extensiones"])
        raise FileNotFoundError(f"No hay sección 'ejecutables' en confirmaciones*.json de {directory}")

    def requires_confirmation(self, entry: AppEntry, is_dir: Callable[[str], bool] = _is_dir) -> bool:
        if entry.launch_kind != "file":
            return False
        suffix = PureWindowsPath(entry.target).suffix.lower()
        return suffix in self.extensions and not is_dir(entry.target)
