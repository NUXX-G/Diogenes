"""Índice de aplicaciones en memoria con búsqueda difusa (rapidfuzz) y ranking con desambiguación."""

from __future__ import annotations

import threading
from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from rapidfuzz import fuzz, process

from hands.apps.models import AppEntry, normalize

# Si dos fuentes dan la misma app, gana la primera: es la que Windows o el cliente de juegos usan de verdad.
PRIORIDAD = ("startapps", "steam", "epic", "startmenu", "registry")
# Fuentes con interfaz propia. `registry` (App Paths) son ejecutables sueltos, a menudo de consola o de apoyo.
CON_INTERFAZ = frozenset({"startapps", "steam", "epic", "startmenu"})
# Con WRatio el ruido (nombres cortos que "contienen" la consulta) llega a ~67; los aciertos reales, a 90+.
SCORE_MINIMO = 72.0
# Diferencia de puntuación a partir de la cual, con el resto igual, hay un ganador claro.
MARGEN_CLARO = 5.0

EXACTA, PREFIJO, DIFUSA = 0, 1, 2


@dataclass(frozen=True)
class Resultado:
    entry: AppEntry
    score: float
    tier: int  # EXACTA > PREFIJO > DIFUSA (menor es mejor)

    @property
    def con_interfaz(self) -> bool:
        return self.entry.source in CON_INTERFAZ


def _prioridad(source: str) -> int:
    return PRIORIDAD.index(source) if source in PRIORIDAD else len(PRIORIDAD)


def _tier(query: str, name: str) -> int:
    if name == query:
        return EXACTA
    if name.startswith(query) or f" {query} " in f" {name} ":
        return PREFIJO
    return DIFUSA


def hay_ganador_claro(resultados: Sequence[Resultado]) -> bool:
    """Ganador claro: único, o mejor en tipo de coincidencia, o con interfaz frente a otro sin ella, o por margen."""
    if not resultados:
        return False
    if len(resultados) == 1:
        return True
    a, b = resultados[0], resultados[1]
    if a.tier != b.tier:
        return True
    if a.con_interfaz != b.con_interfaz:
        return True
    return a.score - b.score > MARGEN_CLARO


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
        """Orden: coincidencia exacta > prefijo/palabra > difusa; a igualdad, con interfaz; luego puntuación."""
        wanted = normalize(query)
        if not wanted:
            return []
        with self._lock:
            entries, names = self._entries, self._names
        matches = process.extract(wanted, names, scorer=fuzz.WRatio, limit=None, score_cutoff=score_minimo)
        resultados = [Resultado(entries[app_id], score, _tier(wanted, names[app_id])) for _n, score, app_id in matches]
        resultados.sort(
            key=lambda r: (r.tier, not r.con_interfaz, -r.score, _prioridad(r.entry.source), len(r.entry.name))
        )
        return resultados[:limit]
