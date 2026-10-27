"""Opciones numeradas que una herramienta ofreció al usuario y cómo se resuelve su respuesta («el primero»)."""

from __future__ import annotations

import json
from dataclasses import dataclass

from hands.apps.models import normalize

_ORDINALES = {
    "primero": 0,
    "primera": 0,
    "1": 0,
    "uno": 0,
    "segundo": 1,
    "segunda": 1,
    "2": 1,
    "dos": 1,
    "tercero": 2,
    "tercera": 2,
    "3": 2,
    "tres": 2,
    "ultimo": -1,
    "ultima": -1,
}
_AFIRMACIONES = {"si", "vale", "ok", "dale", "venga", "claro", "adelante", "correcto", "eso"}
_ARTICULOS = {"el", "la", "lo", "los", "las", "un", "una"}
_MAX_PALABRAS = 4  # una elección es corta; una frase larga es una petición nueva


@dataclass(frozen=True)
class Option:
    n: int
    id: str
    nombre: str


@dataclass(frozen=True)
class PendingChoice:
    request: str  # lo que el usuario pidió originalmente
    options: tuple[Option, ...]


def parse_options(tool_output: str) -> tuple[Option, ...]:
    """Opciones de un resultado JSON con `ganador_claro: false` y `opciones: [{n, id, nombre}]`."""
    try:
        data = json.loads(tool_output)
    except json.JSONDecodeError:
        return ()
    if not isinstance(data, dict) or data.get("ganador_claro") is not False:
        return ()
    options = []
    for raw in data.get("opciones") or []:
        if isinstance(raw, dict) and raw.get("id") and raw.get("nombre"):
            options.append(Option(int(raw.get("n", len(options) + 1)), str(raw["id"]), str(raw["nombre"])))
    return tuple(options)


def is_affirmation(text: str) -> bool:
    return normalize(text) in _AFIRMACIONES


def ask_again(options: tuple[Option, ...]) -> str:
    lista = "\n".join(f"{o.n}. {o.nombre}" for o in options)
    return f"Eso no me dice cuál. Elige una:\n{lista}"


def resolve(text: str, options: tuple[Option, ...]) -> Option | None:
    """La opción a la que se refiere `text`, o None si no es una elección (entonces decide el modelo)."""
    if not options:
        return None
    words = [w for w in normalize(text).split() if w not in _ARTICULOS]
    if not words or len(words) > _MAX_PALABRAS:
        return None

    ordinales = [_ORDINALES[w] for w in words if w in _ORDINALES]
    if len(ordinales) == 1:
        try:
            return options[ordinales[0]]
        except IndexError:
            return None

    phrase = " ".join(words)
    exactas = [o for o in options if normalize(o.nombre) == phrase]
    if len(exactas) == 1:
        return exactas[0]
    contienen = [o for o in options if len(phrase) >= 3 and phrase in normalize(o.nombre).split()]
    if len(contienen) == 1:
        return contienen[0]

    if len(options) == 1 and phrase in _AFIRMACIONES:
        return options[0]
    return None
