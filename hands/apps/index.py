"""Índice de aplicaciones en memoria con búsqueda difusa (rapidfuzz)."""

from __future__ import annotations

import threading
from collections.abc import Iterable
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from hands.apps.models import AppEntry, normalize

# Si dos fuentes dan la misma app, gana la primera: es la que Windows o el cliente de juegos usan de verdad.
PRIORIDAD = ("startapps", "steam", "epic", "startmenu", "registry")
# Con WRatio el ruido (nombres cortos que "contienen" la consulta) llega a ~67; los aciertos reales, a 90+.
SCORE_MINIMO = 72.0


@dataclass(frozen=True)
class Resultado:
    entry: AppEntry
    score: float


def _prioridad(source: str) -> int:
    return PRIORIDAD.index(source) if source in PRIORIDAD else len(PRIORIDAD)


class AppIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: dict[str, AppEntry] = {}
        self._names: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def replace(self, entries: Iterable[AppEntry]) -> None:
        """Sustituye el contenido, quedándose con una sola entrada por nombre normalizado."""
        best: dict[str, AppEntry] = {}
        for entry in entries:
            key = normalize(entry.name)
            actual = best.get(key)
            if actual is None or _prioridad(entry.source) < _prioridad(actual.source):
                best[key] = entry
        by_id = {e.id: e for e in best.values()}
        names = {e.id: normalize(e.name) for e in by_id.values()}
        with self._lock:
            self._entries, self._names = by_id, names

    def get(self, app_id: str) -> AppEntry | None:
        return self._entries.get(app_id)

    def search(self, query: str, limit: int = 5, score_minimo: float = SCORE_MINIMO) -> list[Resultado]:
        wanted = normalize(query)
        if not wanted:
            return []
        with self._lock:
            entries, names = self._entries, self._names
        matches = process.extract(wanted, names, scorer=fuzz.WRatio, limit=None, score_cutoff=score_minimo)
        resultados = [Resultado(entries[app_id], score) for _name, score, app_id in matches]
        resultados.sort(key=lambda r: (-r.score, _prioridad(r.entry.source), len(r.entry.name)))
        return resultados[:limit]
