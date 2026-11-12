"""LLM simulado para medir sin GPU ni Ollama: sigue un guion por orden y calcula cuánto habría tardado.

Modelo de latencia, calibrado el 2026-11-12 en el PC del aula (CPU, 6 hilos, qwen3:4b-instruct cargado) con
`prompt_eval_duration` y `eval_duration` de Ollama: leer el prompt va a ~33 tokens/s (3708 tokens en frío: 113 s; el
mismo prefijo en caché: 0,3 s) y generar a ~3,4 tokens/s (21 tokens: 6,4 s).

    segundos = SOBRECARGA + tokens_nuevos / VELOCIDAD_PROMPT + tokens_generados / VELOCIDAD_GENERACION

`tokens_nuevos` imita la caché de prefijo de Ollama: el prompt se compara con el de la llamada anterior (más lo que se
generó en ella) y solo cuenta lo que hay a partir de la primera diferencia. Como la plantilla de Qwen pone el prompt de
sistema, después las herramientas y después la conversación, cambiar el juego de herramientas o recortar el principio
del historial obliga a recalcular todo lo que va detrás. No se duerme: el tiempo se calcula, así los tests van rápidos.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field

from core.llm.base import LLMResponse, LLMUsage, Message, ToolCall, ToolSpec

OVERHEAD_SECONDS = 0.3
PROMPT_TOKENS_PER_SECOND = 33.0
OUTPUT_TOKENS_PER_SECOND = 3.4
CHARS_PER_TOKEN = 3.5

Policy = Callable[[list[Message], list[ToolSpec]], LLMResponse]


def render(messages: list[Message], tools: list[ToolSpec]) -> str:
    """Aproximación del prompt que construye la plantilla de Qwen3: sistema, herramientas y conversación, en ese orden."""
    system = next((m.content for m in messages if m.role == "system"), "")
    parts = [f"<|im_start|>system\n{system}"]
    if tools:
        parts.append("# Tools\n<tools>")
        parts += [json.dumps({"name": t.name, "description": t.description, "parameters": t.parameters}) for t in tools]
        parts.append("</tools><|im_end|>")
    for m in messages:
        if m.role == "system":
            continue
        calls = "".join(f"<tool_call>{json.dumps({'name': c.name, 'arguments': c.arguments})}" for c in m.tool_calls)
        parts.append(f"<|im_start|>{m.role}\n{m.content}{calls}<|im_end|>")
    return "\n".join(parts)


def output_text(response: LLMResponse) -> str:
    calls = "".join(
        f"<tool_call>{json.dumps({'name': c.name, 'arguments': c.arguments}, ensure_ascii=False)}</tool_call>"
        for c in response.tool_calls
    )
    return response.content + calls


def tokens(text: str) -> int:
    return round(len(text) / CHARS_PER_TOKEN)


def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


@dataclass
class SimCall:
    prompt_tokens: int
    new_tokens: int
    output_tokens: int
    seconds: float
    tools: int


@dataclass
class SimulatedLLM:
    """`policy` decide qué contesta el modelo; aquí solo se mide y se simula la caché."""

    policy: Policy
    calls: list[SimCall] = field(default_factory=list)
    _cached: str = ""

    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        prompt = render(messages, tools)
        response = self.policy(messages, tools)
        out = output_text(response)
        new = tokens(prompt[_common_prefix(prompt, self._cached) :])
        produced = max(1, tokens(out))
        reading, writing = new / PROMPT_TOKENS_PER_SECOND, produced / OUTPUT_TOKENS_PER_SECOND
        seconds = OVERHEAD_SECONDS + reading + writing
        self.calls.append(SimCall(tokens(prompt), new, produced, seconds, len(tools)))
        self._cached = prompt + out
        usage = LLMUsage(tokens(prompt), produced, prompt_seconds=reading, output_seconds=writing)
        return LLMResponse(response.content, response.tool_calls, usage)

    @property
    def seconds(self) -> float:
        return sum(c.seconds for c in self.calls)


# --- guiones ------------------------------------------------------------------------------------------------------
# Cada guion es lo que hace el modelo por el camino bueno (el que el 4B sigue cuando acierta): una lista de pasos que
# se elige según las herramientas que se le enseñan. Un paso es una llamada (con argumentos fijos o sacados del último
# resultado) o el texto final.

Step = tuple[str, object]  # ("call", (nombre, args | fn)) | ("say", texto)


def _turn(messages: list[Message]) -> list[Message]:
    last_user = max(i for i, m in enumerate(messages) if m.role == "user")
    return messages[last_user + 1 :]


def _last_tool(messages: list[Message]) -> str:
    return next((m.content for m in reversed(_turn(messages)) if m.role == "tool"), "")


def ref_of(page: str, predicate: Callable[[str], bool]) -> str:
    """El ref de la primera línea de la página que cumpla `predicate` (así elige un modelo lo que ve)."""
    for line in page.splitlines():
        if line.startswith("[e") and predicate(line):
            return line[1 : line.index("]")]
    return "e999"


def scripted(plan: Callable[[set[str]], list[Step]]) -> Policy:
    def policy(messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        steps = plan({t.name for t in tools})
        done = sum(1 for m in _turn(messages) if m.role == "assistant" and m.tool_calls)
        kind, value = steps[min(done, len(steps) - 1)]
        if kind == "say":
            return LLMResponse(str(value))
        name, args = value  # type: ignore[misc]
        arguments = args(_last_tool(messages)) if callable(args) else dict(args)
        return LLMResponse("", (ToolCall(name, arguments),))

    return policy


def _winner(output: str) -> dict[str, str]:
    try:
        return {"id": str(json.loads(output).get("id_ganador", ""))}
    except json.JSONDecodeError:
        return {"id": ""}


def plan_open(app: str) -> Callable[[set[str]], list[Step]]:
    def plan(tools: set[str]) -> list[Step]:
        if "apps_open" in tools:
            return [("call", ("apps_open", {"nombre": app})), ("say", f"He abierto {app}.")]
        return [
            ("call", ("apps_search", {"nombre": app})),
            ("call", ("apps_launch", _winner)),
            ("say", f"He abierto {app}."),
        ]

    return plan


def plan_close(app: str) -> Callable[[set[str]], list[Step]]:
    return lambda _t: [("call", ("apps_close", {"id": app})), ("say", f"He cerrado {app}.")]


def plan_site_search(site: str, query: str) -> Callable[[set[str]], list[Step]]:
    return lambda _t: [
        ("call", ("web_site_search", {"sitio": site, "consulta": query})),
        ("say", f"Ahí tienes los resultados de «{query}» en {site}."),
    ]


def plan_pause() -> Callable[[set[str]], list[Step]]:
    return lambda _t: [("call", ("media_control", {"accion": "pausa", "sesion": ""})), ("say", "He pausado el vídeo.")]


def plan_most_viewed(channel: str) -> Callable[[set[str]], list[Step]]:
    def click(predicate: Callable[[str], bool]) -> Callable[[str], dict[str, str]]:
        return lambda page: {"ref": ref_of(page, predicate)}

    return lambda _t: [
        ("call", ("web_site_search", {"sitio": "YouTube", "consulta": channel})),
        ("call", ("browser_snapshot", {})),
        ("call", ("browser_click", click(lambda line: f'link "{channel}"' in line and "/@" in line))),
        ("call", ("browser_click", click(lambda line: 'tab "Vídeos"' in line))),
        ("call", ("browser_click", click(lambda line: 'button "Populares"' in line))),
        ("call", ("browser_click", click(lambda line: "/watch" in line))),
        ("say", f"He abierto el vídeo más visto del canal {channel}, tras ordenar por Populares."),
    ]


@dataclass(frozen=True)
class Order:
    text: str
    plan: Callable[[set[str]], list[Step]]


ORDERS = (
    Order("abre Spotify", plan_open("Spotify")),
    Order("cierra la calculadora", plan_close("calculadora")),
    Order("búscame en YouTube cómo hacer mochis", plan_site_search("YouTube", "cómo hacer mochis")),
    Order("pausa el vídeo de YouTube", plan_pause()),
    Order("dame el vídeo más visto del canal Hrom", plan_most_viewed("Hrom")),
)


class Director:
    """Un solo LLM simulado para toda la sesión (la caché de prefijo pasa de un turno a otro); cada orden cambia el
    guion activo."""

    def __init__(self) -> None:
        self._plan: Callable[[set[str]], list[Step]] = lambda _t: [("say", "Vale.")]
        self.llm = SimulatedLLM(self._policy)

    def use(self, order: Order) -> None:
        self._plan = order.plan

    def _policy(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        return scripted(self._plan)(messages, tools)
