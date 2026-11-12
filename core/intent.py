"""Puerta de intención: lo que el usuario nombra se ejecuta; lo que solo propone el modelo pide confirmación.

El modelo puede proponer cualquier herramienta de acción, pero una app, una web o un cierre que el mensaje del usuario
no pidió (ni por verbo ni por nombre) no se ejecutan solos: pasan a confirmación mostrando el destino. Así una página,
un correo o el nombre de una app que "ordenen" algo no consiguen que Diogenes lo haga.

Qué cuenta como pedido por el usuario (nunca lo que escribe el modelo ni lo que devuelve una herramienta):
- el texto del turno actual, y el de la petición original si el usuario está respondiendo a una lista de opciones
  ("el segundo", un nombre) o dice "sí" a una pregunta (entonces cuenta también la pregunta a la que dice sí);
- los pronombres y referencias ("ciérralo", "eso", "la misma", "ábrelo otra vez") se resuelven contra la última app o
  web que el usuario nombró o que se ejecutó por su orden en la conversación: "ciérralo a la fuerza" tras hablar de
  Paint cuenta como pedido de cerrar Paint;
- dentro de una web que el usuario pidió, `browser.click` y `browser.type` van directos; abrir o navegar a OTRO dominio
  que el usuario no nombró (p. ej. uno sacado del contenido de la página, o un enlace que lleva fuera) pide confirmación.

Los verbos, las herramientas de acción y las palabras genéricas viven en `configuracion/confirmaciones.json`
(sección `intencion`); aquí no hay listas escritas a mano.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from hands.apps.models import normalize
from hands.web.domains import host_of, registrable

CONFIG_DIR = Path("configuracion")
MAX_KNOWN_APPS = 500
PRONOUNS = frozenset(
    {"eso", "esto", "ese", "esa", "este", "esta", "ello", "misma", "mismo"}
)  # no "lo"/"la": también son artículos
CLITICS = ("selo", "sela", "los", "las", "lo", "la")  # ciérralo, ábrelo, ciérrala, ponlos, dáselo...
# Palabras que rodean al objetivo de una orden sin formar parte de él ("diogenes, ábreme la X, por favor").
FILLERS = frozenset(
    {
        "diogenes",
        "me",
        "te",
        "que",
        "el",
        "la",
        "los",
        "las",
        "un",
        "una",
        "a",
        "al",
        "por",
        "favor",
        "porfa",
        "ya",
        "y",
    }
)
ID_LIKE = re.compile(r"^[a-z]+:\S+$")  # un id del índice (`startapps:1a2b3c4d`), no el nombre de una app


@dataclass(frozen=True)
class Rule:
    verbs: tuple[str, ...]  # grupos de verbos que piden esta herramienta
    target: str  # "app" | "url" | "sitio"


@dataclass(frozen=True)
class IntentConfig:
    verbs: dict[str, tuple[str, ...]]
    tools: dict[str, Rule]
    free_in_web: frozenset[str]
    generic_words: frozenset[str]
    blocked_when_closing: frozenset[str] = frozenset({"apps.launch"})

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntentConfig:
        tools = {
            name: Rule(tuple(rule["verbos"]), str(rule["objetivo"])) for name, rule in data["herramientas"].items()
        }
        return cls(
            verbs={group: tuple(roots) for group, roots in data["verbos"].items()},
            tools=tools,
            free_in_web=frozenset(data.get("libres_en_web", [])),
            generic_words=frozenset(normalize(w) for w in data.get("palabras_genericas", [])),
            blocked_when_closing=frozenset(data.get("bloqueadas_al_cerrar", ["apps.launch"])),
        )

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> IntentConfig:
        """Lee la sección `intencion` de `confirmaciones.json`; si ese fichero no la trae, la del `.example`."""
        example: dict[str, Any] | None = None
        for name in ("confirmaciones.json", "confirmaciones.example.json"):
            file = directory / name
            if not file.is_file():
                continue
            data = json.loads(file.read_text(encoding="utf-8"))
            if "intencion" in data:
                return cls.from_dict(data["intencion"])
            example = example or data
        raise FileNotFoundError(f"No hay sección 'intencion' en confirmaciones*.json de {directory}")


@dataclass(frozen=True)
class Verdict:
    allowed: bool
    target: str = ""  # a dónde va, para mostrárselo al usuario
    reason: str = ""
    blocked: bool = False  # no se ejecuta ni preguntando: el mensaje pide lo contrario


def _similar(a: str, b: str) -> bool:
    return a == b or (min(len(a), len(b)) >= 5 and SequenceMatcher(None, a, b).ratio() >= 0.85)


def _mentions(token: str, words: set[str]) -> bool:
    return any(_similar(token, w) for w in words)


def _ok(output: str) -> dict[str, Any] | None:
    """El JSON de un resultado correcto (ok distinto de false y sin pedir confirmación), o None."""
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or data.get("ok") is False or data.get("requiere_confirmacion") is True:
        return None
    return data


class IntentGate:
    def __init__(self, config: IntentConfig) -> None:
        self._config = config
        self._mandate: set[str] = set()
        self._known_apps: dict[str, str] = {}
        self._web_hosts: set[str] = set()
        self._web_ok = False
        self._referent: tuple[str, str] | None = (
            None  # ("app" | "web", nombre o dominio) de lo último de lo que se habló
        )

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> IntentGate:
        return cls(IntentConfig.load(directory))

    def reset(self) -> None:
        self._mandate = set()
        self._known_apps.clear()
        self._web_hosts.clear()
        self._web_ok = False
        self._referent = None

    def begin_turn(self, user_text: str) -> None:
        """`user_text` es SOLO lo que el usuario escribió (más, si responde a una pregunta, la pregunta)."""
        words = normalize(user_text).split()
        self._mandate = set(words)
        self._remember_named_app(words)
        if self._referent is not None and self._has_reference(words):
            self._mandate |= self._referent_words()

    def _is_verb_word(self, word: str) -> bool:
        for roots in self._config.verbs.values():
            for root in roots:
                if (word.startswith(root[:-1]) if root.endswith("*") else word == root) and len(word) >= 2:
                    return True
        return False

    def action_target(self, user_text: str) -> str | None:
        """Si el mensaje pide una acción (abrir, cerrar, buscar, poner...), el objetivo sin el verbo ni los rellenos del
        principio ni del final ("ábreme la calculadora" → "calculadora"); None si no pide ninguna o no nombra nada."""
        words = normalize(user_text).split()
        if not any(self._is_verb_word(w) for w in words):
            return None
        start = 0
        while start < len(words) and (self._is_verb_word(words[start]) or words[start] in FILLERS):
            start += 1
        end = len(words)
        while end > start and words[end - 1] in FILLERS:
            end -= 1
        target = words[start:end]
        if not target or all(w in PRONOUNS for w in target):
            return None  # "ábrelo", "abre eso": no hay nada que buscar (lo resuelve la conversación)
        return " ".join(target)

    def _has_reference(self, words: list[str]) -> bool:
        """¿Hay un pronombre ("eso", "la misma") o un verbo con pronombre pegado ("ciérralo", "ábrelo")?"""
        for word in words:
            if word in PRONOUNS:
                return True
            for clitic in CLITICS:
                if word.endswith(clitic) and self._is_verb_word(word[: -len(clitic)]):
                    return True
        return False

    def _referent_words(self) -> set[str]:
        assert self._referent is not None
        kind, text = self._referent
        if kind == "app":
            return set(normalize(text).split())
        return {text, *normalize(text.split(".")[0]).split()}  # youtube.com → youtube.com y youtube

    def _remember_named_app(self, words: list[str]) -> None:
        """Si el mensaje nombra una app ya vista en la conversación, pasa a ser aquello de lo que se habla."""
        latest = -1
        for name in self._known_apps.values():
            tokens = [t for t in normalize(name).split() if t not in self._config.generic_words] or normalize(
                name
            ).split()
            positions = [i for i, w in enumerate(words) if any(_similar(t, w) for t in tokens)]
            if positions and self._names_app(name) and max(positions) > latest:
                latest, self._referent = max(positions), ("app", name)

    def _has_verb(self, groups: tuple[str, ...]) -> bool:
        for group in groups:
            for root in self._config.verbs.get(group, ()):
                if root.endswith("*"):
                    if any(w.startswith(root[:-1]) for w in self._mandate):
                        return True
                elif root in self._mandate:
                    return True
        return False

    def _names_app(self, name: str) -> bool:
        tokens = normalize(name).split()
        significant = [t for t in tokens if t not in self._config.generic_words] or tokens
        if not significant:
            return False
        hits = sum(1 for t in significant if _mentions(t, self._mandate))
        return hits >= max(1, (len(significant) + 1) // 2)

    def _names_host(self, host: str) -> bool:
        domain = registrable(host)
        if domain in self._mandate or host in self._mandate:
            return True
        tokens = normalize(domain.split(".")[0]).split()  # "tienda-rara" son dos palabras al normalizar el mensaje
        return bool(tokens) and all(_mentions(t, self._mandate) for t in tokens)

    def _names_site(self, site: str) -> bool:
        host = host_of(site)
        if host:
            return self._names_host(host)
        tokens = normalize(site).split()
        return bool(tokens) and all(_mentions(t, self._mandate) for t in tokens)

    def _only_closing(self) -> bool:
        """El mensaje pide cerrar y no pide abrir nada: abrir sería lo contrario de lo pedido."""
        return self._has_verb(("cerrar",)) and not self._has_verb(("abrir",))

    def check(self, tool: str, args: dict[str, Any]) -> Verdict:
        if tool in self._config.blocked_when_closing and self._only_closing():
            return Verdict(False, str(args.get("id", "")), "has pedido cerrar, no abrir", blocked=True)
        rule = self._config.tools.get(tool)
        if rule is None:
            if tool in self._config.free_in_web and not self._web_ok:
                return Verdict(False, "navegador", "el modelo actúa en una web que no se ha pedido")
            return Verdict(True)
        verbs_ok = self._has_verb(rule.verbs)
        if rule.target == "app":
            app_id = str(args.get("id", ""))
            name = self._known_apps.get(app_id) or (None if ID_LIKE.match(app_id) else app_id)  # nombre en vez de id
            ok = verbs_ok and name is not None and self._names_app(name)
            return Verdict(ok, name or app_id, "" if ok else "no lo has pedido en tu mensaje")
        if rule.target == "url":
            url = str(args.get("url", ""))
            host = host_of(url)
            if not host:
                return Verdict(False, url, "no es una web http/https")
            if registrable(host) in self._web_hosts:
                return Verdict(True, url)
            ok = verbs_ok and self._names_host(host)
            return Verdict(ok, url, "" if ok else f"{registrable(host)} no es una web que hayas pedido")
        site = str(args.get("sitio", ""))
        ok = verbs_ok and self._names_site(site)
        return Verdict(ok, site, "" if ok else "esa web no la has nombrado en tu mensaje")

    def destination_allowed(self, url: str) -> bool:
        """¿Esa web la ha pedido el usuario, o ya la confirmó o se está trabajando en ella en esta conversación?"""
        host = host_of(url)
        return bool(host) and (registrable(host) in self._web_hosts or self._names_host(host))

    def remember_destination(self, url: str) -> None:
        if host := host_of(url):
            self._web_hosts.add(registrable(host))

    def observe(self, tool: str, args: dict[str, Any], output: str) -> None:
        """Aprende de lo que se ejecutó: qué apps existen (para saber su nombre) y en qué webs se está trabajando."""
        data = _ok(output)
        if data is None:
            return
        if tool == "apps.search":
            for item in (data.get("resultados") or []) + (data.get("opciones") or []):
                known = isinstance(item, dict) and item.get("id") and item.get("nombre")
                if known and len(self._known_apps) < MAX_KNOWN_APPS:
                    self._known_apps[str(item["id"])] = str(item["nombre"])
                    if self._names_app(str(item["nombre"])):  # la nombró el usuario en su mensaje
                        self._referent = ("app", str(item["nombre"]))
            return
        rule = self._config.tools.get(tool)
        if rule is not None and rule.target == "app":  # se ejecutó por orden del usuario: de eso se habla ahora
            app_id = str(args.get("id", ""))
            name = self._known_apps.get(app_id) or (None if ID_LIKE.match(app_id) else app_id)
            if name:
                self._referent = ("app", name)
        if tool in self._config.free_in_web:
            self._web_ok = True
        elif rule is not None and rule.target in ("url", "sitio"):
            urls = [args.get("url", ""), data.get("url", "")]
            for url in urls:
                if host := host_of(str(url)):
                    self._web_hosts.add(registrable(host))
                    self._referent = ("web", registrable(host))
            self._web_ok = True
