"""Proveedor para Ollama (`/api/chat`) con el modo thinking desactivado."""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from typing import Any

import httpx

from core.llm.base import LLMError, LLMResponse, LLMUsage, Message, ToolCall, ToolSpec

DEFAULT_BASE_URL = "http://127.0.0.1:11434"
DEFAULT_MODEL = "qwen3:4b-instruct"
# Ollama descarga el modelo a los 5 min sin uso y recargarlo cuesta ~85 s en la CPU del aula: se mantiene más tiempo.
DEFAULT_KEEP_ALIVE = "30m"
# Cambiar num_ctx o num_thread entre peticiones obliga a Ollama a recargar el modelo: se fijan al arrancar. 4096 no cabe
# todavía: el prompt de sistema y las herramientas ya suman ~4000 tokens y Ollama rechaza lo que no cabe (medido el
# 2026-11-12); se bajará cuando el prompt adelgace.
DEFAULT_NUM_CTX = 8192
# Con los hilos que elige Ollama, el i5-8600 del aula (6 núcleos) leía el prompt a 33 tok/s y generaba a 2,5 tok/s con 4k
# de contexto; con 6 hilos, a 53-66 y 4,2 tok/s (medido el 2026-11-12). 0 deja que decida Ollama.
DEFAULT_NUM_THREAD = 6


def _message_to_wire(m: Message) -> dict[str, Any]:
    wire: dict[str, Any] = {"role": m.role, "content": m.content}
    if m.tool_calls:
        wire["tool_calls"] = [{"function": {"name": c.name, "arguments": c.arguments}} for c in m.tool_calls]
    if m.tool_name:
        wire["tool_name"] = m.tool_name
    return wire


def _tool_to_wire(t: ToolSpec) -> dict[str, Any]:
    return {"type": "function", "function": {"name": t.name, "description": t.description, "parameters": t.parameters}}


def _int(env: Mapping[str, str], name: str, default: int) -> int:
    raw = env.get(name) or ""
    try:
        return int(raw) if raw.strip() else default
    except ValueError:
        raise LLMError(f"{name} debe ser un número entero (vale «{raw}»).") from None


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
        num_ctx: int = DEFAULT_NUM_CTX,
        max_tokens: int = 512,
        timeout: float = 300.0,
        keep_alive: str = DEFAULT_KEEP_ALIVE,
        num_thread: int = DEFAULT_NUM_THREAD,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.model = model
        # num_predict acota la generación: un bucle del modelo no puede quedarse horas (ni cientos de llamadas)
        self._keep_alive = keep_alive
        self._options: dict[str, Any] = {"temperature": temperature, "num_ctx": num_ctx, "num_predict": max_tokens}
        if num_thread > 0:
            self._options["num_thread"] = num_thread
        self._client = client or httpx.AsyncClient(timeout=timeout)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> OllamaProvider:
        """El proveedor con la configuración de las variables `DIOGENES_OLLAMA_*` (las vacías valen por defecto)."""
        env = os.environ if env is None else env
        return cls(
            env.get("DIOGENES_OLLAMA_BASE_URL") or DEFAULT_BASE_URL,
            env.get("DIOGENES_OLLAMA_MODEL") or DEFAULT_MODEL,
            keep_alive=env.get("DIOGENES_OLLAMA_KEEP_ALIVE") or DEFAULT_KEEP_ALIVE,
            num_ctx=_int(env, "DIOGENES_OLLAMA_NUM_CTX", DEFAULT_NUM_CTX),
            num_thread=_int(env, "DIOGENES_OLLAMA_NUM_THREAD", DEFAULT_NUM_THREAD),
        )

    def _body(self, messages: list[Message], tools: list[ToolSpec], options: dict[str, Any]) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": self.model,
            "messages": [_message_to_wire(m) for m in messages],
            "stream": False,
            "think": False,
            "keep_alive": self._keep_alive,
            "options": options,
        }
        if tools:
            body["tools"] = [_tool_to_wire(t) for t in tools]
        return body

    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        return await self._send(self._body(messages, tools, self._options))

    async def warm(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse:
        """Carga el modelo y deja este prefijo en la caché de Ollama generando un solo token: la primera orden de
        verdad ya no paga ni la carga en frío ni la lectura del prompt de sistema y las herramientas."""
        return await self._send(self._body(messages, tools, {**self._options, "num_predict": 1}))

    async def _send(self, body: dict[str, Any]) -> LLMResponse:
        try:
            response = await self._client.post(f"{self.base_url}/api/chat", json=body)
        except httpx.TransportError as exc:
            raise LLMError(f"Ollama no responde en {self.base_url} ({type(exc).__name__}). ¿Está arrancado?") from exc
        if response.status_code == 404:
            raise LLMError(f"Ollama no tiene el modelo '{self.model}'. Descárgalo con: ollama pull {self.model}")
        if response.status_code == 400 and "context size" in response.text:
            raise LLMError(
                f"El prompt no cabe en la ventana de contexto ({self._options['num_ctx']} tokens): "
                "sube DIOGENES_OLLAMA_NUM_CTX (por defecto 8192)."
            )
        if response.status_code >= 400:
            raise LLMError(f"Ollama devolvió {response.status_code}: {response.text[:200]}")
        data = response.json()
        message = data.get("message", {})
        calls = tuple(
            ToolCall(c["function"]["name"], _arguments(c["function"].get("arguments")))
            for c in message.get("tool_calls") or []
        )
        usage = LLMUsage(
            int(data.get("prompt_eval_count") or 0),
            int(data.get("eval_count") or 0),
            float(data.get("load_duration") or 0) / 1e9,
            float(data.get("prompt_eval_duration") or 0) / 1e9,
            float(data.get("eval_duration") or 0) / 1e9,
        )
        return LLMResponse(message.get("content") or "", calls, usage)

    async def aclose(self) -> None:
        await self._client.aclose()
