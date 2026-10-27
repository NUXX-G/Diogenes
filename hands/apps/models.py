"""Modelo común de una aplicación descubierta en el equipo."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Literal

# shell: explorer.exe shell:AppsFolder\<target> · uri: protocolo registrado · file: ejecutable o acceso directo
LaunchKind = Literal["shell", "uri", "file"]

_RUIDO = ("uninstall", "desinstal")


@dataclass(frozen=True)
class AppEntry:
    id: str
    name: str
    source: str
    launch_kind: LaunchKind
    target: str


def normalize(text: str) -> str:
    """Minúsculas, sin acentos ni signos: la forma en la que se compara y se busca."""
    sin_acentos = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", " ", sin_acentos.lower()).strip()


def es_ruido(name: str) -> bool:
    """Desinstaladores y similares: existen como acceso, pero nadie pide abrirlos."""
    n = normalize(name)
    return not n or any(marca in n for marca in _RUIDO)
