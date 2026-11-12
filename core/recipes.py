"""Recetas aprendidas: secuencias de herramientas que ya salieron bien, reutilizables sin el planificador.

Una receta es:
- un **disparador**: la frase del usuario normalizada, con huecos donde iban los datos que cambian
  ("<abr*> {nombre}", "<busc*> en {sitio} {consulta}", "<dame> el video mas visto del canal {canal}");
- unos **pasos**: llamadas a herramientas con esos huecos en los argumentos. En el navegador no se guarda el `ref`
  (cambia en cada página) sino un **selector** estable: rol, nombre (que puede llevar un hueco: el enlace del canal) y la
  forma del enlace (`/@`, `/watch`), o la posición ("el primero" de los vídeos);
- la **comprobación** de cada paso (lanzada, cerrada, verificado, la página cambió), igual que salió la primera vez;
- las **estadísticas**: éxitos, fallos, fallos seguidos (al llegar al tope se desactiva) y última vez.

Los verbos se igualan por su raíz de `confirmaciones.json` (abre, ábreme y abrir son `<abr*>`), pero no entre raíces
distintas: "pon música" no dispara una receta aprendida con "abre Spotify". Un artículo justo antes de un hueco es
opcional ("cierra la calculadora" sirve para "cierra el bloc de notas").

Nada de aquí se salta la seguridad: el agente ejecuta cada paso con la misma puerta de intención, las mismas
confirmaciones y los mismos bloqueos que si lo hubiera propuesto el modelo.
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit

from hands.apps.models import normalize

CONFIG_DIR = Path("configuracion")
SLOT = re.compile(r"\{(\w+)\}")
ARTICLES = frozenset({"el", "la", "los", "las", "un", "una", "al", "del", "lo"})
# Argumentos cuyo valor puede salir de lo que dice el usuario (y convertirse en hueco). Los demás son fijos.
SLOT_ARGS = frozenset({"nombre", "consulta", "sitio", "texto", "url", "id"})
BROWSER_TARGETED = frozenset({"browser.click", "browser.type"})
_PAGE_LINE = re.compile(r'^\[(?P<ref>[\w-]+)\] (?P<role>[\w-]+)(?: "(?P<name>.*)")?(?: -> (?P<href>\S+))?(?:  \(.*)?$')
_EDGE_PUNCT = " ,.;:!?¿¡\"'«»()"


@dataclass(frozen=True)
class RecipeConfig:
    max_streak: int = 3
    alias_failures: int = 2
    filler: tuple[tuple[str, ...], ...] = ()
    corrections: frozenset[str] = frozenset()
    ordinals: dict[str, int] = field(default_factory=dict)
    read_only: frozenset[str] = frozenset({"apps.search", "browser.snapshot", "browser.current", "media.sessions"})

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> RecipeConfig:
        for name in ("recetas.json", "recetas.example.json"):
            file = directory / name
            if file.is_file():
                data = json.loads(file.read_text(encoding="utf-8"))
                return cls(
                    int(data.get("fallos_seguidos_para_desactivar", 3)),
                    int(data.get("fallos_para_olvidar_alias", 2)),
                    tuple(
                        sorted((tuple(normalize(f).split()) for f in data.get("relleno", [])), key=len, reverse=True)
                    ),
                    frozenset(normalize(c) for c in data.get("correcciones", [])),
                    {normalize(k): int(v) for k, v in data.get("ordinales", {}).items()},
                    frozenset(data.get("solo_lectura", cls.read_only)),
                )
        return cls()

    def is_correction(self, text: str) -> bool:
        """¿El mensaje corrige lo anterior ("no, eso no", "te has equivocado")? Basta con que empiece así."""
        words = normalize(text).split()
        return any(words[: len(c.split())] == c.split() for c in self.corrections if c)

    def ordinal(self, text: str) -> int | None:
        found = [self.ordinals[w] for w in normalize(text).split() if w in self.ordinals]
        return found[0] if len(found) == 1 else None


# --- frases: tokens y disparadores ---------------------------------------------------------------------------------


@dataclass(frozen=True)
class Token:
    canon: str  # "<abr*>" si es un verbo conocido; si no, la palabra normalizada
    norm: str
    word: int  # índice de la palabra original de la que sale


VerbRoot = Callable[[str], str | None]


def tokenize(text: str, verb_root: VerbRoot, filler: Iterable[tuple[str, ...]] = ()) -> tuple[list[Token], list[str]]:
    words = text.split()
    tokens = [
        Token(f"<{root}>" if (root := verb_root(n)) else n, n, i)
        for i, w in enumerate(words)
        for n in normalize(w).split()
    ]
    fillers = list(filler)
    changed = True
    while changed and tokens:
        changed = False
        for f in fillers:
            if f and [t.norm for t in tokens[: len(f)]] == list(f):
                tokens, changed = tokens[len(f) :], True
            if f and len(tokens) >= len(f) and [t.norm for t in tokens[-len(f) :]] == list(f):
                tokens, changed = tokens[: -len(f)], True
    return tokens, words


def _find_span(tokens: list[Token], value: str, taken: set[int]) -> tuple[int, int] | None:
    wanted = normalize(value).split()
    if not wanted:
        return None
    norms = [t.norm for t in tokens]
    for start in range(len(tokens) - len(wanted) + 1):
        span = range(start, start + len(wanted))
        if norms[start : start + len(wanted)] == wanted and not taken & set(span):
            return start, start + len(wanted)
    return None


def _slot_regex(trigger: str) -> re.Pattern[str]:
    pieces = trigger.split(" ")
    parts: list[str] = []
    for i, piece in enumerate(pieces):
        slot = SLOT.fullmatch(piece)
        if slot is None:
            parts.append(re.escape(piece) + " ")
            continue
        nxt_is_slot = i + 1 < len(pieces) and SLOT.fullmatch(pieces[i + 1]) is not None
        body = r"\S+" if nxt_is_slot else r"\S+(?: \S+)*?"
        articles = "|".join(sorted(ARTICLES))
        parts.append(f"(?:(?:{articles}) )?(?P<{slot.group(1)}>{body}) ")
    return re.compile("^" + "".join(parts) + "$")


def _value(tokens: list[Token], words: list[str], a: int, b: int) -> str:
    """El texto original (con mayúsculas y acentos) de los tokens [a, b), o los normalizados si no casa por palabras."""
    first, last = tokens[a].word, tokens[b - 1].word
    own = [t for t in tokens if first <= t.word <= last]
    if own and own[0] is tokens[a] and own[-1] is tokens[b - 1]:
        return " ".join(words[first : last + 1]).strip(_EDGE_PUNCT)
    return " ".join(t.norm for t in tokens[a:b])


def match_trigger(trigger: str, tokens: list[Token], words: list[str]) -> dict[str, str] | None:
    """Los valores de los huecos si la frase encaja con el disparador; None si no."""
    canon = "".join(t.canon + " " for t in tokens)
    found = _slot_regex(trigger).fullmatch(canon)
    if found is None:
        return None
    starts: list[int] = []
    pos = 0
    for t in tokens:
        starts.append(pos)
        pos += len(t.canon) + 1
    values: dict[str, str] = {}
    for name in SLOT.findall(trigger):
        a_char, b_char = found.span(name)
        a = starts.index(a_char)
        b = sum(1 for s in starts if s < b_char)
        values[name] = _value(tokens, words, a, b)
    return values


def literal_count(trigger: str) -> int:
    return sum(1 for p in trigger.split(" ") if not SLOT.fullmatch(p))


# --- páginas: selectores estables en vez de refs -------------------------------------------------------------------


@dataclass(frozen=True)
class PageItem:
    ref: str
    role: str
    name: str
    href: str


def parse_page(text: str) -> list[PageItem]:
    items = []
    for line in text.splitlines():
        found = _PAGE_LINE.match(line.strip())
        if found:
            items.append(PageItem(found["ref"], found["role"], found["name"] or "", found["href"] or ""))
    return items


def shape(href: str) -> str:
    """La forma de un enlace, sin lo que cambia: `/@hrom` → `/@`, `/watch?v=x` → `/watch`, `/@hrom/videos` → `/@`."""
    path = urlsplit(href).path if href else ""
    if path.startswith("/@"):
        return "/@"
    first = path.strip("/").split("/")[0] if path.strip("/") else ""
    return f"/{first}" if first else ""


@dataclass(frozen=True)
class Target:
    role: str
    name: str  # normalizado; puede llevar un hueco ("{canal}"); vacío si se elige por posición
    shape: str = ""
    ordinal: int = 0  # 1 = el primero con ese rol y forma; 0 = por nombre

    def describe(self) -> str:
        if self.ordinal:
            return f"{self.role} n.º {self.ordinal}{f' ({self.shape})' if self.shape else ''}"
        return f'{self.role} "{self.name}"'


def target_from(page: str, ref: str, slots: dict[str, str], ordinal_hint: int | None) -> Target | None:
    """Cómo volver a encontrar el elemento `ref` de esta página en otra visita. `slots`: valor normalizado → hueco."""
    items = parse_page(page)
    item = next((i for i in items if i.ref == ref), None)
    if item is None:
        return None
    name = normalize(item.name.rstrip("…"))
    form = shape(item.href)
    for value, slot in sorted(slots.items(), key=lambda kv: -len(kv[0])):
        if value and f" {value} " in f" {name} ":
            return Target(item.role, f" {name} ".replace(f" {value} ", f" {{{slot}}} ").strip(), form)
    if ordinal_hint:
        same = [i for i in items if i.role == item.role and shape(i.href) == form]
        return Target(item.role, "", form, same.index(item) + 1)
    return Target(item.role, name, form)


def resolve_target(target: Target, page: str, values: dict[str, str]) -> str | None:
    """El ref del elemento en la página actual, o None si no está."""
    items = [
        i for i in parse_page(page) if i.role == target.role and (not target.shape or shape(i.href) == target.shape)
    ]
    if target.ordinal:
        return items[target.ordinal - 1].ref if len(items) >= target.ordinal else None
    wanted = fill_text(target.name, {k: normalize(v) for k, v in values.items()})
    names = [(i, normalize(i.name.rstrip("…"))) for i in items]
    exact = next((i.ref for i, n in names if n == wanted), None)
    if exact is not None or not SLOT.search(target.name):
        return exact
    return next((i.ref for i, n in names if f" {wanted} " in f" {n} "), None)


# --- recetas -------------------------------------------------------------------------------------------------------


@dataclass
class RecipeStep:
    tool: str  # nombre MCP
    args: dict[str, Any]
    target: Target | None = None
    checks: tuple[str, ...] = ()
    asked: bool = False  # al aprenderla hubo que confirmarlo: al repetirla se vuelve a preguntar igual

    def as_dict(self) -> dict[str, Any]:
        return {
            "tool": self.tool,
            "args": self.args,
            "target": asdict(self.target) if self.target else None,
            "checks": list(self.checks),
            "asked": self.asked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecipeStep:
        target = Target(**data["target"]) if data.get("target") else None
        args, checks = dict(data.get("args") or {}), tuple(data.get("checks") or ())
        return cls(str(data["tool"]), args, target, checks, bool(data.get("asked")))

    def describe(self) -> str:
        shown = ", ".join(f"{k}={v!r}" for k, v in self.args.items())
        where = f" → {self.target.describe()}" if self.target else ""
        return f"{self.tool}({shown}){where}"


@dataclass
class Recipe:
    trigger: str
    example: str  # la frase con la que se aprendió, para enseñarla en /recetas
    steps: list[RecipeStep]
    origin: str = "auto"  # "auto" (de un turno que salió bien) o "enseñada" (/aprender)
    id: int | None = None
    successes: int = 0
    failures: int = 0
    streak: int = 0  # fallos seguidos
    enabled: bool = True
    last_used: str = ""

    @property
    def slots(self) -> list[str]:
        return SLOT.findall(self.trigger)


def fill_text(text: str, values: dict[str, str]) -> str:
    return SLOT.sub(lambda m: values.get(m.group(1), m.group(0)), text)


def fill_args(args: dict[str, Any], values: dict[str, str]) -> dict[str, Any]:
    filled: dict[str, Any] = {}
    for key, value in args.items():
        if isinstance(value, str) and SLOT.search(value):
            urlish = key == "url"
            filled[key] = fill_text(value, {k: quote(v) if urlish else v for k, v in values.items()})
        else:
            filled[key] = value
    return filled


def _json(output: str) -> dict[str, Any]:
    try:
        data = json.loads(output)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def checks_for(tool: str, output: str) -> tuple[str, ...]:
    """Qué demostró el resultado la primera vez: lo mismo se exige al repetir."""
    data = _json(output)
    found = [key for key in ("lanzada", "cerrada", "verificado") if data.get(key) is True]
    if tool in BROWSER_TARGETED and (data.get("url_cambio") is True or data.get("cambio_visible") is True):
        found.append("cambio")
    return tuple(found)


def passes(step: RecipeStep, output: str) -> bool:
    if output.startswith(("ERROR:", "DENEGADO")):
        return False
    data = _json(output)
    if data.get("ok") is False:
        return False
    for check in step.checks:
        if check == "cambio":
            if data.get("url_cambio") is not True and data.get("cambio_visible") is not True:
                return False
        elif data.get(check) is not True:
            return False
    return True


@dataclass(frozen=True)
class Recorded:
    """Un paso tal cual se ejecutó, con el texto del usuario de su turno (para "el primero")."""

    tool: str
    args: dict[str, Any]
    output: str
    said: str = ""
    asked: bool = False


def _launch_as_open(steps: list[Recorded]) -> list[Recorded]:
    """apps.search(X) + apps.launch(id del ganador) es lo mismo que apps.open(X): se guarda así, que vale para otra X."""
    out: list[Recorded] = []
    last_search: Recorded | None = None
    for step in steps:
        if step.tool == "apps.search":
            last_search = step
        elif step.tool == "apps.launch" and last_search is not None:
            winner = _json(last_search.output).get("id_ganador")
            if winner and winner == step.args.get("id"):
                nombre = {"nombre": last_search.args.get("nombre", "")}
                out.append(Recorded("apps.open", nombre, '{"lanzada": true}', step.said, step.asked))
                continue
        out.append(step)
    return out


# Cómo se llama el hueco si la palabra de antes no lo dice ("del canal {canal}"): según el argumento del que sale.
SLOT_NAMES = {"nombre": "app", "id": "app"}


def _slot_name(tokens: list[Token], start: int, fallback: str, used: set[str], in_slot: set[int]) -> str:
    before = tokens[start - 1] if start > 0 and start - 1 not in in_slot else None
    name = SLOT_NAMES.get(fallback, fallback)
    plain = before is not None and not before.canon.startswith("<") and before.norm not in ARTICLES
    if before is not None and plain and len(before.norm) > 2 and before.norm.isidentifier():
        name = before.norm
    base, n = name, 2
    while name in used:
        name, n = f"{base}{n}", n + 1
    used.add(name)
    return name


def build_recipe(
    phrase: str,
    recorded: list[Recorded],
    verb_root: VerbRoot,
    config: RecipeConfig,
    origin: str = "auto",
) -> Recipe | None:
    """La receta de una frase y los pasos que la resolvieron; None si no hay nada que repetir o no se puede."""
    steps_in = _launch_as_open(recorded)
    tokens, _words = tokenize(phrase, verb_root, config.filler)
    if not tokens:
        return None

    # 1) huecos: valores de los argumentos (y nombres de los elementos pulsados) que el usuario dijo tal cual
    candidates: list[tuple[str, str]] = []  # (valor, nombre de argumento)
    page = ""
    for step in steps_in:
        data = _json(step.output)
        if step.tool in BROWSER_TARGETED:
            item = next((i for i in parse_page(page) if i.ref == step.args.get("ref")), None)
            if item is not None:
                candidates.append((item.name, "valor"))
        for key, value in step.args.items():
            if key in SLOT_ARGS and isinstance(value, str):
                candidates.append((value, key))
        if isinstance(data.get("pagina"), str):
            page = data["pagina"]
    taken: set[int] = set()
    found: list[tuple[int, int, str, str]] = []  # inicio, fin, argumento, valor normalizado
    for value, key in sorted(candidates, key=lambda c: -len(normalize(c[0]))):
        norm = normalize(value)
        if not norm or any(norm == f[3] for f in found):
            continue
        span = _find_span(tokens, value, taken)
        if span is None:
            continue
        taken |= set(range(*span))
        found.append((span[0], span[1], key, norm))
    used: set[str] = set()
    spans = [(a, b, _slot_name(tokens, a, key, used, taken), norm) for a, b, key, norm in sorted(found)]
    slots = {s[3]: s[2] for s in spans}

    # 2) pasos con huecos; en el navegador, selector en vez de ref
    steps: list[RecipeStep] = []
    page = ""
    for step in steps_in:
        data = _json(step.output)
        if step.tool not in config.read_only:
            args = {k: v for k, v in step.args.items() if k != "ref"}
            for key, value in list(args.items()):
                if key in SLOT_ARGS and isinstance(value, str):
                    args[key] = _with_slots(value, slots)
            target = None
            if step.tool in BROWSER_TARGETED:
                target = target_from(page, str(step.args.get("ref", "")), slots, config.ordinal(step.said))
                if target is None:
                    return None
            steps.append(RecipeStep(step.tool, args, target, checks_for(step.tool, step.output), step.asked))
        if isinstance(data.get("pagina"), str):
            page = data["pagina"]
    if not steps:
        return None

    # 3) disparador: la frase con los huecos; el artículo justo antes de un hueco no se guarda (es opcional)
    pieces: list[str] = []
    i = 0
    starts = {s[0]: s for s in spans}
    while i < len(tokens):
        if i in starts:
            if pieces and pieces[-1] in ARTICLES:
                pieces.pop()
            pieces.append(f"{{{starts[i][2]}}}")
            i = starts[i][1]
        else:
            pieces.append(tokens[i].canon)
            i += 1
    return Recipe(" ".join(pieces), phrase.strip(), steps, origin)


def _with_slots(value: str, slots: dict[str, str]) -> str:
    norm = normalize(value)
    for slot_value, slot in sorted(slots.items(), key=lambda kv: -len(kv[0])):
        if norm == slot_value:
            return f"{{{slot}}}"
    out = value
    for slot_value, slot in sorted(slots.items(), key=lambda kv: -len(kv[0])):
        pattern = re.compile(r"\b" + r"\W+".join(map(re.escape, slot_value.split())) + r"\b", re.IGNORECASE)
        out = pattern.sub(f"{{{slot}}}", out)
    return out


# --- almacén -------------------------------------------------------------------------------------------------------

_SCHEMA = """
CREATE TABLE IF NOT EXISTS recipes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    trigger TEXT NOT NULL UNIQUE,
    example TEXT NOT NULL,
    steps TEXT NOT NULL,
    origin TEXT NOT NULL DEFAULT 'auto',
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    streak INTEGER NOT NULL DEFAULT 0,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    last_used TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS aliases (
    alias TEXT PRIMARY KEY,
    app_id TEXT NOT NULL,
    app_name TEXT NOT NULL,
    successes INTEGER NOT NULL DEFAULT 0,
    failures INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


def alias_key(text: str) -> str:
    """Cómo se guarda y se busca un alias: normalizado y sin artículos delante ("el editor" → "editor")."""
    words = normalize(text).split()
    while words and words[0] in ARTICLES:
        words = words[1:]
    return " ".join(words)


@dataclass(frozen=True)
class Alias:
    alias: str
    app_id: str
    app_name: str
    successes: int = 0
    failures: int = 0


class RecipeStore:
    """Recetas y alias en SQLite (la misma base que las plantillas de búsqueda de hands, en tablas propias)."""

    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path))
        self._db.executescript(_SCHEMA)
        self._db.commit()

    def close(self) -> None:
        self._db.close()

    @staticmethod
    def _row(row: tuple[Any, ...]) -> Recipe:
        rid, trigger, example, steps, origin, ok, ko, streak, enabled, last = row
        parsed = [RecipeStep.from_dict(s) for s in json.loads(steps)]
        return Recipe(trigger, example, parsed, origin, rid, ok, ko, streak, bool(enabled), last)

    def all(self) -> list[Recipe]:
        rows = self._db.execute(
            "SELECT id, trigger, example, steps, origin, successes, failures, streak, enabled, last_used "
            "FROM recipes ORDER BY id"
        ).fetchall()
        return [self._row(r) for r in rows]

    def get(self, recipe_id: int) -> Recipe | None:
        return next((r for r in self.all() if r.id == recipe_id), None)

    def save(self, recipe: Recipe) -> int:
        """Guarda la receta; si ya había una con el mismo disparador, la sustituye con las estadísticas a cero."""
        steps = json.dumps([s.as_dict() for s in recipe.steps], ensure_ascii=False)
        self._db.execute(
            "INSERT INTO recipes (trigger, example, steps, origin, successes) VALUES (?, ?, ?, ?, 1) "
            "ON CONFLICT(trigger) DO UPDATE SET example = excluded.example, steps = excluded.steps, "
            "origin = excluded.origin, successes = 1, failures = 0, streak = 0, enabled = 1, "
            "created_at = CURRENT_TIMESTAMP",
            (recipe.trigger, recipe.example, steps, recipe.origin),
        )
        self._db.commit()
        row = self._db.execute("SELECT id FROM recipes WHERE trigger = ?", (recipe.trigger,)).fetchone()
        return int(row[0])

    def has_trigger(self, trigger: str, *, enabled_only: bool = True) -> bool:
        query = "SELECT 1 FROM recipes WHERE trigger = ?" + (" AND enabled = 1" if enabled_only else "")
        return self._db.execute(query, (trigger,)).fetchone() is not None

    def success(self, recipe_id: int) -> None:
        self._db.execute(
            "UPDATE recipes SET successes = successes + 1, streak = 0, last_used = CURRENT_TIMESTAMP WHERE id = ?",
            (recipe_id,),
        )
        self._db.commit()

    def failure(self, recipe_id: int, max_streak: int) -> bool:
        """Suma un fallo; si lleva `max_streak` seguidos, la desactiva. Devuelve True si queda desactivada."""
        self._db.execute(
            "UPDATE recipes SET failures = failures + 1, streak = streak + 1, last_used = CURRENT_TIMESTAMP, "
            "enabled = CASE WHEN streak + 1 >= ? THEN 0 ELSE enabled END WHERE id = ?",
            (max_streak, recipe_id),
        )
        self._db.commit()
        row = self._db.execute("SELECT enabled FROM recipes WHERE id = ?", (recipe_id,)).fetchone()
        return row is not None and not row[0]

    def delete(self, recipe_id: int) -> bool:
        deleted = self._db.execute("DELETE FROM recipes WHERE id = ?", (recipe_id,)).rowcount
        self._db.commit()
        return deleted > 0

    def find(self, tokens: list[Token], words: list[str]) -> tuple[Recipe, dict[str, str]] | None:
        """La receta activa que encaja con la frase: la más concreta (más palabras fijas) y, a igualdad, la más fiable."""
        found: list[tuple[int, float, Recipe, dict[str, str]]] = []
        for recipe in self.all():
            if not recipe.enabled:
                continue
            values = match_trigger(recipe.trigger, tokens, words)
            if values is None or any(not v for v in values.values()):
                continue
            rate = recipe.successes / max(1, recipe.successes + recipe.failures)
            found.append((literal_count(recipe.trigger), rate, recipe, values))
        if not found:
            return None
        found.sort(key=lambda f: (-f[0], -f[1]))
        return found[0][2], found[0][3]

    # --- alias -----------------------------------------------------------------------------------------------------

    def alias(self, name: str) -> Alias | None:
        row = self._db.execute(
            "SELECT alias, app_id, app_name, successes, failures FROM aliases WHERE alias = ?", (alias_key(name),)
        ).fetchone()
        return Alias(*row) if row else None

    def aliases(self) -> list[Alias]:
        rows = self._db.execute(
            "SELECT alias, app_id, app_name, successes, failures FROM aliases ORDER BY alias"
        ).fetchall()
        return [Alias(*r) for r in rows]

    def save_alias(self, name: str, app_id: str, app_name: str) -> None:
        self._db.execute(
            "INSERT INTO aliases (alias, app_id, app_name, successes) VALUES (?, ?, ?, 1) "
            "ON CONFLICT(alias) DO UPDATE SET app_id = excluded.app_id, app_name = excluded.app_name, "
            "successes = 1, failures = 0, updated_at = CURRENT_TIMESTAMP",
            (alias_key(name), app_id, app_name),
        )
        self._db.commit()

    def alias_success(self, name: str) -> None:
        self._db.execute(
            "UPDATE aliases SET successes = successes + 1, updated_at = CURRENT_TIMESTAMP WHERE alias = ?",
            (alias_key(name),),
        )
        self._db.commit()

    def alias_failure(self, name: str, limit: int) -> bool:
        """Suma un fallo; al llegar a `limit`, el alias se olvida. Devuelve True si se ha olvidado."""
        self._db.execute(
            "UPDATE aliases SET failures = failures + 1, updated_at = CURRENT_TIMESTAMP WHERE alias = ?",
            (alias_key(name),),
        )
        gone = self._db.execute(
            "DELETE FROM aliases WHERE alias = ? AND failures >= ?", (alias_key(name), limit)
        ).rowcount
        self._db.commit()
        return gone > 0

    def delete_alias(self, name: str) -> bool:
        deleted = self._db.execute("DELETE FROM aliases WHERE alias = ?", (alias_key(name),)).rowcount
        self._db.commit()
        return deleted > 0
