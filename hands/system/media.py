"""Teclas multimedia por código de tecla virtual (VK_*), no escribiendo texto.

`keybd_event` con el código virtual funciona con cualquier distribución de teclado e idioma y la recibe la app
multimedia que tenga el foco de sonido (Spotify, el navegador, el reproductor del sistema…).
"""

from __future__ import annotations

import sys
from typing import Any, Protocol

KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
MAX_PRESSES = 20

VIRTUAL_KEYS: dict[str, int] = {
    "play_pausa": 0xB3,  # VK_MEDIA_PLAY_PAUSE
    "siguiente": 0xB0,  # VK_MEDIA_NEXT_TRACK
    "anterior": 0xB1,  # VK_MEDIA_PREV_TRACK
    "parar": 0xB2,  # VK_MEDIA_STOP
    "subir_volumen": 0xAF,  # VK_VOLUME_UP
    "bajar_volumen": 0xAE,  # VK_VOLUME_DOWN
    "silencio": 0xAD,  # VK_VOLUME_MUTE
}


class KeySender(Protocol):
    def press(self, vk: int) -> None:
        """Pulsa y suelta una tecla virtual."""


class WinKeySender:
    """`keybd_event` de user32. Solo se construye en Windows."""

    def __init__(self) -> None:
        if sys.platform != "win32":
            raise RuntimeError("Las teclas multimedia solo se envían en Windows")
        import ctypes
        from ctypes import wintypes

        self._keybd_event = ctypes.WinDLL("user32").keybd_event
        self._keybd_event.argtypes = [wintypes.BYTE, wintypes.BYTE, wintypes.DWORD, ctypes.c_size_t]

    def press(self, vk: int) -> None:
        self._keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY, 0)
        self._keybd_event(vk, 0, KEYEVENTF_EXTENDEDKEY | KEYEVENTF_KEYUP, 0)


class Media:
    def __init__(self, sender: KeySender) -> None:
        self._sender = sender

    def key(self, tecla: str, veces: int = 1) -> dict[str, Any]:
        vk = VIRTUAL_KEYS.get(tecla)
        if vk is None:
            return {"ok": False, "mensaje": f"Tecla desconocida: {tecla}. Opciones: {', '.join(VIRTUAL_KEYS)}."}
        times = max(1, min(int(veces), MAX_PRESSES))
        for _ in range(times):
            self._sender.press(vk)
        return {"ok": True, "mensaje": f"{tecla} ×{times}", "tecla": tecla, "veces": times}
