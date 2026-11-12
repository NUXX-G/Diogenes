"""Sesiones multimedia de Windows (System Media Transport Controls): qué suena, en qué app y en qué estado.

`media.key` pulsa una tecla y no sabe qué pasó. Esto lee las sesiones que Windows conoce (Spotify, el navegador, el
reproductor...) con su título y su estado, actúa sobre UNA concreta y VUELVE A LEER el estado para verificar que cambió.
Solo se afirma lo que se leyó después de actuar.

La API es WinRT (`winrt-Windows.Media.Control`, versión fijada). Si no está disponible (otro sistema, la política del
centro bloquea el módulo...), `Sessions` lo dice con su motivo y `media.control` cae a `media.key` con una respuesta
honesta ("he pulsado la tecla; no puedo comprobar si cambió").
"""

from __future__ import annotations

import asyncio
import re
import unicodedata
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

CLOSED, OPENED, CHANGING, STOPPED, PLAYING, PAUSED = range(6)  # GlobalSystemMediaTransportControlsSessionPlaybackStatus
STATUS_NAMES = {
    CLOSED: "cerrada",
    OPENED: "abierta",
    CHANGING: "cambiando",
    STOPPED: "parada",
    PLAYING: "reproduciendo",
    PAUSED: "en pausa",
}
VERIFY_SECONDS = 2.0  # cuánto se espera a que el estado cambie tras actuar
POLL_SECONDS = 0.25

# acción → nombre de la acción canónica. Las claves van normalizadas (minúsculas, sin acentos, "_" entre palabras).
ACTIONS: dict[str, tuple[str, ...]] = {
    "pause": ("pausa", "pausar", "pause", "pon_en_pausa"),
    "play": ("play", "reanuda", "reanudar", "reproducir", "continuar", "continua", "resume"),
    "toggle": ("play_pausa", "play_pause", "playpause", "toggle", "alternar"),
    "next": ("siguiente", "next", "skip", "adelante"),
    "previous": ("anterior", "previous", "prev", "atras"),
    "stop": ("parar", "stop", "detener", "deten"),
}
EXPECTED_AFTER = {"pause": {PAUSED}, "play": {PLAYING}, "stop": {STOPPED, CLOSED}}
KEY_FOR = {  # a qué tecla multimedia cae cada acción si la API no está
    "pause": "play_pausa",
    "play": "play_pausa",
    "toggle": "play_pausa",
    "next": "siguiente",
    "previous": "anterior",
    "stop": "parar",
}


def _slug(text: str) -> str:
    plain = "".join(c for c in unicodedata.normalize("NFD", text.casefold()) if unicodedata.category(c) != "Mn")
    return re.sub(r"[^a-z0-9]+", "_", plain).strip("_")


def canonical_action(accion: str) -> str | None:
    slug = _slug(accion)
    for action, names in ACTIONS.items():
        if slug == action or slug in names:
            return action
    return None


class SessionsUnavailable(RuntimeError):
    """La API de sesiones multimedia de Windows no se puede usar; el texto es el motivo."""


@dataclass(frozen=True)
class MediaSession:
    id: str  # estable mientras la sesión exista: la app (y #2, #3 si hay varias de la misma)
    app: str
    title: str
    artist: str
    status: int

    @property
    def estado(self) -> str:
        return STATUS_NAMES.get(self.status, "desconocido")

    def as_dict(self) -> dict[str, Any]:
        return {"id": self.id, "app": self.app, "titulo": self.title, "artista": self.artist, "estado": self.estado}


class SessionBackend(Protocol):
    async def sessions(self) -> list[MediaSession]:
        """Las sesiones que Windows conoce ahora. Lanza `SessionsUnavailable` si no se puede leer."""

    async def act(self, session_id: str, action: str) -> bool:
        """Pide la acción a esa sesión; True si Windows la aceptó (no que haya surtido efecto)."""


