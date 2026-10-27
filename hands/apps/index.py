"""Índice de aplicaciones en memoria con búsqueda difusa (rapidfuzz) y ranking con desambiguación."""

from __future__ import annotations

import hashlib
import logging
import threading
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

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

log = logging.getLogger(__name__)


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


def _resolver_colisiones(entries: Iterable[AppEntry]) -> dict[str, AppEntry]:
    """Indexa por id. Si dos apps distintas comparten id (colisión del hash corto), ninguna pisa a la otra: las dos
    pasan a un id largo derivado de lo que las distingue (tipo, destino y nombre), independiente del orden."""
    grupos: dict[str, list[AppEntry]] = defaultdict(list)
    for entry in entries:
        grupos[entry.id].append(entry)
    by_id: dict[str, AppEntry] = {}
    for app_id, grupo in grupos.items():
        if len(grupo) == 1:
            by_id[app_id] = grupo[0]
            continue
        log.warning("colisión de id '%s' entre %s: se usan ids largos", app_id, [e.name for e in grupo])
        for entry in grupo:
            largo = hashlib.sha1(f"{entry.launch_kind}|{entry.target}|{entry.name}".encode()).hexdigest()[:16]
            by_id[f"{entry.source}:{largo}"] = replace(entry, id=f"{entry.source}:{largo}")
    return by_id


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
        by_id = _resolver_colisiones(best.values())
        names = {e.id: normalize(e.name) for e in by_id.values()}
        with self._lock:
            self._entries, self._names = by_id, names

    def get(self, app_id: str) -> AppEntry | None:
        return self._entries.get(app_id)

    def search(self, query: str, limit: int = 5, score_minimo: float = SCORE_MINIMO) -> list[Resultado]:
        """Orden: coincidencias (exacta/prefijo/palabra) antes que difusas; entre las coincidencias, las apps con
        interfaz antes que App Paths (así 'chrome' abre Google Chrome, no chrome.exe suelto); luego exacta > prefijo."""
        wanted = normalize(query)
        if not wanted:
            return []
        with self._lock:
            entries, names = self._entries, self._names
        matches = process.extract(wanted, names, scorer=fuzz.WRatio, limit=None, score_cutoff=score_minimo)
        resultados = [Resultado(entries[app_id], score, _tier(wanted, names[app_id])) for _n, score, app_id in matches]
        resultados.sort(
            key=lambda r: (
                0 if r.tier <= PREFIJO else 1,
                not r.con_interfaz,
                r.tier,
                -r.score,
                _prioridad(r.entry.source),
                len(r.entry.name),
            )
        )
        return resultados[:limit]
