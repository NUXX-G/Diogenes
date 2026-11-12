"""Qué host tiene una URL y cuál es su dominio propio. Lo usan la puerta de intención y el navegador."""

from __future__ import annotations

import re
from urllib.parse import urlsplit

_SEGUNDO_NIVEL = frozenset({"co", "com", "org", "net", "gov", "edu", "ac"})  # example.co.uk → 3 etiquetas


def host_of(url: str) -> str:
    """Host de una URL (también sin esquema). Vacío si no es http/https."""
    text = url.strip()
    if re.match(r"[a-z][a-z0-9+.-]*:(?!//|[0-9])", text, re.IGNORECASE):  # javascript:, mailto:… no son webs
        return ""
    if "://" not in text:
        text = "https://" + text.lstrip("/")
    parts = urlsplit(text)
    return (parts.hostname or "").lower() if parts.scheme in ("http", "https") else ""


def registrable(host: str) -> str:
    """Dominio "propio" de un host: www.youtube.com → youtube.com; bbc.co.uk → bbc.co.uk."""
    labels = [label for label in host.lower().strip(".").split(".") if label]
    if len(labels) <= 2 or all(label.isdigit() for label in labels):
        return ".".join(labels)
    if len(labels[-1]) == 2 and labels[-2] in _SEGUNDO_NIVEL:
        return ".".join(labels[-3:])
    return ".".join(labels[-2:])
