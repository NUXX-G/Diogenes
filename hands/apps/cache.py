"""Caché en disco del índice de apps: arrancar hands-mcp no debería esperar a PowerShell en frío.

Guarda las entradas con la hora en `data/apps-index.json` (carpeta ignorada por Git). Vale unos minutos: pasado ese
tiempo, o si el fichero está roto o es de otra versión, se ignora y se vuelve a descubrir. Contiene rutas del equipo
(escritorio, juegos), así que nunca se versiona ni sale del repo.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from hands.apps.models import AppEntry

log = logging.getLogger(__name__)

VERSION = 2
DEFAULT_PATH = Path("data/apps-index.json")
DEFAULT_TTL_SECONDS = 10 * 60


class IndexCache:
    def __init__(
        self,
        path: Path = DEFAULT_PATH,
        ttl: float = DEFAULT_TTL_SECONDS,
        now: Callable[[], float] = time.time,
    ) -> None:
        self._path = path
        self._ttl = ttl
        self._now = now

    def load(self) -> list[AppEntry] | None:
        """Las entradas guardadas si siguen vigentes; None si no hay caché, caducó o no se puede leer."""
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
            if data["version"] != VERSION or self._now() - float(data["guardado"]) > self._ttl:
                return None
            return [
                AppEntry(e["id"], e["name"], e["source"], e["launch_kind"], e["target"], tuple(e.get("aliases", ())))
                for e in data["entradas"]
            ]
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError):
            log.warning("caché del índice ilegible: se ignora", exc_info=True)
            return None

    def save(self, entries: Iterable[AppEntry]) -> None:
        data = {
            "version": VERSION,
            "guardado": self._now(),
            "entradas": [
                {
                    "id": e.id,
                    "name": e.name,
                    "source": e.source,
                    "launch_kind": e.launch_kind,
                    "target": e.target,
                    "aliases": list(e.aliases),
                }
                for e in entries
            ],
        }
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self._path)  # atómico: un corte a medias no deja un JSON cortado
        except OSError:
            log.warning("no se pudo guardar la caché del índice", exc_info=True)
