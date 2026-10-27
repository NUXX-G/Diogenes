"""Cliente MCP de diogenes-core hacia hands-mcp (HTTP en 127.0.0.1 con token)."""

from __future__ import annotations

from contextlib import AsyncExitStack
from typing import Any, Protocol

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

from core.llm.base import ToolSpec


class ToolBackend(Protocol):
    async def tools(self) -> list[ToolSpec]: ...

    async def call(self, name: str, arguments: dict[str, Any]) -> str: ...


class McpTools:
    """Envuelve un `mcp.Client` ya conectado. Para tests se le pasa un cliente en proceso."""

    def __init__(self, client: Client) -> None:
        self._client = client

    async def tools(self) -> list[ToolSpec]:
        listed = await self._client.list_tools()
        return [ToolSpec(t.name, t.description or "", dict(t.input_schema)) for t in listed.tools]

    async def call(self, name: str, arguments: dict[str, Any]) -> str:
        result = await self._client.call_tool(name, arguments)
        text = "\n".join(block.text for block in result.content if getattr(block, "type", "") == "text")
        return f"ERROR: {text}" if result.is_error else text


async def connect_hands(stack: AsyncExitStack, url: str, token: str) -> McpTools:
    http = await stack.enter_async_context(httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"}))
    client = await stack.enter_async_context(Client(streamable_http_client(url, http_client=http)))
    return McpTools(client)
