"""Descriptores OpenSearch: cómo busca una web, leído de la propia web (`<link rel="search">`)."""

from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from urllib.parse import urljoin

PLACEHOLDER = "{query}"
_OPENSEARCH_TYPE = "application/opensearchdescription+xml"
# Parámetros obligatorios de OpenSearch que no son la consulta y tienen un valor neutro razonable.
_NEUTROS = {"inputEncoding": "UTF-8", "outputEncoding": "UTF-8", "language": "*", "startPage": "1", "startIndex": "1"}
_OPCIONAL = re.compile(r"^\{[^{}]*\?\}$")
_CUALQUIER = re.compile(r"\{([^{}]*)\}")


class _LinkFinder(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag != "link":
            return
        a = {k.lower(): (v or "") for k, v in attrs}
        if (
            "search" in a.get("rel", "").lower().split()
            and a.get("type", "").lower() == _OPENSEARCH_TYPE
            and a.get("href")
        ):
            self.hrefs.append(a["href"])


def find_descriptor_url(html: str, base_url: str) -> str | None:
    finder = _LinkFinder()
    finder.feed(html)
    return urljoin(base_url, finder.hrefs[0]) if finder.hrefs else None


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def template_from_descriptor(xml_text: str) -> str | None:
    """Plantilla de búsqueda web (`text/html`, GET) con `{query}`, o None si el descriptor no sirve."""
    # XML que viene de una web: sin DTD ni entidades, que es por donde se ataca a los analizadores.
    if "<!DOCTYPE" in xml_text.upper() or "<!ENTITY" in xml_text.upper():
        return None
    try:
        root = ET.fromstring(xml_text.strip())
    except ET.ParseError:
        return None
    for element in root.iter():
        if _local(element.tag) != "Url":
            continue
        if element.get("type", "text/html").lower() != "text/html" or element.get("method", "get").lower() != "get":
            continue
        template = to_template(element.get("template", ""))
        if template:
            return template
    return None


def to_template(raw: str) -> str | None:
    """Pasa una plantilla OpenSearch a la nuestra: `{searchTerms}` → `{query}`, sin parámetros opcionales."""
    if "{searchTerms}" not in raw or not raw.lower().startswith(("http://", "https://")):
        return None
    base, sep, query = raw.partition("?")
    if sep:
        kept = []
        for param in query.split("&"):
            _name, _eq, value = param.partition("=")
            if _OPCIONAL.match(value):
                continue
            kept.append(param)
        query = "&".join(kept)
    text = base + (sep + query if query else "")
    text = text.replace("{searchTerms}", PLACEHOLDER)

    def neutral(match: re.Match[str]) -> str:
        name = match.group(1)
        if name == "query":
            return match.group(0)
        if name.endswith("?"):
            return ""
        return _NEUTROS.get(name, match.group(0))

    text = _CUALQUIER.sub(neutral, text)
    leftovers = [m for m in _CUALQUIER.findall(text) if m != "query"]
    return None if leftovers else text
