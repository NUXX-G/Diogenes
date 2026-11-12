"""Tiempos de cada turno por etapa: cada llamada al modelo (con tokens), cada herramienta y el total.

Sirve para saber dónde se va el tiempo (en el PC del aula, casi todo es el modelo) y para comparar antes y después de
cada optimización. Se enseña una línea en el CLI y se guarda una línea JSON por turno en `logs/core-AAAA-MM-DD.jsonl`,
sin el texto del usuario ni lo que devuelven las herramientas: solo la ruta, los nombres y los tiempos.
"""

from __future__ import annotations

import json
import time
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

# Por dónde se resolvió el turno: atajo del router, receta aprendida, elección resuelta en código o planificador LLM.
ROUTES = ("atajo", "receta", "eleccion", "planificador", "comando")


@dataclass
class ModelCall:
    seconds: float
    input_tokens: int = 0  # todo el prompt (Ollama los cuenta aunque salgan de su caché)
    output_tokens: int = 0
    tools_sent: int = 0  # cuántas herramientas se le enseñaron
    load_seconds: float = 0.0
    prompt_seconds: float = 0.0  # leer lo que no estaba en caché
    output_seconds: float = 0.0  # generar


@dataclass
class ToolTiming:
    tool: str
    seconds: float
    ok: bool


@dataclass
class TurnMetrics:
    route: str = "planificador"
    model_calls: list[ModelCall] = field(default_factory=list)
    tools: list[ToolTiming] = field(default_factory=list)
    seconds: float = 0.0
    answer_by_template: bool = False  # la respuesta final la redactó el código, no el modelo
    recipe: int | None = None  # receta usada (o intentada) en el turno
    recipe_note: str = ""  # en qué paso falló y por qué, si falló

    @property
    def model_seconds(self) -> float:
        return sum(c.seconds for c in self.model_calls)

    @property
    def tool_seconds(self) -> float:
        return sum(t.seconds for t in self.tools)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["model_seconds"] = round(self.model_seconds, 3)
        data["tool_seconds"] = round(self.tool_seconds, 3)
        return data


class Stopwatch:
    def __init__(self) -> None:
        self._start = time.monotonic()

    def seconds(self) -> float:
        return time.monotonic() - self._start


def describe(m: TurnMetrics) -> str:
    """Una línea para el CLI: `⏱ planificador · modelo 2× 14.1 s (3700→41 tok; prompt 5.3 s, generar 8.6 s) · ...`."""
    route = f"{m.route} (receta n.º {m.recipe})" if m.recipe is not None and m.route != "receta" else m.route
    route = f"receta n.º {m.recipe}" if m.route == "receta" and m.recipe is not None else route
    parts = [f"⏱ {route}"]
    if m.recipe_note:
        parts.append(m.recipe_note)
    if m.model_calls:
        tin = sum(c.input_tokens for c in m.model_calls)
        tout = sum(c.output_tokens for c in m.model_calls)
        reading = sum(c.prompt_seconds for c in m.model_calls)
        writing = sum(c.output_seconds for c in m.model_calls)
        split = f"; prompt {reading:.1f} s, generar {writing:.1f} s" if reading or writing else ""
        tokens = f" ({tin}→{tout} tok{split})" if tin or tout else ""
        load = sum(c.load_seconds for c in m.model_calls)
        cold = f" · carga en frío {load:.1f} s" if load >= 1 else ""
        parts.append(f"modelo {len(m.model_calls)}× {m.model_seconds:.1f} s{tokens}{cold}")
    else:
        parts.append("sin modelo")
    if m.tools:
        parts.append(f"herramientas {len(m.tools)}× {m.tool_seconds:.1f} s")
    if m.answer_by_template:
        parts.append("respuesta sin modelo")
    parts.append(f"total {m.seconds:.1f} s")
    return " · ".join(parts)


class MetricsLog:
    """Una línea JSON por turno. Nunca impide responder: si no se puede escribir, se ignora."""

    def __init__(self, directory: Path, prefix: str = "core") -> None:
        self._directory = directory
        self._prefix = prefix

    def record(self, metrics: TurnMetrics) -> None:
        now = datetime.now().astimezone()
        line = {"ts": now.isoformat(timespec="seconds"), **metrics.as_dict()}
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            file = self._directory / f"{self._prefix}-{now:%Y-%m-%d}.jsonl"
            with file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass
