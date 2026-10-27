"""hands-mcp: las manos de Diogenes en el anfitrión Windows.

Escucha solo en 127.0.0.1 y exige `Authorization: Bearer <HANDS_MCP_TOKEN>`.
Solo arranca con DIOGENES_MODE=personal: en modo clase no existe.
"""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import uvicorn
from dotenv import load_dotenv
from mcp.server.mcpserver import MCPServer
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from hands.actionlog import ActionLog
from hands.apps import discovery, launcher
from hands.apps.index import AppIndex
from hands.apps.models import AppEntry

log = logging.getLogger("hands")

HOST = "127.0.0.1"
REFRESH_SECONDS = 15 * 60


class TokenAuthMiddleware:
    """ASGI puro: rechaza toda petición HTTP sin el token correcto (comparación en tiempo constante)."""

    def __init__(self, app: ASGIApp, token: str) -> None:
        if not token:
            raise ValueError("HANDS_MCP_TOKEN vacío: hands-mcp no arranca sin token")
        self._app = app
        self._token = token.encode()

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http":
            headers = dict(scope["headers"])
            sent = headers.get(b"authorization", b"")
            expected_prefix = b"Bearer "
            candidate = sent[len(expected_prefix) :] if sent.startswith(expected_prefix) else b""
            if not hmac.compare_digest(candidate, self._token):
                await JSONResponse({"error": "no autorizado"}, status_code=401)(scope, receive, send)
                return
        await self._app(scope, receive, send)


def build_server(
    index: AppIndex,
    launch: Callable[[AppEntry], None] = launcher.launch,
    actions: ActionLog | None = None,
) -> MCPServer:
    server = MCPServer("hands-mcp")

    def audited(tool: str, args: dict[str, Any], run: Callable[[], dict[str, Any]]) -> dict[str, Any]:
        started = time.monotonic()
        try:
            result = run()
        except Exception as exc:
            if actions:
                actions.record(tool, args, False, f"{type(exc).__name__}: {exc}", started)
            raise
        if actions:
            actions.record(tool, args, bool(result.get("ok", True)), str(result.get("mensaje", "")), started)
        return result

    @server.tool(
        name="apps.search",
        description=(
            "Busca aplicaciones y juegos instalados en el equipo por nombre (tolera erratas). "
            "Devuelve candidatos con su id; pásalo a apps.launch para abrirlo."
        ),
    )
    def apps_search(nombre: str, limite: int = 5) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            found = index.search(nombre, limit=max(1, min(limite, 10)))
            return {
                "ok": True,
                "mensaje": f"{len(found)} resultados",
                "resultados": [{"id": r.entry.id, "nombre": r.entry.name, "origen": r.entry.source} for r in found],
            }

        return audited("apps.search", {"nombre": nombre, "limite": limite}, run)

    @server.tool(
        name="apps.launch",
        description="Abre una aplicación o juego usando un id devuelto por apps.search. No acepta otras órdenes.",
    )
    def apps_launch(id: str) -> dict[str, Any]:
        def run() -> dict[str, Any]:
            entry = index.get(id)
            if entry is None:
                return {"ok": False, "mensaje": f"id desconocido: {id}. Usa apps.search primero."}
            launch(entry)
            return {"ok": True, "mensaje": f"Lanzado {entry.name}"}

        return audited("apps.launch", {"id": id}, run)

    return server


def create_app(server: MCPServer, token: str) -> ASGIApp:
    return TokenAuthMiddleware(server.streamable_http_app(host=HOST), token)


async def _refresh_forever(index: AppIndex, every: float = REFRESH_SECONDS) -> None:
    while True:
        await asyncio.sleep(every)
        try:
            index.replace(await asyncio.to_thread(discovery.discover))
        except Exception:
            log.warning("no se pudo refrescar el índice de apps", exc_info=True)


async def serve(index: AppIndex, token: str, port: int, actions: ActionLog) -> None:
    index.replace(await asyncio.to_thread(discovery.discover))
    log.info("índice de apps: %d entradas", len(index))
    server = build_server(index, actions=actions)
    config = uvicorn.Config(create_app(server, token), host=HOST, port=port, log_level="info")
    refresh = asyncio.create_task(_refresh_forever(index))
    try:
        await uvicorn.Server(config).serve()
    finally:
        refresh.cancel()


def main() -> None:
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if os.environ.get("DIOGENES_MODE", "clase") != "personal":
        sys.exit(
            "hands-mcp solo arranca con DIOGENES_MODE=personal (en modo clase el control del PC está desactivado)."
        )
    token = os.environ.get("HANDS_MCP_TOKEN", "")
    if not token:
        sys.exit("Falta HANDS_MCP_TOKEN en .env (ver .env.example).")
    port = int(os.environ.get("HANDS_MCP_PORT", "8765"))
    asyncio.run(serve(AppIndex(), token, port, ActionLog(Path("logs"))))


if __name__ == "__main__":
    main()
