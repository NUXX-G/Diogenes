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

# Otras formas de pedir la misma tecla (el modelo o el usuario no siempre dicen "play_pausa"): se aceptan en el servicio,
# no solo en el router. Las claves van normalizadas: minúsculas, sin acentos y con "_" entre palabras.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "play_pausa": (
        "pausa", "pausar", "pause", "play", "reproducir", "reanuda", "reanudar", "continua", "continuar", "resume",
        "para", "play_pause", "playpause", "pausa_play", "play_pausar", "pausa_reproduccion", "toggle",
    ),
    "parar": ("detener", "deten", "stop", "detenerse", "parada"),
    "siguiente": ("next", "skip", "siguiente_cancion", "adelante", "next_track"),
    "anterior": ("previous", "prev", "atras", "anterior_cancion", "previous_track"),
    "subir_volumen": ("sube", "subir", "volumen_arriba", "volume_up", "mas_volumen", "vol_up"),
    "bajar_volumen": ("baja", "bajar", "volumen_abajo", "volume_down", "menos_volumen", "vol_down"),
    "silencio": ("mute", "silenciar", "mutear", "callar", "silencia", "unmute", "quitar_sonido"),
}  # fmt: skip


def _slug(text: str) -> str:
    import re
    import unicodedata

    plain = "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def canonical_key(tecla: str) -> str | None:
    """La tecla del catálogo a la que se refiere `tecla` (con sinónimos), o None si no se reconoce."""
    slug = _slug(tecla)
    if slug in VIRTUAL_KEYS:
        return slug
    for key, names in SYNONYMS.items():
        if slug in names:
            return key
    return None


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
        key = canonical_key(tecla)
        if key is None:
            return {"ok": False, "mensaje": f"Tecla desconocida: {tecla}. Opciones: {', '.join(VIRTUAL_KEYS)}."}
        times = max(1, min(int(veces), MAX_PRESSES))
        for _ in range(times):
            self._sender.press(VIRTUAL_KEYS[key])
        return {
            "ok": True,
            "mensaje": f"He pulsado {key} ×{times}. No puedo comprobar si ha cambiado lo que suena.",
            "tecla": key,
            "veces": times,
            "verificado": False,
            "aviso": f"Solo se ha pulsado la tecla: di «he pulsado {key}», nunca que algo suena, está en pausa o no hay nada.",
        }
