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
# Por si un confirmaciones.json antiguo no trae estas listas.
DEFAULT_NOT_VERBS = ("abierto", "abierta", "abiertos", "abiertas", "cerrado", "cerrada", "cerrados", "cerradas")
DEFAULT_STATE_WORDS = ("abierto", "abierta", "abiertos", "abiertas", "encendido", "encendida", "funcionando")
DEFAULT_MODE_WORDS = ("fuerza", "forzando", "forzar", "forzado", "ya", "todo", "si", "hay", "esta", "tengo", "que")
# Herramientas que buscan y abren por nombre en un paso: se juzgan como apps.launch (un config antiguo no las lista).
OPEN_BY_NAME = ("apps.open",)
# Una URL o un dominio escrito en el mensaje ("youtube.com", "https://es.wikipedia.org/wiki/X").
WEB_ADDRESS = re.compile(
    r"https?://\S+|\b(?:[\w-]+\.)+(?:com|es|org|net|io|tv|dev|app|edu|gob|info|eu|co|me)\b(?:/\S*)?", re.IGNORECASE
)
# Conjunciones que encadenan otra orden ("abre Spotify Y pon Daft Punk").
CHAINING = frozenset({"y", "e", "luego", "despues", "tambien", "ademas", "and", "then"})


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
    not_verbs: frozenset[str] = frozenset()  # "abierto" empieza como "abr*" pero no pide abrir nada
    state_words: frozenset[str] = frozenset()  # "¿está abierto X?": pregunta, no orden
    mode_words: frozenset[str] = frozenset()  # "a la FUERZA": cómo, no qué

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> IntentConfig:
        tools = {
            name: Rule(tuple(rule["verbos"]), str(rule["objetivo"])) for name, rule in data["herramientas"].items()
        }
        blocked = set(data.get("bloqueadas_al_cerrar", ["apps.launch"]))
        if "apps.launch" in tools:
            for name in OPEN_BY_NAME:
                tools.setdefault(name, tools["apps.launch"])
                if "apps.launch" in blocked:
                    blocked.add(name)
        return cls(
            verbs={group: tuple(roots) for group, roots in data["verbos"].items()},
            tools=tools,
            free_in_web=frozenset(data.get("libres_en_web", [])),
            generic_words=frozenset(normalize(w) for w in data.get("palabras_genericas", [])),
            blocked_when_closing=frozenset(blocked),
            not_verbs=frozenset(normalize(w) for w in data.get("no_verbos", DEFAULT_NOT_VERBS)),
            state_words=frozenset(normalize(w) for w in data.get("estado", DEFAULT_STATE_WORDS)),
            mode_words=frozenset(normalize(w) for w in data.get("palabras_de_modo", DEFAULT_MODE_WORDS)),
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
        self._open_seen: list[str] = []
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

    def verb_root(self, word: str) -> str | None:
        """La raíz de `confirmaciones.json` con la que encaja la palabra ("ábreme" → "abr*"), o None si no es un verbo."""
        if word in self._config.not_verbs:
            return None
        for roots in self._config.verbs.values():
            for root in roots:
                if (word.startswith(root[:-1]) if root.endswith("*") else word == root) and len(word) >= 2:
                    return root
        return None

    def _is_verb_word(self, word: str) -> bool:
        if word in self._config.not_verbs:
            return False
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

    def site_request(self, user_text: str) -> tuple[str, str] | None:
        """Si la orden es sobre una web: (sitio o URL, consulta). La consulta va vacía si no busca nada.

        "búscame en YouTube cómo hacer mochis" → ("youtube", "como hacer mochis"); "busca mochis en youtube" →
        ("youtube", "mochis"); "abre ejemplo.com" → ("ejemplo.com", ""). Una web se reconoce por una URL o un dominio, o
        por "en <sitio>" junto a un verbo de buscar; no hay ninguna web escrita a mano."""
        url = WEB_ADDRESS.search(user_text)
        target = self.action_target(user_text) or ""
        if url is not None:
            site = url.group(0).rstrip(".,;:!?)»\"'")
            kept = [w for w in target.split() if w not in normalize(site).split() and w not in FILLERS]
            while kept and kept[-1] in ("en", "de", "del"):
                kept.pop()
            query = " ".join(kept)
            searching = any(
                self.verb_root(w) in self._config.verbs.get("buscar", ()) for w in normalize(user_text).split()
            )
            return site, query if searching else ""
        words = normalize(user_text).split()
        if not any(self.verb_root(w) in self._config.verbs.get("buscar", ()) for w in words[:3]):
            return None
        parts = target.split()
        if len(parts) >= 3 and parts[0] == "en":  # "en youtube cómo hacer mochis"
            return parts[1], " ".join(parts[2:])
        if "en" in parts[1:-1] or (len(parts) >= 3 and parts[-2] == "en"):  # "mochis en youtube"
            cut = len(parts) - 1 - parts[::-1].index("en")
            site, query = " ".join(parts[cut + 1 :]), " ".join(parts[:cut])
            if site and query and len(site.split()) <= 2:
                return site, query
        return None

    def is_compound(self, user_text: str) -> bool:
        """¿Encadena varias órdenes? Una conjunción ("y", "luego", "después") seguida más adelante de otro verbo."""
        words = normalize(user_text).split()
        for i, word in enumerate(words):
            if word in CHAINING and any(self._is_verb_word(w) for w in words[i + 1 :]):
                return True
        return False

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
        words = self._mandate - self._config.not_verbs
        for group in groups:
            for root in self._config.verbs.get(group, ()):
                if root.endswith("*"):
                    if any(w.startswith(root[:-1]) for w in words):
                        return True
                elif root in words:
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

    def asking_state(self, text: str | None = None) -> bool:
        """¿Pregunta si algo está abierto ("¿está abierto el bloc?", "busca si hay un bloc de notas abierto") en vez de
        pedir abrirlo? `text` None: el mensaje del turno actual."""
        words = set(normalize(text).split()) if text is not None else self._mandate
        if not words & self._config.state_words:
            return False
        roots = self._config.verbs.get("abrir", ())
        return not any(self.verb_root(w) in roots and w not in ("quiero", "dame") for w in words)

    def state_target(self, text: str) -> str:
        """De qué app pregunta ("busca si hay un bloc de notas abierto" → "bloc de notas"); '' si de ninguna."""
        skip = self._config.state_words | self._config.mode_words | FILLERS
        words = [w for w in normalize(text).split() if w not in skip and not self._is_verb_word(w)]
        return " ".join(words)

    def close_target(self, text: str, *, whole: bool = False) -> str | None:
        """La app que el mensaje pide cerrar, sin el modo ("cierra a la fuerza el bloc de notas" → "bloc de notas")."""
        target = self.action_target(text)
        if target is None:
            return None
        cut: list[str] = []
        for w in target.split():
            if w in CHAINING and not whole:  # «cierra paint y dime la hora»: lo de después es otra orden
                break
            cut.append(w)
        words = [w for w in cut if w not in self._config.mode_words and w not in FILLERS]
        return " ".join(words) or None

    def referent_app(self) -> str | None:
        """La app de la que se habla ("ciérralo"), si la hay."""
        return self._referent[1] if self._referent and self._referent[0] == "app" else None

    @property
    def open_seen(self) -> list[str]:
        """Las apps que la última llamada a apps.abiertas encontró abiertas."""
        return list(self._open_seen)

    def names_app(self, name: str) -> bool:
        """¿El mensaje del turno nombra esa app?"""
        return self._names_app(name)

    def check(self, tool: str, args: dict[str, Any]) -> Verdict:
        if tool in self._config.blocked_when_closing and self._only_closing():
            target = str(args.get("id") or args.get("nombre") or "")
            return Verdict(False, target, "has pedido cerrar, no abrir", blocked=True)
        if tool in self._config.blocked_when_closing and self.asking_state():
            target = str(args.get("id") or args.get("nombre") or "")
            return Verdict(False, target, "preguntas si está abierto, no pides abrirlo", blocked=True)
        rule = self._config.tools.get(tool)
        if rule is None:
            if tool in self._config.free_in_web and not self._web_ok:
                return Verdict(False, "navegador", "el modelo actúa en una web que no se ha pedido")
            return Verdict(True)
        verbs_ok = self._has_verb(rule.verbs)
        if rule.target == "app":
            app_id = str(args.get("id") or args.get("nombre") or "")
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

    def grant_alias(self, alias: str, app_id: str, name: str) -> bool:
        """El usuario llama «alias» a esa app (lo eligió él de una lista): si su mensaje dice el alias, cuenta como que
        la ha nombrado. Devuelve si se concedió."""
        words = [w for w in normalize(alias).split() if w not in FILLERS]
        if not words or not all(_mentions(w, self._mandate) for w in words):
            return False
        self._known_apps[app_id] = name
        self._mandate |= set(normalize(name).split())
        return True

    def only_closing(self) -> bool:
        """El mensaje pide cerrar y no abrir."""
        return self._only_closing()

    @property
    def in_web(self) -> bool:
        """¿Se está trabajando en una web en esta conversación?"""
        return self._web_ok or bool(self._web_hosts)

    def app_name(self, app_id: str) -> str | None:
        """El nombre de una app que ya salió en la conversación (por su id), o None."""
        return self._known_apps.get(app_id)

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
        if tool == "apps.abiertas":
            names = [str(a["nombre"]) for a in data.get("apps") or [] if isinstance(a, dict) and a.get("nombre")]
            self._open_seen = names or self._open_seen
            for name in names:
                if self._names_app(name):
                    self._referent = ("app", name)
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
        if tool in OPEN_BY_NAME and data.get("id") and data.get("nombre"):  # abrió (o propuso) una app concreta
            self._known_apps[str(data["id"])] = str(data["nombre"])
        for item in data.get("opciones") or []:
            if (
                isinstance(item, dict)
                and item.get("id")
                and item.get("nombre")
                and len(self._known_apps) < MAX_KNOWN_APPS
            ):
                self._known_apps[str(item["id"])] = str(item["nombre"])
        if rule is not None and rule.target == "app":  # se ejecutó por orden del usuario: de eso se habla ahora
            app_id = str(data.get("id") or args.get("id") or args.get("nombre") or "")
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
