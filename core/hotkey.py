"""Atajo global del botón del pánico con `RegisterHotKey` de Windows, sin dependencias ni ganchos de teclado.

Corre en su propio hilo con su bucle de mensajes: el evento llega aunque el agente esté ocupado o la ventana en primer
plano sea otra. El atajo se configura en `configuracion/atajos.json` (`boton_panico`); este módulo no decide qué hacer
al pulsarlo, solo avisa con el `callback`.
"""

from __future__ import annotations

import json
import logging
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

CONFIG_DIR = Path("configuracion")
DEFAULT_COMBO = "ctrl+alt+esc"
HOTKEY_ID = 1
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012
PM_NOREMOVE = 0
MOD_NOREPEAT = 0x4000
START_TIMEOUT_SECONDS = 5.0

_MODIFIERS = {"alt": 0x1, "ctrl": 0x2, "control": 0x2, "shift": 0x4, "win": 0x8}
_KEYS = {"esc": 0x1B, "escape": 0x1B, "space": 0x20, "espacio": 0x20, "enter": 0x0D, "tab": 0x09, "pause": 0x13}
_KEYS |= {f"f{n}": 0x6F + n for n in range(1, 25)}
_KEYS |= {c: ord(c.upper()) for c in "abcdefghijklmnopqrstuvwxyz0123456789"}


def parse_combo(text: str) -> tuple[int, int]:
    """`"ctrl+alt+esc"` → (modificadores, tecla virtual). Exige al menos un modificador y una sola tecla."""
    parts = [p.strip().lower() for p in text.split("+") if p.strip()]
    modifiers = 0
    keys: list[int] = []
    for part in parts:
        if part in _MODIFIERS:
            modifiers |= _MODIFIERS[part]
        elif part in _KEYS:
            keys.append(_KEYS[part])
        else:
            raise ValueError(f"tecla desconocida en el atajo: {part!r}")
    if not modifiers or len(keys) != 1:
        raise ValueError(f"el atajo debe tener modificadores y una sola tecla: {text!r}")
    return modifiers, keys[0]


def load_combo(directory: Path = CONFIG_DIR) -> str:
    """El atajo configurado. Si falta o no es válido, el de siempre: el botón del pánico no puede quedarse sin atajo."""
    for name in ("atajos.json", "atajos.example.json"):
        file = directory / name
        if not file.is_file():
            continue
        combo = json.loads(file.read_text(encoding="utf-8")).get("boton_panico")
        if combo is None:
            continue
        try:
            parse_combo(str(combo))
        except ValueError:
            log.warning("boton_panico no válido en %s: uso %s", name, DEFAULT_COMBO, exc_info=True)
            return DEFAULT_COMBO
        return str(combo)
    return DEFAULT_COMBO


class HotkeyListener:
    def __init__(self, combo: str, callback: Callable[[], None]) -> None:
        self._modifiers, self._key = parse_combo(combo)
        self._callback = callback
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()
        self._registered = False
        self.thread_id = 0

    def start(self) -> bool:
        """Registra el atajo. False si no se puede (otro programa lo usa, o no es Windows)."""
        if sys.platform != "win32":
            return False
        self._thread = threading.Thread(target=self._run, name="diogenes-panic-hotkey", daemon=True)
        self._thread.start()
        self._ready.wait(START_TIMEOUT_SECONDS)
        return self._registered

    def stop(self) -> None:
        if sys.platform != "win32" or self._thread is None or not self._thread.is_alive():
            return
        import ctypes

        ctypes.WinDLL("user32").PostThreadMessageW(self.thread_id, WM_QUIT, 0, 0)
        self._thread.join(START_TIMEOUT_SECONDS)

    def _run(self) -> None:
        import ctypes
        from ctypes import wintypes

        user32: Any = ctypes.WinDLL("user32", use_last_error=True)
        kernel32: Any = ctypes.WinDLL("kernel32")
        message = wintypes.MSG()
        self.thread_id = kernel32.GetCurrentThreadId()
        user32.PeekMessageW(ctypes.byref(message), None, 0, 0, PM_NOREMOVE)  # crea la cola de mensajes del hilo
        self._registered = bool(user32.RegisterHotKey(None, HOTKEY_ID, self._modifiers | MOD_NOREPEAT, self._key))
        self._ready.set()
        if not self._registered:
            log.warning("RegisterHotKey falló (error %s)", ctypes.get_last_error())
            return
        try:
            while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
                if message.message == WM_HOTKEY:
                    self._callback()
        finally:
            user32.UnregisterHotKey(None, HOTKEY_ID)
