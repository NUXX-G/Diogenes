"""Plantillas de búsqueda aprendidas por sitio, en SQLite (la misma base que usarán las recetas)."""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

_SCHEMA = """
CREATE TABLE IF NOT EXISTS site_templates (
    site TEXT PRIMARY KEY,
    base_url TEXT NOT NULL,
    template TEXT NOT NULL,
    source TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
)
"""


@dataclass(frozen=True)
class SiteTemplate:
    site: str
    base_url: str
    template: str
    source: str
    successes: int
    failures: int


class TemplateStore:
    """Una conexión por almacén; hands-mcp la usa desde el hilo del bucle de eventos."""

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.execute(_SCHEMA)
        self._db.commit()

    def get(self, site: str) -> SiteTemplate | None:
        row = self._db.execute(
            "SELECT site, base_url, template, source, successes, failures FROM site_templates WHERE site = ?",
            (site,),
        ).fetchone()
        return SiteTemplate(*row) if row else None

    def save(self, site: str, base_url: str, template: str, source: str) -> None:
        self._db.execute(
            "INSERT INTO site_templates (site, base_url, template, source, successes) VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(site) DO UPDATE SET base_url = excluded.base_url, template = excluded.template, "
            "source = excluded.source, failures = 0, updated_at = CURRENT_TIMESTAMP",
            (site, base_url, template, source),
        )
        self._db.commit()

    def record_success(self, site: str) -> None:
        self._db.execute(
            "UPDATE site_templates SET successes = successes + 1, updated_at = CURRENT_TIMESTAMP WHERE site = ?",
            (site,),
        )
        self._db.commit()

    def record_failure(self, site: str) -> None:
        self._db.execute(
            "UPDATE site_templates SET failures = failures + 1, updated_at = CURRENT_TIMESTAMP WHERE site = ?",
            (site,),
        )
        self._db.commit()

    def forget(self, site: str) -> None:
        self._db.execute("DELETE FROM site_templates WHERE site = ?", (site,))
        self._db.commit()

    def close(self) -> None:
        self._db.close()
