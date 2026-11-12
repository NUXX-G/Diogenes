"""Cuánto se usa cada cosa (apps, recetas, alias), con decaimiento en el tiempo: lo que no usas baja.

Todo local, en la tabla `usage` de `data/diogenes.db`. Lo escribe `diogenes-core` (cada app abierta, receta que sale
bien o alias usado) y lo lee hands-mcp para ordenar y desempatar `apps.search` / `apps.open`. Una puntuación es la
suma de usos, cada uno multiplicado por 0,5 por cada `vida_media` transcurrida desde entonces.

El uso solo ordena y desempata: nunca se salta la cobertura de `apps.open` («spotify y pon daft punk» sigue sin abrir
Spotify) ni ninguna confirmación.
"""

from __future__ import annotations

import sqlite3
import time
from collections.abc import Callable
from pathlib import Path

DEFAULT_HALF_LIFE_DAYS = 14.0
# Para desempatar por uso: el primero tiene que llevar al menos tantos usos (ya con el decaimiento) y el doble que el
# segundo. Así un uso suelto no decide nada.
MIN_SCORE = 3.0
FACTOR = 2.0

SCHEMA = """
CREATE TABLE IF NOT EXISTS usage (
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    label TEXT NOT NULL DEFAULT '',
    score REAL NOT NULL DEFAULT 0,
    updated REAL NOT NULL DEFAULT 0,
    PRIMARY KEY (kind, key)
)
"""


def decayed(score: float, since: float, now: float, half_life_days: float = DEFAULT_HALF_LIFE_DAYS) -> float:
    if half_life_days <= 0:
        return score
    return score * 0.5 ** (max(0.0, now - since) / (half_life_days * 86400))


class UsageReader:
    """Lectura de las puntuaciones de las apps desde hands (otro proceso escribe): se relee como mucho cada pocos s."""

    def __init__(
        self,
        path: Path | str,
        half_life_days: float = DEFAULT_HALF_LIFE_DAYS,
        ttl: float = 5.0,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._path = str(path)
        self._half_life = half_life_days
        self._ttl = ttl
        self._clock = clock
        self._cache: dict[str, float] = {}
        self._read_at = float("-inf")

    def apps(self) -> dict[str, float]:
        now = self._clock()
        if now - self._read_at < self._ttl:
            return self._cache
        self._read_at = now
        try:
            with sqlite3.connect(self._path) as db:
                db.execute(SCHEMA)
                rows = db.execute("SELECT key, score, updated FROM usage WHERE kind = 'app'").fetchall()
        except sqlite3.Error:
            return self._cache
        self._cache = {key: decayed(score, updated, now, self._half_life) for key, score, updated in rows}
        return self._cache


def clear_by_use(first: str, second: str | None, scores: dict[str, float]) -> bool:
    """¿El uso decide entre las dos primeras? Solo si la primera se usa bastante y mucho más que la segunda."""
    top = scores.get(first, 0.0)
    other = scores.get(second, 0.0) if second else 0.0
    return top >= MIN_SCORE and top >= FACTOR * max(other, 0.5)
