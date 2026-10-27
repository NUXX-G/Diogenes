"""Lector mínimo del formato VDF/ACF de Valve (pares "clave" "valor" y bloques anidados)."""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r'"((?:[^"\\]|\\.)*)"|([{}])')
_ESCAPES = {"\\\\": "\\", '\\"': '"', "\\n": "\n", "\\t": "\t"}


def _unescape(text: str) -> str:
    return re.sub(r'\\[\\"nt]', lambda m: _ESCAPES[m.group(0)], text)


def parse(text: str) -> dict[str, Any]:
    tokens = [(m.group(1), m.group(2)) for m in _TOKEN.finditer(text)]
    pos = 0

    def block() -> dict[str, Any]:
        nonlocal pos
        result: dict[str, Any] = {}
        while pos < len(tokens):
            string, brace = tokens[pos]
            pos += 1
            if brace == "}":
                return result
            if string is None:
                continue
            key = _unescape(string)
            if pos >= len(tokens):
                break
            value, value_brace = tokens[pos]
            pos += 1
            if value_brace == "{":
                result[key] = block()
            elif value is not None:
                result[key] = _unescape(value)
        return result

    return block()
