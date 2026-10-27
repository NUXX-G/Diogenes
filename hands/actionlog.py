"""Registro de cada acción (herramienta, argumentos, resultado) en `logs/`, en JSON por línea.

Nunca guarda capturas ni texto de formularios: solo lo que se pidió y si salió bien.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path
from typing import Any


class ActionLog:
    def __init__(self, directory: Path, prefix: str = "hands") -> None:
        self._directory = directory
        self._prefix = prefix

    def record(self, tool: str, args: dict[str, Any], ok: bool, summary: str, started: float) -> None:
        now = datetime.now().astimezone()
        line = {
            "ts": now.isoformat(timespec="seconds"),
            "tool": tool,
            "args": args,
            "ok": ok,
            "result": summary[:300],
            "ms": round((time.monotonic() - started) * 1000),
        }
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            file = self._directory / f"{self._prefix}-{now:%Y-%m-%d}.jsonl"
            with file.open("a", encoding="utf-8") as fh:
                fh.write(json.dumps(line, ensure_ascii=False) + "\n")
        except OSError:
            pass  # el registro nunca debe impedir que la acción responda