class WinrtBackend:
    """Implementación real con `winrt-Windows.Media.Control`. Solo en Windows; los módulos se importan al usarla."""

    @staticmethod
    async def _manager() -> Any:
        try:
            from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        except Exception as exc:
            raise SessionsUnavailable(
                f"no puedo cargar la API de sesiones multimedia ({type(exc).__name__}: {exc})"
            ) from exc
        try:
            return await Manager.request_async()
        except Exception as exc:
            raise SessionsUnavailable(
                f"Windows no me deja leer las sesiones multimedia ({type(exc).__name__}: {exc})"
            ) from exc

    @staticmethod
    def _ids(raw: list[Any]) -> list[tuple[str, Any]]:
        seen: dict[str, int] = {}
        out: list[tuple[str, Any]] = []
        for session in raw:
            app = str(session.source_app_user_model_id)
            seen[app] = seen.get(app, 0) + 1
            out.append((app if seen[app] == 1 else f"{app}#{seen[app]}", session))
        return out

    async def sessions(self) -> list[MediaSession]:
        manager = await self._manager()
        found: list[MediaSession] = []
        for sid, session in self._ids(list(manager.get_sessions())):
            try:
                props = await session.try_get_media_properties_async()
                title, artist = str(props.title or ""), str(props.artist or "")
            except Exception:  # noqa: BLE001  una sesión que no da propiedades se lista igual, sin título
                title = artist = ""
            found.append(
                MediaSession(
                    sid,
                    str(session.source_app_user_model_id),
                    title,
                    artist,
                    int(session.get_playback_info().playback_status),
                )
            )
        return found

    async def act(self, session_id: str, action: str) -> bool:
        manager = await self._manager()
        calls: dict[str, str] = {
            "pause": "try_pause_async",
            "play": "try_play_async",
            "toggle": "try_toggle_play_pause_async",
            "next": "try_skip_next_async",
            "previous": "try_skip_previous_async",
            "stop": "try_stop_async",
        }
        for sid, session in self._ids(list(manager.get_sessions())):
            if sid == session_id:
                return bool(await getattr(session, calls[action])())
        return False


def _norm(text: str) -> str:
    return _slug(text).replace("_", " ")


