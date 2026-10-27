"""CLI de texto de Diogenes: `uv run python -m core --texto`."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Awaitable, Callable
from contextlib import AsyncExitStack
from typing import Any

from dotenv import load_dotenv

from core.agent import Agent, Step
from core.llm.base import LLMError
from core.llm.ollama import DEFAULT_BASE_URL, DEFAULT_MODEL, OllamaProvider
from core.mcp_client import connect_hands

SALIR = {"salir", "exit", "quit"}


def format_step(step: Step) -> str:
    args = ", ".join(f"{k}={v!r}" for k, v in step.arguments.items())
    return f"  {'✓' if step.ok else '✗'} {step.tool}({args})"


async def repl(
    agent: Agent,
    read: Callable[[], Awaitable[str]],
    write: Callable[[str], None],
) -> None:
    while True:
        try:
            line = (await read()).strip()
        except EOFError:
            return
        if not line:
            continue
        if line.lower() in SALIR:
            return
        try:
            result = await agent.run(line)
        except LLMError as exc:
            write(f"Error del modelo: {exc}")
            continue
        write(f"{result.answer}  ({len(result.steps)} acciones · {result.seconds:.1f} s)")


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
    )

    async def confirm(tool: str, args: dict[str, Any]) -> bool:
        answer = await asyncio.to_thread(input, f"  ⚠ Confirmar {tool}({args})? [s/N] ")
        return answer.strip().lower() in {"s", "si", "sí", "y", "yes"}

    async def read() -> str:
        return await asyncio.to_thread(input, "> ")

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
        agent = Agent(llm, tools, confirm=confirm, on_step=lambda s: print(format_step(s)))
        print(f"Diogenes ({llm.model}). Escribe 'salir' para terminar.")
        try:
            await repl(agent, read, print)
        finally:
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
