"""Mide las órdenes típicas: llamadas al modelo, tokens y tiempo por orden, dos pasadas seguidas en la misma sesión.

    uv run python -m core.bench                      # LLM simulado (rápido, sin Ollama)
    uv run python -m core.bench --real               # qwen3:4b-instruct en Ollama (el PC sigue siendo falso)
    uv run python -m core.bench --salida modelos/latencia-base.json

Nunca toca el PC de verdad: hands es real, pero las ventanas, el lanzador, la multimedia y la web son dobles.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

from core.agent import SYSTEM_PROMPT, Agent, _wire_names
from core.bench.fakes import APPS, FakePC
from core.bench.sim import ORDERS, TEACH_PHRASE, TEACH_STEPS, Director, Order, SimulatedLLM, scripted
from core.llm.base import LLMProvider, Message, WarmableLLM
from core.mcp_client import McpTools
from core.recipes import RecipeStore
from core.safety import ConfirmationPolicy
from core.toolsets import ALL, ToolSelector

CALC = APPS[1]


def _reset_world(pc: FakePC) -> None:
    """Cada orden empieza igual: la calculadora abierta y un vídeo sonando."""
    if not any(w.title == "Calculadora" for w in pc.windows.windows):
        pc.windows.open(CALC)
    pc.media.__init__()  # type: ignore[misc]


async def run(
    orders: tuple[Order, ...] = ORDERS,
    *,
    real: LLMProvider | None = None,
    passes: int = 2,
    prepare: Any = None,
    toolset: str = ALL,
    recipes: bool = False,
    teach: bool = False,
    warm: bool = False,
) -> list[dict[str, Any]]:
    """Ejecuta las órdenes `passes` veces en una misma sesión y devuelve una fila por orden y pasada."""
    director = Director()
    llm: LLMProvider = real or director.llm
    pc = FakePC()
    rows: list[dict[str, Any]] = []
    asked: list[str] = []

    async def yes(tool: str, _args: dict[str, Any]) -> bool:
        asked.append(tool)
        return True

    async with _client(pc) as client:
        if warm and real is not None:  # deja el prompt de sistema y las herramientas en la caché de Ollama
            specs, _ = _wire_names(await McpTools(client).tools())
            prime = real.warm if isinstance(real, WarmableLLM) else real.chat
            await prime([Message("system", SYSTEM_PROMPT), Message("user", "hola")], specs)
        store = RecipeStore(":memory:") if recipes or teach else None
        if teach and store is not None:
            await _teach(McpTools(client), store)
        agent = Agent(
            llm,
            McpTools(client),
            policy=ConfirmationPolicy.load(),
            confirm=yes,
            toolset=ToolSelector.load(toolset),
            recipes=store,
        )
        if prepare is not None:
            await prepare(agent, director, pc)
        for n in range(1, passes + 1):
            for order in orders:
                _reset_world(pc)
                director.use(order)
                before = len(director.llm.calls)
                asked.clear()
                result = await agent.run(order.text)
                m = result.metrics
                sim = director.llm.calls[before:]
                model_s = m.model_seconds if real else sum(c.seconds for c in sim)
                code_s = m.seconds - m.model_seconds
                rows.append(
                    {
                        "pasada": n,
                        "orden": order.text,
                        "ruta": m.route,
                        "llamadas_modelo": len(m.model_calls),
                        "tokens_entrada": sum(c.input_tokens for c in m.model_calls),
                        "tokens_salida": sum(c.output_tokens for c in m.model_calls),
                        "segundos_modelo": round(model_s, 2),
                        "segundos_resto": round(code_s, 3),
                        "segundos_total": round(model_s + code_s, 2),
                        "respuesta_sin_modelo": m.answer_by_template,
                        "herramientas": [t.tool for t in m.tools],
                        "confirmaciones": list(asked),
                        "respuesta": result.answer,
                    }
                )
    return rows


async def _teach(tools: McpTools, store: RecipeStore) -> None:
    """Enseña la receta de Hrom con /aprender, como lo haría el usuario, con un modelo simulado aparte: no cuenta en
    la medición (es un coste de una sola vez)."""
    current: list[Any] = []
    teacher = Agent(SimulatedLLM(lambda m, t: scripted(lambda _t: current[0])(m, t)), tools, recipes=store)
    teacher.command(f"/aprender {TEACH_PHRASE}")
    for said, steps in TEACH_STEPS:
        current[:] = [steps]
        await teacher.run(said)
    teacher.command("/fin")


def _client(pc: FakePC) -> Any:
    from mcp import Client

    return Client(pc.server())


def table(rows: list[dict[str, Any]]) -> str:
    out = [
        "| Pasada | Orden | Ruta | Llamadas al modelo | Tokens (entrada→salida) | Tiempo |",
        "|---|---|---|---|---|---|",
    ]
    for r in rows:
        out.append(
            f"| {r['pasada']} | {r['orden']} | {r['ruta']} | {r['llamadas_modelo']} | "
            f"{r['tokens_entrada']}→{r['tokens_salida']} | {r['segundos_total']:.1f} s |"
        )
    total = sum(r["segundos_total"] for r in rows)
    calls = sum(r["llamadas_modelo"] for r in rows)
    out.append(f"| | **Total** | | **{calls}** | | **{total:.1f} s** |")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core.bench", description=__doc__.splitlines()[0])
    parser.add_argument("--real", action="store_true", help="usa Ollama de verdad (el PC sigue siendo falso)")
    parser.add_argument("--pasadas", type=int, default=2)
    parser.add_argument("--salida", type=Path, help="guarda las filas en JSON")
    parser.add_argument("--herramientas", default=ALL, choices=("todas", "relevantes"), help="qué herramientas ve")
    parser.add_argument("--recetas", action="store_true", help="con recetas y alias (base en memoria)")
    parser.add_argument("--ensenar", action="store_true", help="enseña antes la receta de Hrom con /aprender")
    parser.add_argument("--calentar", action="store_true", help="con --real, una llamada previa que no cuenta")
    args = parser.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # la consola de Windows no es UTF-8 por defecto
    real = None
    if args.real:
        from dotenv import load_dotenv

        from core.llm.ollama import OllamaProvider

        load_dotenv()
        real = OllamaProvider.from_env()  # la misma configuración que el CLI (hilos, num_ctx, keep_alive)
    rows = asyncio.run(
        run(
            real=real,
            passes=args.pasadas,
            toolset=args.herramientas,
            recipes=args.recetas,
            teach=args.ensenar,
            warm=args.calentar,
        )
    )
    print(table(rows))
    for r in rows:
        print(f"- {r['orden']} (pasada {r['pasada']}): {r['herramientas']} → {r['respuesta'][:120]!r}")
    if args.salida:
        args.salida.parent.mkdir(parents=True, exist_ok=True)
        args.salida.write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main())
