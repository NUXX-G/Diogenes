"""Índice de aplicaciones en memoria con búsqueda difusa (rapidfuzz) y ranking con desambiguación."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace
from difflib import SequenceMatcher

from rapidfuzz import fuzz, process

from hands.apps.models import AppEntry, normalize
from hands.apps.usage import clear_by_use

# Si dos fuentes dan la misma app, gana la primera: es la que Windows o el cliente de juegos usan de verdad.
PRIORIDAD = ("startapps", "steam", "epic", "startmenu", "carpetas", "desktop", "registry")
# Fuentes con interfaz propia. `registry` (App Paths) son ejecutables sueltos, a menudo de consola o de apoyo.
CON_INTERFAZ = frozenset({"startapps", "steam", "epic", "startmenu", "carpetas", "desktop"})
# Con WRatio el ruido (nombres cortos que "contienen" la consulta) llega a ~67; los aciertos reales, a 90+.
SCORE_MINIMO = 72.0
# Diferencia de puntuación a partir de la cual, con el resto igual, hay un ganador claro.
MARGEN_CLARO = 5.0

EXACTA, PREFIJO, DIFUSA = 0, 1, 2
ALIAS_SEP = "#"

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


# Palabras que acompañan al nombre de una app sin ser parte de él ("abre EL bloc DE notas", "la APP de Spotify").
CONECTORES = frozenset(
    {
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "de",
        "del",
        "al",
        "a",
        "en",
        "mi",
        "app",
        "aplicacion",
        "programa",
        "juego",
    }
)


def palabra_encaja(palabra: str, nombres: list[str]) -> bool:
    for n in nombres:
        if palabra == n or (len(palabra) >= 3 and n.startswith(palabra)):
            return True
        if min(len(palabra), len(n)) >= 4 and SequenceMatcher(None, palabra, n).ratio() >= 0.8:
            return True
    return False


def cubre(query: str, entry: AppEntry) -> bool:
    """¿La consulta nombra esa app y nada más? Con una sola palabra decide el índice (tolera erratas y abreviaturas);
    con varias, cada palabra tiene que encajar con el nombre o un alias: "spotify y pon daft punk" no es "Spotify"."""
    palabras = [w for w in normalize(query).split() if w not in CONECTORES]
    if len(palabras) <= 1:
        return True
    nombres = [w for text in (entry.name, *entry.aliases) for w in normalize(text).split()]
    return all(palabra_encaja(p, nombres) for p in palabras)


def por_uso(resultados: Sequence[Resultado], uso: dict[str, float]) -> list[Resultado]:
    """Reordena por uso solo DENTRO de los grupos que el índice ya considera iguales (mismo tipo de coincidencia y de
    fuente): una app muy usada no adelanta a una coincidencia exacta, pero desempata entre dos parecidas."""
    if not uso:
        return list(resultados)
    puesto = {id(r): n for n, r in enumerate(resultados)}

    def grupo(r: Resultado) -> tuple[int, bool, int]:
        return (0 if r.tier <= PREFIJO else 1, not r.con_interfaz, r.tier)

    return sorted(resultados, key=lambda r: (grupo(r), -uso.get(r.entry.id, 0.0), puesto[id(r)]))


def ganador_por_uso(resultados: Sequence[Resultado], uso: dict[str, float]) -> bool:
    """Sin ganador claro por nombre, ¿lo hay por uso? Las dos primeras del mismo grupo y la primera mucho más usada."""
    if len(resultados) < 2:
        return False
    a, b = resultados[0], resultados[1]
    if (a.tier, a.con_interfaz) != (b.tier, b.con_interfaz):
        return False
    return clear_by_use(a.entry.id, b.entry.id, uso)


class AppIndex:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._ready = threading.Event()  # se activa con la primera carga, de la caché o del descubrimiento
        self._entries: dict[str, AppEntry] = {}
        self._names: dict[str, str] = {}

    def __len__(self) -> int:
        return len(self._entries)

    @property
    def ready(self) -> bool:
        return self._ready.is_set()

    async def wait_ready(self, timeout: float) -> bool:
        """Espera a la primera carga sin bloquear el bucle de eventos. False si no llegó a tiempo."""
        return self._ready.is_set() or await asyncio.to_thread(self._ready.wait, timeout)

    def replace(self, entries: Iterable[AppEntry]) -> None:
        """Sustituye el contenido, quedándose con una sola entrada por nombre normalizado."""
        best: dict[str, AppEntry] = {}
        for entry in entries:
            key = normalize(entry.name)
            actual = best.get(key)
            if actual is None or _prioridad(entry.source) < _prioridad(actual.source):
                best[key] = entry
        by_id = _resolver_colisiones(best.values())
        # Cada alias es otra clave de búsqueda de la misma entrada: "<id>#<n>"
        names = {e.id: normalize(e.name) for e in by_id.values()}
        for e in by_id.values():
            for n, alias in enumerate(e.aliases):
                if normalize(alias):
                    names[f"{e.id}{ALIAS_SEP}{n}"] = normalize(alias)
        with self._lock:
            self._entries, self._names = by_id, names
        self._ready.set()

    def refresh_source(self, source: str, entries: Iterable[AppEntry]) -> bool:
        """Cambia solo las entradas de una fuente (p. ej. el escritorio) sin rehacer todo el descubrimiento.
        Devuelve True si cambió algo."""
        fresh = list(entries)
        current = [e for e in self._entries.values() if e.source == source]
        if {(e.id, e.name) for e in current} == {(e.id, e.name) for e in fresh}:
            return False
        self.replace([*(e for e in self._entries.values() if e.source != source), *fresh])
        return True

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
        best: dict[str, Resultado] = {}  # una entrada puede casar por su nombre y por un alias: cuenta la mejor
        for _n, score, key in matches:
            app_id = key.split(ALIAS_SEP, 1)[0]
            found = Resultado(entries[app_id], score, _tier(wanted, names[key]))
            current = best.get(app_id)
            if current is None or (found.tier, -found.score) < (current.tier, -current.score):
                best[app_id] = found
        resultados = list(best.values())
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
