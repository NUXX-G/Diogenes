"""Confirmación impuesta en código para las acciones irreversibles.

Esta es la defensa real contra una orden inyectada (en una web, un correo, un nombre de app): el modelo puede
proponer cualquier herramienta, pero las de la lista no se ejecutan sin un "sí" humano. Si no hay forma de
preguntar, la respuesta es no.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable, Iterable
from pathlib import Path
from typing import Any

Confirm = Callable[[str, dict[str, Any]], Awaitable[bool]]

CONFIG_DIR = Path("configuracion")


class ConfirmationPolicy:
    def __init__(self, tools: Iterable[str]) -> None:
        self._tools = frozenset(tools)

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> ConfirmationPolicy:
        """Lee `confirmaciones.json` (real, no versionado) o, si no existe, el `.example`. Un fichero roto lanza."""
        for name in ("confirmaciones.json", "confirmaciones.example.json"):
            file = directory / name
            if file.is_file():
                data = json.loads(file.read_text(encoding="utf-8"))
                return cls(data["herramientas"])
        raise FileNotFoundError(f"No hay confirmaciones.json ni confirmaciones.example.json en {directory}")

    def requires(self, tool: str) -> bool:
        return tool in self._tools

    async def allows(self, tool: str, args: dict[str, Any], confirm: Confirm | None) -> bool:
        if not self.requires(tool):
            return True
        return confirm is not None and await confirm(tool, args)
