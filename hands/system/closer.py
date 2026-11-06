"""Cerrar aplicaciones: primero como el botón X (deja guardar) y, solo si sigue abierta, forzar con otra confirmación.

- `close` pide cerrar las ventanas de la app con WM_CLOSE y espera un poco. Antes pide confirmación al usuario
  (devuelve `requiere_confirmacion`, igual que las herramientas del navegador).
- `force_close` termina el proceso. Solo se acepta si antes se intentó el cierre suave de ese mismo proceso y la app sigue
  abierta; la confirmación (distinta, y siempre) la impone el agente porque `apps.force_close` está en la lista de
  herramientas irreversibles de `configuracion/confirmaciones.json`.
- Hay procesos que nunca se cierran (sección `cierre.procesos_protegidos`), además de los de Diogenes mismo y el
  navegador de Diogenes mientras esté abierto.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from hands.apps.models import AppEntry, normalize
from hands.system.windows import Win, WindowBackend

CONFIG_DIR = Path("configuracion")
SOFT_MEMORY_SECONDS = 15 * 60  # cuánto tiempo vale "ya intenté cerrarla suavemente" para poder forzar
MIN_TITLE_SCORE = 90.0


@dataclass(frozen=True)
class CloseConfig:
    confirm_soft_close: bool
    wait_seconds: float
    protected: frozenset[str]  # nombres de ejecutable en minúsculas

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CloseConfig:
        return cls(
            confirm_soft_close=bool(data.get("confirmar_cierre_suave", True)),
            wait_seconds=float(data.get("espera_segundos", 4)),
            protected=frozenset(str(p).lower() for p in data.get("procesos_protegidos", [])),
        )

    @classmethod
    def load(cls, directory: Path = CONFIG_DIR) -> CloseConfig:
        """Sección `cierre` de `confirmaciones.json`; si ese fichero no la trae, la del `.example`."""
        for name in ("confirmaciones.json", "confirmaciones.example.json"):
            file = directory / name
            if file.is_file():
                data = json.loads(file.read_text(encoding="utf-8"))
                if "cierre" in data:
                    return cls.from_dict(data["cierre"])
        raise FileNotFoundError(f"No hay sección 'cierre' en confirmaciones*.json de {directory}")


def _stem(win: Win) -> str:
    return normalize(win.exe_name.removesuffix(".exe"))


def matches(entry: AppEntry, win: Win) -> bool:
    """¿Es esta ventana de esa app? Por el nombre del ejecutable o porque el título contiene el nombre de la app."""
    name = normalize(entry.name)
    if not name:
        return False
    stem = _stem(win)
    if stem and fuzz.ratio(name, stem) >= MIN_TITLE_SCORE:
        return True
    title = normalize(win.title)
    return len(name) >= 4 and fuzz.partial_ratio(name, title) >= MIN_TITLE_SCORE


class AppCloser:
    def __init__(
        self,
        backend: WindowBackend,
        config: CloseConfig,
        protected_pids: Callable[[], set[int]] = lambda: set(),
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._backend = backend
        self._config = config
        self._extra_protected = protected_pids
        self._sleep = sleep
        self._soft_tried: dict[int, float] = {}

    def _protected(self, win: Win) -> bool:
        own = {os.getpid(), os.getppid()}
        return win.exe_name in self._config.protected or win.pid in own or win.pid in self._extra_protected()

    def _windows_of(self, entry: AppEntry) -> list[Win]:
        return [w for w in self._backend.list_windows() if matches(entry, w)]

    def _description(self, wins: list[Win]) -> str:
        exes = sorted({w.exe_name or f"pid {w.pid}" for w in wins})
        titles = "; ".join(w.title[:60] for w in wins[:3])
        return f"{', '.join(exes)} · {len(wins)} ventana(s): {titles}"

    def _refusal(self, entry: AppEntry, wins: list[Win]) -> dict[str, Any] | None:
        if not wins:
            return {"ok": False, "mensaje": f"{entry.name} no está abierta (no veo ninguna ventana suya)."}
        if any(self._protected(w) for w in wins):
            return {
                "ok": False,
                "parar": True,
                "mensaje": f"{entry.name} es un proceso protegido (Diogenes, el sistema o su navegador): no lo cierro.",
            }
        return None

    def close(self, entry: AppEntry, confirmed: bool) -> dict[str, Any]:
        wins = self._windows_of(entry)
        if refusal := self._refusal(entry, wins):
            return refusal
        if self._config.confirm_soft_close and not confirmed:
            return {
                "ok": False,
                "requiere_confirmacion": True,
                "objetivo": f"{entry.name} ({self._description(wins)})",
                "motivo": "se cerrará como con el botón X: si tiene cambios sin guardar, te preguntará",
            }
        for win in wins:
            self._backend.post_close(win.hwnd)
        now = time.monotonic()
        for pid in {w.pid for w in wins}:
            self._soft_tried[pid] = now
        remaining = self._wait_until_closed(wins)
        if not remaining:
            return {"ok": True, "cerrada": True, "mensaje": f"He cerrado {entry.name}."}
        return {
            "ok": True,
            "cerrada": False,
            "sigue_abierta": True,
            "mensaje": (
                f"Le he pedido a {entry.name} que se cierre, pero sigue abierta (quizá pregunta si guardar). "
                "Forzar el cierre perdería los cambios sin guardar y necesita otra confirmación."
            ),
        }

    def _wait_until_closed(self, wins: list[Win]) -> list[Win]:
        hwnds = {w.hwnd for w in wins}
        deadline = time.monotonic() + self._config.wait_seconds
        while True:
            remaining = [w for w in self._backend.list_windows() if w.hwnd in hwnds]
            if not remaining or time.monotonic() >= deadline:
                return remaining
            self._sleep(0.25)

    def force_close(self, entry: AppEntry) -> dict[str, Any]:
        wins = self._windows_of(entry)
        if refusal := self._refusal(entry, wins):
            return refusal
        now = time.monotonic()
        pids = {w.pid for w in wins}
        if not all(now - self._soft_tried.get(pid, -SOFT_MEMORY_SECONDS) < SOFT_MEMORY_SECONDS for pid in pids):
            return {
                "ok": False,
                "mensaje": f"Antes hay que intentar cerrar {entry.name} con apps.close; solo se fuerza si sigue abierta.",
            }
        failed = [pid for pid in pids if not self._backend.terminate(pid)]
        for pid in pids - set(failed):
            self._soft_tried.pop(pid, None)
        if failed:
            return {"ok": False, "mensaje": f"No he podido terminar {entry.name} (procesos {sorted(failed)})."}
        return {"ok": True, "cerrada": True, "forzada": True, "mensaje": f"He forzado el cierre de {entry.name}."}
