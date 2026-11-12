"""Qué herramientas se enseñan al modelo en cada petición (opcional: `DIOGENES_HERRAMIENTAS=relevantes`).

Medido en el aula (2026-11-12): Ollama reutiliza el prompt anterior hasta la primera diferencia, y la plantilla de Qwen
pone las herramientas justo después del prompt de sistema. Quitar herramientas del final apenas cuesta, pero volver a
enseñarlas obliga a recalcularlas junto con toda la conversación (~20 s). Por eso viene desactivado: enseñar siempre
todas es lo más rápido en uso normal. Si se activa, los grupos opcionales van al final y en orden fijo, para que lo que
cambie sea siempre la cola del prompt.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from core.llm.base import ToolSpec
from hands.apps.models import normalize

CONFIG_DIR = Path("configuracion")
ALL, RELEVANT = "todas", "relevantes"
_URL = re.compile(r"https?://|\b[\w-]+\.(?:com|es|org|net|io|tv|dev|app|edu|gob|info)\b", re.IGNORECASE)


@dataclass(frozen=True)
class Group:
    name: str
    prefix: str  # nombre MCP: "browser."
    cues: frozenset[str]


class ToolSelector:
    def __init__(self, groups: tuple[Group, ...], mode: str = ALL, media_cues: frozenset[str] = frozenset()) -> None:
        self._groups = groups
        self.mode = mode if mode in (ALL, RELEVANT) else ALL
        self.media_cues = media_cues  # palabras que hacen de una orden algo multimedia (para la búsqueda forzada)

    @classmethod
    def load(cls, mode: str = ALL, directory: Path = CONFIG_DIR) -> ToolSelector:
        for name in ("herramientas.json", "herramientas.example.json"):
            file = directory / name
            if file.is_file():
                data = json.loads(file.read_text(encoding="utf-8"))
                groups = tuple(
                    Group(key, spec["prefijo"], frozenset(normalize(w) for w in spec["pistas"]))
                    for key, spec in data["grupos"].items()
                )
                cues = data.get("busqueda_forzada", {}).get("multimedia", [])
                return cls(groups, mode, frozenset(normalize(w) for w in cues))
        return cls((), mode)

    def is_media(self, text: str) -> bool:
        return bool(set(normalize(text).split()) & self.media_cues)

    def _group_of(self, mcp_name: str) -> Group | None:
        return next((g for g in self._groups if mcp_name.startswith(g.prefix)), None)

    def wanted(self, text: str, *, in_web: bool = False) -> set[str]:
        """Grupos con pistas en el texto. Una URL o estar ya trabajando en una web cuentan como navegador."""
        words = set(normalize(text).split())
        found = {g.name for g in self._groups if words & g.cues}
        if (in_web or _URL.search(text)) and any(g.name == "navegador" for g in self._groups):
            found.add("navegador")
        return found

    def select(
        self,
        specs: list[ToolSpec],
        to_mcp: dict[str, str],
        text: str,
        *,
        in_web: bool = False,
        everything: bool = False,
    ) -> list[ToolSpec]:
        """Las herramientas para esta petición: las básicas, y las de cada grupo opcional solo si hay pistas."""
        if self.mode == ALL or everything:
            return specs
        wanted = self.wanted(text, in_web=in_web)
        base: list[ToolSpec] = []
        optional: dict[str, list[ToolSpec]] = {g.name: [] for g in self._groups}
        for spec in specs:
            group = self._group_of(to_mcp.get(spec.name, spec.name))
            if group is None:
                base.append(spec)
            elif group.name in wanted:
                optional[group.name].append(spec)
        return base + [s for g in self._groups for s in optional[g.name]]
