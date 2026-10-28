"""Reglas de seguridad del navegador, leídas de `configuracion/confirmaciones.json` (sección `navegador`).

Las listas viven en la configuración, no en el código. Si el fichero real no trae la sección, se usa la del `.example`.
Se comparan nombres accesibles sin acentos ni mayúsculas y por palabras completas ("comment" no casa con "comments").
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from hands.apps.models import normalize
from hands.browser.snapshot import CLICKABLE_ROLES

CONFIG_DIR = Path("configuracion")
ACTIONABLE = (
    frozenset({"button", "link", "menuitem", "menuitemcheckbox", "menuitemradio", "tab", "option"}) | CLICKABLE_ROLES
)


def _matches(name: str, terms: frozenset[str]) -> str | None:
    padded = f" {normalize(name)} "
    return next((t for t in sorted(terms) if f" {t} " in padded), None)


@dataclass(frozen=True)
class BrowserRules:
    irreversible: frozenset[str]
    payment: frozenset[str]
    search_fields: frozenset[str]
    minimal_notice: frozenset[str] = frozenset()
    order_criteria: tuple[tuple[str, frozenset[str]], ...] = ()

    @classmethod
    def from_lists(
        cls,
        irreversible: Iterable[str],
        payment: Iterable[str],
        search: Iterable[str],
        minimal_notice: Iterable[str] = (),
        order_criteria: Mapping[str, Iterable[str]] | None = None,
    ) -> BrowserRules:
        return cls(
            frozenset(normalize(t) for t in irreversible),
            frozenset(normalize(t) for t in payment),
            frozenset(normalize(t) for t in search),
            frozenset(normalize(t) for t in minimal_notice),
            tuple((k, frozenset(normalize(t) for t in terms)) for k, terms in (order_criteria or {}).items()),
        )

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> BrowserRules:
        section: dict[str, list[str]] | None = None
        for name in ("confirmaciones.json", "confirmaciones.example.json"):
            file = directory / name
            if file.is_file():
                section = json.loads(file.read_text(encoding="utf-8")).get("navegador")
                if section is not None:
                    break
        if section is None:
            raise FileNotFoundError(f"No hay sección 'navegador' en confirmaciones*.json de {directory}")
        return cls.from_lists(
            section["verbos_irreversibles"],
            section["terminos_de_pago"],
            section["campos_de_busqueda"],
            section.get("avisos_minimos", []),
            section.get("criterios_de_orden", {}),
        )

    def payment_term(self, role: str, name: str) -> str | None:
        return _matches(name, self.payment) if role in ACTIONABLE else None

    def irreversible_term(self, role: str, name: str) -> str | None:
        return _matches(name, self.irreversible) if role in ACTIONABLE else None

    def order_criteria_of(self, role: str, name: str) -> list[str]:
        """Criterios de orden que aplica pulsar este elemento ('barato', 'visto'...): una ordenación o filtro de la web."""
        if role not in ACTIONABLE:
            return []
        return [key for key, terms in self.order_criteria if _matches(name, terms)]

    def minimal_notice_term(self, role: str, name: str) -> str | None:
        """Botón de un aviso (cookies, etc.) que lo cierra con lo mínimo: rechazar."""
        return _matches(name, self.minimal_notice) if role in {"button", "link"} else None

    def is_search_field(self, role: str, name: str) -> bool:
        return role == "searchbox" or _matches(name, self.search_fields) is not None