class Sessions:
    def __init__(
        self,
        backend: SessionBackend,
        press_key: Callable[[str, int], dict[str, Any]] | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        verify_seconds: float = VERIFY_SECONDS,
    ) -> None:
        self._backend = backend
        self._press_key = press_key
        self._sleep = sleep
        self._verify_seconds = verify_seconds

    async def listing(self) -> dict[str, Any]:
        try:
            found = await self._backend.sessions()
        except SessionsUnavailable as exc:
            return {
                "ok": False,
                "disponible": False,
                "mensaje": (
                    f"No puedo ver qué suena: {exc}. Usa media_key: solo podré decir que he pulsado la tecla, "
                    "no si ha cambiado algo."
                ),
            }
        items = [s.as_dict() for s in found]
        mensaje = (
            f"{len(items)} sesiones multimedia" if items else "Windows no ve ninguna sesión multimedia ahora mismo"
        )
        siguiente = (
            "Para actuar sobre una: media_control con su id en sesion y la acción; devuelve el estado comprobado."
            if items
            else "Si el usuario pidió pausar, reanudar o cambiar de pista, prueba media_key: solo pulsa la tecla y "
            "solo podrás decir que la has pulsado."
        )
        return {"ok": True, "disponible": True, "mensaje": mensaje, "sesiones": items, "siguiente": siguiente}

    @staticmethod
    def _pick(found: list[MediaSession], wanted: str) -> tuple[MediaSession | None, list[MediaSession]]:
        """La sesión pedida (por id, app o título) o, sin pedir ninguna, la única que suena (o la única que hay)."""
        if not wanted.strip():
            playing = [s for s in found if s.status == PLAYING]
            pool = playing or found
            return (pool[0], []) if len(pool) == 1 else (None, pool)
        key = _norm(wanted)
        exact = [s for s in found if key in {_norm(s.id), _norm(s.app)}]
        loose = exact or [s for s in found if key in _norm(s.app) or key in _norm(s.title) or key in _norm(s.id)]
        return (loose[0], []) if len(loose) == 1 else (None, loose)

    async def _settled(self, session_id: str, before: MediaSession, action: str) -> MediaSession | None:
        """Vuelve a leer la sesión hasta que el estado sea el esperado (o se acabe el tiempo). None si ya no existe."""
        waited, current = 0.0, None
        while True:
            current = next((s for s in await self._backend.sessions() if s.id == session_id), None)
            if current is None:
                return None
            if action in EXPECTED_AFTER:
                done = current.status in EXPECTED_AFTER[action]
            elif action == "toggle":
                done = current.status != before.status
            else:  # next / previous: cambia la pista
                done = (current.title, current.artist) != (before.title, before.artist)
            if done or waited >= self._verify_seconds:
                return current
            await self._sleep(POLL_SECONDS)
            waited += POLL_SECONDS

    async def control(self, sesion: str, accion: str) -> dict[str, Any]:
        action = canonical_action(accion)
        if action is None:
            return {"ok": False, "mensaje": f"Acción desconocida: {accion}. Opciones: {', '.join(ACTIONS)}."}
        try:
            found = await self._backend.sessions()
        except SessionsUnavailable as exc:
            return self._fallback(action, str(exc))
        if not found:
            return {
                "ok": False,
                "mensaje": (
                    "Windows no ve ninguna sesión multimedia activa, así que no puedo controlar ni comprobar nada con la API. "
                    "Si el usuario insiste, prueba media_key (solo pulsa la tecla; no podré decir si cambió algo)."
                ),
            }
        target, candidates = self._pick(found, sesion)
        if target is None:
            if not candidates:
                return {
                    "ok": False,
                    "mensaje": f"No hay ninguna sesión que encaje con «{sesion}».",
                    "sesiones": [s.as_dict() for s in found],
                }
            return {
                "ok": False,
                "mensaje": "Hay varias sesiones posibles: pregunta cuál (usa su id).",
                "sesiones": [s.as_dict() for s in candidates],
            }
        accepted = await self._backend.act(target.id, action)
        after = await self._settled(target.id, target, action)
        return self._report(action, target, after, accepted)

    @staticmethod
    def _report(action: str, before: MediaSession, after: MediaSession | None, accepted: bool) -> dict[str, Any]:
        base = {"sesion": before.id, "app": before.app, "accion": action, "estado_antes": before.estado}
        if after is None:
            return {
                **base,
                "ok": True,
                "verificado": False,
                "mensaje": f"He pedido «{action}» a {before.app}, pero la sesión ya no existe; no puedo comprobar el resultado.",
            }
        base |= {"estado_despues": after.estado, "titulo": after.title}
        if action in EXPECTED_AFTER:
            changed = after.status in EXPECTED_AFTER[action]
            claim = f"{before.app} ahora está {after.estado}"
        elif action == "toggle":
            changed = after.status != before.status
            claim = f"{before.app} pasó de {before.estado} a {after.estado}"
        else:
            changed = (after.title, after.artist) != (before.title, before.artist)
            claim = f"{before.app} ahora tiene «{after.title}»"
        if changed:
            return {**base, "ok": True, "verificado": True, "mensaje": f"Hecho y comprobado: {claim}."}
        why = "Windows rechazó la orden" if not accepted else "la orden se aceptó pero el estado no cambió"
        return {
            **base,
            "ok": False,
            "verificado": False,
            "mensaje": f"No ha cambiado nada: {why}; {before.app} sigue {after.estado}.",
        }

    def _fallback(self, action: str, reason: str) -> dict[str, Any]:
        """Sin la API: se pulsa la tecla y se dice solo eso."""
        key = KEY_FOR[action]
        if self._press_key is None:
            return {"ok": False, "disponible": False, "mensaje": f"No puedo usar las sesiones multimedia: {reason}."}
        pressed = self._press_key(key, 1)
        toggle = " (es la tecla de alternar play/pausa: no sé en qué estado estaba)" if key == "play_pausa" else ""
        return {
            "ok": bool(pressed.get("ok")),
            "verificado": False,
            "disponible": False,
            "mensaje": f"He pulsado {key}{toggle}; no puedo comprobar si ha cambiado lo que suena ({reason}).",
        }
