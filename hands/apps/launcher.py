"""Lanzamiento de una entrada del índice. Solo se lanza lo descubierto, nunca una orden libre."""

from __future__ import annotations

import os
import subprocess
import sys
from dataclasses import dataclass
from typing import Literal

from hands.apps.models import AppEntry


@dataclass(frozen=True)
class Plan:
    how: Literal["run", "open"]
    argv: tuple[str, ...]


def plan(entry: AppEntry) -> Plan:
    """Cómo se lanza: `shell:AppsFolder` por explorer, o `os.startfile` para protocolos y archivos."""
    if entry.launch_kind == "shell":
        return Plan("run", ("explorer.exe", f"shell:AppsFolder\\{entry.target}"))
    return Plan("open", (entry.target,))


def launch(entry: AppEntry) -> None:
    if sys.platform != "win32":
        raise RuntimeError("Lanzar aplicaciones solo funciona en Windows")
    p = plan(entry)
    if p.how == "run":
        subprocess.Popen(p.argv)  # explorer.exe devuelve códigos no fiables: no se espera
    else:
        os.startfile(p.argv[0])  # type: ignore[attr-defined]
