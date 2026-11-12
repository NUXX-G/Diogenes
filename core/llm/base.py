"""Interfaz común de los proveedores de LLM (Ollama por defecto, otros compatibles como opción)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol, runtime_checkable


class LLMError(Exception):
    """El proveedor no está disponible o respondió algo inservible; el mensaje dice qué hacer."""


@dataclass(frozen=True)
class ToolSpec:
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, Any]


@dataclass(frozen=True)
class Message:
    role: Literal["system", "user", "assistant", "tool"]
    content: str = ""
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    tool_name: str | None = None  # solo en role == "tool": a qué llamada responde


@dataclass(frozen=True)
class LLMUsage:
    """Lo que costó una llamada según el proveedor. Ollama cuenta en `input_tokens` el prompt entero aunque gran parte
    salga de su caché de prefijo: el coste real de leerlo es `prompt_seconds` (medido en el aula: 113 s en frío y 0,3 s
    con el mismo prefijo en caché). Generar es lo caro: ~3,4 tokens/s en la CPU del aula."""

    input_tokens: int = 0
    output_tokens: int = 0
    load_seconds: float = 0.0  # cargar el modelo en memoria (solo en frío)
    prompt_seconds: float = 0.0  # leer el prompt (lo que no estaba en caché)
    output_seconds: float = 0.0  # generar la respuesta


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    usage: LLMUsage | None = None


class LLMProvider(Protocol):
    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...


@runtime_checkable
class WarmableLLM(Protocol):
    """Un proveedor que sabe precargar el modelo y un prefijo del prompt sin generar una respuesta entera."""

    async def warm(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...
