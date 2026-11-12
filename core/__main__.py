"""CLI de texto de Diogenes: `uv run python -m core --texto`."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from pathlib import Path

from dotenv import load_dotenv

from core.agent import PANIC_TOOL, Agent, Step
from core.hotkey import HotkeyListener, load_combo
from core.llm.ollama import DEFAULT_BASE_URL, DEFAULT_KEEP_ALIVE, DEFAULT_MODEL, DEFAULT_NUM_CTX, OllamaProvider
from core.mcp_client import connect_hands
from core.metrics import MetricsLog
from core.recipes import RecipeStore
from core.session import Session
from core.toolsets import ToolSelector


def format_step(step: Step) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in step.arguments.items())
    line = f"  {'✓' if step.ok else '✗'} {step.tool}({args})"
    try:
        data = json.loads(step.result)
    except json.JSONDecodeError:
        return line
    if isinstance(data, dict) and data.get("abierto_en"):  # web.open / web.site_search: cómo y dónde se hizo
        how = f"{data['estrategia']} · " if data.get("estrategia") else ""
        line += f"  → {how}{data['abierto_en']}"
    return line


async def repl(
    agent: Agent,
    read: Callable[[], Awaitable[str]],
    write: Callable[[str], None],
    session: Session | None = None,
) -> None:
    await (session or Session(write)).serve(agent, read)


async def amain() -> int:
    mode = os.environ.get("DIOGENES_MODE", "clase")
    if mode != "personal":
        print("El control del PC solo funciona con DIOGENES_MODE=personal (ver .env.example).", file=sys.stderr)
        return 2
    token = os.environ.get("HANDS_MCP_TOKEN", "")
    if not token:
        print("Falta HANDS_MCP_TOKEN en .env (ver .env.example).", file=sys.stderr)
        return 2
    url = f"http://127.0.0.1:{os.environ.get('HANDS_MCP_PORT', '8765')}/mcp"
    llm = OllamaProvider(
        os.environ.get("DIOGENES_OLLAMA_BASE_URL", DEFAULT_BASE_URL),
        os.environ.get("DIOGENES_OLLAMA_MODEL", DEFAULT_MODEL),
        keep_alive=os.environ.get("DIOGENES_OLLAMA_KEEP_ALIVE", DEFAULT_KEEP_ALIVE),
        num_ctx=int(os.environ.get("DIOGENES_OLLAMA_NUM_CTX", DEFAULT_NUM_CTX)),
    )

    async def read() -> str:
        return await asyncio.to_thread(input, "> ")

    recipes = RecipeStore(Path(os.environ.get("DIOGENES_DB_PATH", "data/diogenes.db")))
    async with AsyncExitStack() as stack:
        try:
            tools = await connect_hands(stack, url, token)
            await tools.tools()  # falla ya si hands-mcp no está o el token es otro
        except Exception as exc:  # noqa: BLE001
            print(
                f"No puedo conectar con hands-mcp en {url} ({type(exc).__name__}).\n"
                "Arráncalo en otra terminal con: uv run python -m hands.server",
                file=sys.stderr,
            )
            return 1
        session = Session(
            print,
            release=lambda: tools.call(PANIC_TOOL, {}),
            metrics=MetricsLog(Path("logs")),
            show_metrics=os.environ.get("DIOGENES_TIEMPOS", "1") != "0",
        )
        agent = Agent(
            llm,
            tools,
            confirm=session.confirm,
            on_step=lambda s: print(format_step(s)),
            toolset=ToolSelector.load(os.environ.get("DIOGENES_HERRAMIENTAS", "todas")),
            recipes=recipes,
        )
        loop = asyncio.get_running_loop()
        combo = load_combo()
        hotkey = HotkeyListener(combo, lambda: loop.call_soon_threadsafe(session.panic))
        print(f"Diogenes ({llm.model}). Escribe 'salir' para terminar o '/nuevo' para olvidar la conversación.")
        print(
            "Recetas: /aprender <frase> · /fin · /cancelar · /recetas · /receta borrar <n.º> · /alias · /alias borrar <x>"
        )
        if hotkey.start():
            print(f"Botón del pánico: {combo} o /parar (también /para).")
        else:
            print(f"AVISO: no he podido registrar {combo} (¿lo usa otro programa?). Para cancelar, escribe /parar.")
        try:
            await repl(agent, read, print, session)
        finally:
            hotkey.stop()
            await llm.aclose()
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="core", description="Diogenes, asistente personal con IA local")
    parser.add_argument("--texto", action="store_true", help="CLI de texto")
    args = parser.parse_args(argv)
    if not args.texto:
        parser.error("de momento solo existe --texto")
    load_dotenv()
    try:
        return asyncio.run(amain())
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
