"""Proveedor para Ollama (`/api/chat`) con el modo thinking desactivado."""

from __future__ import annotations

import json
from typing import Any

import httpx

from core.llm.base import LLMError, LLMResponse, Message, ToolCall, ToolSpec

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b-instruct"


def _message_to_wire(m: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        wire["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]
    if m.tool_name:
        wire["tool_name"] = m.tool_name
    return wire


def _tool_to_wire(t: ToolSpec) -> dict[str, Any]:
    return {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}


def _arguments(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            return {}
    return raw if isinstance(raw, dict) else {}


class OllamaProvider:
    def __init__(
        self,
        base_url: str = DEFAULT_BASE_URL,
        model: str = DEFAULT_MODEL,
        *,
        temperature: float = 0.2,
        num_ctx: int = 8192,
        timeout: float = 300.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        self._options = {"temperature": temperature, "num_ctx": num_ctx}
        self._client = client or httpx.AsyncClient(timeout=timeout)

    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_wire(m) for m in messages],
            "stream": False,
            "think": False,
            "options": self._options,
        }
        if tools:
            body["tools"] = [_tool_to_wire(t) for t in tools]
        try:
            response = await self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.TransportError as exc:
            raise LLMError(f"Ollama no responde en {self.base_url} ({type(exc).__name__}). ¿Está arrancado?") from exc
        if response.status_code == 404:
            raise LLMError(f"Ollama no tiene el modelo '{self.model}'. Descárgalo con: ollama pull {self.model}")
        if response.status_code >= 400:
            raise LLMError(f"Ollama devolvió {response.status_code}: {response.text[:200]}")
        message = response.json().get("message", {})
        calls = tuple(
            ToolCall(c["function"]["name"], _arguments(c["function"].get("arguments")))
            for c in message.get("tool_calls") or []
        )
        return LLMResponse(message.get("content") or "", calls)

    async def aclose(self) -> None:
        await self._client.aclose()
