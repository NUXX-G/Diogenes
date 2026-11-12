"""Interfaz común de los proveedores de LLM (Ollama por defecto, otros compatibles como opción)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol


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
    """Lo que costó una llamada según el proveedor. `input_tokens` son los que evaluó de verdad: con la caché de prefijo
    de Ollama, la parte del prompt que ya tenía calculada (sistema, herramientas, historial) no cuenta."""

    input_tokens: int = 0
    output_tokens: int = 0
    load_seconds: float = 0.0  # cargar el modelo en memoria (solo en frío)


@dataclass(frozen=True)
class LLMResponse:
    content: str
    tool_calls: tuple[ToolCall, ...] = field(default_factory=tuple)
    usage: LLMUsage | None = None


class LLMProvider(Protocol):
    async def chat(self, messages: list[Message], tools: list[ToolSpec]) -> LLMResponse: ...
